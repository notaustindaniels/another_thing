"""parallax_engine.scene.merger — deterministic Python merger (§11.6.5).

Stitches per-scene fragment YAML files into the single ``scene.yaml`` the
renderer consumes, after validating cross-scene transition pairing.

No LLM calls.  No randomness.  Pure Python.  (SPEC.md §11.13 anti-pattern 12)

Public API
----------
- merge(fragment_dir, storyboard, output_dir=None) → (merged_dict, log_dict)
- MergeError

Algorithm (§11.6.5)
--------------------
1. Load all ``scene_N.yaml`` files from *fragment_dir*, sorted by scene_index.
2. Validate fragment coverage (must match storyboard scene indices exactly).
3. Validate cross-scene transition pairing (biome_wipe and portal_reveal).
4. Compute cumulative ``start_t`` for each scene.
5. Promote shared palette from ``storyboard.visual_vocabulary`` to the project
   block of the merged document.
6. Assemble ``scenes`` list with timing offsets injected.
7. Write ``scene.yaml`` and ``scene.merge.log.json`` to *output_dir* if given.
8. Return ``(merged_dict, log_dict)``.

Transition pairing rules (§11.3.5.2 and §11.6.5)
-------------------------------------------------
*biome_wipe*
    Scene N's ``transition_out.type == 'biome_wipe'`` ↔
    Scene N+1's ``transition_in.type == 'biome_wipe'``;
    ``paired_scene`` on each side must point to the other.

*portal_reveal*
    Scene N's ``transition_out.type == 'portal_reveal'`` ↔
    Scene N+1's ``transition_in.type == 'portal_reveal'``;
    ``paired_scene`` on each side must point to the other.

The merger reads pairing data from the *storyboard* (already Pydantic-validated)
and cross-checks against any transition blocks present in the actual fragments.
Mismatches raise ``MergeError``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from parallax_engine.director.schema import Storyboard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FRAGMENT_GLOB = "scene_*.yaml"
_SCENE_YAML_FILENAME = "scene.yaml"
_LOG_FILENAME = "scene.merge.log.json"

# Transition types that require symmetric paired_scene cross-references.
_PAIRED_TYPES: frozenset[str] = frozenset({"biome_wipe", "portal_reveal"})


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class MergeError(Exception):
    """Raised when the merger detects unresolvable structural or pairing violations.

    The project manager (§11.9.1) catches this and re-dispatches the offending
    scene to the scene-designer.
    """


# ---------------------------------------------------------------------------
# Fragment I/O helpers
# ---------------------------------------------------------------------------


def _load_fragment(path: Path) -> dict[str, Any]:
    """Parse one scene fragment YAML file; raise MergeError on malformed input."""
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise MergeError(
            f"Fragment {path.name} must be a YAML mapping; got {type(raw).__name__}"
        )
    return raw


def _collect_fragments(fragment_dir: Path) -> list[dict[str, Any]]:
    """Load all ``scene_N.yaml`` files from *fragment_dir*, sorted by scene_index.

    Raises MergeError if no fragment files are found or if a file is missing
    required keys.
    """
    paths = sorted(fragment_dir.glob(_FRAGMENT_GLOB))
    if not paths:
        raise MergeError(
            f"No scene fragment files (scene_*.yaml) found in {fragment_dir}"
        )
    fragments: list[dict[str, Any]] = []
    for p in paths:
        frag = _load_fragment(p)
        for required_key in ("scene_index", "duration_s"):
            if required_key not in frag:
                raise MergeError(
                    f"Fragment {p.name} is missing required key '{required_key}'"
                )
        fragments.append(frag)
    # Sort by scene_index (fragments may arrive in any order)
    fragments.sort(key=lambda f: int(f["scene_index"]))
    return fragments


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_coverage(
    fragments: list[dict[str, Any]],
    storyboard: Storyboard,
) -> None:
    """Ensure fragments cover exactly the storyboard's scene indices."""
    expected: set[int] = {s.index for s in storyboard.scenes}
    actual: set[int] = {int(f["scene_index"]) for f in fragments}
    missing = expected - actual
    extra = actual - expected
    errors: list[str] = []
    if missing:
        errors.append(f"  missing fragments for scene indices: {sorted(missing)}")
    if extra:
        errors.append(f"  unexpected fragment scene indices: {sorted(extra)}")
    if errors:
        raise MergeError("Fragment coverage mismatch:\n" + "\n".join(errors))


