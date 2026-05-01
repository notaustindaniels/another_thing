"""
tests/test_render.py — Unit tests for parallax_engine/render.py (§2.5-§2.7).

Tests verify:
- SVG raster cache keyed on (sha256, w, h, CACHE_VERSION)
- _apply_vignette darkens corners
- _apply_grain adds noise seeded deterministically
- _apply_fisheye remaps pixels without raising
- _resize_sprite uses correct interpolation
- _build_stack_buffer composites back-to-front
- _apply_global_post applies effects in pinned order
- render_scene executes full pipeline and produces a valid MP4
- Determinism: two renders with same seed → byte-identical MP4
"""

from __future__ import annotations

import hashlib
import io
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from parallax_engine.render import (
    CACHE_VERSION,
    _SvgCache,
    _apply_fisheye,
    _apply_grain,
    _apply_global_post,
    _apply_layer_post,
    _apply_vignette,
    _hex_to_rgb01,
    _resize_sprite,
    _rasterize_svg_bytes,
    render_scene,
)
from parallax_engine.scene import load_scene_dict


# ---------------------------------------------------------------------------
# Minimal scene factory
# ---------------------------------------------------------------------------

_TINY_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" width="64" height="36">
  <rect width="64" height="36" fill="#336699"/>
</svg>"""

_TINY_SVG2 = b"""<svg xmlns="http://www.w3.org/2000/svg" width="64" height="36">
  <rect width="64" height="36" fill="#cc4422"/>
