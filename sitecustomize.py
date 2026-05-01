"""
sitecustomize.py — Python startup hook for parallax-engine.

Auto-imported by Python's ``site`` module at interpreter startup whenever
``/Users/austin/parallax-skill`` is on ``sys.path`` (which is true under the
editable install — the ``_editable_impl_parallax_engine.pth`` file adds it).

Sole responsibility: prepend ``<sys.prefix>/bin`` to ``os.environ["PATH"]``
so that ``shutil.which("ffmpeg")`` and bare ``"ffmpeg"`` subprocess lookups
always resolve to the LGPL FFmpeg shipped with this Python environment,
not to any GPL-tainted system FFmpeg that may be on the shell's PATH.

Without this hook, ``.conda-env/bin/python tools/validate_licensing.py``
runs in a fresh process whose PATH is whatever the shell exports — which
typically does NOT include ``<sys.prefix>/bin`` and may include a system
FFmpeg, causing ``check_system_ffmpeg`` to either pick up the wrong binary
or report "ffmpeg not found on PATH". The same issue affects any subprocess
launched via ``.conda-env/bin/python`` that calls ``ffmpeg`` by bare name.

The same fix is also applied (and re-applied defensively at every call) by
``parallax_engine.encode._ensure_env_bin_on_path()`` for FFmpeg invocations
inside parallax-engine code itself. This file is the matching startup-time
hook for processes that never import parallax-engine.
"""

from __future__ import annotations

import os
import sys


def _ensure_env_bin_on_path() -> None:
    env_bin = os.path.join(sys.prefix, "bin")
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if not parts or parts[0] != env_bin:
        parts = [p for p in parts if p != env_bin]
        os.environ["PATH"] = os.pathsep.join([env_bin] + parts)


_ensure_env_bin_on_path()
