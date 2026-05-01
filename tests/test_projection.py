"""
tests/test_projection.py
=========================
Unit tests for parallax_engine.projection.

Covers:
  - project_points: the locked inner API (also tested by validate_projection.py)
  - project_world_points: the full §2.1 world-to-screen pipeline
  - compute_near_cull: near-cull opacity helper
  - CSS-equivalence: camera at origin with no rotation reduces to CSS formula
  - Determinism: byte-identical output on repeated calls

SPEC anchors: §2.1, §7, §9.2
"""

from __future__ import annotations

import numpy as np
import pytest

from parallax_engine.projection import (
    compute_near_cull,
    project_points,
    project_world_points,
)

PERSP = 800.0
W = 1920
H = 1080
OX = W / 2.0   # 960.0
OY = H / 2.0   # 540.0


# ---------------------------------------------------------------------------
# project_points — inner function
# ---------------------------------------------------------------------------

class TestProjectPoints:
    """Basic correctness tests for the inner perspective-divide function."""

    def test_identity_at_z_zero(self):
        """At z=0, s=1 and screen = centre + offset."""
        x = np.array([0.0, 100.0, -250.0], dtype=np.float64)
        y = np.array([0.0,  50.0,  120.0], dtype=np.float64)
        z = np.zeros(3, dtype=np.float64)
        xs, ys = project_points(x, y, z, PERSP, W, H)
        np.testing.assert_allclose(xs, OX + x, atol=1e-9)
        np.testing.assert_allclose(ys, OY + y, atol=1e-9)

    def test_scale_factor_at_half_perspective(self):
        """At z = p/2, s = 2 → offsets double."""
        x = np.array([100.0, -50.0], dtype=np.float64)
        y = np.array([200.0,  75.0], dtype=np.float64)
        z = np.full(2, PERSP / 2.0, dtype=np.float64)
        xs, ys = project_points(x, y, z, PERSP, W, H)
        np.testing.assert_allclose(xs, OX + x * 2.0, atol=1e-9)
        np.testing.assert_allclose(ys, OY + y * 2.0, atol=1e-9)

    def test_scale_factor_at_negative_perspective(self):
        """At z = -p, s = 0.5 → offsets halve."""
        x = np.array([400.0, -200.0], dtype=np.float64)
        y = np.array([100.0,  300.0], dtype=np.float64)
        z = np.full(2, -PERSP, dtype=np.float64)
        xs, ys = project_points(x, y, z, PERSP, W, H)
        np.testing.assert_allclose(xs, OX + x * 0.5, atol=1e-9)
        np.testing.assert_allclose(ys, OY + y * 0.5, atol=1e-9)

    def test_origin_always_maps_to_centre(self):
        """(0, 0) maps to canvas centre regardless of z."""
        z = np.array([-2 * PERSP, -PERSP, 0.0, PERSP * 0.99], dtype=np.float64)
        xs, ys = project_points(np.zeros(4), np.zeros(4), z, PERSP, W, H)
        np.testing.assert_allclose(xs, OX, atol=1e-9)
        np.testing.assert_allclose(ys, OY, atol=1e-9)

    def test_dtype_preserved_float32(self):
        x = np.array([100.0, 0.0], dtype=np.float32)
        y = np.array([50.0,  0.0], dtype=np.float32)
        z = np.array([0.0, 100.0], dtype=np.float32)
        xs, ys = project_points(x, y, z, PERSP, W, H)
        assert xs.dtype == np.float32
        assert ys.dtype == np.float32

    def test_dtype_preserved_float64(self):
        x = np.array([100.0], dtype=np.float64)
        y = np.array([50.0],  dtype=np.float64)
        z = np.array([0.0],   dtype=np.float64)
        xs, ys = project_points(x, y, z, PERSP, W, H)
        assert xs.dtype == np.float64
        assert ys.dtype == np.float64

    def test_singularity_non_finite(self):
        """At z = perspective_px, output must be non-finite or raise."""
        x = np.array([100.0], dtype=np.float64)
        y = np.array([50.0],  dtype=np.float64)
        z = np.array([PERSP], dtype=np.float64)
        try:
            xs, ys = project_points(x, y, z, PERSP, W, H)
            # If it returned, values must be non-finite
            assert not (np.all(np.isfinite(xs)) and np.all(np.isfinite(ys))), (
                f"Singularity returned finite values: xs={xs}, ys={ys}"
            )
        except (ZeroDivisionError, FloatingPointError, ValueError):
            pass  # Also acceptable

    def test_behind_viewer_sign_flip(self):
        """z > perspective_px → s < 0 → screen offsets flip sign."""
        x = np.array([100.0], dtype=np.float64)
        y = np.array([0.0],   dtype=np.float64)
        z = np.array([PERSP * 2.0], dtype=np.float64)
        xs, _ = project_points(x, y, z, PERSP, W, H)
        # Positive x_centered → negative offset from centre when s < 0
        assert xs[0] < OX, f"Expected xs < centre ({OX}), got {xs[0]}"

    def test_mismatched_shapes_raise(self):
        with pytest.raises((ValueError, AssertionError)):
            project_points(np.zeros(5), np.zeros(3), np.zeros(5), PERSP, W, H)

    def test_determinism_byte_identical(self):
        rng = np.random.default_rng(0xABCDEF)
        x = rng.uniform(-2000.0, 2000.0, 500)
        y = rng.uniform(-2000.0, 2000.0, 500)
        z = rng.uniform(-PERSP * 2, PERSP - 1.0, 500)
        xs1, ys1 = project_points(x, y, z, PERSP, W, H)
        xs2, ys2 = project_points(x, y, z, PERSP, W, H)
        assert xs1.tobytes() == xs2.tobytes(), "x output not byte-identical"
        assert ys1.tobytes() == ys2.tobytes(), "y output not byte-identical"


