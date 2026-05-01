"""
tests/test_post_processing.py — P6.M01 acceptance criteria tests.

Covers:
  1. .cube LUT parsing and trilinear application
  2. Fisheye backward warp with precomputed pixel map; byte-identical across calls
  3. Grain seeded per §9.3; same seed + frame index → same pattern
  4. Light leaks: screen blend formula; opacity=0 → no change; opacity=1 → full blend
  5. Vignette smoothstep formula
  6. Pinned execution order: vignette → grain → light_leaks → fisheye → color_grade
  7. _FISHEYE_MAP_CACHE stores and reuses precomputed maps
"""
from __future__ import annotations

import io
import tempfile
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import numpy as np
import pytest

from parallax_engine.render import (
    _FISHEYE_MAP_CACHE,
    _apply_color_grade,
    _apply_cube_lut,
    _apply_fisheye,
    _apply_grain,
    _apply_light_leaks,
    _apply_vignette,
    _apply_global_post,
    _compute_fisheye_map,
    _parse_cube_lut,
)
from parallax_engine.seeds import spawn_channel, GRAIN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _solid_frame(H: int = 32, W: int = 48, r=0.5, g=0.5, b=0.5, a=1.0) -> np.ndarray:
    """Return a solid-color float32 RGBA frame."""
    frame = np.zeros((H, W, 4), dtype=np.float32)
    frame[:, :, 0] = r
    frame[:, :, 1] = g
    frame[:, :, 2] = b
    frame[:, :, 3] = a
    return frame


