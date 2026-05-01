"""
tests/test_tools.py — Tests for parallax_engine.tools (P3.M02).

Covers:
  - parallax_engine.tools.render.render_scene
  - parallax_engine.tools.qa.diff_frames
  - parallax_engine.tools.qa.ssim_score
  - parallax_engine.tools.qa._ssim_grayscale (internal, but tested for math)

All tests are self-contained: they create temporary directories, write synthetic
frames, call the tools, and assert on the returned dicts.
"""
from __future__ import annotations

import json
import pathlib
import tempfile

import cv2
import numpy as np
import pytest

from parallax_engine.tools.qa import _ssim_grayscale, diff_frames, ssim_score


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _solid_frame(h: int, w: int, color_bgr: tuple[int, int, int]) -> np.ndarray:
    """Return a solid-color uint8 BGR image."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color_bgr
    return img


def _write_frames(directory: pathlib.Path, frames: list[np.ndarray]) -> None:
    """Write a list of BGR frames as frame_NNNNN.png."""
    directory.mkdir(parents=True, exist_ok=True)
    for i, frame in enumerate(frames):
        cv2.imwrite(str(directory / f"frame_{i:05d}.png"), frame)


# ---------------------------------------------------------------------------
# Tests: diff_frames
# ---------------------------------------------------------------------------

class TestDiffFrames:
    def test_identical_frames_zero_diff(self, tmp_path: pathlib.Path) -> None:
        """Identical frame sequences → overall_max=0, overall_mean=0."""
        frame = _solid_frame(64, 64, (100, 150, 200))
        dir_a = tmp_path / "dir_a"
        dir_b = tmp_path / "dir_b"
        _write_frames(dir_a, [frame, frame, frame])
        _write_frames(dir_b, [frame, frame, frame])

        result = diff_frames(dir_a, dir_b)

        assert result["n_frames"] == 3
        assert result["overall_max"] == pytest.approx(0.0)
        assert result["overall_mean"] == pytest.approx(0.0)
        assert len(result["frames"]) == 3

    def test_known_diff(self, tmp_path: pathlib.Path) -> None:
        """Frame differing by 50 in one channel → max_abs_diff = 50."""
        frame_a = _solid_frame(32, 32, (100, 100, 100))
        frame_b = _solid_frame(32, 32, (150, 100, 100))  # B channel +50
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_frames(dir_a, [frame_a])
        _write_frames(dir_b, [frame_b])

        result = diff_frames(dir_a, dir_b)

        assert result["n_frames"] == 1
        assert result["overall_max"] == pytest.approx(50.0)

    def test_empty_directories(self, tmp_path: pathlib.Path) -> None:
        """Empty frame dirs → n_frames=0, zeros."""
        dir_a = tmp_path / "empty_a"
        dir_b = tmp_path / "empty_b"
        dir_a.mkdir()
        dir_b.mkdir()

        result = diff_frames(dir_a, dir_b)

        assert result["n_frames"] == 0
        assert result["overall_max"] == 0.0
        assert result["overall_mean"] == 0.0
        assert result["frames"] == []

    def test_missing_counterpart_skipped(self, tmp_path: pathlib.Path) -> None:
        """Frames in dir_a without counterpart in dir_b are skipped."""
        frame = _solid_frame(16, 16, (10, 20, 30))
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_frames(dir_a, [frame, frame, frame])  # 3 frames
        _write_frames(dir_b, [frame])                 # only 1 frame

        result = diff_frames(dir_a, dir_b)

        assert result["n_frames"] == 1  # only the matched pair

    def test_result_keys_present(self, tmp_path: pathlib.Path) -> None:
        """Result dict has expected keys: n_frames, overall_max, overall_mean, frames."""
        frame = _solid_frame(8, 8, (0, 0, 0))
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_frames(dir_a, [frame])
        _write_frames(dir_b, [frame])

        result = diff_frames(dir_a, dir_b)

        assert set(result.keys()) >= {"n_frames", "overall_max", "overall_mean", "frames"}

    def test_per_frame_records_structure(self, tmp_path: pathlib.Path) -> None:
        """Each frame record has file, max_abs_diff, mean_abs_diff."""
        frame_a = _solid_frame(16, 16, (10, 20, 30))
        frame_b = _solid_frame(16, 16, (15, 25, 35))
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_frames(dir_a, [frame_a])
        _write_frames(dir_b, [frame_b])

        result = diff_frames(dir_a, dir_b)

        assert len(result["frames"]) == 1
        rec = result["frames"][0]
        assert "file" in rec
        assert "max_abs_diff" in rec
        assert "mean_abs_diff" in rec
        assert rec["file"] == "frame_00000.png"

    def test_json_serialisable(self, tmp_path: pathlib.Path) -> None:
        """Result dict is fully JSON-serialisable."""
        frame = _solid_frame(8, 8, (1, 2, 3))
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_frames(dir_a, [frame])
        _write_frames(dir_b, [frame])

        result = diff_frames(dir_a, dir_b)

        # Should not raise
        json.dumps(result)

    def test_mismatched_sizes_handled(self, tmp_path: pathlib.Path) -> None:
        """Frame pairs with different sizes are resized and compared without error."""
        frame_a = _solid_frame(64, 64, (100, 100, 100))
        frame_b = _solid_frame(128, 128, (100, 100, 100))
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_frames(dir_a, [frame_a])
        _write_frames(dir_b, [frame_b])

        result = diff_frames(dir_a, dir_b)

        assert result["n_frames"] == 1
        # Resized identical color → should be ~0 diff
        assert result["overall_max"] < 5.0


# ---------------------------------------------------------------------------
# Tests: ssim_score
# ---------------------------------------------------------------------------

class TestSsimScore:
    def test_identical_frames_ssim_one(self, tmp_path: pathlib.Path) -> None:
        """Identical frame sequences → mean_ssim very close to 1.0."""
        frame = _solid_frame(64, 64, (80, 120, 200))
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_frames(dir_a, [frame, frame])
        _write_frames(dir_b, [frame, frame])

        result = ssim_score(dir_a, dir_b)

        assert result["n_frames"] == 2
        assert result["mean_ssim"] == pytest.approx(1.0, abs=1e-4)
        assert result["min_ssim"] == pytest.approx(1.0, abs=1e-4)

    def test_different_frames_ssim_lower(self, tmp_path: pathlib.Path) -> None:
        """Frames with large pixel differences have SSIM < 0.99."""
        frame_a = _solid_frame(64, 64, (0, 0, 0))
        frame_b = _solid_frame(64, 64, (255, 255, 255))
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_frames(dir_a, [frame_a])
        _write_frames(dir_b, [frame_b])

        result = ssim_score(dir_a, dir_b)

        assert result["n_frames"] == 1
        # Black vs white → very low SSIM
        assert result["mean_ssim"] < 0.5

    def test_empty_directories(self, tmp_path: pathlib.Path) -> None:
        """Empty dirs → n_frames=0, zeros."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        result = ssim_score(dir_a, dir_b)

        assert result["n_frames"] == 0
        assert result["mean_ssim"] == 0.0
        assert result["min_ssim"] == 0.0
        assert result["frames"] == []

    def test_result_keys_present(self, tmp_path: pathlib.Path) -> None:
        """Result dict has expected keys."""
        frame = _solid_frame(32, 32, (50, 100, 150))
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_frames(dir_a, [frame])
        _write_frames(dir_b, [frame])

        result = ssim_score(dir_a, dir_b)

        assert set(result.keys()) >= {"n_frames", "mean_ssim", "min_ssim", "frames"}

    def test_json_serialisable(self, tmp_path: pathlib.Path) -> None:
        """Result dict is JSON-serialisable."""
        frame = _solid_frame(16, 16, (10, 20, 30))
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_frames(dir_a, [frame])
        _write_frames(dir_b, [frame])

        result = ssim_score(dir_a, dir_b)

        json.dumps(result)

    def test_per_frame_records_structure(self, tmp_path: pathlib.Path) -> None:
        """Each frame record has file and ssim."""
        frame = _solid_frame(32, 32, (200, 100, 50))
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_frames(dir_a, [frame])
        _write_frames(dir_b, [frame])

        result = ssim_score(dir_a, dir_b)

        assert len(result["frames"]) == 1
        rec = result["frames"][0]
        assert "file" in rec
        assert "ssim" in rec


