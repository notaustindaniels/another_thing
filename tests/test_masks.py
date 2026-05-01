"""
tests/test_masks.py — Unit tests for parallax_engine/masks.py (P1.M05).

Covers:
- alpha_over: premultiplied over operator
- build_mask_alpha: all five growth kinds, all three anchors
- Near-cull handoff: build_mask_alpha returns ones when anchor layer near-culls
- composite_with_mask: F = D*M + S*(1-M) compositing rule
- L2 in-front-of-mask rule: closer layers alpha-over'd unmasked
- All outputs are float32 in [0, 1]
- No loops over pixels
"""

import textwrap

import numpy as np
import pytest
import yaml

from parallax_engine.masks import alpha_over, build_mask_alpha, composite_with_mask
from parallax_engine.scene import load_scene_dict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

H, W = 108, 192  # small resolution for fast tests


def _cam(cx=0, cy=0, cz=0, yaw=0, pitch=0, roll=0):
    return {"cx": float(cx), "cy": float(cy), "cz": float(cz),
            "yaw": float(yaw), "pitch": float(pitch), "roll": float(roll)}


def _rgba(r, g, b, a, shape=(H, W)):
    buf = np.zeros((*shape, 4), dtype=np.float32)
    buf[:, :, 0] = r * a  # premultiplied
    buf[:, :, 1] = g * a
    buf[:, :, 2] = b * a
    buf[:, :, 3] = a
    return buf


# Simple scene with two stacks, one world-anchor mask
_SCENE_PORTAL = textwrap.dedent(f"""\
version: 1
meta:
  duration_s: 8
  fps: 30
  resolution: [{W}, {H}]
  perspective_px: 1200
  origin: [{W//2}, {H//2}]
  seed: 1
stacks:
  forest:
    layers:
      - id: sky
        src: assets/sky.svg
        scene_xyz: [0, 0, -10500]
        plate_size: [3840, 2160]
      - id: portal_tree
        src: assets/portal_tree.svg
        scene_xyz: [0, 0, -4500]
        plate_size: [3840, 2160]
        svg_paint_id: silhouette
      - id: leaves_fg
        src: assets/leaves_fg.svg
        scene_xyz: [0, 0, -500]
        plate_size: [3840, 2160]
  city:
    layers:
      - id: city_sky
        src: assets/city_sky.svg
        scene_xyz: [0, 0, -10500]
        plate_size: [3840, 2160]
camera:
  mode: drone
  drone:
    path:
      kind: bezier
      controls: [[0,0,0],[0,0,-4400]]
      duration_s: 8
    poi_lookahead_s: 0.5
    spring_halflife_s: 0.20
    noise: {{z_amp: 0, xy_amp: 0, hz: 1.0}}
    bank_from_velocity: 0.0
masks:
  - id: portal
    path_svg: assets/portal_tree.svg
    silhouette_id_in_svg: silhouette
    path_id_in_svg: hole
    attached_to_layer: forest.portal_tree
    anchor: world
    src_stack: forest
    dest_stack: city
    matte: alpha
    growth: {{kind: perspective}}
""")

# Scene with screen-anchored radius mask
_SCENE_RADIUS = textwrap.dedent(f"""\
version: 1
meta:
  duration_s: 4
  fps: 30
  resolution: [{W}, {H}]
  perspective_px: 1200
  origin: [{W//2}, {H//2}]
  seed: 5
stacks:
  src:
    layers:
      - {{id: bg, src: x.svg, scene_xyz: [0,0,-1000], plate_size: [1920, 1080]}}
  dst:
    layers:
      - {{id: bg, src: y.svg, scene_xyz: [0,0,-1000], plate_size: [1920, 1080]}}
camera:
  mode: keyframed
  keyframed:
    - {{t: 0, x: 0, y: 0, z: 0}}
    - {{t: 4, x: 0, y: 0, z: 0}}
masks:
  - id: iris
    src_stack: src
    dest_stack: dst
    anchor: screen
    growth: {{kind: radius, t0: 1.0, t1: 2.0, r0: 0, r1: 500, feather_px: 20}}
    matte: alpha
""")


def _raw(s):
    return yaml.safe_load(s)


# ===========================================================================
# TestAlphaOver
# ===========================================================================

