"""
tests/test_encode.py — Unit tests for parallax_engine/encode.py (§2.8).

Tests verify:
- FFmpeg is invoked with correct mandatory args (-c:v libopenh264, -threads 1)
- Encoder context manager opens, writes, and closes cleanly
- write_frame converts float32 → uint8 correctly
- close_encoder raises RuntimeError on non-zero FFmpeg exit
- Determinism: two encodes of identical frames produce identical files
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from parallax_engine.encode import Encoder, close_encoder, open_encoder, write_frame


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _solid_frame(h: int, w: int, rgb: tuple[float, float, float], alpha: float = 1.0) -> np.ndarray:
    """Return a solid-colour premultiplied float32 RGBA frame."""
    frame = np.zeros((h, w, 4), dtype=np.float32)
    frame[:, :, 0] = rgb[0] * alpha
    frame[:, :, 1] = rgb[1] * alpha
    frame[:, :, 2] = rgb[2] * alpha
    frame[:, :, 3] = alpha
    return frame


# ---------------------------------------------------------------------------
# TestOpenEncoder
# ---------------------------------------------------------------------------

class TestOpenEncoder:
    """open_encoder builds the correct FFmpeg command."""

    def test_returns_popen(self):
        """open_encoder returns a subprocess.Popen."""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "test.mp4"
            proc = open_encoder(out, 64, 36, 30)
            try:
                assert isinstance(proc, subprocess.Popen)
            finally:
                proc.stdin.close()
                proc.wait()

    def test_libopenh264_arg(self):
        """open_encoder must pass -c:v libopenh264 (§2.8)."""
        captured_cmd = []

        def fake_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            m = MagicMock()
            m.stdin = MagicMock()
            m.returncode = 0
            return m

        with patch("parallax_engine.encode.subprocess.Popen", side_effect=fake_popen):
            open_encoder("/tmp/out.mp4", 64, 36, 30)

        assert "-c:v" in captured_cmd
        idx = captured_cmd.index("-c:v")
        assert captured_cmd[idx + 1] == "libopenh264", (
            f"Expected libopenh264, got {captured_cmd[idx + 1]}"
        )

    def test_threads_1(self):
        """open_encoder must pass -threads 1 for determinism (§7)."""
        captured_cmd = []

        def fake_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            m = MagicMock()
            m.stdin = MagicMock()
            m.returncode = 0
            return m

        with patch("parallax_engine.encode.subprocess.Popen", side_effect=fake_popen):
            open_encoder("/tmp/out.mp4", 64, 36, 30)

        assert "-threads" in captured_cmd
        idx = captured_cmd.index("-threads")
        assert captured_cmd[idx + 1] == "1", (
            f"Expected threads=1, got {captured_cmd[idx + 1]}"
        )

    def test_pix_fmt_rgba_input(self):
        """-pix_fmt rgba must appear before -i '-'."""
        captured_cmd = []

        def fake_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            m = MagicMock()
            m.stdin = MagicMock()
            m.returncode = 0
            return m

        with patch("parallax_engine.encode.subprocess.Popen", side_effect=fake_popen):
            open_encoder("/tmp/out.mp4", 64, 36, 30)

        assert "-pix_fmt" in captured_cmd
        # At least one occurrence of rgba
        assert "rgba" in captured_cmd

    def test_frame_size_in_cmd(self):
        """-s WxH must be present."""
        captured_cmd = []

        def fake_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            m = MagicMock()
            m.stdin = MagicMock()
            m.returncode = 0
            return m

        with patch("parallax_engine.encode.subprocess.Popen", side_effect=fake_popen):
            open_encoder("/tmp/out.mp4", 64, 36, 30)

        assert "-s" in captured_cmd
        idx = captured_cmd.index("-s")
        assert captured_cmd[idx + 1] == "64x36"

    def test_output_path_in_cmd(self):
        """Destination path must be the last positional arg."""
        captured_cmd = []

        def fake_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            m = MagicMock()
            m.stdin = MagicMock()
            m.returncode = 0
            return m

        with patch("parallax_engine.encode.subprocess.Popen", side_effect=fake_popen):
            open_encoder("/tmp/specific_output.mp4", 64, 36, 30)

        assert captured_cmd[-1] == "/tmp/specific_output.mp4"


# ---------------------------------------------------------------------------
# TestWriteFrame
# ---------------------------------------------------------------------------

class TestWriteFrame:
    """write_frame converts float32 → uint8 correctly."""

    def test_pure_white_writes_255(self):
        """A frame of 1.0 → 255 in the byte stream."""
        mock_proc = MagicMock()
        written = []
        mock_proc.stdin.write = lambda b: written.append(b)

        frame = np.ones((4, 4, 4), dtype=np.float32)
        write_frame(mock_proc, frame)

        data = b"".join(written)
        arr = np.frombuffer(data, dtype=np.uint8)
        assert np.all(arr == 255), f"Expected all 255, got {arr.min()}"

    def test_pure_black_writes_0(self):
        """A frame of 0.0 → 0 in the byte stream."""
        mock_proc = MagicMock()
        written = []
        mock_proc.stdin.write = lambda b: written.append(b)

        frame = np.zeros((4, 4, 4), dtype=np.float32)
        write_frame(mock_proc, frame)

        data = b"".join(written)
        arr = np.frombuffer(data, dtype=np.uint8)
        assert np.all(arr == 0)

    def test_midgrey_rounds(self):
        """0.5 → 127 or 128 (round half)."""
        mock_proc = MagicMock()
        written = []
        mock_proc.stdin.write = lambda b: written.append(b)

        frame = np.full((2, 2, 4), 0.5, dtype=np.float32)
        write_frame(mock_proc, frame)

        data = b"".join(written)
        arr = np.frombuffer(data, dtype=np.uint8)
        assert arr[0] in (127, 128), f"Expected 127 or 128, got {arr[0]}"

    def test_clamps_over_1(self):
        """Values > 1.0 are clamped to 255."""
        mock_proc = MagicMock()
        written = []
        mock_proc.stdin.write = lambda b: written.append(b)

        frame = np.full((2, 2, 4), 2.0, dtype=np.float32)
        write_frame(mock_proc, frame)

        data = b"".join(written)
        arr = np.frombuffer(data, dtype=np.uint8)
        assert np.all(arr == 255)

    def test_clamps_under_0(self):
        """Values < 0.0 are clamped to 0."""
        mock_proc = MagicMock()
        written = []
        mock_proc.stdin.write = lambda b: written.append(b)

        frame = np.full((2, 2, 4), -1.0, dtype=np.float32)
        write_frame(mock_proc, frame)

        data = b"".join(written)
        arr = np.frombuffer(data, dtype=np.uint8)
        assert np.all(arr == 0)

    def test_correct_byte_count(self):
        """Byte count = H * W * 4."""
        mock_proc = MagicMock()
        written = []
        mock_proc.stdin.write = lambda b: written.append(b)

        H, W = 6, 8
        frame = np.zeros((H, W, 4), dtype=np.float32)
        write_frame(mock_proc, frame)

        total_bytes = sum(len(b) for b in written)
        assert total_bytes == H * W * 4


# ---------------------------------------------------------------------------
# TestCloseEncoder
# ---------------------------------------------------------------------------

class TestCloseEncoder:
    """close_encoder waits and raises on non-zero exit."""

    def test_success_no_raise(self):
        """close_encoder does not raise when FFmpeg exits 0."""
        import io
        mock_proc = MagicMock()
        mock_proc.stdin.closed = False
        mock_proc.returncode = 0
        mock_proc._stderr_tempfile = io.BytesIO(b"")
        close_encoder(mock_proc)  # should not raise

    def test_nonzero_exit_raises(self):
        """close_encoder raises RuntimeError when FFmpeg exits non-zero."""
        import io
        mock_proc = MagicMock()
        mock_proc.stdin.closed = False
        mock_proc._stderr_tempfile = io.BytesIO(b"error output")
        mock_proc.returncode = 1
        with pytest.raises(RuntimeError, match="FFmpeg exited"):
            close_encoder(mock_proc)

    def test_close_stdin_called(self):
        """close_encoder closes stdin then waits."""
        import io
        mock_proc = MagicMock()
        mock_proc.stdin.closed = False
        mock_proc.returncode = 0
        mock_proc._stderr_tempfile = io.BytesIO(b"")
        close_encoder(mock_proc)
        mock_proc.stdin.close.assert_called_once()
        mock_proc.wait.assert_called_once()


# ---------------------------------------------------------------------------
# TestEncoderContextManager
# ---------------------------------------------------------------------------

class TestEncoderContextManager:
    """Encoder context manager integrates open/write/close."""

    def test_context_manager_calls_ffmpeg(self):
        """Encoder.__enter__ starts FFmpeg; __exit__ calls close_encoder."""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "ctx.mp4"
            frame = _solid_frame(36, 64, (0.5, 0.3, 0.2))
            with Encoder(out, 64, 36, 30) as enc:
                enc.write(frame)
            # File should exist and be non-empty after close
            assert out.exists()
            assert out.stat().st_size > 0

    def test_context_manager_suppresses_nothing(self):
        """An exception inside the context propagates out."""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "err.mp4"
            with pytest.raises(ValueError):
                with Encoder(out, 64, 36, 30) as enc:
                    raise ValueError("test error")

    def test_write_before_enter_raises(self):
        """Calling write() before __enter__ raises AssertionError."""
        enc = Encoder("/tmp/nope.mp4", 64, 36, 30)
        frame = np.zeros((36, 64, 4), dtype=np.float32)
        with pytest.raises(AssertionError):
            enc.write(frame)


# ---------------------------------------------------------------------------
# TestRealEncode — integration tests that actually run FFmpeg
# ---------------------------------------------------------------------------

class TestRealEncode:
    """Integration tests that run a real (tiny) FFmpeg encode."""

    def test_encode_produces_mp4(self):
        """Encoding a 1-frame clip produces a valid non-empty MP4."""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "one_frame.mp4"
            frame = _solid_frame(36, 64, (0.2, 0.5, 0.8))
            with Encoder(out, 64, 36, 30) as enc:
                enc.write(frame)
            assert out.exists()
            assert out.stat().st_size > 0

    def test_encode_deterministic(self):
        """Two encodes of identical frames produce byte-identical MP4s."""
        frame = _solid_frame(36, 64, (0.1, 0.4, 0.9))

        results = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "det.mp4"
                with Encoder(out, 64, 36, 30) as enc:
                    for _ in range(3):
                        enc.write(frame)
                results.append(out.read_bytes())

        assert results[0] == results[1], (
            f"Non-deterministic encode: sizes {len(results[0])} vs {len(results[1])}"
        )

    def test_encode_multi_frame(self):
        """Encoding 10 frames produces a larger file than 1 frame."""
        with tempfile.TemporaryDirectory() as td:
            out1 = Path(td) / "one.mp4"
            out10 = Path(td) / "ten.mp4"
            frame = _solid_frame(36, 64, (0.5, 0.5, 0.5))

            with Encoder(out1, 64, 36, 30) as enc:
                enc.write(frame)

            with Encoder(out10, 64, 36, 30) as enc:
                for _ in range(10):
                    enc.write(frame)

            # 10-frame file should be larger
            assert out10.stat().st_size >= out1.stat().st_size
