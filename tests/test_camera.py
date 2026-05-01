"""
tests/test_camera.py — Unit tests for parallax_engine/camera.py and seeds.py (P1.M04).

Covers:
- BezierPath: endpoints, linear interpolation, de Casteljau correctness
- SeededNoise: determinism, axis independence, value range, smoothness
- drone_camera_track: shape, dtype, convergence, determinism, roll/yaw range
- kf_camera_track: shape, dtype, easing functions, boundary frames
- seeds.spawn_channel: stable channel IDs, non-negative validation
- Holden spring integrator: analytic verification for one step
"""

import textwrap

import numpy as np
import pytest
import yaml

from parallax_engine.camera import (
    BezierPath,
    SeededNoise,
    _apply_ease,
    _bracket,
    _de_casteljau,
    drone_camera_track,
    kf_camera_track,
)
from parallax_engine.scene import load_scene_dict
from parallax_engine.seeds import (
    CAMERA_NOISE,
    GRAIN,
    LIGHT_LEAKS,
    make_rng,
    spawn_channel,
)

# ---------------------------------------------------------------------------
# Minimal scene fixtures
# ---------------------------------------------------------------------------

_SCENE_DRONE = textwrap.dedent("""\
version: 1
meta:
  duration_s: 5.0
  fps: 30
  resolution: [1920, 1080]
  perspective_px: 1200
  origin: [960, 540]
  seed: 42
stacks:
  default:
    layers:
      - { id: bg, src: assets/bg.svg, scene_xyz: [0,0,-5000], plate_size: [3840,2160] }
camera:
  mode: drone
  drone:
    path:
      kind: bezier
      controls: [[0,0,0], [100,20,-2000], [0,0,-4500]]
      duration_s: 5.0
    poi_lookahead_s: 0.5
    spring_halflife_s: 0.18
    noise: { z_amp: 20, xy_amp: 5, hz: 0.8 }
    bank_from_velocity: 0.40
masks: []
""")

_SCENE_KF = textwrap.dedent("""\
version: 1
meta:
  duration_s: 6.0
  fps: 30
  resolution: [1920, 1080]
  perspective_px: 1200
  origin: [960, 540]
  seed: 99
stacks:
  default:
    layers:
      - { id: bg, src: assets/bg.svg, scene_xyz: [0,0,-5000], plate_size: [3840,2160] }
camera:
  mode: keyframed
  keyframed:
    - { t: 0.0,  x: 0,    y: 0, z: 0,     yaw: 0,   pitch: 0, roll: 0, ease: linear }
    - { t: 3.0,  x: 200,  y: 0, z: -1000, yaw: 0.1, pitch: 0, roll: 0, ease: easeInOutCubic }
    - { t: 6.0,  x: 0,    y: 0, z: -2000, yaw: 0,   pitch: 0, roll: 0, ease: easeOutQuint }
masks: []
""")


def _raw(s):
    return yaml.safe_load(s)


# ===========================================================================
# TestBezierPath
# ===========================================================================