class TestAlphaOver:
    def test_full_coverage(self):
        """Opaque src at (0,0) completely replaces dst."""
        dst = np.full((H, W, 4), 0.5, dtype=np.float32)
        src = _rgba(1.0, 0.0, 0.0, 1.0)
        alpha_over(dst, src, 0, 0)
        np.testing.assert_allclose(dst[:, :, 0], 1.0, atol=1e-6)
        np.testing.assert_allclose(dst[:, :, 3], 1.0, atol=1e-6)

    def test_transparent_src_no_change(self):
        """Fully transparent src leaves dst unchanged."""
        dst = _rgba(0.0, 1.0, 0.0, 1.0)
        dst_copy = dst.copy()
        src = _rgba(1.0, 0.0, 0.0, 0.0)
        alpha_over(dst, src, 0, 0)
        np.testing.assert_array_equal(dst, dst_copy)

    def test_partial_alpha(self):
        """Alpha=0.5 src blends with dst."""
        dst = _rgba(0.0, 0.0, 0.0, 1.0)  # opaque black
        src = np.zeros((H, W, 4), dtype=np.float32)
        src[:, :, 0] = 0.5   # pre-multiplied red with alpha=0.5
        src[:, :, 3] = 0.5
        alpha_over(dst, src, 0, 0)
        # Expected: src_rgb + dst_rgb * (1 - alpha) = 0.5 + 0 * 0.5 = 0.5
        np.testing.assert_allclose(dst[:, :, 0], 0.5, atol=1e-5)
        np.testing.assert_allclose(dst[:, :, 3], 0.5 + 1.0 * 0.5, atol=1e-5)

    def test_offset_placement(self):
        """Src at offset (10, 20) only affects the corresponding region."""
        dst = np.zeros((H, W, 4), dtype=np.float32)
        src = np.ones((10, 10, 4), dtype=np.float32)
        alpha_over(dst, src, 10, 20)
        # Region at (10:30, 10:20) in y,x should be modified
        assert dst[20, 10, 3] == pytest.approx(1.0, abs=1e-5)
        assert dst[0, 0, 3] == pytest.approx(0.0, abs=1e-5)

    def test_out_of_bounds_clip(self):
        """Src placed partially outside dst should not raise."""
        dst = np.zeros((H, W, 4), dtype=np.float32)
        src = np.ones((50, 50, 4), dtype=np.float32)
        alpha_over(dst, src, W - 10, H - 10)  # mostly out of bounds

    def test_fully_out_of_bounds(self):
        """Src completely outside dst should be no-op."""
        dst = np.zeros((H, W, 4), dtype=np.float32)
        src = np.ones((10, 10, 4), dtype=np.float32)
        alpha_over(dst, src, W + 100, H + 100)
        np.testing.assert_array_equal(dst, 0.0)

    def test_premultiplied_formula(self):
        """Verify: F = src + dst * (1 - src_alpha)."""
        dst = np.full((5, 5, 4), 0.8, dtype=np.float32)
        src = np.full((5, 5, 4), 0.6, dtype=np.float32)
        src[:, :, 3] = 0.6  # alpha
        expected = src + dst * (1.0 - 0.6)
        alpha_over(dst, src, 0, 0)
        np.testing.assert_allclose(dst, expected, atol=1e-6)


# ===========================================================================
# TestBuildMaskAlpha — output type and shape
# ===========================================================================

