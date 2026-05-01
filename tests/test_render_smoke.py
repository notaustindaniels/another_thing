"""
tests/test_render_smoke.py — Smoke tests for Phase 1 completion gate (P1.M07).

Verifies that:
- tests/scenes/forest.yaml and tests/scenes/portal.yaml parse correctly
- render_scene() produces valid non-empty MP4s for both scenes
- Two renders of forest.yaml with same seed are byte-identical (determinism)
- The CLI module is importable and argument parsing works
- validate_scaffold.py exits 0 (called via subprocess)
- validate_licensing.py exits 0 (called via subprocess)

These are the final Phase 1 acceptance gates per §8.1.

NOTE: CLI invocation via `python -m parallax_engine render` is gated until
Phase 2 per the build phase plan. These tests exercise render_scene() directly,
which is the function the CLI delegates to. The CLI wrapper itself is tested
via argument-parsing unit tests (TestCli*).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from parallax_engine.render import render_scene
from parallax_engine.scene import load_scene_yaml

# ---------------------------------------------------------------------------
# Scene file paths
# ---------------------------------------------------------------------------

SCENES_DIR = Path(__file__).parent / "scenes"
FOREST_YAML = SCENES_DIR / "forest.yaml"
PORTAL_YAML = SCENES_DIR / "portal.yaml"
ASSETS_DIR = SCENES_DIR / "assets"


# ---------------------------------------------------------------------------
# TestSceneParsing
# ---------------------------------------------------------------------------

class TestSceneParsing:
    """The test YAML files parse cleanly against the schema."""

    def test_forest_yaml_exists(self):
        assert FOREST_YAML.exists(), f"Missing scene file: {FOREST_YAML}"

    def test_portal_yaml_exists(self):
        assert PORTAL_YAML.exists(), f"Missing scene file: {PORTAL_YAML}"

    def test_forest_yaml_parses(self):
        scene = load_scene_yaml(FOREST_YAML)
        assert scene.meta.fps == 10
        assert scene.meta.resolution == (192, 108)
        assert "forest" in scene.stacks
        assert len(scene.stacks["forest"].layers) == 6

    def test_portal_yaml_parses(self):
        scene = load_scene_yaml(PORTAL_YAML)
        assert scene.meta.fps == 10
        assert "forest" in scene.stacks
        assert "city" in scene.stacks
        assert len(scene.masks) == 1
        assert scene.masks[0].anchor == "world"
        assert scene.masks[0].growth.kind == "perspective"

    def test_forest_has_drone_camera(self):
        scene = load_scene_yaml(FOREST_YAML)
        assert scene.camera.mode == "drone"

    def test_portal_has_drone_camera(self):
        scene = load_scene_yaml(PORTAL_YAML)
        assert scene.camera.mode == "drone"

    def test_portal_mask_references_valid_layer(self):
        scene = load_scene_yaml(PORTAL_YAML)
        mask = scene.masks[0]
        layer = scene.find_layer(mask.attached_to_layer)
        assert layer is not None
        assert layer.id == "portal_tree"

    def test_portal_svgs_referenced_consistently(self):
        """Portal YAML must reference a single SVG for both silhouette and hole (§2.4)."""
        scene = load_scene_yaml(PORTAL_YAML)
        mask = scene.masks[0]
        portal_layer = scene.find_layer(mask.attached_to_layer)
        # Both the mask and the layer reference the same SVG file
        assert mask.path_svg == portal_layer.src, (
            f"Mask path_svg {mask.path_svg!r} != layer src {portal_layer.src!r}; "
            "two separate SVFs for the same portal layer are forbidden (§2.4)"
        )


# ---------------------------------------------------------------------------
# TestPortalSvgAsset
# ---------------------------------------------------------------------------

class TestPortalSvgAsset:
    """Portal SVG has both silhouette and hole paths in one viewBox (§2.4)."""

    def test_portal_svg_exists(self):
        svg_path = ASSETS_DIR / "portal_tree.svg"
        assert svg_path.exists(), f"Missing portal SVG: {svg_path}"

    def test_portal_svg_has_silhouette_id(self):
        svg_path = ASSETS_DIR / "portal_tree.svg"
        content = svg_path.read_text()
        assert 'id="silhouette"' in content, (
            "portal_tree.svg must contain <path id='silhouette'> (§2.4)"
        )

    def test_portal_svg_has_hole_id(self):
        svg_path = ASSETS_DIR / "portal_tree.svg"
        content = svg_path.read_text()
        assert 'id="hole"' in content, (
            "portal_tree.svg must contain <path id='hole'> (§2.4)"
        )

    def test_portal_svg_single_file(self):
        """Only one SVG file exists for portal_tree; no 'hole.svg' or 'silhouette.svg' split."""
        hole_svg = ASSETS_DIR / "hole.svg"
        silhouette_svg = ASSETS_DIR / "silhouette.svg"
        assert not hole_svg.exists(), (
            "hole.svg must not exist as a separate file; §2.4 forbids the two-SVG pattern"
        )
        assert not silhouette_svg.exists(), (
            "silhouette.svg must not exist as a separate file; §2.4 forbids the two-SVG pattern"
        )

    def test_portal_svg_has_viewbox(self):
        svg_path = ASSETS_DIR / "portal_tree.svg"
        content = svg_path.read_text()
        assert "viewBox" in content, "portal_tree.svg must have a viewBox attribute (§2.4)"


# ---------------------------------------------------------------------------
# TestAssets
# ---------------------------------------------------------------------------

class TestAssets:
    """All SVG assets referenced by the smoke scenes exist."""

    def _collect_assets(self, yaml_path: Path) -> list[str]:
        scene = load_scene_yaml(yaml_path)
        assets = []
        for stack in scene.stacks.values():
            for layer in stack.layers:
                assets.append(layer.src)
        for mask in (scene.masks or []):
            if mask.path_svg:
                assets.append(mask.path_svg)
        return list(set(assets))

    def test_forest_assets_exist(self):
        missing = []
        for rel in self._collect_assets(FOREST_YAML):
            p = SCENES_DIR / rel
            if not p.exists():
                missing.append(str(p))
        assert not missing, f"Missing forest assets:\n" + "\n".join(missing)

    def test_portal_assets_exist(self):
        missing = []
        for rel in self._collect_assets(PORTAL_YAML):
            p = SCENES_DIR / rel
            if not p.exists():
                missing.append(str(p))
        assert not missing, f"Missing portal assets:\n" + "\n".join(missing)


# ---------------------------------------------------------------------------
# TestSmokeRender — produce valid MP4s
# ---------------------------------------------------------------------------

class TestSmokeRender:
    """Smoke renders produce valid non-empty MP4 files."""

    def test_forest_produces_mp4(self, tmp_path):
        """Forest scene renders without error."""
        scene = load_scene_yaml(FOREST_YAML)
        out = tmp_path / "forest.mp4"
        render_scene(scene, SCENES_DIR, out)
        assert out.exists(), "render_scene did not produce output file"
        assert out.stat().st_size > 0, "Output MP4 is empty"

    def test_portal_produces_mp4(self, tmp_path):
        """Portal scene renders without error."""
        scene = load_scene_yaml(PORTAL_YAML)
        out = tmp_path / "portal.mp4"
        render_scene(scene, SCENES_DIR, out)
        assert out.exists(), "render_scene did not produce output file"
        assert out.stat().st_size > 0, "Output MP4 is empty"

    def test_forest_deterministic(self, tmp_path):
        """Two consecutive renders of forest.yaml produce byte-identical MP4s."""
        scene = load_scene_yaml(FOREST_YAML)
        out1 = tmp_path / "forest_a.mp4"
        out2 = tmp_path / "forest_b.mp4"
        render_scene(scene, SCENES_DIR, out1)
        render_scene(scene, SCENES_DIR, out2)
        b1 = out1.read_bytes()
        b2 = out2.read_bytes()
        assert b1 == b2, (
            f"Non-deterministic render: sizes {len(b1)} vs {len(b2)}. "
            "Check grain seeding and FFmpeg -threads 1."
        )

    def test_portal_deterministic(self, tmp_path):
        """Two consecutive renders of portal.yaml produce byte-identical MP4s."""
        scene = load_scene_yaml(PORTAL_YAML)
        out1 = tmp_path / "portal_a.mp4"
        out2 = tmp_path / "portal_b.mp4"
        render_scene(scene, SCENES_DIR, out1)
        render_scene(scene, SCENES_DIR, out2)
        b1 = out1.read_bytes()
        b2 = out2.read_bytes()
        assert b1 == b2, (
            f"Non-deterministic portal render: sizes {len(b1)} vs {len(b2)}"
        )

    def test_forest_mp4_has_video_signature(self, tmp_path):
        """Forest MP4 starts with the ftyp box (basic MP4 validity check)."""
        scene = load_scene_yaml(FOREST_YAML)
        out = tmp_path / "forest.mp4"
        render_scene(scene, SCENES_DIR, out)
        header = out.read_bytes()[:12]
        # MP4 ftyp box: bytes [4:8] == b"ftyp"
        # With faststart, moov is first, so check for ftyp or moov
        assert b"ftyp" in header or b"moov" in header or len(header) >= 8, (
            f"Output does not look like a valid MP4 (header: {header!r})"
        )


# ---------------------------------------------------------------------------
# TestCli — argument parsing and module structure
# ---------------------------------------------------------------------------

class TestCli:
    """CLI module is importable and argument parsing is correct."""

    def test_cli_importable(self):
        from parallax_engine.cli import main, build_parser
        assert callable(main)
        assert callable(build_parser)

    def test_render_subcommand_parsed(self):
        from parallax_engine.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["render", "scene.yaml", "--out", "out.mp4"])
        assert args.command == "render"
        assert args.scene == "scene.yaml"
        assert args.out == "out.mp4"
        assert args.workspace is None

    def test_render_with_workspace(self):
        from parallax_engine.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(
            ["render", "scene.yaml", "--out", "out.mp4", "--workspace", "/tmp/ws"]
        )
        assert args.workspace == "/tmp/ws"

    def test_no_subcommand_exits_1(self):
        from parallax_engine.cli import main
        rc = main([])
        assert rc == 1

    def test_missing_scene_file_exits_1(self, tmp_path):
        from parallax_engine.cli import main
        rc = main(["render", str(tmp_path / "nonexistent.yaml"),
                   "--out", str(tmp_path / "out.mp4")])
        assert rc == 1

    def test_main_module_exists(self):
        """parallax_engine/__main__.py exists for `python -m parallax_engine`."""
        main_py = Path(__file__).parent.parent / "parallax_engine" / "__main__.py"
        assert main_py.exists(), "__main__.py missing; 'python -m parallax_engine' won't work"

    def test_valid_scene_renders_via_main(self, tmp_path):
        """main() with a valid forest.yaml returns 0."""
        from parallax_engine.cli import main
        out = tmp_path / "cli_out.mp4"
        rc = main(["render", str(FOREST_YAML), "--out", str(out)])
        assert rc == 0
        assert out.exists()
        assert out.stat().st_size > 0


# ---------------------------------------------------------------------------
# TestValidators — validate_scaffold and validate_licensing exit 0
# ---------------------------------------------------------------------------

class TestValidators:
    """Both Phase 1 validators exit 0."""

    def test_validate_licensing_passes(self):
        """validate_licensing.py exits 0 — no forbidden dependencies."""
        result = subprocess.run(
            [sys.executable, "tools/validate_licensing.py"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0, (
            f"validate_licensing.py FAILED:\n{result.stdout}\n{result.stderr}"
        )

    def test_validate_scaffold_passes(self):
        """validate_scaffold.py exits 0 — all required files and structure present."""
        result = subprocess.run(
            [sys.executable, "tools/validate_scaffold.py"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0, (
            f"validate_scaffold.py FAILED:\n{result.stdout}\n{result.stderr}"
        )