class TestBezierPath:
    def test_start_point(self):
        """t=0 returns first control point."""
        p = BezierPath([[0, 0, 0], [100, 0, -500], [0, 0, -1000]], 10.0)
        np.testing.assert_allclose(p.eval(0.0), [0.0, 0.0, 0.0], atol=1e-10)

    def test_end_point(self):
        """t=duration_s returns last control point."""
        p = BezierPath([[0, 0, 0], [100, 0, -500], [0, 0, -1000]], 10.0)
        np.testing.assert_allclose(p.eval(10.0), [0.0, 0.0, -1000.0], atol=1e-10)

    def test_clamping_beyond_end(self):
        """t > duration_s is clamped to end."""
        p = BezierPath([[0, 0, 0], [0, 0, -100]], 5.0)
        np.testing.assert_allclose(p.eval(999.0), p.eval(5.0), atol=1e-12)

    def test_clamping_before_start(self):
        """t < 0 is clamped to start."""
        p = BezierPath([[0, 0, 0], [0, 0, -100]], 5.0)
        np.testing.assert_allclose(p.eval(-1.0), p.eval(0.0), atol=1e-12)

    def test_linear_midpoint(self):
        """Linear path (2 control points) at midpoint = average of endpoints."""
        p = BezierPath([[0, 0, 0], [100, 40, -200]], 10.0)
        mid = p.eval(5.0)
        np.testing.assert_allclose(mid, [50.0, 20.0, -100.0], atol=1e-10)

    def test_quadratic_midpoint(self):
        """Quadratic Bezier midpoint is known analytically."""
        # B(0.5) = 0.25*P0 + 0.5*P1 + 0.25*P2
        p = BezierPath([[0, 0, 0], [100, 0, 0], [0, 0, 0]], 1.0)
        mid = p.eval(0.5)
        expected = 0.25 * np.array([0, 0, 0]) + 0.5 * np.array([100, 0, 0]) + 0.25 * np.array([0, 0, 0])
        np.testing.assert_allclose(mid, expected, atol=1e-10)

    def test_shape(self):
        p = BezierPath([[0, 0, 0], [1, 2, 3]], 1.0)
        assert p.eval(0.5).shape == (3,)

    def test_dtype(self):
        p = BezierPath([[0, 0, 0], [1, 2, 3]], 1.0)
        assert p.eval(0.5).dtype == np.float64

    def test_one_control_raises(self):
        with pytest.raises(ValueError, match="2 control"):
            BezierPath([[0, 0, 0]], 1.0)

    def test_bad_shape_raises(self):
        with pytest.raises(ValueError):
            BezierPath([[0, 0], [1, 2]], 1.0)

    def test_de_casteljau_cubic(self):
        """Cubic Bezier at t=0.5 matches analytical Bernstein expansion."""
        pts = np.array([[0, 0, 0], [1, 3, 0], [2, 3, 0], [3, 0, 0]], dtype=float)
        t = 0.5
        result = _de_casteljau(pts, t)
        # B(t) = sum(C(3,k)*t^k*(1-t)^(3-k)*P[k])
        from math import comb
        expected = sum(
            comb(3, k) * (t ** k) * ((1 - t) ** (3 - k)) * pts[k]
            for k in range(4)
        )
        np.testing.assert_allclose(result, expected, atol=1e-12)

    def test_deterministic(self):
        p = BezierPath([[0, 0, 0], [50, 10, -300], [0, 0, -600]], 10.0)
        v1 = p.eval(3.7)
        v2 = p.eval(3.7)
        np.testing.assert_array_equal(v1, v2)


# ===========================================================================
# TestSeededNoise
# ===========================================================================

class TestSeededNoise:
    def _noise(self, seed_int: int = 0) -> SeededNoise:
        ss = np.random.SeedSequence(seed_int)
        return SeededNoise(ss)

    def test_value_range(self):
        """All sample values must be in [-1, 1]."""
        n = self._noise(7)
        for t in np.linspace(0, 20, 300):
            v = n.sample(t, axis=0)
            assert -1.0 <= v <= 1.0, f"Out of range at t={t}: {v}"

    def test_determinism(self):
        """Same seed + same t + same axis → identical float."""
        n1 = self._noise(42)
        n2 = self._noise(42)
        for t in [0.0, 0.33, 1.0, 7.5, 15.99]:
            assert n1.sample(t, axis=0) == n2.sample(t, axis=0)
            assert n1.sample(t, axis=2) == n2.sample(t, axis=2)

    def test_axis_independence(self):
        """Different axes must not be identical (they are independently seeded)."""
        n = self._noise(13)
        vals0 = [n.sample(t, axis=0) for t in np.linspace(0.5, 5.5, 50)]
        vals1 = [n.sample(t, axis=1) for t in np.linspace(0.5, 5.5, 50)]
        assert vals0 != vals1

    def test_different_seeds_differ(self):
        """Different seeds produce different noise."""
        n1 = self._noise(0)
        n2 = self._noise(1)
        vals1 = [n1.sample(t) for t in np.linspace(0, 5, 20)]
        vals2 = [n2.sample(t) for t in np.linspace(0, 5, 20)]
        assert vals1 != vals2

    def test_smoothness(self):
        """Noise at close t values should not differ by more than the interpolation range."""
        n = self._noise(5)
        eps = 0.001
        for t in np.linspace(0.5, 4.5, 20):
            v1 = n.sample(t)
            v2 = n.sample(t + eps)
            # With quintic smoothstep, derivative is bounded; change should be small
            assert abs(v2 - v1) < 0.2, (
                f"Noise not smooth at t={t}: delta={abs(v2-v1)}"
            )

    def test_axis_wraparound(self):
        """axis >= n_axes should wrap around (modulo), not raise."""
        n = SeededNoise(np.random.SeedSequence(0), n_axes=4)
        v0 = n.sample(1.5, axis=0)
        v4 = n.sample(1.5, axis=4)  # wraps to axis 0
        assert v0 == v4

    def test_returns_float(self):
        n = self._noise(0)
        result = n.sample(3.14)
        assert isinstance(result, float)