def _make_identity_cube(size: int = 4) -> str:
    """Generate a minimal identity .cube LUT of the given size."""
    lines = [
        f"TITLE \"Identity {size}\"",
        f"LUT_3D_SIZE {size}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]
    # R-fastest, B-slowest
    for b in range(size):
        for g in range(size):
            for r in range(size):
                rv = r / (size - 1) if size > 1 else 0.0
                gv = g / (size - 1) if size > 1 else 0.0
                bv = b / (size - 1) if size > 1 else 0.0
                lines.append(f"{rv:.6f} {gv:.6f} {bv:.6f}")
    return "\n".join(lines) + "\n"


def _write_cube(content: str, tmp_path: Path) -> Path:
    p = tmp_path / "test.cube"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# 1. .cube LUT parsing
# ---------------------------------------------------------------------------

class TestParseCubeLut:
    def test_parses_identity_lut(self, tmp_path):
        cube = _make_identity_cube(size=4)
        p = _write_cube(cube, tmp_path)
        lut = _parse_cube_lut(p)
        assert lut.shape == (4, 4, 4, 3)
        assert lut.dtype == np.float32

    def test_identity_lut_values(self, tmp_path):
        """Identity LUT: lut[b,g,r] ≈ (r/(N-1), g/(N-1), b/(N-1))."""
        N = 4
        cube = _make_identity_cube(size=N)
        p = _write_cube(cube, tmp_path)
        lut = _parse_cube_lut(p)
        for b in range(N):
            for g in range(N):
                for r in range(N):
                    expected = np.array(
                        [r / (N - 1), g / (N - 1), b / (N - 1)], dtype=np.float32
                    )
                    np.testing.assert_allclose(lut[b, g, r], expected, atol=1e-5)

    def test_comments_and_blank_lines_ignored(self, tmp_path):
        """Lines starting with # and blank lines are silently skipped."""
        N = 2
        lines = [
            "# This is a comment",
            "",
            "TITLE \"test\"",
            "# Another comment",
            "LUT_3D_SIZE 2",
            "",
            "DOMAIN_MIN 0.0 0.0 0.0",
            "DOMAIN_MAX 1.0 1.0 1.0",
            "# data follows",
        ]
        # 8 data entries for N=2
        for b in range(N):
            for g in range(N):
                for r in range(N):
                    lines.append(f"{float(r):.1f} {float(g):.1f} {float(b):.1f}")
        content = "\n".join(lines) + "\n"
        p = _write_cube(content, tmp_path)
        lut = _parse_cube_lut(p)
        assert lut.shape == (2, 2, 2, 3)

    def test_missing_lut_size_raises_valueerror(self, tmp_path):
        content = "0.0 0.0 0.0\n0.5 0.5 0.5\n"
        p = _write_cube(content, tmp_path)
        with pytest.raises(ValueError, match="LUT_3D_SIZE"):
            _parse_cube_lut(p)

    def test_wrong_entry_count_raises_valueerror(self, tmp_path):
        content = "LUT_3D_SIZE 2\n0.0 0.0 0.0\n"  # only 1 of 8 entries
        p = _write_cube(content, tmp_path)
        with pytest.raises(ValueError, match="Expected 8"):
            _parse_cube_lut(p)

    def test_size_2_round_trip(self, tmp_path):
        """2x2x2 LUT: all 8 corners present, correct R-fastest order."""
        N = 2
        cube = _make_identity_cube(size=N)
        p = _write_cube(cube, tmp_path)
        lut = _parse_cube_lut(p)
        # R=0, G=0, B=0 → (0,0,0)
        np.testing.assert_allclose(lut[0, 0, 0], [0.0, 0.0, 0.0], atol=1e-6)
        # R=1, G=0, B=0 → (1,0,0)
        np.testing.assert_allclose(lut[0, 0, 1], [1.0, 0.0, 0.0], atol=1e-6)
        # R=0, G=1, B=0 → (0,1,0)
        np.testing.assert_allclose(lut[0, 1, 0], [0.0, 1.0, 0.0], atol=1e-6)
        # R=0, G=0, B=1 → (0,0,1)
        np.testing.assert_allclose(lut[1, 0, 0], [0.0, 0.0, 1.0], atol=1e-6)


# ---------------------------------------------------------------------------
# 2. _apply_cube_lut — trilinear application
# ---------------------------------------------------------------------------

class TestApplyCubeLut:
    def test_identity_lut_leaves_frame_unchanged(self, tmp_path):
        """Applying an identity LUT must not change any pixel."""
        N = 33
        cube = _make_identity_cube(size=N)
        p = _write_cube(cube, tmp_path)
        lut = _parse_cube_lut(p)

        frame = _solid_frame(16, 16, r=0.3, g=0.6, b=0.9)
        result = _apply_cube_lut(frame, lut)
        np.testing.assert_allclose(result[:, :, :3], frame[:, :, :3], atol=1e-3)
        np.testing.assert_array_equal(result[:, :, 3], frame[:, :, 3])  # alpha unchanged

    def test_alpha_channel_unchanged(self, tmp_path):
        """LUT must never modify the alpha channel."""
        N = 4
        cube = _make_identity_cube(size=N)
        p = _write_cube(cube, tmp_path)
        lut = _parse_cube_lut(p)

        rng = np.random.default_rng(42)
        frame = rng.random((8, 8, 4), dtype=np.float64).astype(np.float32)
        result = _apply_cube_lut(frame, lut)
        np.testing.assert_array_equal(result[:, :, 3], frame[:, :, 3])

    def test_constant_shift_lut(self, tmp_path):
        """A LUT that shifts R+0.1 should shift all red values by ~0.1."""
        N = 2
        lines = [
            "LUT_3D_SIZE 2",
            "DOMAIN_MIN 0.0 0.0 0.0",
            "DOMAIN_MAX 1.0 1.0 1.0",
        ]
        # R-fastest, B-slowest; shift R output by +0.1 (clamped to 1.0)
        for b in range(N):
            for g in range(N):
                for r in range(N):
                    rv = min(1.0, float(r) + 0.1)
                    gv = float(g)
                    bv = float(b)
                    lines.append(f"{rv:.4f} {gv:.4f} {bv:.4f}")
        content = "\n".join(lines) + "\n"
        p = _write_cube(content, tmp_path)
        lut = _parse_cube_lut(p)

        # Pure black frame → after shift R should be ~0.1
        frame = _solid_frame(4, 4, r=0.0, g=0.0, b=0.0)
        result = _apply_cube_lut(frame, lut)
        np.testing.assert_allclose(result[:, :, 0], 0.1, atol=0.01)
        np.testing.assert_allclose(result[:, :, 1], 0.0, atol=0.01)
        np.testing.assert_allclose(result[:, :, 2], 0.0, atol=0.01)

    def test_result_clamped_to_0_1(self, tmp_path):
        """Output values must always be in [0, 1]."""
        N = 2
        lines = ["LUT_3D_SIZE 2"]
        for _ in range(N ** 3):
            lines.append("2.0 -0.5 1.5")  # out-of-range values
        p = _write_cube("\n".join(lines) + "\n", tmp_path)
        lut = _parse_cube_lut(p)
        frame = _solid_frame(4, 4, r=0.5, g=0.5, b=0.5)
        result = _apply_cube_lut(frame, lut)
        assert result.min() >= 0.0
        assert result[:, :, :3].max() <= 1.0

    def test_apply_color_grade_missing_file_returns_frame(self, tmp_path):
        """_apply_color_grade with a missing file warns and returns frame unchanged."""
        frame = _solid_frame(4, 4, r=0.2, g=0.4, b=0.6)
        missing = tmp_path / "nonexistent.cube"
        import warnings
        with warnings.catch_warnings(record=True):
            result = _apply_color_grade(frame, missing)
        np.testing.assert_array_equal(result, frame)


# ---------------------------------------------------------------------------
# 3. Fisheye precomputed pixel map
# ---------------------------------------------------------------------------

class TestFisheyeMap:
    def test_compute_fisheye_map_shape(self):
        H, W, k1, k2 = 32, 48, 0.18, 0.06
        mx, my = _compute_fisheye_map(H, W, k1, k2)
        assert mx.shape == (H, W)
        assert my.shape == (H, W)
        assert mx.dtype == np.float32
        assert my.dtype == np.float32

    def test_fisheye_map_cached(self):
        """Same (H, W, k1, k2) returns the same objects from the cache."""
        H, W, k1, k2 = 16, 24, 0.10, 0.02
        _FISHEYE_MAP_CACHE.pop((H, W, k1, k2), None)  # ensure cold start
        mx1, my1 = _compute_fisheye_map(H, W, k1, k2)
        mx2, my2 = _compute_fisheye_map(H, W, k1, k2)
        assert mx1 is mx2
        assert my1 is my2

    def test_fisheye_different_params_different_maps(self):
        H, W = 16, 24
        _FISHEYE_MAP_CACHE.pop((H, W, 0.10, 0.00), None)
        _FISHEYE_MAP_CACHE.pop((H, W, 0.20, 0.00), None)
        mx1, my1 = _compute_fisheye_map(H, W, 0.10, 0.00)
        mx2, my2 = _compute_fisheye_map(H, W, 0.20, 0.00)
        assert not np.array_equal(mx1, mx2)

    def test_zero_distortion_is_identity(self):
        """k1=k2=0 → pixel map is identity (src_x == col, src_y == row)."""
        H, W = 8, 12
        mx, my = _compute_fisheye_map(H, W, 0.0, 0.0)
        xs = np.tile(np.arange(W, dtype=np.float32), (H, 1))
        ys = np.tile(np.arange(H, dtype=np.float32)[:, None], (1, W))
        np.testing.assert_allclose(mx, xs, atol=1e-5)
        np.testing.assert_allclose(my, ys, atol=1e-5)

    def test_two_frames_same_params_byte_identical(self):
        """
        The key acceptance criterion: two applications of fisheye with the
        same (H, W, k1, k2) on the same frame produce byte-identical arrays.
        """
        H, W, k1, k2 = 32, 48, 0.18, 0.06
        rng = np.random.default_rng(7)
        frame = rng.random((H, W, 4), dtype=np.float64).astype(np.float32)
        # Compute map once
        mx, my = _compute_fisheye_map(H, W, k1, k2)
        out1 = _apply_fisheye(frame, k1, k2, map_x=mx, map_y=my)
        out2 = _apply_fisheye(frame, k1, k2, map_x=mx, map_y=my)
        # Byte-identical: same float32 bits
        assert np.array_equal(out1, out2)
        # Also verify via public interface (uses cache)
        out3 = _apply_fisheye(frame, k1, k2)
        out4 = _apply_fisheye(frame, k1, k2)
        assert np.array_equal(out3, out4)

    def test_fisheye_uses_lanczos4(self):
        """Verify cv2.remap is called with INTER_LANCZOS4 (not INTER_LINEAR)."""
        import cv2
        H, W, k1, k2 = 8, 12, 0.1, 0.0
        frame = _solid_frame(H, W)
        mx, my = _compute_fisheye_map(H, W, k1, k2)
        with patch("parallax_engine.render.cv2.remap") as mock_remap:
            mock_remap.return_value = np.zeros((H, W), dtype=np.float32)
            _apply_fisheye(frame, k1, k2, map_x=mx, map_y=my)
            for call_args in mock_remap.call_args_list:
                assert call_args.kwargs.get("interpolation") == cv2.INTER_LANCZOS4 or \
                       call_args.args[3] == cv2.INTER_LANCZOS4


# ---------------------------------------------------------------------------
# 4. Grain — seeded per §9.3
# ---------------------------------------------------------------------------

class TestGrain:
    def test_same_seed_same_frame_identical_grain(self):
        """Same RNG state → identical grain pattern."""
        H, W = 32, 48
        frame = _solid_frame(H, W, r=0.5, g=0.5, b=0.5)
        seed = 12345
        grain_ss = spawn_channel(seed, GRAIN)
        children = grain_ss.spawn(10)

        rng1 = np.random.default_rng(children[3])
        out1 = _apply_grain(frame.copy(), sigma=4.0, rng=rng1)

        rng2 = np.random.default_rng(children[3])
        out2 = _apply_grain(frame.copy(), sigma=4.0, rng=rng2)

        np.testing.assert_array_equal(out1, out2)

    def test_different_frame_index_different_grain(self):
        """Different frame indices → different grain patterns (overwhelmingly likely)."""
        H, W = 32, 48
        frame = _solid_frame(H, W, r=0.5, g=0.5, b=0.5)
        seed = 42
        grain_ss = spawn_channel(seed, GRAIN)
        children = grain_ss.spawn(10)

        rng1 = np.random.default_rng(children[0])
        out1 = _apply_grain(frame.copy(), sigma=4.0, rng=rng1)

        rng2 = np.random.default_rng(children[1])
        out2 = _apply_grain(frame.copy(), sigma=4.0, rng=rng2)

        assert not np.array_equal(out1, out2)

    def test_grain_only_affects_rgb_not_alpha(self):
        """Grain must not change the alpha channel."""
        H, W = 16, 16
        frame = _solid_frame(H, W, r=0.5, g=0.5, b=0.5, a=0.75)
        rng = np.random.default_rng(99)
        result = _apply_grain(frame.copy(), sigma=4.0, rng=rng)
        np.testing.assert_array_equal(result[:, :, 3], frame[:, :, 3])

    def test_grain_output_clamped_0_1(self):
        """After grain, all pixel values must be in [0, 1]."""
        H, W = 16, 16
        # Edge frames near 0 and 1
        frame_dark = _solid_frame(H, W, r=0.01, g=0.01, b=0.01)
        frame_bright = _solid_frame(H, W, r=0.99, g=0.99, b=0.99)
        for frame in [frame_dark, frame_bright]:
            rng = np.random.default_rng(0)
            result = _apply_grain(frame, sigma=10.0, rng=rng)
            assert result.min() >= 0.0
            assert result.max() <= 1.0

    def test_zero_sigma_grain_no_change(self):
        """sigma=0 (or very small): grain should not significantly change pixels."""
        H, W = 16, 16
        frame = _solid_frame(H, W, r=0.5, g=0.5, b=0.5)
        rng = np.random.default_rng(0)
        result = _apply_grain(frame.copy(), sigma=0.0, rng=rng)
        np.testing.assert_allclose(result, frame, atol=1e-6)


# ---------------------------------------------------------------------------
# 5. Light leaks — screen blend formula, opacity semantics
# ---------------------------------------------------------------------------

class TestLightLeaks:
    def _make_sprite_png(self, tmp_path: Path, H: int, W: int,
                         r: int, g: int, b: int, a: int = 255) -> Path:
        """Write a solid-color RGBA PNG for use as a light-leak sprite."""
        from PIL import Image
        arr = np.full((H, W, 4), [r, g, b, a], dtype=np.uint8)
        img = Image.fromarray(arr, mode="RGBA")
        p = tmp_path / "leak.png"
        img.save(str(p))
        return p

    def test_opacity_zero_leaves_frame_unchanged(self, tmp_path):
        """opacity=0 → screen blend contribution vanishes; output = dst."""
        H, W = 16, 16
        sprite = self._make_sprite_png(tmp_path, H, W, 255, 128, 0, 255)
        frame = _solid_frame(H, W, r=0.4, g=0.6, b=0.8)
        result = _apply_light_leaks(frame.copy(), sprite, opacity=0.0, blend_mode="screen")
        # Screen blend: out = 1 - (1-dst)*(1-src*0) = 1 - (1-dst)*1 = dst
        np.testing.assert_allclose(result[:, :, :3], frame[:, :, :3], atol=1e-5)

    def test_opacity_one_full_screen_blend(self, tmp_path):
        """opacity=1 with a fully-opaque white sprite → output=1 (screen to white)."""
        H, W = 8, 8
        sprite = self._make_sprite_png(tmp_path, H, W, 255, 255, 255, 255)
        frame = _solid_frame(H, W, r=0.5, g=0.5, b=0.5)
        result = _apply_light_leaks(frame.copy(), sprite, opacity=1.0, blend_mode="screen")
        # Screen: out = 1 - (1-0.5)*(1-1.0) = 1 - 0.5*0 = 1.0
        np.testing.assert_allclose(result[:, :, :3], 1.0, atol=1e-4)

    def test_screen_blend_formula(self, tmp_path):
        """
        Verify formula: out = 1 - (1 - dst) * (1 - src * opacity).
        Uses a known src color, known opacity, known dst color.
        """
        H, W = 4, 4
        dst_val = 0.4
        src_val = 0.6
        opacity = 0.5
        expected = 1.0 - (1.0 - dst_val) * (1.0 - src_val * opacity)

        sprite = self._make_sprite_png(
            tmp_path, H, W,
            int(src_val * 255), int(src_val * 255), int(src_val * 255), 255
        )
        frame = _solid_frame(H, W, r=dst_val, g=dst_val, b=dst_val)
        result = _apply_light_leaks(frame, sprite, opacity=opacity, blend_mode="screen")
        np.testing.assert_allclose(result[:, :, :3], expected, atol=0.01)

    def test_missing_sprite_returns_frame_unchanged(self, tmp_path):
        """A missing sprite file is a no-op (graceful degradation)."""
        frame = _solid_frame(8, 8, r=0.3, g=0.6, b=0.9)
        missing = tmp_path / "no_such_file.png"
        result = _apply_light_leaks(frame.copy(), missing, opacity=0.8, blend_mode="screen")
        np.testing.assert_array_equal(result, frame)

    def test_alpha_preserved_after_light_leaks(self, tmp_path):
        """Light leaks must not alter the alpha channel of the output frame."""
        H, W = 8, 8
        sprite = self._make_sprite_png(tmp_path, H, W, 200, 100, 50, 200)
        frame = _solid_frame(H, W, r=0.3, g=0.3, b=0.3, a=1.0)
        result = _apply_light_leaks(frame.copy(), sprite, opacity=0.5, blend_mode="screen")
        np.testing.assert_array_equal(result[:, :, 3], frame[:, :, 3])


# ---------------------------------------------------------------------------
# 6. Vignette — smoothstep formula
# ---------------------------------------------------------------------------

class TestVignette:
    def test_center_pixel_unchanged(self):
        """
        The centre pixel of the frame should be minimally affected by vignette
        (it's at dist≈0, well inside the radius).
        """
        H, W = 64, 64
        frame = _solid_frame(H, W, r=0.8, g=0.8, b=0.8)
        result = _apply_vignette(frame.copy(), strength=0.9, radius=0.5)
        cy, cx = H // 2, W // 2
        # Centre brightness should be close to original (low vignette at centre)
        assert result[cy, cx, 0] > 0.7

    def test_corner_darkened(self):
        """Corners are beyond the vignette radius and should be darkened."""
        H, W = 64, 64
        frame = _solid_frame(H, W, r=1.0, g=1.0, b=1.0)
        result = _apply_vignette(frame.copy(), strength=1.0, radius=0.3)
        # Top-left corner should be significantly darker
        assert result[0, 0, 0] < 0.5

    def test_zero_strength_no_change(self):
        """strength=0 → vignette is identity."""
        H, W = 16, 16
        frame = _solid_frame(H, W, r=0.7, g=0.5, b=0.3)
        result = _apply_vignette(frame.copy(), strength=0.0, radius=0.85)
        np.testing.assert_allclose(result, frame, atol=1e-6)

    def test_alpha_unchanged(self):
        """Vignette must never touch the alpha channel."""
        H, W = 16, 16
        frame = _solid_frame(H, W, r=0.5, g=0.5, b=0.5, a=0.7)
        result = _apply_vignette(frame.copy(), strength=0.9, radius=0.5)
        np.testing.assert_array_equal(result[:, :, 3], frame[:, :, 3])

    def test_output_clamped(self):
        """Values must stay in [0, 1] after vignette with valid strength ≤ 1."""
        H, W = 32, 32
        frame = _solid_frame(H, W, r=1.0, g=1.0, b=1.0)
        # strength=1.0 is the maximum valid value; mask = 1 - 1*smooth ∈ [0,1]
        result = _apply_vignette(frame.copy(), strength=1.0, radius=0.1)
        assert result.min() >= 0.0
        assert result.max() <= 1.0


# ---------------------------------------------------------------------------
# 7. Pinned execution order: vignette → grain → light_leaks → fisheye → color_grade
# ---------------------------------------------------------------------------

class TestPinnedOrder:
    """
    Verify that _apply_global_post calls effects in the required order.

    We build a mock scene and use patch to intercept each _apply_* call,
    recording call order via a shared list.
    """

    def _make_scene_with_all_effects(self, tmp_path: Path):
        """Return a Scene instance with all five global post-effects configured."""
        from parallax_engine.scene import Scene
        import yaml

        # Create a minimal light-leak sprite so _apply_light_leaks doesn't no-op
        sprite_path = tmp_path / "leak.png"
        from PIL import Image
        img = Image.new("RGBA", (4, 4), color=(200, 100, 50, 255))
        img.save(str(sprite_path))

        scene_yaml = f"""
version: 1
meta:
  seed: 1234
  resolution: [32, 32]
  fps: 24
  duration_s: 1.0
  bg_color: "#000000"
  perspective_px: 800.0
  origin: [16, 16]
camera:
  mode: drone
  drone:
    path:
      kind: bezier
      controls: [[0,0,0], [0,0,-500], [0,0,-1000]]
      duration_s: 1.0
    poi_lookahead_s: 0.55
    spring_halflife_s: 0.18
    noise: {{z_amp: 0, xy_amp: 0, hz: 0.5}}
    bank_from_velocity: 0.0
stacks:
  main:
    layers: []
post:
  global:
    vignette:
      strength: 0.5
      radius: 0.85
    grain:
      sigma: 4.0
    light_leaks:
      sprite: "leak.png"
      opacity: 0.3
      blend: screen
    fisheye:
      k1: 0.18
      k2: 0.06
    color_grade:
      lut: "test.cube"
"""
        scene = Scene.model_validate(yaml.safe_load(scene_yaml))
        return scene

    def test_pinned_order_vignette_first_color_grade_last(self, tmp_path):
        """Effects run in pinned order regardless of scene field declaration order."""
        scene = self._make_scene_with_all_effects(tmp_path)

        seed = 1234
        frame = _solid_frame(32, 32, r=0.5, g=0.5, b=0.5)

        # Pre-spawn grain children (as render_scene does)
        grain_ss = spawn_channel(seed, GRAIN)
        grain_children = grain_ss.spawn(1)

        # Identity LUT
        lut_path = tmp_path / "test.cube"
        lut_path.write_text(_make_identity_cube(4))
        lut = _parse_cube_lut(lut_path)

        call_order: list[str] = []

        original_vignette = _apply_vignette
        original_grain = _apply_grain
        original_leaks = _apply_light_leaks
        original_fisheye = _apply_fisheye
        original_lut = _apply_cube_lut

        def wrap_vignette(f, *a, **kw):
            call_order.append("vignette")
            return original_vignette(f, *a, **kw)

        def wrap_grain(f, *a, **kw):
            call_order.append("grain")
            return original_grain(f, *a, **kw)

        def wrap_leaks(f, *a, **kw):
            call_order.append("light_leaks")
            return original_leaks(f, *a, **kw)

        def wrap_fisheye(f, *a, **kw):
            call_order.append("fisheye")
            return original_fisheye(f, *a, **kw)

        def wrap_lut(f, *a, **kw):
            call_order.append("color_grade")
            return original_lut(f, *a, **kw)

        import parallax_engine.render as rmod
        with (
            patch.object(rmod, "_apply_vignette", side_effect=wrap_vignette),
            patch.object(rmod, "_apply_grain", side_effect=wrap_grain),
            patch.object(rmod, "_apply_light_leaks", side_effect=wrap_leaks),
            patch.object(rmod, "_apply_fisheye", side_effect=wrap_fisheye),
            patch.object(rmod, "_apply_cube_lut", side_effect=wrap_lut),
        ):
            _apply_global_post(
                frame, scene, seed, 0, tmp_path, grain_children,
                fisheye_map=_compute_fisheye_map(32, 32, 0.18, 0.06),
                cached_lut=lut,
            )

        assert call_order == ["vignette", "grain", "light_leaks", "fisheye", "color_grade"], \
            f"Unexpected order: {call_order}"