def _check_pair(
    idx_a: int,
    trans_a: dict[str, Any] | None,
    idx_b: int,
    trans_b: dict[str, Any] | None,
    direction: str,  # "out" or "in" for idx_a
    t_type: str,
    errors: list[str],
) -> None:
    """Check that trans_a (on scene idx_a) and trans_b (on scene idx_b) form a
    valid symmetric pair for *t_type*.

    *trans_a* is the outgoing transition of scene *idx_a* (or the incoming
    transition if direction == 'in').
    *trans_b* is the complementary transition on scene *idx_b*.
    """
    if trans_a is None:
        return  # fragment doesn't specify transitions; skip

    actual_type = trans_a.get("type", "")
    if actual_type != t_type:
        return  # not this pairing type

    if trans_b is None:
        errors.append(
            f"Scene {idx_a} has transition_{direction}.type='{t_type}' "
            f"but scene {idx_b} has no complementary transition"
        )
        return

    actual_b_type = trans_b.get("type", "")
    if actual_b_type != t_type:
        errors.append(
            f"Transition type mismatch: scene {idx_a} {direction}='{t_type}' "
            f"but scene {idx_b} complementary='{actual_b_type}'"
        )

    # Check paired_scene cross-references when present
    paired_a = trans_a.get("paired_scene")
    if paired_a is not None and int(paired_a) != idx_b:
        errors.append(
            f"Scene {idx_a} transition_{direction}.paired_scene={paired_a} "
            f"must reference scene {idx_b}"
        )

    paired_b = trans_b.get("paired_scene")
    if paired_b is not None and int(paired_b) != idx_a:
        errors.append(
            f"Scene {idx_b} complementary transition paired_scene={paired_b} "
            f"must reference scene {idx_a}"
        )


def _validate_transition_pairing(
    fragments: list[dict[str, Any]],
    storyboard: Storyboard,
) -> None:
    """Validate biome_wipe and portal_reveal pairs reference each other (§11.6.5).

    Two levels of validation:
    1. Storyboard-level (using Pydantic-validated TransitionSpec objects).
    2. Fragment-level (using raw dict blocks from the fragments themselves).

    Raises MergeError listing all violations found.
    """
    errors: list[str] = []

    # ── 1. Storyboard-level ─────────────────────────────────────────────────
    sb_by_index = {s.index: s for s in storyboard.scenes}

    for scene in storyboard.scenes:
        tout = scene.transition_out
        if tout is None or tout.type not in _PAIRED_TYPES:
            continue

        next_idx = scene.index + 1
        if next_idx not in sb_by_index:
            errors.append(
                f"Storyboard: scene {scene.index} has transition_out.type="
                f"'{tout.type}' but scene {next_idx} does not exist"
            )
            continue

        next_scene = sb_by_index[next_idx]
        tin = next_scene.transition_in

        if tin.type != tout.type:
            errors.append(
                f"Storyboard: scene {scene.index} out='{tout.type}' "
                f"but scene {next_idx} in='{tin.type}' — types must match"
            )

        if tout.paired_scene is not None and tout.paired_scene != next_idx:
            errors.append(
                f"Storyboard: scene {scene.index} transition_out.paired_scene="
                f"{tout.paired_scene} must be {next_idx}"
            )

        if tin.paired_scene is not None and tin.paired_scene != scene.index:
            errors.append(
                f"Storyboard: scene {next_idx} transition_in.paired_scene="
                f"{tin.paired_scene} must be {scene.index}"
            )

    # ── 2. Fragment-level ───────────────────────────────────────────────────
    frag_by_index: dict[int, dict[str, Any]] = {
        int(f["scene_index"]): f for f in fragments
    }

    for frag in fragments:
        idx = int(frag["scene_index"])
        tout_f = frag.get("transition_out")
        if tout_f is None:
            continue

        t_type = tout_f.get("type", "")
        if t_type not in _PAIRED_TYPES:
            continue

        next_idx = idx + 1
        next_frag = frag_by_index.get(next_idx)
        if next_frag is None:
            errors.append(
                f"Fragment: scene {idx} transition_out.type='{t_type}' "
                f"but fragment for scene {next_idx} not found"
            )
            continue

        tin_f = next_frag.get("transition_in")
        _check_pair(
            idx_a=idx,
            trans_a=tout_f,
            idx_b=next_idx,
            trans_b=tin_f,
            direction="out",
            t_type=t_type,
            errors=errors,
        )

    if errors:
        raise MergeError(
            f"Transition pairing violations ({len(errors)} error(s)):\n"
            + "\n".join(f"  • {e}" for e in errors)
        )


def _validate_duration_consistency(
    fragments: list[dict[str, Any]],
    storyboard: Storyboard,
) -> None:
    """Warn (log) when fragment durations differ from storyboard durations.

    Not a hard error — the scene-designer may have slightly adjusted timing.
    Logs a warning rather than raising MergeError so legitimate rounding
    doesn't abort the merge.
    """
    sb_by_index = {s.index: s for s in storyboard.scenes}
    for frag in fragments:
        idx = int(frag["scene_index"])
        sb_dur = sb_by_index[idx].duration_s
        frag_dur = float(frag["duration_s"])
        if abs(frag_dur - sb_dur) > 0.1:
            logger.warning(
                "Scene %d duration mismatch: storyboard=%.2fs fragment=%.2fs",
                idx, sb_dur, frag_dur,
            )


# ---------------------------------------------------------------------------
# Assembly helpers
# ---------------------------------------------------------------------------