# ===========================================================================
# TestHoldenSpring
# ===========================================================================

class TestHoldenSpring:
    """Verify the Holden exact-discrete critically-damped spring against analytic."""

    def _one_step(self, x0, v0, tgt, omega, dt):
        """Execute one Holden spring step."""
        x = np.array(x0, dtype=np.float64)
        v = np.array(v0, dtype=np.float64)
        tgt = np.array(tgt, dtype=np.float64)
        e  = np.exp(-omega * dt)
        j0 = x - tgt
        j1 = v + j0 * omega
        x_new = e * (j0 + j1 * dt) + tgt
        v_new = e * (v - j1 * omega * dt)
        return x_new, v_new

    def test_single_step_matches_analytic(self):
        """
        For x0=[1,0,0], v0=[0,0,0], tgt=[0,0,0]:
        analytic critically-damped x(dt) = x0 * (1 + omega*dt) * exp(-omega*dt).
        """
        h = 0.18      # halflife
        omega = np.log(2.0) / h
        dt = 1.0 / 30.0
        x0 = np.array([100.0, 0.0, 0.0])
        x_step, _ = self._one_step(x0, [0, 0, 0], [0, 0, 0], omega, dt)
        # Analytic solution for critically-damped spring:
        # x(dt) = (x0 + (v0 + x0*omega)*dt) * exp(-omega*dt)  with v0=0
        x_analytic = x0 * (1.0 + omega * dt) * np.exp(-omega * dt)
        np.testing.assert_allclose(x_step, x_analytic, rtol=1e-12, atol=1e-12)

    def test_converges_to_target(self):
        """After many steps the spring position should be near the target."""
        h = 0.18
        omega = np.log(2.0) / h
        dt = 1.0 / 30.0
        x = np.array([500.0, -200.0, 100.0])
        v = np.zeros(3)
        tgt = np.zeros(3)
        for _ in range(300):  # 10 seconds of simulation
            e  = np.exp(-omega * dt)
            j0 = x - tgt
            j1 = v + j0 * omega
            x  = e * (j0 + j1 * dt) + tgt
            v  = e * (v - j1 * omega * dt)
        np.testing.assert_allclose(x, tgt, atol=1e-6)

    def test_nonzero_initial_velocity(self):
        """Spring with nonzero initial velocity still converges."""
        h = 0.18
        omega = np.log(2.0) / h
        dt = 1.0 / 30.0
        x = np.array([0.0, 0.0, 0.0])
        v = np.array([1000.0, 0.0, 0.0])  # push away from tgt
        tgt = np.zeros(3)
        for _ in range(300):
            e  = np.exp(-omega * dt)
            j0 = x - tgt
            j1 = v + j0 * omega
            x  = e * (j0 + j1 * dt) + tgt
            v  = e * (v - j1 * omega * dt)
        np.testing.assert_allclose(x, tgt, atol=1e-6)

    def test_stability_large_dt(self):
        """Exact-discrete integrator should remain bounded for large dt."""
        h = 0.18
        omega = np.log(2.0) / h
        dt = 5.0   # very large timestep
        x = np.array([100.0, 0.0, 0.0])
        v = np.zeros(3)
        tgt = np.zeros(3)
        for _ in range(10):
            e  = np.exp(-omega * dt)
            j0 = x - tgt
            j1 = v + j0 * omega
            x  = e * (j0 + j1 * dt) + tgt
            v  = e * (v - j1 * omega * dt)
        # Should converge; exact-discrete spring is unconditionally stable
        assert np.all(np.abs(x) < 110.0), "Spring diverged with large dt"
        assert np.all(np.isfinite(x))


