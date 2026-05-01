"""
parallax_engine/tools/render.py -- MCP tool wrapper for the renderer.

Provides ``render_scene``, the in-process deterministic tool that the lead
orchestrator calls to convert a scene YAML into an MP4.  This is thin:
it delegates all heavy lifting to ``parallax_engine.render`` (Phase 1).

Design notes
------------
- Returns a plain dict (JSON-serialisable) so the harness can embed the
  result in a tool-use response without extra serialisation.
- Never raises; all exceptions are caught and reported in the status dict.
- workspace/out.mp4 is the canonical output path (ss3.2).

SPEC anchors: ss3.1, ss3.3
"""
from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any


def render_scene(
    scene_yaml_path: str | Path,
    workspace: str | Path,
) -> dict[str, Any]:
    """
    Render a scene YAML to ``workspace/out.mp4``.

    Parameters
    ----------
    scene_yaml_path:
        Path to a valid scene YAML file (production format, ss2.2).
    workspace:
        Root directory for the current render job.  Asset paths in the
        scene YAML are resolved relative to this directory.  The output
        MP4 is written to ``workspace/out.mp4``.

    Returns
    -------
    dict with keys:

    ok : bool
        True if the render completed without errors.
    out_mp4 : str
        Absolute path to the output MP4 (may not exist if ``ok`` is False).
    message : str
        Human-readable status message.
    n_frames : int or None
        Number of frames in the scene (None if scene could not be parsed).
    size_bytes : int
        Size of the output MP4 in bytes (0 if render failed).
    """
    from parallax_engine.render import render_scene as _render_scene
    from parallax_engine.scene import SceneVersionError, load_scene_yaml

    scene_path = Path(scene_yaml_path).resolve()
    workspace_path = Path(workspace).resolve()
    out_path = workspace_path / "out.mp4"

    # --- Parse scene ---
    try:
        scene = load_scene_yaml(scene_path)
    except SceneVersionError as exc:
        return {
            "ok": False,
            "out_mp4": str(out_path),
            "message": f"scene version error: {exc}",
            "n_frames": None,
            "size_bytes": 0,
        }
    except Exception as exc:
        return {
            "ok": False,
            "out_mp4": str(out_path),
            "message": f"scene parse error: {exc}",
            "n_frames": None,
            "size_bytes": 0,
        }

    n_frames = int(scene.meta.duration_s * scene.meta.fps)

    # --- Render ---
    try:
        workspace_path.mkdir(parents=True, exist_ok=True)
        _render_scene(scene, workspace_path, out_path)
    except Exception as exc:
        return {
            "ok": False,
            "out_mp4": str(out_path),
            "message": f"render error: {exc}\n{traceback.format_exc()}",
            "n_frames": n_frames,
            "size_bytes": 0,
        }

    size_bytes = out_path.stat().st_size if out_path.exists() else 0
    return {
        "ok": True,
        "out_mp4": str(out_path),
        "message": (
            f"rendered {n_frames} frames to {out_path.name} "
            f"({size_bytes} bytes)"
        ),
        "n_frames": n_frames,
        "size_bytes": size_bytes,
    }