def _build_project_block(storyboard: Storyboard) -> dict[str, Any]:
    """Build the top-level project block, promoting palette from storyboard (§11.6.5)."""
    vv = storyboard.visual_vocabulary
    proj = storyboard.project

    # Palette — promoted from visual_vocabulary per §11.6.5
    palette: dict[str, Any] = {
        "primary": list(vv.palette.primary),
        "secondary": list(vv.palette.secondary),
        "neutrals": list(vv.palette.neutrals),
    }
    if vv.palette.forbidden:
        palette["forbidden"] = list(vv.palette.forbidden)

    block: dict[str, Any] = {
        "title": proj.title,
        "total_duration_s": proj.total_duration_s,
        "fps": proj.target_fps,
        "aspect_ratio": proj.aspect_ratio,
        "shape_language": vv.shape_language,
        "palette": palette,
    }
    if vv.palette_progression_note:
        block["palette_progression_note"] = vv.palette_progression_note
    return block


def _compute_start_times(fragments: list[dict[str, Any]]) -> list[float]:
    """Compute cumulative start_t for each fragment (seconds from piece start)."""
    times: list[float] = []
    t = 0.0
    for frag in fragments:
        times.append(round(t, 6))
        t += float(frag["duration_s"])
    return times


def _assemble_scene_entry(
    frag: dict[str, Any],
    start_t: float,
) -> dict[str, Any]:
    """Return a shallow copy of *frag* with ``start_t`` injected."""
    entry = dict(frag)
    entry["start_t"] = start_t
    return entry


def _build_log(
    fragments: list[dict[str, Any]],
    start_times: list[float],
    storyboard: Storyboard,
) -> dict[str, Any]:
    """Build the merge log dict (written as ``scene.merge.log.json``)."""
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    scenes_log = []
    for frag, start_t in zip(fragments, start_times):
        idx = int(frag["scene_index"])
        scenes_log.append(
            {
                "scene_index": idx,
                "duration_s": float(frag["duration_s"]),
                "start_t": start_t,
                "fragment_file": f"scene_{idx}.yaml",
            }
        )
    return {
        "merged_at": now_iso,
        "merger_version": "1.0",
        "storyboard_title": storyboard.project.title,
        "scene_count": len(fragments),
        "total_duration_s": sum(float(f["duration_s"]) for f in fragments),
        "palette_source": "storyboard.visual_vocabulary.palette",
        "transition_validation": "passed",
        "scenes": scenes_log,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def merge(
    fragment_dir: Path,
    storyboard: Storyboard,
    output_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge scene fragments into a unified scene.yaml document + merge log.

    Parameters
    ----------
    fragment_dir:
        Directory containing ``scene_N.yaml`` files written by the scene-designer.
    storyboard:
        Validated ``Storyboard`` object (provides transition specs and project
        metadata for palette propagation and timing).
    output_dir:
        If provided, ``scene.yaml`` and ``scene.merge.log.json`` are written here.
        Directory is created if it doesn't exist.
        If ``None``, no files are written; only the dicts are returned.

    Returns
    -------
    merged_dict:
        The assembled document as a plain Python dict (YAML-serialisable).
        Structure::

            version: 1
            project:
              title: ...
              fps: ...
              palette: {...}
            scenes:
              - scene_index: 1
                start_t: 0.0
                duration_s: 5.0
                ...
              - scene_index: 2
                start_t: 5.0
                ...

    log_dict:
        Merge log suitable for JSON serialisation.

    Raises
    ------
    MergeError
        On structural violations: missing/extra fragments, transition pairing
        failures, malformed YAML, missing required keys.
    """
    fragment_dir = Path(fragment_dir)

    # ── Load ─────────────────────────────────────────────────────────────────
    fragments = _collect_fragments(fragment_dir)

    # ── Validate ──────────────────────────────────────────────────────────────
    _validate_coverage(fragments, storyboard)
    _validate_transition_pairing(fragments, storyboard)
    _validate_duration_consistency(fragments, storyboard)

    # ── Assemble ──────────────────────────────────────────────────────────────
    start_times = _compute_start_times(fragments)
    project_block = _build_project_block(storyboard)

    scenes_list = [
        _assemble_scene_entry(frag, start_t)
        for frag, start_t in zip(fragments, start_times)
    ]

    merged: dict[str, Any] = {
        "version": 1,
        "project": project_block,
        "scenes": scenes_list,
    }

    log = _build_log(fragments, start_times, storyboard)

    # ── Write outputs ─────────────────────────────────────────────────────────
    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        scene_yaml_path = out / _SCENE_YAML_FILENAME
        with scene_yaml_path.open("w", encoding="utf-8") as fh:
            yaml.dump(
                merged,
                fh,
                sort_keys=True,
                allow_unicode=True,
                default_flow_style=False,
            )
        logger.info(
            "Wrote merged scene.yaml (%d scenes) → %s", len(scenes_list), scene_yaml_path
        )

        log_path = out / _LOG_FILENAME
        with log_path.open("w", encoding="utf-8") as fh:
            json.dump(log, fh, indent=2, sort_keys=True)
        logger.info("Wrote merge log → %s", log_path)

    return merged, log