# ===========================================================================
# TestDroneCameraTrack
# ===========================================================================

class TestDroneCameraTrack:
    def _scene(self):
        return load_scene_dict(_raw(_SCENE_DRONE))

    def test_shape(self):
        scene = self._scene()
        T = scene.meta.duration_s
        fps = scene.meta.fps
        track = drone_camera_track(scene, T, fps)
        assert track.shape == (int(T * fps), 6)

    def test_dtype(self):
        scene = self._scene()
        track = drone_camera_track(scene, scene.meta.duration_s, scene.meta.fps)
        assert track.dtype == np.float64

    def test_determinism_bit_identical(self):
        """Two calls with same scene must produce bit-identical output."""
        scene = self._scene()
        T, fps = scene.meta.duration_s, scene.meta.fps
        t1 = drone_camera_track(scene, T, fps)
        t2 = drone_camera_track(scene, T, fps)
        np.testing.assert_array_equal(t1, t2)

    def test_different_seeds_differ(self):
        """Different seeds must produce different tracks."""
        s1 = load_scene_dict(_raw(_SCENE_DRONE))
        raw2 = _raw(_SCENE_DRONE)
        raw2["meta"]["seed"] = 9999
        s2 = load_scene_dict(raw2)
        t1 = drone_camera_track(s1, s1.meta.duration_s, s1.meta.fps)
        t2 = drone_camera_track(s2, s2.meta.duration_s, s2.meta.fps)
        assert not np.array_equal(t1, t2)

    def test_all_finite(self):
        scene = self._scene()
        track = drone_camera_track(scene, scene.meta.duration_s, scene.meta.fps)
        assert np.all(np.isfinite(track)), "Track contains non-finite values"

    def test_columns_xyz_yaw_pitch_roll(self):
        """Columns 3,4,5 are yaw, pitch, roll — angles in radians."""
        scene = self._scene()
        track = drone_camera_track(scene, scene.meta.duration_s, scene.meta.fps)
        yaw   = track[:, 3]
        pitch = track[:, 4]
        roll  = track[:, 5]
        # yaw and pitch should be in [-pi, pi]
        assert np.all(np.abs(yaw)   <= np.pi + 1e-6)
        assert np.all(np.abs(pitch) <= np.pi / 2 + 1e-6)
        # roll from tanh is bounded by k_bank (0.40)
        assert np.all(np.abs(roll) <= 0.40 + 1e-9)

    def test_spring_converges_to_bezier_end(self):
        """After enough time, rig should be near the Bezier end point."""
        scene = self._scene()
        track = drone_camera_track(scene, scene.meta.duration_s, scene.meta.fps)
        # Last frame xyz should be close to last Bezier control point
        end_ctrl = np.array(scene.camera.drone.path.controls[-1], dtype=float)
        last_xyz = track[-1, :3]
        # Allow generous tolerance (spring + noise offset)
        dist = np.linalg.norm(last_xyz - end_ctrl)
        assert dist < 500.0, (
            f"Spring did not converge near Bezier end; dist={dist:.1f}"
        )

    def test_frame_count_exact(self):
        """n must be exactly int(T * fps), not rounded up."""
        scene = self._scene()
        T, fps = 5.0, 30
        track = drone_camera_track(scene, T, fps)
        assert len(track) == 150

    def test_position_smooth(self):
        """Position should not jump discontinuously between frames."""
        scene = self._scene()
        track = drone_camera_track(scene, scene.meta.duration_s, scene.meta.fps)
        xyz = track[:, :3]
        deltas = np.diff(xyz, axis=0)
        max_jump = np.max(np.linalg.norm(deltas, axis=1))
        # Spring + noise should not cause jumps larger than a few hundred units/frame
        assert max_jump < 300.0, f"Position jump too large: {max_jump:.1f}"


# ===========================================================================
# TestKfCameraTrack
# ===========================================================================

