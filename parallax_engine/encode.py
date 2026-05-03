"""
parallax_engine/encode.py — FFmpeg encoder subprocess wrapper (§2.8).

Receives premultiplied-RGBA ``float32`` frames from the render pipeline,
converts each to ``uint8`` RGBA, and streams them to an FFmpeg subprocess
that encodes with ``libopenh264`` to an MP4 file.

Encoding parameters (non-negotiable per §2.8 and §7):
    -c:v libopenh264       — LGPL H.264 codec; Cisco pays MPEG-LA royalties
    -threads 1             — required for byte-identical (deterministic) output
    -pix_fmt yuv420p       — standard H.264 chroma sub-sampling
    -movflags +faststart   — moov atom at start for streaming

Usage
-----
Low-level::

    proc = open_encoder(out_path, width, height, fps)
    for frame_rgba_f32 in frames:
        write_frame(proc, frame_rgba_f32)
    close_encoder(proc)

Context manager::

    with Encoder(out_path, width, height, fps) as enc:
        for frame in frames:
            enc.write(frame)

SPEC anchors: §2.8, §5.2, §7
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# FFmpeg binary resolution (§5, §7)
# ---------------------------------------------------------------------------
#
# parallax-engine ships with the LGPL FFmpeg build (libopenh264 only) under
# <sys.prefix>/bin. Bare "ffmpeg" lookups via subprocess depend on
# os.environ["PATH"], which does NOT include <sys.prefix>/bin when Python is
# invoked as ".conda-env/bin/python" (no shell activation). That can cause
# subprocess to either (a) fail with FileNotFoundError or (b) — worse —
# silently pick up a GPL-tainted system ffmpeg. Both contaminate the resale
# story and are caught by tools/validate_licensing.py.
#
# All FFmpeg invocations in parallax_engine/* MUST go through _ffmpeg_binary()
# so the env-local LGPL build is always selected.

def _ensure_env_bin_on_path() -> None:
    """Prepend ``<sys.prefix>/bin`` to ``os.environ['PATH']`` (idempotent)."""
    env_bin = str(Path(sys.prefix) / "bin")
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if not parts or parts[0] != env_bin:
        parts = [p for p in parts if p != env_bin]
        os.environ["PATH"] = os.pathsep.join([env_bin] + parts)


def _ffmpeg_binary() -> str:
    """
    Return the absolute path to the FFmpeg binary parallax-engine should
    invoke. Always prefers the env-local LGPL build at ``<sys.prefix>/bin``.

    Raises
    ------
    FileNotFoundError
        If no ffmpeg is on PATH after prepending ``<sys.prefix>/bin``.
    """
    _ensure_env_bin_on_path()
    binary = shutil.which("ffmpeg")
    if binary is None:
        raise FileNotFoundError(
            f"ffmpeg not found on PATH (after prepending "
            f"{Path(sys.prefix) / 'bin'}). Install the LGPL build of FFmpeg "
            "into the Python environment."
        )
    return binary


# Run the PATH fix once at import time so subprocesses launched later in the
# same process (e.g. the validator invoked by test_validate_licensing_passes)
# inherit a PATH that resolves to the env-local LGPL FFmpeg.
_ensure_env_bin_on_path()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: FFmpeg arguments that are non-negotiable (§2.8, §7). The leading binary is
#: resolved via _ffmpeg_binary() at call time, not stored here.
_ENCODER_ARGS_TEMPLATE = [
    "-y",
    "-f", "rawvideo",
    "-pix_fmt", "rgba",
    # -s and -r inserted at call time
    "-i", "-",
    "-c:v", "libopenh264",
    # -b:v and -g inserted at call time
    "-pix_fmt", "yuv420p",
    "-movflags", "+faststart",
    "-threads", "1",   # determinism guarantee (§7)
]


# ---------------------------------------------------------------------------
# Low-level API
# ---------------------------------------------------------------------------

def open_encoder(
    out_path: str | Path,
    width: int,
    height: int,
    fps: int,
    bitrate_kbps: int = 8000,
) -> subprocess.Popen:
    """
    Open FFmpeg subprocess for encoding.

    Parameters
    ----------
    out_path:
        Destination MP4 file path.
    width, height:
        Frame dimensions in pixels.
    fps:
        Output frame rate.
    bitrate_kbps:
        Target bitrate in kbps (default 8000 = 8 Mbit/s).

    Returns
    -------
    A :class:`subprocess.Popen` object.  Caller must write frames to
    ``proc.stdin`` and call :func:`close_encoder` when done.
    """
    gop = fps * 2   # keyframe interval
    cmd = [
        _ffmpeg_binary(), "-y",
        "-f", "rawvideo",
        "-pix_fmt", "rgba",
        "-s", f"{width}x{height}",
        "-r", str(fps),
        "-i", "-",
        "-c:v", "libopenh264",
        "-b:v", f"{bitrate_kbps}k",
        "-g", str(gop),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-threads", "1",   # determinism (§7)
        str(out_path),
    ]
    # IMPORTANT: stderr goes to a TemporaryFile, not subprocess.PIPE.
    # FFmpeg writes progress + warnings to stderr continuously during encoding
    # (a few hundred bytes per frame). With stderr=subprocess.PIPE and no
    # background reader thread, the OS pipe buffer (~64 KiB on macOS / Linux)
    # fills after a few hundred frames, FFmpeg blocks on its next stderr
    # write, stops reading stdin, and Python deadlocks on the next
    # stdin.write(). That's the "render appears stuck at ~7-10 MB" failure
    # mode. Using an OS-backed temp file gives FFmpeg unbounded stderr capacity
    # while still preserving the bytes for diagnostic readout in close_encoder.
    stderr_file = tempfile.TemporaryFile(prefix="parallax_ffmpeg_stderr_")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=stderr_file)
    # Stash the file handle on the proc object so close_encoder can read it.
    proc._stderr_tempfile = stderr_file  # type: ignore[attr-defined]
    return proc


def write_frame(proc: subprocess.Popen, frame_f32: np.ndarray) -> None:
    """
    Write one frame to the encoder.

    Parameters
    ----------
    proc:
        Open encoder process returned by :func:`open_encoder`.
    frame_f32:
        ``(H, W, 4)`` float32 premultiplied RGBA in ``[0.0, 1.0]``.
        Values are clipped before conversion.
    """
    frame_u8 = (np.clip(frame_f32, 0.0, 1.0) * 255.0).astype(np.uint8)
    proc.stdin.write(frame_u8.tobytes())  # type: ignore[union-attr]


def close_encoder(proc: subprocess.Popen) -> None:
    """
    Close the encoder subprocess and wait for it to finish.

    Closes stdin (signals end-of-stream to FFmpeg) then waits for the
    process to exit.

    Raises
    ------
    RuntimeError
        If FFmpeg exits with a non-zero return code.
    """
    # Close stdin to signal EOF to FFmpeg, wait for it to finish,
    # then read any stderr output for diagnostics.
    if proc.stdin and not proc.stdin.closed:
        proc.stdin.close()  # type: ignore[union-attr]
    proc.wait()
    stderr_bytes = b""
    stderr_file = getattr(proc, "_stderr_tempfile", None)
    if stderr_file is not None:
        try:
            stderr_file.seek(0)
            stderr_bytes = stderr_file.read()
        except Exception:
            pass
        finally:
            try:
                stderr_file.close()
            except Exception:
                pass
    if proc.returncode != 0:
        raise RuntimeError(
            f"FFmpeg exited with code {proc.returncode}:\n"
            + (stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else "")
        )


# ---------------------------------------------------------------------------
# Context-manager wrapper
# ---------------------------------------------------------------------------

class Encoder:
    """
    Context-manager wrapper around the FFmpeg encoder subprocess.

    Ensures ``close_encoder`` is always called even if the render loop
    raises an exception.

    Example::

        with Encoder("out.mp4", 1920, 1080, 30) as enc:
            for frame in frames:
                enc.write(frame)
    """

    def __init__(
        self,
        out_path: str | Path,
        width: int,
        height: int,
        fps: int,
        bitrate_kbps: int = 8000,
    ) -> None:
        self.out_path = out_path
        self.width = width
        self.height = height
        self.fps = fps
        self.bitrate_kbps = bitrate_kbps
        self._proc: subprocess.Popen | None = None

    def __enter__(self) -> "Encoder":
        self._proc = open_encoder(
            self.out_path, self.width, self.height,
            self.fps, self.bitrate_kbps,
        )
        return self

    def write(self, frame_f32: np.ndarray) -> None:
        """Write one premultiplied float32 RGBA frame."""
        assert self._proc is not None, "Encoder not started"
        write_frame(self._proc, frame_f32)

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._proc is not None:
            try:
                if exc_type is None:
                    # Normal exit: close stdin + wait + check return code
                    close_encoder(self._proc)
                else:
                    # Error path: close stdin gracefully; don't raise on FFmpeg error
                    try:
                        if self._proc.stdin and not self._proc.stdin.closed:
                            self._proc.stdin.close()  # type: ignore[union-attr]
                        self._proc.wait(timeout=5)
                    except Exception:
                        pass
                    # Clean up stderr temp file even on the error path
                    stderr_file = getattr(self._proc, "_stderr_tempfile", None)
                    if stderr_file is not None:
                        try:
                            stderr_file.close()
                        except Exception:
                            pass
            finally:
                self._proc = None
        return False   # do not suppress exceptions