class TestBuildMaskAlphaOutputContract:
    """All build_mask_alpha calls must return float32 (H,W) in [0,1]."""

    def _check(self, M, msg=""):
        assert M.shape == (H, W), f"{msg} wrong shape {M.shape}"
        assert M.dtype == np.float32, f"{msg} wrong dtype {M.dtype}"
        assert float(M.min()) >= -1e-6, f"{msg} min < 0: {M.min()}"
        assert float(M.max()) <= 1.0 + 1e-6, f"{msg} max > 1: {M.max()}"

    def test_world_perspective(self):
        scene = load_scene_dict(_raw(_SCENE_PORTAL))
        cam = _cam(cz=0)
        M = build_mask_alpha(scene.masks[0], scene, cam, (H, W), t=4.0)
        self._check(M, "world/perspective")

    def test_screen_radius_before_t0(self):
        scene = load_scene_dict(_raw(_SCENE_RADIUS))
        cam = _cam()
        M = build_mask_alpha(scene.masks[0], scene, cam, (H, W), t=0.0)
        self._check(M, "screen/radius before t0")

    def test_screen_radius_after_t1(self):
        scene = load_scene_dict(_raw(_SCENE_RADIUS))
        cam = _cam()
        M = build_mask_alpha(scene.masks[0], scene, cam, (H, W), t=3.0)
        self._check(M, "screen/radius after t1")

    def test_screen_gradient_x(self):
        raw = _raw(_SCENE_RADIUS)
        raw["masks"][0]["growth"] = {"kind": "gradient", "t0": 1.0, "t1": 2.0, "axis": "x"}
        scene = load_scene_dict(raw)
        M = build_mask_alpha(scene.masks[0], scene, _cam(), (H, W), t=1.5)
        self._check(M, "screen/gradient-x")

    def test_screen_gradient_y(self):
        raw = _raw(_SCENE_RADIUS)
        raw["masks"][0]["growth"] = {"kind": "gradient", "t0": 1.0, "t1": 2.0, "axis": "y"}
        scene = load_scene_dict(raw)
        M = build_mask_alpha(scene.masks[0], scene, _cam(), (H, W), t=1.5)
        self._check(M, "screen/gradient-y")

    def test_screen_displaced_edge(self):
        raw = _raw(_SCENE_RADIUS)
        raw["masks"][0]["growth"] = {
            "kind": "displaced_edge", "t0": 1.0, "t1": 2.0,
            "displacement_map": "nonexistent.png", "amp": 40
        }
        scene = load_scene_dict(raw)
        M = build_mask_alpha(scene.masks[0], scene, _cam(), (H, W), t=1.5)
        self._check(M, "screen/displaced_edge")

    def test_screen_matte_seq(self):
        raw = _raw(_SCENE_RADIUS)
        raw["masks"][0]["growth"] = {"kind": "matte_seq", "t0": 1.0, "t1": 2.0}
        scene = load_scene_dict(raw)
        M = build_mask_alpha(scene.masks[0], scene, _cam(), (H, W), t=1.5)
        self._check(M, "screen/matte_seq")


# ===========================================================================
# TestNearCullHandoff
# ===========================================================================

class TestNearCullHandoff:
    """When the mask's anchor layer is near-culled, build_mask_alpha returns ones."""

    def test_near_cull_at_z_exceeds_threshold(self):
        """
        Camera at z=0, layer at z_scene=-4500.  Push camera very close to layer
        so z_cam = z_layer - z_cam approaches perspective_px (cull window).
        cull_start = perspective_px - 720 = 480, cull_end = perspective_px - 300 = 900.
        Place camera at cz = -4500 + 920 → z_cam ≈ 920 (past cull_end=900 → opacity=0).
        """
        scene = load_scene_dict(_raw(_SCENE_PORTAL))
        # Layer portal_tree at scene_xyz=[0,0,-4500].
        # We want z_cam (layer z in camera frame) to be > perspective_px - 300 = 900.
        # With camera at cz = -4500 + 950 = -3550, layer z in camera frame ≈ 950.
        cam = _cam(cz=-3550)
        M = build_mask_alpha(scene.masks[0], scene, cam, (H, W), t=4.0)
        np.testing.assert_allclose(M, 1.0, atol=1e-5)

    def test_not_culled_at_far_camera(self):
        """Camera far from layer: near-cull opacity > 0 → near-cull path does NOT trigger."""
        from parallax_engine.projection import compute_near_cull, project_world_points
        scene = load_scene_dict(_raw(_SCENE_PORTAL))
        cam_dict = _cam(cz=0)
        # With camera at z=0, layer at z=-4500: z_cam = -4500
        layer = scene.find_layer("forest.portal_tree")
        xyz = np.array(layer.scene_xyz, dtype=np.float64)
        _s, _sc, z_cam_arr = project_world_points(
            xyz[None, :], cam_dict,
            float(scene.meta.perspective_px),
            (float(scene.meta.origin[0]), float(scene.meta.origin[1])),
        )
        z_cam = float(z_cam_arr[0])
        cull_op = compute_near_cull(z_cam, float(scene.meta.perspective_px))
        # Layer is far from camera → should NOT be near-culled
        assert cull_op >= 0.001, (
            f"Expected layer to NOT be near-culled; cull_opacity={cull_op}, z_cam={z_cam}"
        )
        # build_mask_alpha should return a valid float32 (H,W) matte
        M = build_mask_alpha(scene.masks[0], scene, cam_dict, (H, W), t=4.0)
        assert M.shape == (H, W)
        assert M.dtype == np.float32
        assert float(M.min()) >= 0.0 and float(M.max()) <= 1.0


# ===========================================================================
# TestRadiusMask
# ===========================================================================

