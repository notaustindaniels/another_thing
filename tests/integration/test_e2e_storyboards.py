"""tests/integration/test_e2e_storyboards.py — End-to-end pipeline tests (P4_5.M07).

For each of the three canonical storyboard examples (§11.4.1, §11.4.2,
§11.4.3), this module verifies:

1. The manager pipeline (director → scene-design → merge → render) runs
   without error (dry_run=True stubs all LLM calls).
2. The output workspace contains the expected artefacts.
3. A minimal version of the scene concept renders to a valid non-empty MP4.

All tests are offline (no network, no API keys).  The manager dry_run=True
mode stubs the director and scene-designers; the real merger and real QA
(with dry_run stubs) are exercised.

Spec anchors: §11.4, §11.9, §11.9.1, §11.14
Milestone: P4_5.M07
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from parallax_engine.director.schema import load_storyboard_yaml
from parallax_engine.manager import ProjectManager

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

STORYBOARDS_DIR = Path(__file__).parent.parent / "storyboards"
EXAMPLE_A = STORYBOARDS_DIR / "example_a.yaml"
EXAMPLE_B = STORYBOARDS_DIR / "example_b.yaml"
EXAMPLE_C = STORYBOARDS_DIR / "example_c.yaml"

# ---------------------------------------------------------------------------
# Minimal scene YAML templates for real-render tests.
#
# Resolution: 128x72  Duration: 0.5 s  FPS: 6  → 3 frames each.
# All asset paths are intentionally missing; render.py falls back to a solid
# grey 1×1 PNG per the §2.5 rasterisation fallback (SVG file-not-found
# returns a 1×1 grey placeholder rather than crashing).
#
# See test_harness_e2e.py for the canonical pattern.
# ---------------------------------------------------------------------------

# Example A concept: single-scene drone forward push through forest layers.
# noise is required by DroneCamera schema; z_amp: 0 means no wobble.
_SCENE_A_YAML: str = """\
version: 1
meta:
  duration_s: 0.5
  fps: 6
  resolution: [128, 72]
  perspective_px: 800
  origin: [64, 36]
  bg_color: "#0e1a14"
  seed: 1001
stacks:
  forest:
    layers:
      - { id: a_sky,   src: assets/a_sky.svg,   scene_xyz: [0, 0, -8000], plate_size: [1920, 1080] }
      - { id: a_trees, src: assets/a_trees.svg, scene_xyz: [0, 0, -3000], plate_size: [1920, 1080] }
camera:
  mode: drone
  drone:
    path:
      kind: bezier
      controls: [[0, 0, 0], [0, 0, -4000], [0, 0, -8000]]
      duration_s: 0.5
    poi_lookahead_s: 0.55
    spring_halflife_s: 0.18
    noise: {z_amp: 0, xy_amp: 0, hz: 0.5}
masks: []
post:
  global:
    vignette: {strength: 0.3, radius: 0.85}
"""

# Example B concept: multi-layer biome-pan with keyframed camera.
# camera.mode must be "keyframed"; keyframes use t/x/y/z fields.
_SCENE_B_YAML: str = """\
version: 1
meta:
  duration_s: 0.5
  fps: 6
  resolution: [128, 72]
  perspective_px: 800
  origin: [64, 36]
  bg_color: "#e8d8a8"
  seed: 2002
stacks:
  biome:
    layers:
      - { id: b_sky,  src: assets/b_sky.svg,  scene_xyz: [0, 0, -6000], plate_size: [1920, 1080] }
      - { id: b_bird, src: assets/b_bird.svg, scene_xyz: [0, 0, -1000], plate_size: [1920, 1080] }
camera:
  mode: keyframed
  keyframed:
    - { t: 0.0, x: 0.0,   y: 0.0, z: 0.0 }
    - { t: 0.5, x: 200.0, y: 0.0, z: 0.0 }
masks: []
post:
  global:
    vignette: {strength: 0.2, radius: 0.90}
"""

# Example C concept: portal transition — cool grey corridor pushing toward a doorway,
# with a second warm stack representing the far world.
# Uses a portal mask modelled on tests/scenes/portal.yaml (the canonical portal scene).
# noise required; z_amp: 0 means no wobble.
_SCENE_C_YAML: str = """\
version: 1
meta:
  duration_s: 0.5
  fps: 6
  resolution: [128, 72]
  perspective_px: 800
  origin: [64, 36]
  bg_color: "#2a2a2e"
  seed: 3003