# ---------------------------------------------------------------------------
# Tests: _ssim_grayscale (internal — test the math directly)
# ---------------------------------------------------------------------------

class TestSsimGrayscale:
    def test_identical_image_ssim_one(self) -> None:
        """SSIM of an image against itself must be 1.0."""
        rng = np.random.default_rng(42)
        img = rng.integers(0, 256, size=(64, 64), dtype=np.uint8)

        result = _ssim_grayscale(img, img)

        assert result == pytest.approx(1.0, abs=1e-6)

    def test_constant_images(self) -> None:
        """Two identical constant images → SSIM == 1.0."""
        img = np.full((32, 32), 128, dtype=np.uint8)

        result = _ssim_grayscale(img, img)

        assert result == pytest.approx(1.0, abs=1e-6)

    def test_range_zero_to_one(self) -> None:
        """SSIM is always in [0, 1] for natural images."""
        rng = np.random.default_rng(7)
        img_a = rng.integers(0, 256, size=(64, 64), dtype=np.uint8)
        img_b = rng.integers(0, 256, size=(64, 64), dtype=np.uint8)

        result = _ssim_grayscale(img_a, img_b)

        assert 0.0 <= result <= 1.0

    def test_different_images_ssim_below_one(self) -> None:
        """SSIM of two genuinely different images is < 1.0."""
        img_a = np.full((32, 32), 0, dtype=np.uint8)
        img_b = np.full((32, 32), 255, dtype=np.uint8)

        result = _ssim_grayscale(img_a, img_b)

        assert result < 0.5

    def test_symmetry(self) -> None:
        """SSIM is symmetric: ssim(a,b) == ssim(b,a)."""
        rng = np.random.default_rng(99)
        img_a = rng.integers(0, 256, size=(32, 32), dtype=np.uint8)
        img_b = rng.integers(0, 256, size=(32, 32), dtype=np.uint8)

        assert _ssim_grayscale(img_a, img_b) == pytest.approx(
            _ssim_grayscale(img_b, img_a), abs=1e-8
        )