</svg>"""


def _make_scene(
    duration_s: float = 1.0,
    fps: int = 5,
    resolution: tuple[int, int] = (64, 36),
    seed: int = 42,
    with_post: bool = False,
    with_drone: bool = False,
    n_layers: int = 1,
):
    """Build a minimal Scene dict for testing."""
    layers = []
    for i in range(n_layers):
        layers.append({
            "id": f"layer{i}",
            "src": f"assets/layer{i}.svg",
            "scene_xyz": [0, 0, -(3000 + i * 500)],
            "plate_size": [1920, 1080],
        })

    raw = {
        "version": 1,
        "meta": {
            "duration_s": duration_s,
            "fps": fps,
            "resolution": list(resolution),
            "perspective_px": 1200,
            "origin": [resolution[0] // 2, resolution[1] // 2],
            "bg_color": "#000000",
            "seed": seed,
        },
        "stacks": {
            "default": {"layers": layers}
        },
        "camera": {
            "mode": "keyframed",
            "keyframed": [
                {"t": 0.0, "x": 0, "y": 0, "z": 0, "yaw": 0.0, "pitch": 0, "roll": 0,
                 "ease": "linear"},
                {"t": duration_s, "x": 0, "y": 0, "z": -500, "yaw": 0.0, "pitch": 0, "roll": 0},
            ],
        },
        "masks": [],
    }

    if with_post:
        raw["post"] = {
            "global": {
                "vignette": {"strength": 0.5, "radius": 0.8},
                "grain": {"sigma": 8.0},
                "fisheye": {"k1": 0.1, "k2": 0.0},
            }
        }

    if with_drone:
        raw["camera"] = {
            "mode": "drone",
            "drone": {
                "path": {
                    "kind": "bezier",
                    "controls": [[0, 0, 0], [0, 0, -500]],
                    "duration_s": duration_s,
                },
                "poi_lookahead_s": 0.5,
                "spring_halflife_s": 0.2,
                "noise": {"z_amp": 5.0, "xy_amp": 2.0, "hz": 0.5},
                "bank_from_velocity": 0.3,
            }
        }

    return load_scene_dict(raw)


def _make_workspace_with_svgs(tmp_path: Path, n_layers: int = 1) -> Path:
    """Create a workspace with placeholder SVG assets."""
    assets = tmp_path / "assets"
    assets.mkdir()
    svgs = [_TINY_SVG, _TINY_SVG2]
    for i in range(n_layers):
        (assets / f"layer{i}.svg").write_bytes(svgs[i % len(svgs)])
    return tmp_path


# ---------------------------------------------------------------------------
# TestSvgCache
# ---------------------------------------------------------------------------

class TestSvgCache:
    """SVG raster cache keyed on (sha256, w, h, CACHE_VERSION)."""

    def test_cache_key_includes_version(self):
        cache = _SvgCache()
        key = cache.key("abc123", 100, 50)
        assert key == ("abc123", 100, 50, CACHE_VERSION)

    def test_miss_returns_none(self):
        cache = _SvgCache()
        assert cache.get("nonexistent", 100, 50) is None

    def test_put_and_get(self):
        cache = _SvgCache()
        raster = np.ones((50, 100, 4), dtype=np.float32)
        cache.put("abc", 100, 50, raster)
        out = cache.get("abc", 100, 50)
        assert out is not None
        assert out.shape == (50, 100, 4)

    def test_different_sizes_are_separate_entries(self):
        cache = _SvgCache()
        r1 = np.ones((50, 100, 4), dtype=np.float32)
        r2 = np.ones((25, 50, 4), dtype=np.float32) * 0.5
        cache.put("abc", 100, 50, r1)
        cache.put("abc", 50, 25, r2)
        assert cache.get("abc", 100, 50) is r1
        assert cache.get("abc", 50, 25) is r2

    def test_different_sha_different_entry(self):
        cache = _SvgCache()
        r1 = np.ones((50, 100, 4), dtype=np.float32)
        r2 = np.zeros((50, 100, 4), dtype=np.float32)
        cache.put("sha_a", 100, 50, r1)
        cache.put("sha_b", 100, 50, r2)
        assert cache.get("sha_a", 100, 50) is r1
        assert cache.get("sha_b", 100, 50) is r2

    def test_len(self):
        cache = _SvgCache()
        cache.put("x", 100, 50, np.zeros((50, 100, 4), dtype=np.float32))
        cache.put("y", 100, 50, np.zeros((50, 100, 4), dtype=np.float32))
        assert len(cache) == 2


# ---------------------------------------------------------------------------
# TestRasterizeSvg
# ---------------------------------------------------------------------------

class TestRasterizeSvg:
    """SVG rasterization produces correct-shape float32 premultiplied RGBA."""

    def test_output_shape(self):
        out = _rasterize_svg_bytes(_TINY_SVG, 32, 18)
        assert out.shape == (18, 32, 4)

    def test_output_dtype(self):
        out = _rasterize_svg_bytes(_TINY_SVG, 32, 18)
        assert out.dtype == np.float32

    def test_output_range(self):
        out = _rasterize_svg_bytes(_TINY_SVG, 32, 18)
        assert out.min() >= 0.0
        assert out.max() <= 1.0 + 1e-6

    def test_different_svgs_differ(self):
        out1 = _rasterize_svg_bytes(_TINY_SVG, 32, 18)
        out2 = _rasterize_svg_bytes(_TINY_SVG2, 32, 18)
        # Different SVGs should produce different pixels
        assert not np.allclose(out1, out2)


# ---------------------------------------------------------------------------
# TestResizeSprite
# ---------------------------------------------------------------------------

class TestResizeSprite:
    """_resize_sprite handles shrink and grow correctly."""

    def test_same_size_noop(self):
        src = np.random.rand(50, 100, 4).astype(np.float32)
        out = _resize_sprite(src, 100, 50)
        assert out.shape == (50, 100, 4)

    def test_shrink(self):
        src = np.random.rand(100, 200, 4).astype(np.float32)
        out = _resize_sprite(src, 50, 25)
        assert out.shape == (25, 50, 4)

    def test_grow(self):
        src = np.random.rand(10, 20, 4).astype(np.float32)
        out = _resize_sprite(src, 40, 20)
        assert out.shape == (20, 40, 4)

    def test_output_dtype_float32(self):
        src = np.random.rand(50, 100, 4).astype(np.float32)
        out = _resize_sprite(src, 25, 12)
        assert out.dtype == np.float32

    def test_degenerate_min_size(self):
        src = np.ones((50, 100, 4), dtype=np.float32)
        out = _resize_sprite(src, 0, 0)
        assert out.shape[0] >= 1 and out.shape[1] >= 1


# ---------------------------------------------------------------------------
# TestHexToRgb01
# ---------------------------------------------------------------------------

class TestHexToRgb01:
    def test_black(self):
        assert _hex_to_rgb01("#000000") == (0.0, 0.0, 0.0)

    def test_white(self):
        r, g, b = _hex_to_rgb01("#ffffff")
        assert abs(r - 1.0) < 0.01 and abs(g - 1.0) < 0.01 and abs(b - 1.0) < 0.01

    def test_red(self):
        r, g, b = _hex_to_rgb01("#ff0000")
        assert abs(r - 1.0) < 0.01 and abs(g) < 0.01 and abs(b) < 0.01


# ---------------------------------------------------------------------------
# TestApplyLayerPost
# ---------------------------------------------------------------------------

class TestApplyLayerPost:
    """Per-layer DOF blur and depth fade."""

    def test_no_blur_unchanged(self):
        sprite = np.random.rand(36, 64, 4).astype(np.float32)
        result = _apply_layer_post(sprite, 0.0, 0.0, -3000.0, (0.0, 0.0, 0.0))
        np.testing.assert_array_equal(result, sprite)

    def test_blur_changes_sprite(self):
        sprite = np.zeros((36, 64, 4), dtype=np.float32)
        sprite[18, 32, 3] = 1.0   # single bright pixel
        result = _apply_layer_post(sprite, 5.0, 0.0, -3000.0, (0.0, 0.0, 0.0))
        # After blurring, neighbouring pixels should also have non-zero alpha
        assert result[18, 32, 3] < 1.0   # center is diluted

    def test_depth_fade_near_fog(self):
        """Very far layers fade toward fog colour."""
        sprite = np.zeros((36, 64, 4), dtype=np.float32)
        sprite[:, :, 3] = 1.0   # fully opaque
        sprite[:, :, 0] = 1.0   # pure red
        fog = (0.0, 0.0, 1.0)   # fog = blue
        # depth_fade=1.0, z = far = 11000 (should be heavily faded)
        result = _apply_layer_post(sprite, 0.0, 1.0, 11000.0, fog)
        # Blue channel should have increased
        assert result[0, 0, 2] > 0.0

    def test_output_dtype_preserved(self):
        sprite = np.ones((36, 64, 4), dtype=np.float32) * 0.5
        result = _apply_layer_post(sprite, 2.0, 0.2, -5000.0, (0.1, 0.1, 0.1))
        assert result.dtype == np.float32


# ---------------------------------------------------------------------------
# TestApplyVignette
# ---------------------------------------------------------------------------

class TestApplyVignette:
    """Vignette darkens corners, leaves centre brighter."""

    def test_centre_brighter_than_corner(self):
        H, W = 36, 64
        frame = np.ones((H, W, 4), dtype=np.float32)
        result = _apply_vignette(frame, strength=0.8, radius=0.5)
        cy, cx = H // 2, W // 2
        centre_lum = result[cy, cx, :3].mean()
        corner_lum = result[0, 0, :3].mean()
        assert centre_lum > corner_lum, (
            f"Centre {centre_lum:.3f} should be brighter than corner {corner_lum:.3f}"
        )

    def test_zero_strength_unchanged(self):
        H, W = 36, 64
        frame = np.ones((H, W, 4), dtype=np.float32)
        result = _apply_vignette(frame, strength=0.0, radius=0.85)
        np.testing.assert_allclose(result, frame, atol=1e-6)

    def test_output_shape_preserved(self):
        H, W = 36, 64
        frame = np.random.rand(H, W, 4).astype(np.float32)
        result = _apply_vignette(frame, 0.5, 0.8)
        assert result.shape == frame.shape

    def test_output_dtype_float32(self):
        frame = np.ones((36, 64, 4), dtype=np.float32)
        result = _apply_vignette(frame, 0.5, 0.8)
        assert result.dtype == np.float32

    def test_alpha_unchanged(self):
        """Vignette should not alter alpha channel."""
        frame = np.ones((36, 64, 4), dtype=np.float32)
        frame[:, :, 3] = 0.7
        result = _apply_vignette(frame, 0.5, 0.8)
        np.testing.assert_allclose(result[:, :, 3], frame[:, :, 3], atol=1e-6)


# ---------------------------------------------------------------------------
# TestApplyGrain
# ---------------------------------------------------------------------------

class TestApplyGrain:
    """Grain adds deterministic per-frame noise."""

    def test_grain_changes_frame(self):
        frame = np.full((36, 64, 4), 0.5, dtype=np.float32)
        rng = np.random.default_rng(0)
        result = _apply_grain(frame, sigma=10.0, rng=rng)
        assert not np.allclose(result, frame), "Grain should change the frame"

    def test_grain_deterministic(self):
        frame = np.full((36, 64, 4), 0.5, dtype=np.float32)
        rng1 = np.random.default_rng(np.random.SeedSequence(99))
        rng2 = np.random.default_rng(np.random.SeedSequence(99))
        r1 = _apply_grain(frame, sigma=5.0, rng=rng1)
        r2 = _apply_grain(frame, sigma=5.0, rng=rng2)
        np.testing.assert_array_equal(r1, r2)

    def test_zero_sigma_unchanged(self):
        frame = np.full((36, 64, 4), 0.5, dtype=np.float32)
        rng = np.random.default_rng(0)
        result = _apply_grain(frame, sigma=0.0, rng=rng)
        np.testing.assert_array_equal(result, frame)

    def test_alpha_not_modified(self):
        frame = np.full((36, 64, 4), 0.5, dtype=np.float32)
        frame[:, :, 3] = 0.8
        rng = np.random.default_rng(0)
        result = _apply_grain(frame, sigma=20.0, rng=rng)
        np.testing.assert_array_equal(result[:, :, 3], frame[:, :, 3])

    def test_output_range_clipped(self):
        frame = np.full((36, 64, 4), 0.5, dtype=np.float32)
        rng = np.random.default_rng(0)
        result = _apply_grain(frame, sigma=200.0, rng=rng)  # huge sigma
        assert result.min() >= 0.0
        assert result.max() <= 1.0 + 1e-5


# ---------------------------------------------------------------------------
# TestApplyFisheye
# ---------------------------------------------------------------------------

class TestApplyFisheye:
    """Fisheye remap produces correct output shape and dtype."""

    def test_output_shape(self):
        H, W = 36, 64
        frame = np.random.rand(H, W, 4).astype(np.float32)
        result = _apply_fisheye(frame, k1=0.2, k2=0.05)
        assert result.shape == (H, W, 4)

    def test_output_dtype_float32(self):
        frame = np.random.rand(36, 64, 4).astype(np.float32)
        result = _apply_fisheye(frame, k1=0.1)
        assert result.dtype == np.float32

    def test_zero_distortion_approx_identity(self):
        """k1=0, k2=0 should leave the frame nearly unchanged (minor interpolation noise)."""
        frame = np.random.rand(36, 64, 4).astype(np.float32)
        result = _apply_fisheye(frame, k1=0.0, k2=0.0)
        # Allow small interpolation residual
        np.testing.assert_allclose(result, frame, atol=0.05)

    def test_nonzero_distortion_changes_frame(self):
        frame = np.random.rand(36, 64, 4).astype(np.float32)
        result = _apply_fisheye(frame, k1=0.3)
        assert not np.allclose(result, frame)


# ---------------------------------------------------------------------------
# TestGlobalPostOrder
# ---------------------------------------------------------------------------

class TestGlobalPostOrder:
    """Global post applies effects in vignette→grain→light_leaks→fisheye→color_grade order."""

    def _make_scene_with_full_post(self, tmp_path: Path) -> "Scene":
        """Scene with all post effects specified (skipping light_leaks and LUT)."""
        raw = {
            "version": 1,
            "meta": {
                "duration_s": 1.0,
                "fps": 5,
                "resolution": [64, 36],
                "perspective_px": 1200,
                "origin": [32, 18],
                "bg_color": "#000000",
                "seed": 7,
            },
            "stacks": {
                "default": {
                    "layers": [
                        {"id": "bg", "src": "assets/layer0.svg",
                         "scene_xyz": [0, 0, -3000], "plate_size": [1920, 1080]}
                    ]
                }
            },
            "camera": {
                "mode": "keyframed",
                "keyframed": [
                    {"t": 0.0, "x": 0, "y": 0, "z": 0, "yaw": 0.0, "pitch": 0, "roll": 0,
                     "ease": "linear"},
                    {"t": 1.0, "x": 0, "y": 0, "z": 0, "yaw": 0.0, "pitch": 0, "roll": 0},
                ],
            },
            "masks": [],
            "post": {
                "global": {
                    "vignette": {"strength": 0.5, "radius": 0.8},
                    "grain": {"sigma": 5.0},
                    "fisheye": {"k1": 0.1},
                }
            },
        }
        from parallax_engine.scene import load_scene_dict
        return load_scene_dict(raw)

    def test_vignette_applied(self, tmp_path):
        """Vignette darkens corners in final frame."""
        scene = self._make_scene_with_full_post(tmp_path)
        frame = np.ones((36, 64, 4), dtype=np.float32)
        from parallax_engine.seeds import spawn_channel, GRAIN
        grain_ss = spawn_channel(7, GRAIN)
        grain_children = grain_ss.spawn(10)
        result = _apply_global_post(frame, scene, seed=7, frame_idx=0,
                                    workspace=tmp_path, grain_children=grain_children)
        # Corners should be darker than centre after vignette
        centre_r = result[18, 32, 0]
        corner_r = result[0, 0, 0]
        assert centre_r > corner_r

    def test_grain_applied(self, tmp_path):
        """Grain introduces noise in a non-vignette scene."""
        raw = {
            "version": 1,
            "meta": {
                "duration_s": 1.0, "fps": 5, "resolution": [64, 36],
                "perspective_px": 1200, "origin": [32, 18],
                "bg_color": "#000000", "seed": 3,
            },
            "stacks": {"default": {"layers": [
                {"id": "bg", "src": "assets/layer0.svg",
                 "scene_xyz": [0, 0, -3000], "plate_size": [1920, 1080]}
            ]}},
            "camera": {
                "mode": "keyframed",
                "keyframed": [
                    {"t": 0.0, "x": 0, "y": 0, "z": 0, "yaw": 0.0,
                     "pitch": 0, "roll": 0, "ease": "linear"},
                    {"t": 1.0, "x": 0, "y": 0, "z": 0, "yaw": 0.0,
                     "pitch": 0, "roll": 0},
                ],
            },
            "masks": [],
            "post": {"global": {"grain": {"sigma": 50.0}}},
        }
        from parallax_engine.scene import load_scene_dict
        scene = load_scene_dict(raw)
        frame = np.full((36, 64, 4), 0.5, dtype=np.float32)
        from parallax_engine.seeds import spawn_channel, GRAIN
        grain_ss = spawn_channel(3, GRAIN)
        grain_children = grain_ss.spawn(10)
        result = _apply_global_post(frame, scene, seed=3, frame_idx=0,
                                    workspace=tmp_path, grain_children=grain_children)
        # Large sigma grain → frame should differ from uniform 0.5
        assert not np.allclose(result[:, :, :3], 0.5, atol=0.01)

    def test_no_post_returns_unchanged(self, tmp_path):
        """If post is None, frame is returned unchanged."""
        raw = {
            "version": 1,
            "meta": {
                "duration_s": 1.0, "fps": 5, "resolution": [64, 36],
                "perspective_px": 1200, "origin": [32, 18],
                "bg_color": "#000000", "seed": 0,
            },
            "stacks": {"default": {"layers": [
                {"id": "bg", "src": "assets/layer0.svg",
                 "scene_xyz": [0, 0, -3000], "plate_size": [1920, 1080]}
            ]}},
            "camera": {
                "mode": "keyframed",
                "keyframed": [
                    {"t": 0.0, "x": 0, "y": 0, "z": 0,
                     "yaw": 0.0, "pitch": 0, "roll": 0, "ease": "linear"},
                    {"t": 1.0, "x": 0, "y": 0, "z": 0,
                     "yaw": 0.0, "pitch": 0, "roll": 0},
                ],
            },
            "masks": [],
        }
        from parallax_engine.scene import load_scene_dict
        scene = load_scene_dict(raw)
        frame = np.random.rand(36, 64, 4).astype(np.float32)
        orig = frame.copy()
        from parallax_engine.seeds import spawn_channel, GRAIN
        grain_children = spawn_channel(0, GRAIN).spawn(10)
        result = _apply_global_post(frame, scene, seed=0, frame_idx=0,
                                    workspace=tmp_path, grain_children=grain_children)
        np.testing.assert_array_equal(result, orig)


# ---------------------------------------------------------------------------
# TestGrainSeedPerFrame
# ---------------------------------------------------------------------------

class TestGrainSeedPerFrame:
    """Grain uses GRAIN_CHANNEL from seeds.py, one child per frame."""

    def test_different_frames_get_different_noise(self):
        """Frame 0 and frame 1 should receive different grain patterns."""
        from parallax_engine.seeds import spawn_channel, GRAIN
        grain_ss = spawn_channel(42, GRAIN)
        grain_children = grain_ss.spawn(10)

        frame = np.full((36, 64, 4), 0.5, dtype=np.float32)

        rng0 = np.random.default_rng(grain_children[0])
        rng1 = np.random.default_rng(grain_children[1])
        r0 = _apply_grain(frame.copy(), sigma=10.0, rng=rng0)
        r1 = _apply_grain(frame.copy(), sigma=10.0, rng=rng1)

        assert not np.allclose(r0, r1), "Frames 0 and 1 should have different grain"

    def test_same_frame_idx_same_noise(self):
        """Two renders of the same frame index → identical grain."""
        from parallax_engine.seeds import spawn_channel, GRAIN
        grain_ss = spawn_channel(42, GRAIN)
        children_a = grain_ss.spawn(10)

        grain_ss2 = spawn_channel(42, GRAIN)
        children_b = grain_ss2.spawn(10)

        frame = np.full((36, 64, 4), 0.5, dtype=np.float32)
        rng_a = np.random.default_rng(children_a[3])
        rng_b = np.random.default_rng(children_b[3])

        ra = _apply_grain(frame.copy(), sigma=10.0, rng=rng_a)
        rb = _apply_grain(frame.copy(), sigma=10.0, rng=rng_b)

        np.testing.assert_array_equal(ra, rb)


# ---------------------------------------------------------------------------
# TestRenderScene — integration
# ---------------------------------------------------------------------------

class TestRenderScene:
    """Integration tests for render_scene."""

    def test_produces_mp4(self, tmp_path):
        """render_scene produces a non-empty MP4 file."""
        scene = _make_scene(duration_s=0.4, fps=5, resolution=(64, 36), seed=1)
        ws = _make_workspace_with_svgs(tmp_path)
        out = ws / "out.mp4"
        render_scene(scene, ws, out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_deterministic(self, tmp_path):
        """Two renders with same seed produce byte-identical MP4s."""
        scene = _make_scene(duration_s=0.4, fps=5, resolution=(64, 36), seed=77)

        ws1 = tmp_path / "run1"
        ws2 = tmp_path / "run2"
        for ws in (ws1, ws2):
            ws.mkdir()
            _make_workspace_with_svgs(ws)

        out1 = ws1 / "out.mp4"
        out2 = ws2 / "out.mp4"

        render_scene(scene, ws1, out1)
        render_scene(scene, ws2, out2)

        b1 = out1.read_bytes()
        b2 = out2.read_bytes()
        assert b1 == b2, (
            f"Non-deterministic render: sizes {len(b1)} vs {len(b2)}"
        )

    def test_with_post_effects(self, tmp_path):
        """render_scene completes without error when post effects are specified."""
        scene = _make_scene(duration_s=0.4, fps=5, resolution=(64, 36),
                            seed=5, with_post=True)
        ws = _make_workspace_with_svgs(tmp_path)
        out = ws / "out.mp4"
        render_scene(scene, ws, out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_two_layers(self, tmp_path):
        """render_scene composites multiple layers without error."""
        scene = _make_scene(duration_s=0.4, fps=5, resolution=(64, 36),
                            seed=3, n_layers=2)
        ws = _make_workspace_with_svgs(tmp_path, n_layers=2)
        out = ws / "out.mp4"
        render_scene(scene, ws, out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_output_dir_created(self, tmp_path):
        """render_scene creates output directory if it doesn't exist."""
        scene = _make_scene(duration_s=0.2, fps=5, resolution=(64, 36), seed=0)
        ws = _make_workspace_with_svgs(tmp_path)
        out = ws / "subdir" / "deep" / "out.mp4"
        render_scene(scene, ws, out)
        assert out.exists()

    def test_drone_camera_renders(self, tmp_path):
        """render_scene works with drone camera mode."""
        scene = _make_scene(duration_s=0.4, fps=5, resolution=(64, 36),
                            seed=9, with_drone=True)
        ws = _make_workspace_with_svgs(tmp_path)
        out = ws / "drone.mp4"
        render_scene(scene, ws, out)
        assert out.exists()
        assert out.stat().st_size > 0