class TestRadiusMask:
    def test_before_t0_is_zero(self):
        """Before t0, radius = r0 = 0, so matte is all zeros."""
        scene = load_scene_dict(_raw(_SCENE_RADIUS))
        M = build_mask_alpha(scene.masks[0], scene, _cam(), (H, W), t=0.5)
        assert float(M.max()) < 1e-6

    def test_at_t1_radius_covers_center(self):
        """After t1, the full radius r1=500 should cover the center pixels."""
        scene = load_scene_dict(_raw(_SCENE_RADIUS))
        M = build_mask_alpha(scene.masks[0], scene, _cam(), (H, W), t=2.5)
        # Center pixel should be 1
        cy, cx = H // 2, W // 2
        assert M[cy, cx] == pytest.approx(1.0, abs=0.01)

    def test_feathering_blurs_edge(self):
        """With feather_px > 0, there should be a soft edge (non-binary matte).
        Use a small radius (40px) that fits inside the 192x108 test frame."""
        raw = _raw(_SCENE_RADIUS)
        # r1=40 fits inside frame; feather_px=10 softens the edge
        raw["masks"][0]["growth"]["r1"] = 40
        raw["masks"][0]["growth"]["feather_px"] = 10
        scene = load_scene_dict(raw)
        # At t=1.5 (u=0.5), radius=20 (half of 40)
        M = build_mask_alpha(scene.masks[0], scene, _cam(), (H, W), t=1.5)
        # Should have a soft transition band between inside and outside
        intermediate = (M > 0.01) & (M < 0.99)
        assert intermediate.any(), "Feathered radius mask should have soft edge"

    def test_invert(self):
        """Inverted mask flips 0 and 1."""
        raw = _raw(_SCENE_RADIUS)
        raw["masks"][0]["invert"] = True
        raw["masks"][0]["growth"]["feather_px"] = 0.0
        scene = load_scene_dict(raw)
        M = build_mask_alpha(scene.masks[0], scene, _cam(), (H, W), t=0.5)
        # Before t0 with r0=0: without invert all zeros; with invert all ones
        np.testing.assert_allclose(M, 1.0, atol=1e-5)


# ===========================================================================
# TestCompositeWithMask
# ===========================================================================

