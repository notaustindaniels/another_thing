"""tests/test_regression.py — Canonical storyboard golden hash regression suite (P6.M03).

Verifies byte-identical determinism for all three canonical storyboard rendering paths
against pre-computed golden SHA-256 hashes stored in evidence/golden/*.sha256.

Storyboard mapping (§11.4):
  Example A — §11.4.1: drone-FPV forest flythrough → canonical_a.yaml
  Example B — §11.4.2: keyframed biome pan          → canonical_b.yaml
  Example C — §11.4.3: portal transition             → canonical_c.yaml

Determinism contract (§7):
  Same seed + same renderer commit + same FFmpeg version = byte-identical MP4.

How the suite works:
  1. Read golden SHA-256 from evidence/golden/<id>.sha256 (committed to repo).
  2. Re-render the canonical scene into a fresh temp directory.
  3. Compute SHA-256 of the new render.
  4. Assert exact match — any byte difference is a regression.

Performance:
  All three renders complete in ≤ 5 minutes each on a standard laptop
  (evidence recorded in evidence/golden/*_meta.json).

Acceptance criteria (P6.M03):
  ✓ Three golden MP4 hashes stored in evidence/golden/*.sha256
  ✓ tests/test_regression.py re-renders all three and passes byte-identical assert
  ✓ All three renders complete in under 5 minutes each on a standard laptop
  ✓ validate_licensing.py exits 0
"""
from __future__ import annotations

import hashlib
import json
import time
import warnings
from pathlib import Path

import pytest

from parallax_engine.render import render_scene
from parallax_engine.scene import load_scene_yaml

# ── paths ──────────────────────────────────────────────────────────────────

REPO_ROOT    = Path(__file__).parent.parent
SCENES_DIR   = REPO_ROOT / "tests" / "scenes"
EVIDENCE_DIR = REPO_ROOT / "evidence" / "golden"

# CI time limit per render (seconds).  §11.4 says ≤ 5 minutes each.
MAX_RENDER_SECONDS = 300

# ── canonical scene descriptors ────────────────────────────────────────────

CANONICAL = [
    {
        "id": "example_a",
        "yaml": SCENES_DIR / "canonical_a.yaml",
        "description": "§11.4.1 — drone-FPV forest flythrough",
    },
    {
        "id": "example_b",
        "yaml": SCENES_DIR / "canonical_b.yaml",
        "description": "§11.4.2 — keyframed biome pan",
    },
    {
        "id": "example_c",
        "yaml": SCENES_DIR / "canonical_c.yaml",
        "description": "§11.4.3 — portal transition",
    },
]


# ── helper ─────────────────────────────────────────────────────────────────


def _read_golden_hash(scene_id: str) -> str:
    """Read the golden SHA-256 stored in evidence/golden/<id>.sha256.

    File format: ``<hex_hash>  <id>.mp4\\n``  (sha256sum compatible).
    """
    sha_file = EVIDENCE_DIR / f"{scene_id}.sha256"
    assert sha_file.exists(), (
        f"Golden hash file missing: {sha_file}\n"
        "Run `python generate_golden_hashes.py` to generate it."
    )
    line = sha_file.read_text().strip()
    # Format: "<hash>  <filename>" — take first whitespace-separated token
    return line.split()[0]


def _render_and_hash(yaml_path: Path, scenes_dir: Path, tmp_path: Path) -> tuple[str, float, int]:
    """Render *yaml_path* and return (sha256_hex, elapsed_s, mp4_size_bytes)."""
    scene = load_scene_yaml(yaml_path)
    out_mp4 = tmp_path / "out.mp4"
    t0 = time.monotonic()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        render_scene(scene, scenes_dir, out_mp4)
    elapsed = time.monotonic() - t0
    mp4_bytes = out_mp4.read_bytes()
    sha256 = hashlib.sha256(mp4_bytes).hexdigest()
    return sha256, elapsed, len(mp4_bytes)


# ── golden hash file integrity ─────────────────────────────────────────────


class TestGoldenHashFiles:
    """Verify that evidence/golden/*.sha256 files are present and well-formed."""

    def test_golden_dir_exists(self) -> None:
        assert EVIDENCE_DIR.is_dir(), (
            f"evidence/golden/ missing.  Run `python generate_golden_hashes.py`."
        )

    @pytest.mark.parametrize("scene", CANONICAL, ids=[s["id"] for s in CANONICAL])
    def test_sha256_file_present(self, scene: dict) -> None:
        sha_file = EVIDENCE_DIR / f"{scene['id']}.sha256"
        assert sha_file.exists(), (
            f"Missing golden hash: {sha_file}.  "
            "Run `python generate_golden_hashes.py` to generate it."
        )

    @pytest.mark.parametrize("scene", CANONICAL, ids=[s["id"] for s in CANONICAL])
    def test_sha256_file_is_64_hex_chars(self, scene: dict) -> None:
        """SHA-256 hex digest must be exactly 64 characters."""
        golden = _read_golden_hash(scene["id"])
        assert len(golden) == 64, f"Bad golden hash length: {len(golden)} chars"
        assert all(c in "0123456789abcdef" for c in golden), (
            f"Non-hex chars in golden hash: {golden[:20]}…"
        )

    @pytest.mark.parametrize("scene", CANONICAL, ids=[s["id"] for s in CANONICAL])
    def test_meta_json_present(self, scene: dict) -> None:
        meta_file = EVIDENCE_DIR / f"{scene['id']}_meta.json"
        assert meta_file.exists(), f"Missing meta JSON: {meta_file}"
        with meta_file.open() as fh:
            data = json.load(fh)
        assert "sha256" in data
        assert "render_time_s" in data
        # render_time_s must be < 300s (§11.4 / P6.M03 acceptance criterion)
        assert data["render_time_s"] < MAX_RENDER_SECONDS, (
            f"Golden render for {scene['id']} exceeded 5-minute limit: "
            f"{data['render_time_s']:.1f}s"
        )

    @pytest.mark.parametrize("scene", CANONICAL, ids=[s["id"] for s in CANONICAL])
    def test_canonical_yaml_present(self, scene: dict) -> None:
        assert scene["yaml"].exists(), f"Canonical scene YAML missing: {scene['yaml']}"