# ---------------------------------------------------------------------------
# Tests: tools.render.render_scene (import + error path only; no actual render)
# ---------------------------------------------------------------------------

class TestRenderSceneTool:
    def test_importable(self) -> None:
        """render_scene is importable from parallax_engine.tools.render."""
        from parallax_engine.tools.render import render_scene  # noqa: F401

    def test_bad_scene_path_returns_error_dict(self, tmp_path: pathlib.Path) -> None:
        """Non-existent scene path → ok=False, no exception raised."""
        from parallax_engine.tools.render import render_scene

        result = render_scene(
            scene_yaml_path=tmp_path / "nonexistent.yaml",
            workspace=tmp_path / "ws",
        )

        assert isinstance(result, dict)
        assert result["ok"] is False
        assert "message" in result
        assert "out_mp4" in result
        assert "n_frames" in result
        assert "size_bytes" in result

    def test_bad_yaml_returns_error_dict(self, tmp_path: pathlib.Path) -> None:
        """Malformed YAML → ok=False, message describes parse error."""
        from parallax_engine.tools.render import render_scene

        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(": invalid: yaml: {{{{")

        result = render_scene(
            scene_yaml_path=bad_yaml,
            workspace=tmp_path / "ws",
        )

        assert isinstance(result, dict)
        assert result["ok"] is False
        assert result["n_frames"] is None

    def test_wrong_version_returns_error_dict(self, tmp_path: pathlib.Path) -> None:
        """scene.yaml with wrong version → ok=False."""
        from parallax_engine.tools.render import render_scene

        bad_yaml = tmp_path / "scene.yaml"
        bad_yaml.write_text("version: 99\nmeta:\n  resolution: [640, 360]\n")

        result = render_scene(
            scene_yaml_path=bad_yaml,
            workspace=tmp_path / "ws",
        )

        assert isinstance(result, dict)
        assert result["ok"] is False

    def test_result_is_json_serialisable(self, tmp_path: pathlib.Path) -> None:
        """Even error results are JSON-serialisable."""
        from parallax_engine.tools.render import render_scene

        result = render_scene(
            scene_yaml_path=tmp_path / "missing.yaml",
            workspace=tmp_path / "ws",
        )

        json.dumps(result)