# ---------------------------------------------------------------------------
# project_world_points — full §2.1 pipeline
# ---------------------------------------------------------------------------

class TestProjectWorldPoints:
    """Tests for the full world-to-screen projection."""

    def _identity_cam(self) -> dict:
        return dict(cx=0.0, cy=0.0, cz=0.0, yaw=0.0, pitch=0.0, roll=0.0)

    def test_identity_camera_equals_css(self):
        """
        CSS equivalence (SPEC.md §2.1): when camera is at origin with no
        rotation, project_world_points reduces to the CSS formula
          s = p / (p - z),  X = O_x + x * s,  Y = O_y + y * s.
        """
        cam = self._identity_cam()
        persp = 1200.0
        origin = (960.0, 540.0)
        pts = np.array([
            [0.0, 0.0, 0.0],
            [100.0, 50.0, -500.0],
            [-200.0, -100.0, -1000.0],
        ], dtype=np.float64)

        s_got, xy_got, zc_got = project_world_points(pts, cam, persp, origin)

        for i, (x, y, z) in enumerate(pts):
            s_exp = persp / (persp - z)
            x_exp = origin[0] + x * s_exp
            y_exp = origin[1] + y * s_exp
            np.testing.assert_allclose(s_got[i], s_exp, rtol=1e-9)
            np.testing.assert_allclose(xy_got[i, 0], x_exp, atol=1e-6)
            np.testing.assert_allclose(xy_got[i, 1], y_exp, atol=1e-6)

    def test_z_cam_matches_expectation(self):
        """Camera at (0,0,0), no rotation: z_cam == world z."""
        cam = self._identity_cam()
        pts = np.array([[0.0, 0.0, -500.0],
                        [0.0, 0.0,  200.0]], dtype=np.float64)
        _, _, zc = project_world_points(pts, cam, 1200.0, (960.0, 540.0))
        np.testing.assert_allclose(zc, [-500.0, 200.0], atol=1e-9)

    def test_camera_translation(self):
        """Camera translated by (dx, dy, dz) shifts all points inversely."""
        cam = dict(cx=100.0, cy=50.0, cz=-200.0, yaw=0.0, pitch=0.0, roll=0.0)
        persp = 1200.0
        origin = (960.0, 540.0)
        # A point at the camera position should project to centre
        pts = np.array([[100.0, 50.0, -200.0]], dtype=np.float64)
        _, xy, _ = project_world_points(pts, cam, persp, origin)
        np.testing.assert_allclose(xy[0, 0], origin[0], atol=1e-9)
        np.testing.assert_allclose(xy[0, 1], origin[1], atol=1e-9)

    def test_single_point_shape(self):
        """1D input (shape (3,)) should be handled without error."""
        cam = self._identity_cam()
        pt = np.array([0.0, 0.0, -500.0])
        s, xy, zc = project_world_points(pt, cam, 1200.0, (960.0, 540.0))
        assert s.shape == (1,)
        assert xy.shape == (1, 2)
        assert zc.shape == (1,)

    def test_output_shapes(self):
        cam = self._identity_cam()
        n = 7
        pts = np.random.default_rng(42).uniform(-1000, 0, (n, 3))
        s, xy, zc = project_world_points(pts, cam, 1200.0, (960.0, 540.0))
        assert s.shape == (n,)
        assert xy.shape == (n, 2)
        assert zc.shape == (n,)

    def test_determinism(self):
        cam = dict(cx=10.0, cy=-5.0, cz=-100.0, yaw=0.1, pitch=-0.05, roll=0.03)
        pts = np.random.default_rng(99).uniform(-2000, 0, (50, 3))
        s1, xy1, zc1 = project_world_points(pts, cam, 1200.0, (960.0, 540.0))
        s2, xy2, zc2 = project_world_points(pts, cam, 1200.0, (960.0, 540.0))
        assert s1.tobytes() == s2.tobytes()
        assert xy1.tobytes() == xy2.tobytes()
        assert zc1.tobytes() == zc2.tobytes()


# ---------------------------------------------------------------------------
# compute_near_cull
# ---------------------------------------------------------------------------

class TestComputeNearCull:
    def test_far_away_is_fully_opaque(self):
        assert compute_near_cull(-5000.0, 1200.0) == 1.0

    def test_at_cull_start_is_one(self):
        # cull_start = 1200 - 720 = 480
        assert compute_near_cull(480.0, 1200.0) == 1.0

    def test_past_cull_end_is_zero(self):
        # cull_end = 1200 - 300 = 900
        assert compute_near_cull(900.0, 1200.0) == 0.0

    def test_midpoint_is_half(self):
        # midpoint of [480, 900] = 690
        result = compute_near_cull(690.0, 1200.0)
        assert abs(result - 0.5) < 1e-9

    def test_clamps_below_zero(self):
        assert compute_near_cull(1200.0, 1200.0) == 0.0

    def test_clamps_above_one(self):
        assert compute_near_cull(-999999.0, 1200.0) == 1.0