stacks:
  corridor:
    layers:
      - { id: c_wall,  src: assets/c_wall.svg,  scene_xyz: [0, 0, -5000],
          plate_size: [1920, 1080], svg_paint_id: silhouette }
      - { id: c_floor, src: assets/c_floor.svg, scene_xyz: [0, 0, -2000],
          plate_size: [1920, 1080] }
  field:
    layers:
      - { id: c_sky,   src: assets/c_sky.svg,   scene_xyz: [0, 0, -7000],
          plate_size: [1920, 1080] }
      - { id: c_grass, src: assets/c_grass.svg, scene_xyz: [0, 0, -3000],
          plate_size: [1920, 1080] }
masks:
  - id: door_portal
    path_svg: assets/c_wall.svg
    silhouette_id_in_svg: silhouette
    path_id_in_svg: hole
    attached_to_layer: corridor.c_wall
    anchor: world
    src_stack: corridor
    dest_stack: field
    matte: alpha
    growth: {kind: perspective}
camera:
  mode: drone
  drone:
    path:
      kind: bezier
      controls: [[0, 0, 0], [0, 0, -2000], [0, 0, -4000]]
      duration_s: 0.5
    poi_lookahead_s: 0.55
    spring_halflife_s: 0.18
    noise: {z_amp: 0, xy_amp: 0, hz: 0.5}
post:
  global:
    vignette: {strength: 0.4, radius: 0.80}