class TestKfCameraTrack:
    def _scene(self):
        return load_scene_dict(_raw(_SCENE_KF))

    def test_shape(self):
        scene = self._scene()
        T, fps = scene.meta.duration_s, scene.meta.fps
        track = kf_camera_track(scene, T, fps)
        assert track.shape == (int(T * fps), 6)

    def test_dtype(self):
        scene = self._scene()
        track = kf_camera_track(scene, scene.meta.duration_s, scene.meta.fps)
        assert track.dtype == np.float64

    def test_first_frame_matches_first_kf(self):
        """Frame 0 must equal the first keyframe's values."""
        scene = self._scene()
        track = kf_camera_track(scene, scene.meta.duration_s, scene.meta.fps)
        kf0 = scene.camera.keyframed[0]
        np.testing.assert_allclose(
            track[0], [kf0.x, kf0.y, kf0.z, kf0.yaw, kf0.pitch, kf0.roll],
            atol=1e-10,
        )

    def test_last_frame_near_last_kf(self):
        """Last frame should be close to the last keyframe (clamped)."""
        scene = self._scene()
        T, fps = scene.meta.duration_s, scene.meta.fps
        track = kf_camera_track(scene, T, fps)
        kf_last = scene.camera.keyframed[-1]
        expected = [kf_last.x, kf_last.y, kf_last.z,
                    kf_last.yaw, kf_last.pitch, kf_last.roll]
        np.testing.assert_allclose(track[-1], expected, atol=0.5)

    def test_determinism_bit_identical(self):
        scene = self._scene()
        T, fps = scene.meta.duration_s, scene.meta.fps
        t1 = kf_camera_track(scene, T, fps)
        t2 = kf_camera_track(scene, T, fps)
        np.testing.assert_array_equal(t1, t2)

    def test_all_finite(self):
        scene = self._scene()
        track = kf_camera_track(scene, scene.meta.duration_s, scene.meta.fps)
        assert np.all(np.isfinite(track))

    def test_easing_affects_midpoint(self):
        """
        Verify easeInOutCubic output against analytic value.

        Use a simple 2-keyframe scene at fps=100 so frame timings are exact.
        Keyframes: t=0 x=0 ease=easeInOutCubic, t=1 x=100.
        At frame 25 (t=0.25): u=0.25, easeInOutCubic(0.25)=0.15625, x=15.625.
        With linear easing at same u: x=25.0.  Difference proves easing is active.
        """
        raw = {
            "version": 1,
            "meta": {"duration_s": 1.0, "fps": 100, "resolution": [1920, 1080],
                     "perspective_px": 1200, "origin": [960, 540], "seed": 1},
            "stacks": {"s": {"layers": [
                {"id": "bg", "src": "x.svg", "scene_xyz": [0, 0, -1000],
                 "plate_size": [1920, 1080]}
            ]}},
            "camera": {"mode": "keyframed", "keyframed": [
                {"t": 0.0, "x": 0.0, "y": 0.0, "z": 0.0, "ease": "easeInOutCubic"},
                {"t": 1.0, "x": 100.0, "y": 0.0, "z": 0.0},
            ]},
            "masks": [],
        }
        scene = load_scene_dict(raw)
        track = kf_camera_track(scene, 1.0, 100)
        # Frame 25: t=0.25, u=0.25, easeInOutCubic(0.25) = 3*0.0625-2*0.015625 = 0.15625
        x_val = track[25, 0]
        np.testing.assert_allclose(x_val, 15.625, atol=1e-9)

    def test_linear_easing_midpoint(self):
        """
        With linear easing between two keyframes, midpoint x is exactly average.
        Use a simple 2-keyframe scene with linear easing.
        """
        raw = {
            "version": 1,
            "meta": {"duration_s": 2.0, "fps": 30, "resolution": [1920, 1080],
                     "perspective_px": 1200, "origin": [960, 540], "seed": 1},
            "stacks": {"s": {"layers": [
                {"id": "bg", "src": "x.svg", "scene_xyz": [0, 0, -1000], "plate_size": [1920, 1080]}
            ]}},
            "camera": {"mode": "keyframed", "keyframed": [
                {"t": 0.0, "x": 0.0, "y": 0.0, "z": 0.0, "ease": "linear"},
                {"t": 2.0, "x": 100.0, "y": 0.0, "z": 0.0},
            ]},
            "masks": [],
        }
        scene = load_scene_dict(raw)
        track = kf_camera_track(scene, 2.0, 30)
        # At frame 30 (t=1.0, u=0.5), x should be 50.0
        np.testing.assert_allclose(track[30, 0], 50.0, atol=0.1)


# ===========================================================================
# TestEasings
# ===========================================================================

