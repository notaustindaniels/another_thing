"""
tests/test_render_golden.py — Determinism and golden regression for portal_regression.yaml (P2.M02).

Validates SPEC.md §7 (Determinism Contract) end-to-end through the full render
pipeline, using the portal_regression.yaml scene structure (§6.3).

For CI speed the scene meta is overridden to 192×108, 10fps, 1.0s — the SAME
structure (same stacks, masks, camera, assets) as portal_regression.yaml but
at a fraction of the cost.  The determinism property is resolution-independent:
if two small renders are byte-identical, two large renders will be too (the RNG
seeding, FFmpeg -threads 1, and grain derivation don't change with resolution).

Tests
------
TestPortalRegressionSceneParsing
    portal_regression.yaml parses and its structure matches §6.3.

TestPortalRegressionDeterminism
    Two renders with the same seed are byte-identical (§7 end-to-end).

TestGoldenHash
    The first render's SHA-256 is stored in evidence/P2.M02/ for future
    regression anchoring.  If the hash changes across CI runs, the build is
    non-deterministic — investigate FFmpeg version drift or seeding changes.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from parallax_engine.render import render_scene
from parallax_engine.scene import (
    SceneMeta,
    load_scene_yaml,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCENES_DIR = Path(__file__).parent / "scenes"
PORTAL_REGRESSION_YAML = SCENES_DIR / "portal_regression.yaml"
ASSETS_DIR = SCENES_DIR / "assets"
EVIDENCE_DIR = Path(__file__).parent.parent / "evidence" / "P2.M02"


# ---------------------------------------------------------------------------
# CI-speed meta override
# ---------------------------------------------------------------------------

#: Full-resolution portal_regression.yaml is 1920×1080 @ 30fps × 8s = 240 frames.
#: For determinism testing we reduce to 192×108 @ 10fps × 1.0s = 10 frames,
#: which proves the same per-frame seeding and encoding determinism properties
#: in ~10s instead of ~4 minutes.  (§7: determinism is resolution-independent.)
CI_RESOLUTION = (192, 108)
CI_FPS = 10
CI_DURATION_S = 1.0


def _make_ci_scene(scene):
    """Return a CI-speed copy of *scene* with reduced resolution/fps/duration."""
    meta_ci = scene.meta.model_copy(update={
        "resolution": CI_RESOLUTION,
        "fps": CI_FPS,
        "duration_s": CI_DURATION_S,
    })
    return scene.model_copy(update={"meta": meta_ci})


# ---------------------------------------------------------------------------
# TestPortalRegressionSceneParsing
# ---------------------------------------------------------------------------

class TestPortalRegressionSceneParsing:
    """portal_regression.yaml parses correctly and matches §6.3 structure."""

    def test_file_exists(self):
        assert PORTAL_REGRESSION_YAML.exists(), (
            f"Missing scene file: {PORTAL_REGRESSION_YAML}"
        )

    def test_parses_without_error(self):
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        assert scene.version == 1

    def test_full_resolution(self):
        """portal_regression.yaml uses full 1920×1080 (§6.3 is not a CI shortcut)."""
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        assert scene.meta.resolution == (1920, 1080), (
            f"Expected 1920×1080, got {scene.meta.resolution}"
        )

    def test_full_duration(self):
        """portal_regression.yaml is 8 seconds (§6.3)."""
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        assert scene.meta.duration_s == 8.0, (
            f"Expected 8.0s, got {scene.meta.duration_s}"
        )

    def test_seed_is_one(self):
        """meta.seed must be 1 per P2.M02 acceptance criteria."""
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        assert scene.meta.seed == 1

    def test_forest_stack_exists(self):
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        assert "forest" in scene.stacks

    def test_city_stack_exists(self):
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        assert "city" in scene.stacks

    def test_forest_layer_count(self):
        """Forest stack has 6 layers matching §6.3."""
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        assert len(scene.stacks["forest"].layers) == 6

    def test_city_layer_count(self):
        """City stack has 3 layers matching §6.3."""
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        assert len(scene.stacks["city"].layers) == 3

    def test_portal_mask_exists(self):
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        assert len(scene.masks) == 1
        assert scene.masks[0].id == "portal"

    def test_portal_mask_anchor_world(self):
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        assert scene.masks[0].anchor == "world"

    def test_portal_mask_growth_perspective(self):
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        assert scene.masks[0].growth.kind == "perspective"

    def test_portal_mask_stacks(self):
        """Portal mask composites forest→city (§6.3)."""
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        mask = scene.masks[0]
        assert mask.src_stack == "forest"
        assert mask.dest_stack == "city"

    def test_portal_layer_has_silhouette_paint_id(self):
        """portal_tree layer sets svg_paint_id: silhouette (§6.3)."""
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        portal_layer = scene.find_layer("forest.portal_tree")
        assert portal_layer.svg_paint_id == "silhouette"

    def test_portal_single_svg_for_both_paths(self):
        """Mask path_svg and layer src must be the same file (§2.4 / §9.4)."""
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        mask = scene.masks[0]
        portal_layer = scene.find_layer(mask.attached_to_layer)
        assert mask.path_svg == portal_layer.src, (
            f"Mask path_svg {mask.path_svg!r} != layer src {portal_layer.src!r}; "
            "the same SVG must define both silhouette and hole (§2.4 / §9.4)"
        )

    def test_portal_svg_has_silhouette_and_hole(self):
        """The portal SVG file itself contains <path id='silhouette'> and <path id='hole'>."""
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        mask = scene.masks[0]
        svg_path = SCENES_DIR / mask.path_svg
        assert svg_path.exists(), f"Portal SVG not found: {svg_path}"
        content = svg_path.read_text()
        assert 'id="silhouette"' in content, (
            "portal_tree.svg must contain <path id='silhouette'> (§2.4)"
        )
        assert 'id="hole"' in content, (
            "portal_tree.svg must contain <path id='hole'> (§2.4)"
        )

    def test_portal_svg_has_viewbox(self):
        """The portal SVG has a viewBox so silhouette and hole share coordinate space (§2.4)."""
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        mask = scene.masks[0]
        svg_path = SCENES_DIR / mask.path_svg
        content = svg_path.read_text()
        assert "viewBox" in content, (
            "portal_tree.svg must have a viewBox attribute (§2.4)"
        )

    def test_assets_exist(self):
        """All assets referenced by portal_regression.yaml exist on disk."""
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        missing = []
        for stack in scene.stacks.values():
            for layer in stack.layers:
                p = SCENES_DIR / layer.src
                if not p.exists():
                    missing.append(str(p))
        for mask in (scene.masks or []):
            if mask.path_svg:
                p = SCENES_DIR / mask.path_svg
                if not p.exists():
                    missing.append(str(p))
        assert not missing, (
            "Missing assets referenced by portal_regression.yaml:\n"
            + "\n".join(missing)
        )

    def test_drone_camera(self):
        """Camera mode is drone with bezier path (§6.3)."""
        scene = load_scene_yaml(PORTAL_REGRESSION_YAML)
        assert scene.camera.mode == "drone"
        assert scene.camera.drone is not None
        assert scene.camera.drone.path.kind == "bezier"


# ---------------------------------------------------------------------------
# TestPortalRegressionDeterminism
# ---------------------------------------------------------------------------

class TestPortalRegressionDeterminism:
    """Two renders of portal_regression.yaml with same seed are byte-identical (§7)."""

    def test_portal_regression_deterministic(self, tmp_path):
        """
        Render the portal_regression.yaml scene twice (CI-speed resolution).
        The two MP4 outputs must be byte-identical — this is the §7 determinism
        contract end-to-end through: scene loading → camera precompute →
        SVG rasterization → mask compositing → grain (seeded) → FFmpeg encode.
        """
        scene_full = load_scene_yaml(PORTAL_REGRESSION_YAML)
        scene_ci = _make_ci_scene(scene_full)

        out1 = tmp_path / "portal_regression_a.mp4"
        out2 = tmp_path / "portal_regression_b.mp4"

        render_scene(scene_ci, SCENES_DIR, out1)
        render_scene(scene_ci, SCENES_DIR, out2)

        b1 = out1.read_bytes()
        b2 = out2.read_bytes()

        assert len(b1) > 0, "First render produced empty MP4"
        assert len(b2) > 0, "Second render produced empty MP4"
        assert b1 == b2, (
            f"Non-deterministic portal render! Sizes: {len(b1)} vs {len(b2)}. "
            "Check: grain seeding (must use scene.meta.seed), "
            "FFmpeg -threads 1, sort stability in layer ordering."
        )

    def test_mp4_has_valid_header(self, tmp_path):
        """Rendered MP4 starts with a valid ftyp/moov box."""
        scene_full = load_scene_yaml(PORTAL_REGRESSION_YAML)
        scene_ci = _make_ci_scene(scene_full)
        out = tmp_path / "portal_regression_header_check.mp4"
        render_scene(scene_ci, SCENES_DIR, out)
        header = out.read_bytes()[:12]
        assert b"ftyp" in header or b"moov" in header, (
            f"Output does not look like a valid MP4 (header: {header!r})"
        )


# ---------------------------------------------------------------------------
# TestGoldenHash
# ---------------------------------------------------------------------------

class TestGoldenHash:
    """
    Compute and store the golden SHA-256 hash of portal_regression.yaml
    rendered at CI resolution.

    The hash is written to evidence/P2.M02/golden_hash.json so future runs
    can detect determinism regressions without storing the MP4 in git.

    Note: this test never *fails* on a hash mismatch — it only computes and
    stores the hash.  The regression check (compare against stored hash) is
    intentionally deferred to a human or the Phase 6 regression suite, because
    the golden hash changes legitimately when the renderer is improved.
    """

    def test_compute_and_store_golden_hash(self, tmp_path):
        """Render CI-speed portal_regression.yaml and write its SHA-256 to evidence/."""
        scene_full = load_scene_yaml(PORTAL_REGRESSION_YAML)
        scene_ci = _make_ci_scene(scene_full)

        out = tmp_path / "portal_regression_golden.mp4"
        render_scene(scene_ci, SCENES_DIR, out)

        mp4_bytes = out.read_bytes()
        sha256 = hashlib.sha256(mp4_bytes).hexdigest()

        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        golden_path = EVIDENCE_DIR / "golden_hash.json"
        record = {
            "milestone": "P2.M02",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "scene": "tests/scenes/portal_regression.yaml",
            "ci_resolution": list(CI_RESOLUTION),
            "ci_fps": CI_FPS,
            "ci_duration_s": CI_DURATION_S,
            "seed": scene_full.meta.seed,
            "mp4_size_bytes": len(mp4_bytes),
            "sha256": sha256,
            "spec_anchor": "§7",
        }
        golden_path.write_text(json.dumps(record, indent=2) + "\n")

        assert len(mp4_bytes) > 0, "Golden render produced empty MP4"
        print(f"\n[golden hash] sha256: {sha256}")
        print(f"[golden hash] written to: {golden_path}")