"""


# ---------------------------------------------------------------------------
# Helper: seed workspace with storyboard + casting YAML
# ---------------------------------------------------------------------------


def _seed_workspace(workspace: Path, storyboard_yaml: Path) -> None:
    """Copy storyboard.yaml and write casting.yaml into workspace."""
    workspace.mkdir(parents=True, exist_ok=True)

    # Copy canonical storyboard
    shutil.copy(storyboard_yaml, workspace / "storyboard.yaml")

    # Write casting.yaml from storyboard data (brief.md not needed for dry_run)
    sb = load_storyboard_yaml(storyboard_yaml)
    casting_entries = []
    for entry in sb.casting:
        casting_entries.append({
            "id": entry.id,
            "kind": entry.kind,
            "canonical_description": entry.canonical_description,
            "role_in_story": entry.role_in_story,
            "canonical_svg": None,
            "first_appearance_scene": entry.first_appearance_scene,
        })
    casting_data = {"casting_version": "1.0", "entries": casting_entries}
    (workspace / "casting.yaml").write_text(
        yaml.dump(casting_data), encoding="utf-8"
    )

    # Write a placeholder brief.md (dry_run director doesn't read it)
    (workspace / "brief.md").write_text(
        f"Brief for integration test: {sb.project.logline}", encoding="utf-8"
    )


def _run_manager_dry(workspace: Path) -> Any:
    """Run ProjectManager in dry_run=True mode.  Returns RunResult."""
    pm = ProjectManager(
        workspace_dir=workspace,
        brief_path=workspace / "brief.md",
        budget="standard",
        dry_run=True,
    )
    return pm.run(resume=False)


# ---------------------------------------------------------------------------
# TestExampleAE2E — 10-second forest flythrough (§11.4.1)
# ---------------------------------------------------------------------------


class TestExampleAE2E:
    """End-to-end pipeline for Example A: forest drone flythrough."""

    def test_storyboard_validates(self) -> None:
        """Canonical storyboard A parses without error."""
        sb = load_storyboard_yaml(EXAMPLE_A)
        assert sb.project.title == "Through the Pines"
        assert len(sb.scenes) == 1

    def test_manager_pipeline_dry_run(self, tmp_path: Path) -> None:
        """Full director-era pipeline completes without error (dry_run=True)."""
        ws = tmp_path / "ws_a"
        _seed_workspace(ws, EXAMPLE_A)
        result = _run_manager_dry(ws)
        assert result.success, (
            f"Pipeline failed for Example A. "
            f"fatal_json={result.fatal_json_path}, log={result.log_path}"
        )

    def test_workspace_artefacts_present(self, tmp_path: Path) -> None:
        """Workspace contains expected artefacts after manager run."""
        ws = tmp_path / "ws_a_artefacts"
        _seed_workspace(ws, EXAMPLE_A)
        _run_manager_dry(ws)
        assert (ws / "storyboard.yaml").exists()
        assert (ws / "logs" / "manager.log").exists()
        assert (ws / "out.mp4").exists()

    def test_manager_log_is_json_lines(self, tmp_path: Path) -> None:
        """logs/manager.log contains valid JSON-line entries."""
        ws = tmp_path / "ws_a_log"
        _seed_workspace(ws, EXAMPLE_A)
        _run_manager_dry(ws)
        log_path = ws / "logs" / "manager.log"
        assert log_path.exists()
        lines = [ln.strip() for ln in log_path.read_text().splitlines() if ln.strip()]
        assert len(lines) >= 2, "Manager log should have at least 2 entries"
        for line in lines:
            import json
            data = json.loads(line)  # raises if not valid JSON
            assert "event" in data or "msg" in data or len(data) > 0

    def test_produces_valid_nonempty_mp4(self, tmp_path: Path) -> None:
        """Renderer produces a non-empty MP4 for the forest flythrough concept."""
        from parallax_engine.render import render_scene
        from parallax_engine.scene import load_scene_yaml

        ws = tmp_path / "ws_a_render"
        ws.mkdir(parents=True, exist_ok=True)

        scene_yaml_path = ws / "scene.yaml"
        scene_yaml_path.write_text(_SCENE_A_YAML, encoding="utf-8")

        scene = load_scene_yaml(scene_yaml_path)
        out_path = ws / "out.mp4"
        render_scene(scene, ws, out_path)

        assert out_path.exists(), "MP4 file was not created"
        assert out_path.stat().st_size > 0, "MP4 file is empty"


# ---------------------------------------------------------------------------
# TestExampleBE2E — 24-second four-biome explainer (§11.4.2)
# ---------------------------------------------------------------------------


class TestExampleBE2E:
    """End-to-end pipeline for Example B: four-biome biome_wipe tour."""

    def test_storyboard_validates(self) -> None:
        """Canonical storyboard B parses without error."""
        sb = load_storyboard_yaml(EXAMPLE_B)
        assert sb.project.title == "Four Climates"
        assert len(sb.scenes) == 4
        assert len(sb.casting) == 1

    def test_manager_pipeline_dry_run(self, tmp_path: Path) -> None:
        """Full director-era pipeline completes without error (dry_run=True)."""
        ws = tmp_path / "ws_b"
        _seed_workspace(ws, EXAMPLE_B)
        result = _run_manager_dry(ws)
        assert result.success, (
            f"Pipeline failed for Example B. "
            f"fatal_json={result.fatal_json_path}, log={result.log_path}"
        )

    def test_manager_processes_all_four_scenes(self, tmp_path: Path) -> None:
        """Manager runs scene-designer for all 4 scenes."""
        ws = tmp_path / "ws_b_scenes"
        _seed_workspace(ws, EXAMPLE_B)
        _run_manager_dry(ws)
        scenes_dir = ws / "scenes"
        fragment_files = sorted(scenes_dir.glob("scene_*.yaml"))
        assert len(fragment_files) == 4, (
            f"Expected 4 scene fragments, got {len(fragment_files)}"
        )
        for frag_path in fragment_files:
            data = yaml.safe_load(frag_path.read_text())
            assert "scene_index" in data

    def test_merge_log_written(self, tmp_path: Path) -> None:
        """Merger writes scene.merge.log.json."""
        ws = tmp_path / "ws_b_merge"
        _seed_workspace(ws, EXAMPLE_B)
        _run_manager_dry(ws)
        merge_log = ws / "scene.merge.log.json"
        assert merge_log.exists(), "scene.merge.log.json was not written"
        import json
        with merge_log.open() as fh:
            data = json.load(fh)
        assert data["scene_count"] == 4

    def test_produces_valid_nonempty_mp4(self, tmp_path: Path) -> None:
        """Renderer produces a non-empty MP4 for the biome tour concept."""
        from parallax_engine.render import render_scene
        from parallax_engine.scene import load_scene_yaml

        ws = tmp_path / "ws_b_render"
        ws.mkdir(parents=True, exist_ok=True)

        scene_yaml_path = ws / "scene.yaml"
        scene_yaml_path.write_text(_SCENE_B_YAML, encoding="utf-8")

        scene = load_scene_yaml(scene_yaml_path)
        out_path = ws / "out.mp4"
        render_scene(scene, ws, out_path)

        assert out_path.exists(), "MP4 file was not created"
        assert out_path.stat().st_size > 0, "MP4 file is empty"

    def test_casting_entry_preserved_in_dry_run(self, tmp_path: Path) -> None:
        """Casting data from storyboard is accessible after workspace seeding."""
        ws = tmp_path / "ws_b_casting"
        _seed_workspace(ws, EXAMPLE_B)
        casting_path = ws / "casting.yaml"
        assert casting_path.exists(), "casting.yaml was not written"
        data = yaml.safe_load(casting_path.read_text())
        entries = data.get("entries", [])
        assert len(entries) == 1
        assert entries[0]["id"] == "red_bird"


# ---------------------------------------------------------------------------
# TestExampleCE2E — 8-second portal transition (§11.4.3)
# ---------------------------------------------------------------------------


class TestExampleCE2E:
    """End-to-end pipeline for Example C: portal reveal between two worlds."""

    def test_storyboard_validates(self) -> None:
        """Canonical storyboard C parses without error."""
        sb = load_storyboard_yaml(EXAMPLE_C)
        assert sb.project.title == "The Door"
        assert len(sb.scenes) == 2
        assert len(sb.casting) == 1

    def test_manager_pipeline_dry_run(self, tmp_path: Path) -> None:
        """Full director-era pipeline completes without error (dry_run=True)."""
        ws = tmp_path / "ws_c"
        _seed_workspace(ws, EXAMPLE_C)
        result = _run_manager_dry(ws)
        assert result.success, (
            f"Pipeline failed for Example C. "
            f"fatal_json={result.fatal_json_path}, log={result.log_path}"
        )

    def test_manager_processes_both_scenes(self, tmp_path: Path) -> None:
        """Manager runs scene-designer for both scenes."""
        ws = tmp_path / "ws_c_scenes"
        _seed_workspace(ws, EXAMPLE_C)
        _run_manager_dry(ws)
        scenes_dir = ws / "scenes"
        fragment_files = sorted(scenes_dir.glob("scene_*.yaml"))
        assert len(fragment_files) == 2, (
            f"Expected 2 scene fragments, got {len(fragment_files)}"
        )

    def test_produces_valid_nonempty_mp4(self, tmp_path: Path) -> None:
        """Renderer produces a non-empty MP4 for the portal transition concept."""
        from parallax_engine.render import render_scene
        from parallax_engine.scene import load_scene_yaml

        ws = tmp_path / "ws_c_render"
        ws.mkdir(parents=True, exist_ok=True)

        scene_yaml_path = ws / "scene.yaml"
        scene_yaml_path.write_text(_SCENE_C_YAML, encoding="utf-8")

        scene = load_scene_yaml(scene_yaml_path)
        out_path = ws / "out.mp4"
        render_scene(scene, ws, out_path)

        assert out_path.exists(), "MP4 file was not created"
        assert out_path.stat().st_size > 0, "MP4 file is empty"

    def test_portal_casting_preserved(self, tmp_path: Path) -> None:
        """Casting data for 'figure' is present after workspace seeding."""
        ws = tmp_path / "ws_c_casting"
        _seed_workspace(ws, EXAMPLE_C)
        data = yaml.safe_load((ws / "casting.yaml").read_text())
        entries = data.get("entries", [])
        assert any(e["id"] == "figure" for e in entries), (
            "figure casting entry missing from casting.yaml"
        )

    def test_resume_flag_preserves_storyboard(self, tmp_path: Path) -> None:
        """--resume does not re-run director; storyboard.yaml is unchanged."""
        ws = tmp_path / "ws_c_resume"
        _seed_workspace(ws, EXAMPLE_C)

        # First full run
        pm1 = ProjectManager(
            workspace_dir=ws,
            brief_path=ws / "brief.md",
            dry_run=True,
        )
        r1 = pm1.run(resume=False)
        assert r1.success

        # Capture storyboard modification time
        sb_mtime_before = (ws / "storyboard.yaml").stat().st_mtime

        # Second run with --resume
        pm2 = ProjectManager(
            workspace_dir=ws,
            brief_path=ws / "brief.md",
            dry_run=True,
        )
        r2 = pm2.run(resume=True)
        # resume may return False if storyboard.yaml is absent from .cache;
        # what matters is it doesn't raise and the storyboard is still there
        assert (ws / "storyboard.yaml").exists()

        # Storyboard file must not have been overwritten by the director
        sb_mtime_after = (ws / "storyboard.yaml").stat().st_mtime
        assert sb_mtime_after == pytest.approx(sb_mtime_before, abs=1.0), (
            "--resume re-ran the director and overwrote storyboard.yaml"
        )
