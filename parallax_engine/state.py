"""
parallax_engine.state
======================
Checkpoint read/write for the agent harness.

Implements SPEC.md §3.8:
  After each major phase completion (manifest, assets-done, masks-done,
  camera-done, render-done, qa-pass-N), write checkpoints/state.json.
  On startup, read the checkpoint and skip completed phases.

  Do NOT rely on the SDK's session resume across machines.

Design
------
Checkpoints are JSON files written atomically (write-to-temp + rename) to
prevent partial reads on crash.  The schema is a versioned dict; unknown
keys are preserved on round-trip so older checkpoints stay forward-compatible.

The "phases" field maps phase name → ISO-8601 timestamp of completion.
The harness uses `is_phase_done()` to decide whether to skip a phase.

Major phase names (from §3.8):
    manifest        — scene.yaml written by scene-designer
    assets-done     — all asset-generator subagents returned ok
    masks-done      — all mask-author subagents returned ok
    camera-done     — camera-pather written to scene.yaml
    render-done     — render_scene tool returned successfully
    qa-pass-1       — first qa-critic pass returned PASS
    qa-pass-2       — second qa-critic pass returned PASS
    qa-pass-3       — third qa-critic pass returned PASS (final)

Custom phase names are allowed; the harness can define additional phases.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

# Canonical phase names (not exhaustive — custom names are allowed)
PHASE_MANIFEST = "manifest"
PHASE_ASSETS_DONE = "assets-done"
PHASE_MASKS_DONE = "masks-done"
PHASE_CAMERA_DONE = "camera-done"
PHASE_RENDER_DONE = "render-done"
PHASE_QA_PASS_1 = "qa-pass-1"
PHASE_QA_PASS_2 = "qa-pass-2"
PHASE_QA_PASS_3 = "qa-pass-3"

CHECKPOINT_SCHEMA_VERSION = "1.0"


def _checkpoint_path(workspace: Path) -> Path:
    """Return the canonical checkpoint file path."""
    return workspace / "checkpoints" / "state.json"


def _iso_now() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_checkpoint(
    *,
    workspace: Path | str,
    phase: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Mark *phase* as complete in the checkpoint file.

    If the file already exists, it is read and updated (so previous phases
    are preserved).  If the file does not exist, a fresh checkpoint is created.

    The write is atomic: we write to a temp file in the same directory then
    rename, so a crash mid-write leaves the old file intact.

    Parameters
    ----------
    workspace:
        Root workspace directory (checkpoint goes in workspace/checkpoints/).
    phase:
        Phase identifier to mark complete.  Use the PHASE_* constants or a
        custom string.
    extra:
        Optional extra metadata to store alongside the phase timestamp
        (e.g. {"output_path": "workspace/out.mp4"}).  Stored under
        state["phase_meta"][phase].

    Returns
    -------
    dict
        The full checkpoint state after this write (useful for logging).
    """
    workspace = Path(workspace)
    checkpoint_path = _checkpoint_path(workspace)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing state or start fresh.
    state = read_checkpoint(workspace=workspace)

    # Update the phase.
    state.setdefault("phases", {})[phase] = _iso_now()
    if extra:
        state.setdefault("phase_meta", {})[phase] = extra

    # Write atomically.
    tmp_path = checkpoint_path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(tmp_path), str(checkpoint_path))

    return state


def read_checkpoint(*, workspace: Path | str) -> dict[str, Any]:
    """
    Read the checkpoint file and return the state dict.

    If no checkpoint file exists, returns a fresh state dict with no
    completed phases.  Never raises FileNotFoundError.

    Parameters
    ----------
    workspace:
        Root workspace directory.

    Returns
    -------
    dict
        Checkpoint state with at least these keys:
          - "schema_version": str
          - "phases": dict[str, str]  (phase → ISO-8601 timestamp)
          - "phase_meta": dict[str, Any]  (phase → extra metadata)
    """
    workspace = Path(workspace)
    checkpoint_path = _checkpoint_path(workspace)

    if not checkpoint_path.exists():
        return {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "phases": {},
            "phase_meta": {},
        }

    raw = checkpoint_path.read_text(encoding="utf-8")
    state: dict[str, Any] = json.loads(raw)

    # Ensure required keys exist even in old-format checkpoints.
    state.setdefault("schema_version", CHECKPOINT_SCHEMA_VERSION)
    state.setdefault("phases", {})
    state.setdefault("phase_meta", {})

    return state


def is_phase_done(*, workspace: Path | str, phase: str) -> bool:
    """
    Return True if *phase* has been completed in the checkpoint.

    Parameters
    ----------
    workspace:
        Root workspace directory.
    phase:
        Phase identifier to check.
    """
    state = read_checkpoint(workspace=Path(workspace))
    return phase in state.get("phases", {})


def completed_phases(*, workspace: Path | str) -> list[str]:
    """
    Return a list of all completed phase names, sorted by completion timestamp.

    Parameters
    ----------
    workspace:
        Root workspace directory.
    """
    state = read_checkpoint(workspace=Path(workspace))
    phases = state.get("phases", {})
    # Sort by timestamp string (ISO-8601 sorts lexicographically)
    return sorted(phases.keys(), key=lambda p: phases[p])


def reset_checkpoint(*, workspace: Path | str) -> None:
    """
    Delete the checkpoint file (used for fresh runs or testing).

    Parameters
    ----------
    workspace:
        Root workspace directory.
    """
    checkpoint_path = _checkpoint_path(Path(workspace))
    if checkpoint_path.exists():
        checkpoint_path.unlink()