# ── byte-identical regression tests ────────────────────────────────────────


class TestRegressionByteIdentical:
    """Re-render each canonical scene and assert byte-identical SHA-256 match.

    These tests prove the §7 determinism contract holds: same seed +
    same renderer = same MP4 bytes.
    """

    def test_example_a_byte_identical(self, tmp_path: Path) -> None:
        """§11.4.1 forest flythrough: re-render matches golden hash."""
        scene_info = CANONICAL[0]  # example_a
        golden = _read_golden_hash(scene_info["id"])

        sha256, elapsed, size = _render_and_hash(
            scene_info["yaml"], SCENES_DIR, tmp_path
        )

        assert elapsed < MAX_RENDER_SECONDS, (
            f"Render exceeded 5-minute limit: {elapsed:.1f}s "
            f"(§11.4 / P6.M03 acceptance criterion)"
        )
        assert sha256 == golden, (
            f"REGRESSION DETECTED — Example A (forest flythrough) SHA-256 mismatch!\n"
            f"  Expected (golden): {golden}\n"
            f"  Got (re-render):   {sha256}\n"
            f"  MP4 size: {size} bytes, render time: {elapsed:.1f}s\n"
            f"  Check: grain seeding (§9.3), FFmpeg -threads 1 (§7), "
            f"         sort stability in layer ordering."
        )

    def test_example_b_byte_identical(self, tmp_path: Path) -> None:
        """§11.4.2 biome pan: re-render matches golden hash."""
        scene_info = CANONICAL[1]  # example_b
        golden = _read_golden_hash(scene_info["id"])

        sha256, elapsed, size = _render_and_hash(
            scene_info["yaml"], SCENES_DIR, tmp_path
        )

        assert elapsed < MAX_RENDER_SECONDS, (
            f"Render exceeded 5-minute limit: {elapsed:.1f}s"
        )
        assert sha256 == golden, (
            f"REGRESSION DETECTED — Example B (biome pan) SHA-256 mismatch!\n"
            f"  Expected (golden): {golden}\n"
            f"  Got (re-render):   {sha256}\n"
            f"  MP4 size: {size} bytes, render time: {elapsed:.1f}s"
        )

    def test_example_c_byte_identical(self, tmp_path: Path) -> None:
        """§11.4.3 portal transition: re-render matches golden hash."""
        scene_info = CANONICAL[2]  # example_c
        golden = _read_golden_hash(scene_info["id"])

        sha256, elapsed, size = _render_and_hash(
            scene_info["yaml"], SCENES_DIR, tmp_path
        )

        assert elapsed < MAX_RENDER_SECONDS, (
            f"Render exceeded 5-minute limit: {elapsed:.1f}s"
        )
        assert sha256 == golden, (
            f"REGRESSION DETECTED — Example C (portal transition) SHA-256 mismatch!\n"
            f"  Expected (golden): {golden}\n"
            f"  Got (re-render):   {sha256}\n"
            f"  MP4 size: {size} bytes, render time: {elapsed:.1f}s\n"
            f"  Check: portal mask compositing, grain seeding."
        )


# ── determinism within-session tests ───────────────────────────────────────


class TestRenderDeterminismLocal:
    """Render each canonical scene twice in the same session and assert byte-identical.

    These tests are faster than the golden hash comparison (same render twice)
    and catch RNG state contamination within a single test session.
    """

    @pytest.mark.parametrize("scene", CANONICAL, ids=[s["id"] for s in CANONICAL])
    def test_deterministic_double_render(self, scene: dict, tmp_path: Path) -> None:
        """Two renders of the same canonical scene must be byte-identical (§7)."""
        s = load_scene_yaml(scene["yaml"])
        out1 = tmp_path / "render1.mp4"
        out2 = tmp_path / "render2.mp4"

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            render_scene(s, SCENES_DIR, out1)
            render_scene(s, SCENES_DIR, out2)

        b1 = out1.read_bytes()
        b2 = out2.read_bytes()

        assert len(b1) > 0, f"First render empty for {scene['id']}"
        assert len(b2) > 0, f"Second render empty for {scene['id']}"
        assert b1 == b2, (
            f"Non-deterministic render detected for {scene['id']}!\n"
            f"  Run 1 size: {len(b1)} bytes\n"
            f"  Run 2 size: {len(b2)} bytes\n"
            f"  Likely causes: wall-clock randomness, unseeded grain, "
            f"  multi-threaded FFmpeg (-threads 1 must be set per §7)."
        )