class TestEasings:
    def test_all_easings_at_zero(self):
        for name in ("linear", "easeInOutCubic", "easeOutQuint", "easeInOutSine"):
            assert _apply_ease(name, 0.0) == pytest.approx(0.0, abs=1e-10)

    def test_all_easings_at_one(self):
        for name in ("linear", "easeInOutCubic", "easeOutQuint", "easeInOutSine"):
            assert _apply_ease(name, 1.0) == pytest.approx(1.0, abs=1e-10)

    def test_linear(self):
        assert _apply_ease("linear", 0.5) == pytest.approx(0.5, abs=1e-10)

    def test_ease_in_out_cubic(self):
        # u=0.5: 3*0.25 - 2*0.125 = 0.75 - 0.25 = 0.5
        assert _apply_ease("easeInOutCubic", 0.5) == pytest.approx(0.5, abs=1e-10)
        # u=0.25: 3*0.0625 - 2*0.015625 = 0.1875 - 0.03125 = 0.15625
        assert _apply_ease("easeInOutCubic", 0.25) == pytest.approx(0.15625, abs=1e-10)

    def test_ease_out_quint_midpoint(self):
        # 1 - (1-0.5)^5 = 1 - 0.03125 = 0.96875
        assert _apply_ease("easeOutQuint", 0.5) == pytest.approx(0.96875, abs=1e-10)

    def test_ease_in_out_sine_midpoint(self):
        # -(cos(pi*0.5) - 1) / 2 = -(-1)/2 = 0.5
        assert _apply_ease("easeInOutSine", 0.5) == pytest.approx(0.5, abs=1e-10)

    def test_clamp_below_zero(self):
        assert _apply_ease("linear", -1.0) == pytest.approx(0.0)

    def test_clamp_above_one(self):
        assert _apply_ease("linear", 2.0) == pytest.approx(1.0)

    def test_unknown_easing_raises(self):
        with pytest.raises(ValueError, match="Unknown easing"):
            _apply_ease("easeInBounce", 0.5)


# ===========================================================================
# TestSeeds
# ===========================================================================

class TestSeeds:
    def test_channel_id_constants_distinct(self):
        assert len({CAMERA_NOISE, GRAIN, LIGHT_LEAKS}) == 3

    def test_camera_noise_is_zero(self):
        assert CAMERA_NOISE == 0

    def test_spawn_channel_returns_seedsequence(self):
        child = spawn_channel(42, CAMERA_NOISE)
        assert isinstance(child, np.random.SeedSequence)

    def test_spawn_channel_stable(self):
        """Same seed + channel_id → same child (positional stability)."""
        c1 = spawn_channel(1234, CAMERA_NOISE)
        c2 = spawn_channel(1234, CAMERA_NOISE)
        # Compare by generating identical RNGs
        rng1 = np.random.default_rng(c1)
        rng2 = np.random.default_rng(c2)
        v1 = rng1.random(10)
        v2 = rng2.random(10)
        np.testing.assert_array_equal(v1, v2)

    def test_different_seeds_different_channels(self):
        """Different root seeds give different child sequences."""
        c1 = spawn_channel(1, CAMERA_NOISE)
        c2 = spawn_channel(2, CAMERA_NOISE)
        rng1 = np.random.default_rng(c1)
        rng2 = np.random.default_rng(c2)
        assert not np.array_equal(rng1.random(10), rng2.random(10))

    def test_channel_ids_independent(self):
        """Different channel IDs for same seed give independent streams."""
        c0 = spawn_channel(42, CAMERA_NOISE)
        c1 = spawn_channel(42, GRAIN)
        rng0 = np.random.default_rng(c0)
        rng1 = np.random.default_rng(c1)
        assert not np.array_equal(rng0.random(10), rng1.random(10))

    def test_negative_channel_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            spawn_channel(42, -1)

    def test_make_rng_returns_generator(self):
        rng = make_rng(42, CAMERA_NOISE)
        assert isinstance(rng, np.random.Generator)

    def test_make_rng_deterministic(self):
        rng1 = make_rng(42, CAMERA_NOISE)
        rng2 = make_rng(42, CAMERA_NOISE)
        np.testing.assert_array_equal(rng1.random(20), rng2.random(20))
