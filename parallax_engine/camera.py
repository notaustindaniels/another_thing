"""
parallax_engine/camera.py — Drone and keyframed camera tracks (§2.3).

Public API
----------
- SeededNoise               Vendored multi-axis value noise (replaces PyPI ``noise``)
- BezierPath                de Casteljau Bezier evaluator
- drone_camera_track        (scene, T, fps) -> (n, 6) float64
- kf_camera_track           (scene, T, fps) -> (n, 6) float64

Determinism
-----------
Both track functions produce bit-identical output for the same inputs.
No wall-clock randomness.  ``drone_camera_track`` seeds from
``scene.meta.seed`` via :data:`parallax_engine.seeds.CAMERA_NOISE`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from parallax_engine.seeds import CAMERA_NOISE, spawn_channel

if TYPE_CHECKING:
    from parallax_engine.scene import Scene


# ===========================================================================
# Vendored value noise (replaces PyPI `noise`)
# ===========================================================================

class SeededNoise:
    """
    Multi-axis 1D value noise, seeded deterministically from a
    ``numpy.random.SeedSequence`` child.

    This is the vendor-bundled replacement for the ``noise`` (MIT) PyPI
    package — see SPEC.md §5.1: *"noise or vendored simplex"*.

    Implementation
    --------------
    Each axis has an independent lookup table of ``_TABLE`` random values
    in ``[−1, 1]``.  ``sample(t, axis)`` decomposes ``t`` into integer +
    fractional parts and interpolates between adjacent table entries using
    Ken Perlin's quintic smoothstep (6t⁵ − 15t⁴ + 10t³), giving C² continuity.

    Table period
    ------------
    With ``_TABLE = 256`` cells and typical drone-camera noise inputs
    ``t_sec * hz`` (e.g. 10 s × 0.7 Hz = 7.0), the noise repeats after
    ``256 / hz`` seconds — far beyond typical scene durations.
    """

    _TABLE: int = 256

    def __init__(
        self,
        ss: np.random.SeedSequence,
        n_axes: int = 8,
    ) -> None:
        """
        Parameters
        ----------
        ss:
            A ``SeedSequence`` child (e.g. from :func:`parallax_engine.seeds.spawn_channel`).
        n_axes:
            Number of independent noise channels.  Default 8.
        """
        rng = np.random.default_rng(ss)
        # (n_axes, TABLE) float64 in [-1, 1]
        self._tables: np.ndarray = rng.uniform(
            -1.0, 1.0, (n_axes, self._TABLE)
        ).astype(np.float64)
        self._n_axes = n_axes

    def sample(self, t: float, axis: int = 0) -> float:
        """
        Return a smoothly-interpolated noise value in ``[−1, 1]``.

        Parameters
        ----------
        t:
            Input coordinate.  Integer part selects the table cell; fractional
            part drives the interpolation.  Typically ``t_sec * hz``.
        axis:
            Independent channel index (0 = x-jitter, 1 = y-jitter,
            2 = z-wobble, …).  Wraps around if ``axis >= n_axes``.
        """
        table = self._tables[int(axis) % self._n_axes]
        N = self._TABLE
        t_f = float(t)
        t_floor = np.floor(t_f)
        tf = t_f - t_floor                          # fractional part, [0, 1)
        ti = int(t_floor) % N                       # integer part, [0, TABLE)

        # Ken Perlin quintic smoothstep: 6t⁵ − 15t⁴ + 10t³  (C² continuity)
        s = tf * tf * tf * (tf * (tf * 6.0 - 15.0) + 10.0)

        a = table[ti]
        b = table[(ti + 1) % N]
        return float(a + s * (b - a))


# ===========================================================================
# Bezier path
# ===========================================================================

class BezierPath:
    """
    Bezier curve evaluated via de Casteljau's algorithm.

    Parameters
    ----------
    controls:
        Sequence of 3-element control points ``[[x, y, z], …]``.
        Length N gives a degree-(N−1) curve.
    duration_s:
        Curve duration in seconds.  ``eval(t_sec)`` maps *t_sec* to ``[0, 1]``.
    """

    def __init__(self, controls: list, duration_s: float) -> None:
        self.controls = np.asarray(controls, dtype=np.float64)
        if self.controls.ndim != 2 or self.controls.shape[1] != 3:
            raise ValueError(
                f"controls must have shape (N, 3); got {self.controls.shape}"
            )
        if len(self.controls) < 2:
            raise ValueError("BezierPath needs at least 2 control points")
        self.duration_s = float(duration_s)

    def eval(self, t_sec: float) -> np.ndarray:
        """
        Evaluate the curve at *t_sec* seconds (clamped to ``[0, duration_s]``).

        Returns a ``(3,)`` float64 array ``[x, y, z]``.
        """
        t = float(np.clip(t_sec / self.duration_s, 0.0, 1.0))
        return _de_casteljau(self.controls, t)


def _de_casteljau(pts: np.ndarray, t: float) -> np.ndarray:
    """
    Evaluate a Bezier curve at parameter ``t ∈ [0, 1]`` using de Casteljau's
    algorithm (O(n²), numerically stable, no trigonometry).
    """
    pts = pts.copy()
    n = len(pts)
    one_minus_t = 1.0 - t
    for k in range(1, n):
        pts[: n - k] = one_minus_t * pts[: n - k] + t * pts[1 : n - k + 1]
    return pts[0]


# ===========================================================================
# Easing functions for keyframed camera
# ===========================================================================

def _ease_linear(u: float) -> float:
    return u


def _ease_in_out_cubic(u: float) -> float:
    """Smoothstep: 3u² − 2u³"""
    return 3.0 * u * u - 2.0 * u * u * u


def _ease_out_quint(u: float) -> float:
    """Ease out quintic: 1 − (1−u)⁵"""
    v = 1.0 - u
    return 1.0 - v * v * v * v * v


def _ease_in_out_sine(u: float) -> float:
    """Sine ease: −(cos(π·u) − 1) / 2"""
    return -(np.cos(np.pi * u) - 1.0) / 2.0


_EASINGS: dict = {
    "linear": _ease_linear,
    "easeInOutCubic": _ease_in_out_cubic,
    "easeOutQuint": _ease_out_quint,
    "easeInOutSine": _ease_in_out_sine,
}


def _apply_ease(name: str, u: float) -> float:
    fn = _EASINGS.get(name)
    if fn is None:
        raise ValueError(
            f"Unknown easing {name!r}; allowed: {sorted(_EASINGS)}"
        )
    return fn(float(np.clip(u, 0.0, 1.0)))


# ===========================================================================
# Keyframed camera track
# ===========================================================================

def _bracket(kfs: list, t: float):
    """Return (prev, next) keyframe pair bracketing *t*."""
    if t <= kfs[0].t:
        return kfs[0], kfs[1]
    if t >= kfs[-1].t:
        return kfs[-2], kfs[-1]
    for i in range(len(kfs) - 1):
        if kfs[i].t <= t <= kfs[i + 1].t:
            return kfs[i], kfs[i + 1]
    return kfs[-2], kfs[-1]  # unreachable, but safe


def kf_camera_track(scene: "Scene", T: float, fps: int) -> np.ndarray:
    """
    Compute a keyframed camera track (§2.3).

    Parameters
    ----------
    scene:
        Parsed :class:`~parallax_engine.scene.Scene`; ``scene.camera.mode``
        must be ``"keyframed"``.
    T:
        Total duration in seconds (typically ``scene.meta.duration_s``).
    fps:
        Frames per second (typically ``scene.meta.fps``).

    Returns
    -------
    ``(n, 6)`` float64 array of ``(x, y, z, yaw, pitch, roll)`` per frame,
    where ``n = int(T * fps)``.
    """
    kfs = sorted(scene.camera.keyframed, key=lambda k: k.t)
    n = int(T * fps)
    out = np.empty((n, 6), dtype=np.float64)
    _keys = ("x", "y", "z", "yaw", "pitch", "roll")

    for i in range(n):
        t = i / float(fps)
        a, b = _bracket(kfs, t)
        dt_kf = b.t - a.t
        raw_u = (t - a.t) / max(dt_kf, 1e-9)
        u = _apply_ease(a.ease, float(np.clip(raw_u, 0.0, 1.0)))
        for j, key in enumerate(_keys):
            va = float(getattr(a, key))
            vb = float(getattr(b, key))
            out[i, j] = (1.0 - u) * va + u * vb

    return out


# ===========================================================================
# Drone camera track
# ===========================================================================

def drone_camera_track(scene: "Scene", T: float, fps: int) -> np.ndarray:
    """
    Compute a drone-FPV camera track (§2.3).

    Implements the exact Holden critically-damped spring integrator, a
    Bezier path lookahead, seeded simplex noise wobble, and roll-from-velocity
    banking.

    Parameters
    ----------
    scene:
        Parsed :class:`~parallax_engine.scene.Scene`; ``scene.camera.mode``
        must be ``"drone"``.
    T:
        Total duration in seconds.
    fps:
        Frames per second.

    Returns
    -------
    ``(n, 6)`` float64 array of ``(x, y, z, yaw, pitch, roll)`` per frame,
    where ``n = int(T * fps)``.

    Determinism
    -----------
    Seeded from ``scene.meta.seed`` via
    :data:`parallax_engine.seeds.CAMERA_NOISE`.  Bit-identical across runs.
    """
    n = int(T * fps)
    dt = 1.0 / float(fps)

    cfg = scene.camera.drone
    path = BezierPath(
        [[p[0], p[1], p[2]] for p in cfg.path.controls],
        cfg.path.duration_s,
    )
    look_dt = float(cfg.poi_lookahead_s)
    h = float(cfg.spring_halflife_s)
    k_bank = float(cfg.bank_from_velocity)
    n_amp = cfg.noise

    # --- Seeded noise (deterministic) ---
    ss_child = spawn_channel(scene.meta.seed, CAMERA_NOISE)
    noise = SeededNoise(ss_child)

    # --- Spring angular frequency ---
    # ω = ln(2) / h  →  half-life of h seconds
    omega = np.log(2.0) / h

    # --- Initial state: rig at t=0 Bezier point, velocity zero ---
    x = path.eval(0.0).copy()
    v = np.zeros(3, dtype=np.float64)

    poses = np.empty((n, 6), dtype=np.float64)

    for i in range(n):
        t = i * dt

        # Lookahead target on the Bezier path
        tgt = path.eval(min(t + look_dt, T)).copy()

        # Add noise to target
        nx = n_amp.xy_amp * noise.sample(t * n_amp.hz, axis=0)
        ny = n_amp.xy_amp * noise.sample(t * n_amp.hz, axis=1)
        nz = n_amp.z_amp  * noise.sample(t * n_amp.hz, axis=2)
        tgt_n = tgt + np.array([nx, ny, nz], dtype=np.float64)

        # --- Holden exact-discrete critically-damped spring (§2.3) ---
        # Reference: Holden (2015) "Exponential Map, Simple Spring, and More"
        e  = np.exp(-omega * dt)
        j0 = x - tgt_n
        j1 = v + j0 * omega
        x  = e * (j0 + j1 * dt) + tgt_n
        v  = e * (v - j1 * omega * dt)

        # --- Look-at: aim at a point further along the curve ---
        look_pt = path.eval(min(t + look_dt + 0.25, T))
        fwd = look_pt - x
        yaw   = np.arctan2(fwd[0], -fwd[2])
        pitch = np.arctan2(-fwd[1], np.hypot(fwd[0], fwd[2]))

        # --- Bank from lateral velocity ---
        v_lat = v[0] * np.cos(yaw) - v[2] * np.sin(yaw)
        roll  = -k_bank * np.tanh(v_lat / 200.0)

        poses[i] = (x[0], x[1], x[2], yaw, pitch, roll)

    return poses