class TestCompositeWithMask:
    def _scene(self):
        return load_scene_dict(_raw(_SCENE_RADIUS))

    def test_output_shape_dtype(self):
        scene = self._scene()
        S = _rgba(1.0, 0.0, 0.0, 1.0)  # red
        D = _rgba(0.0, 0.0, 1.0, 1.0)  # blue
        F = composite_with_mask(scene, scene.masks[0], S, D, _cam(), (H, W), t=0.0)
        assert F.shape == (H, W, 4)
        assert F.dtype == np.float32

    def test_m_zero_gives_src(self):
        """When M=0 everywhere (t<t0), F should equal S."""
        scene = self._scene()
        S = _rgba(1.0, 0.0, 0.0, 1.0)
        D = _rgba(0.0, 0.0, 1.0, 1.0)
        # t=0.5 < t0=1.0 → r0=0 → M=0 (no radius)
        F = composite_with_mask(scene, scene.masks[0], S, D, _cam(), (H, W), t=0.5)
        np.testing.assert_allclose(F, S, atol=1e-5)

    def test_m_one_gives_dst(self):
        """When M=1 everywhere, F should equal D."""
        # Use near-cull scenario
        scene = load_scene_dict(_raw(_SCENE_PORTAL))
        S = _rgba(1.0, 0.0, 0.0, 1.0)
        D = _rgba(0.0, 0.0, 1.0, 1.0)
        # Camera near layer → near-cull → M=1
        cam = _cam(cz=-3550)
        F = composite_with_mask(scene, scene.masks[0], S, D, cam, (H, W), t=4.0)
        np.testing.assert_allclose(F, D, atol=1e-5)

    def test_compositing_formula(self):
        """F = D*M + S*(1-M) holds at all pixels."""
        scene = self._scene()
        S = _rgba(1.0, 0.0, 0.0, 1.0)
        D = _rgba(0.0, 0.0, 1.0, 1.0)
        t = 1.5  # mid-transition
        from parallax_engine.masks import build_mask_alpha
        cam = _cam()
        M = build_mask_alpha(scene.masks[0], scene, cam, (H, W), t)
        m4 = M[:, :, None]
        expected = D * m4 + S * (1.0 - m4)
        F = composite_with_mask(scene, scene.masks[0], S, D, cam, (H, W), t)
        np.testing.assert_allclose(F, expected, atol=1e-6)

    def test_output_in_valid_range(self):
        """All output values must be in [0, 1] for float32 RGBA."""
        scene = self._scene()
        S = _rgba(0.5, 0.3, 0.8, 0.9)
        D = _rgba(0.2, 0.7, 0.1, 0.6)
        F = composite_with_mask(scene, scene.masks[0], S, D, _cam(), (H, W), t=1.5)
        assert float(F.min()) >= -1e-5
        assert float(F.max()) <= 1.0 + 1e-5

    def test_l2_rule_adds_layer(self):
        """L2 rule: a layer closer than the mask should appear unmasked in F."""
        scene = load_scene_dict(_raw(_SCENE_PORTAL))
        S = _rgba(0.0, 1.0, 0.0, 1.0)  # green source
        D = _rgba(0.0, 0.0, 1.0, 1.0)  # blue dest
        cam = _cam()
        t = 4.0

        # Pre-render a solid white sprite for leaves_fg (z=-500, closer than portal_tree z=-4500)
        sprite = np.ones((H, W, 4), dtype=np.float32)
        sprite[:, :, 0] = 1.0
        sprite[:, :, 1] = 1.0
        sprite[:, :, 2] = 1.0

        layer_sprites = {"forest.leaves_fg": sprite}
        F = composite_with_mask(
            scene, scene.masks[0], S, D, cam, (H, W), t, layer_sprites=layer_sprites
        )
        # With a fully-opaque white sprite on top, red channel of F should be close to 1.0
        assert float(F[:, :, 0].mean()) > 0.8

    def test_l2_rule_skips_far_layers(self):
        """Layers farther than the mask layer should NOT be re-rendered."""
        scene = load_scene_dict(_raw(_SCENE_PORTAL))
        S = _rgba(0.0, 0.0, 0.0, 1.0)  # black
        D = _rgba(0.0, 0.0, 1.0, 1.0)  # blue
        cam = _cam()
        t = 4.0

        # sky layer is at z=-10500, farther than portal_tree at z=-4500 → NOT applied
        sprite_white = np.ones((H, W, 4), dtype=np.float32)
        layer_sprites = {"forest.sky": sprite_white}  # should be skipped
        F_with = composite_with_mask(
            scene, scene.masks[0], S, D, cam, (H, W), t, layer_sprites=layer_sprites
        )
        F_without = composite_with_mask(
            scene, scene.masks[0], S, D, cam, (H, W), t, layer_sprites=None
        )
        np.testing.assert_allclose(F_with, F_without, atol=1e-6)


# ===========================================================================
# TestGradientMask
# ===========================================================================

class TestGradientMask:
    def _grad_scene(self, axis="x"):
        raw = _raw(_SCENE_RADIUS)
        raw["masks"][0]["growth"] = {"kind": "gradient", "t0": 1.0, "t1": 2.0, "axis": axis}
        return load_scene_dict(raw)

    def test_before_t0_near_zero(self):
        scene = self._grad_scene()
        M = build_mask_alpha(scene.masks[0], scene, _cam(), (H, W), t=0.5)
        assert float(M.max()) < 1e-3

    def test_at_t1_has_nonzero(self):
        scene = self._grad_scene()
        M = build_mask_alpha(scene.masks[0], scene, _cam(), (H, W), t=2.5)
        assert float(M.max()) > 0.5

    def test_gradient_x_varies_horizontally(self):
        """Gradient-x mask should have non-uniform values along x axis."""
        scene = self._grad_scene("x")
        M = build_mask_alpha(scene.masks[0], scene, _cam(), (H, W), t=1.5)
        # At mid-transition, left columns should differ from right columns
        left_mean = float(M[:, :W//4].mean())
        right_mean = float(M[:, 3*W//4:].mean())
        assert abs(right_mean - left_mean) > 0.05, (
            f"Gradient should vary: left={left_mean}, right={right_mean}"
        )

    def test_gradient_y_varies_vertically(self):
        """Gradient-y mask should have non-uniform values along y axis."""
        scene = self._grad_scene("y")
        M = build_mask_alpha(scene.masks[0], scene, _cam(), (H, W), t=1.5)
        top_mean = float(M[:H//4, :].mean())
        bot_mean = float(M[3*H//4:, :].mean())
        assert abs(bot_mean - top_mean) > 0.05, (
            f"Gradient-y should vary: top={top_mean}, bottom={bot_mean}"
        )
