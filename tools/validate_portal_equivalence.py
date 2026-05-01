#!/usr/bin/env python3
"""
validate_portal_equivalence.py
==============================

THE PHASE 2 ARCHITECTURAL GATE.

This is the single most important correctness test in the parallax-engine
build. Phases 3, 4, 4.5, 5, and 6 cannot start until this passes. If this
script ever exits non-zero, the entire architectural premise of the
project is in question and a human must inspect.

WHAT THIS VALIDATES
-------------------

Synthetic reference equivalence: this validator generates a deterministic
test scene with known geometry, computes what each frame *should* look
like analytically (from the projection equation, mask compositing rules,
and layer ordering rules in SPEC.md §2), then compares the engine's
rendered output against the analytical answer pixel-by-pixel.

If this passes, the unified engine correctly implements:
  - The projection equation (already verified in isolation by
    validate_projection.py, but here verified end-to-end through the
    renderer pipeline)
  - Back-to-front layer compositing
  - Per-pixel alpha mask compositing (the math underlying the portal
    mechanic — see references/portal_mechanic.md)
  - The L2 trick (layers with render_pass="above_mask" render in front
    of the masked layer regardless of Z)
  - Scene YAML loading and frame export (PNG sequence or MP4)

If any of these is wrong, no further phases can proceed safely.

WHAT THIS DOES NOT VALIDATE
---------------------------

This is NOT an aesthetic check. It does not compare against any rendered
reference video. It does not check that the output looks like the drone
reference videos. It only verifies the engine's math is correct.

Aesthetic equivalence to the drone reference style is the agent's job
during Phases 3 and 4 (camera motion, atmospheric effects, asset style),
guided by references/sources/*.mp4 and references/transcripts/.
The validators for those phases (validate_camera.py, validate_roll.py)
test specific motion characteristics; they do not visually compare.

DESIGN: WHY SYNTHETIC, NOT VIDEO-VS-VIDEO
-----------------------------------------

An earlier draft compared the engine output against a rendered MP4 from
the prior CSS-3D Remotion implementation. That approach was wrong for
two reasons:

1. The prior output used different rasterization, different parallax-
   factor distribution, different camera path code. SSIM-against-prior
   would tell us "different code produces different pixels" — true by
   construction, useless as a correctness signal.

2. The prior output's overall aesthetic is something the user wants to
   move AWAY from. Forcing the new engine to chase visual equivalence
   would lock in the parts of the prior work the user explicitly rejects.

The synthetic approach is strictly stronger:
  - The "right answer" is computed from first principles (the equations
    in SPEC.md §2), not borrowed from a flawed reference.
  - Tolerances can be tight: pixel-level for solid regions, small for
    anti-aliased boundaries.
  - Reproducible: anyone can rerun without needing a specific reference
    video.
  - Tests the engine's math in isolation from aesthetic decisions.

THE TEST SCENE
--------------

The validator owns the scene. It writes a YAML file to
`workspace/synthetic_test/scene.yaml` with known geometry:

  Canvas: 640 x 360 px, 30 fps, 1.0 second (30 frames)
  Perspective: 800 px
  Camera: static at (0, 0, 0) — no motion (keeps math clean)
  Layers (back-to-front):
    L0 "back":            solid BGR (200, 80, 40), z=0, full canvas
    L1 "middle_masked":   solid BGR (40, 60, 200), z=0, full canvas,
                          with circular alpha mask: alpha=0.0 inside
                          radius 100 from (0, 0) world, alpha=1.0 outside
    L2 "front_above":     solid BGR (60, 200, 60), z=200, world rect
                          (10, -50) to (130, 50). Projected scale=1.333
                          → screen rect (333, 113) to (493, 247). This
                          covers ONLY the right half of the mask circle,
                          leaving the left half exposed so L0 shows
                          through the mask hole.
                          render_pass="above_mask"

Expected output for every frame (camera is static):
  - Outside the L2 projected rect AND outside the L1 mask circle:
      red (40, 60, 200) — L1 covering L0 (mask alpha=1.0 outside circle)
  - Outside the L2 projected rect AND inside the L1 mask circle:
      blue (200, 80, 40) — L0 visible through L1's mask hole (alpha=0.0)
                            (this proves the mask actually composited)
  - Inside the L2 projected rect (regardless of mask):
      green (60, 200, 60) — L2 paints over everything (above_mask)
                            (this proves the L2 above_mask trick works)

The validator computes this expected frame analytically and compares
against the engine's actual output. All three colors must be visible
in the frame; if any one is absent, the engine has a layer-compositing
bug.

THRESHOLDS
----------

PASS_MEAN_ABS_ERROR     = 3.0   (mean absolute per-pixel error in 0..255)
PASS_MAX_ABS_ERROR      = 80    (max single-pixel error; catches alignment bugs)
PASS_GLOBAL_SSIM        = 0.97  (much tighter than video-vs-video)

These are calibrated for the synthetic case. Solid color regions should
be near-exact; only mask-circle and rect boundaries have anti-aliasing
that pulls the error up.

INPUTS / OUTPUTS
----------------

The validator writes:
  workspace/synthetic_test/scene.yaml          (the test scene definition)
  workspace/synthetic_test/expected/frame_NNNNN.png  (analytical reference)

The agent must produce one of:
  workspace/synthetic_test/out.mp4             (preferred)
  workspace/synthetic_test/frames/frame_NNNNN.png  (acceptable fallback
                                                    if encoder not yet
                                                    implemented)

The validator reads the agent's output and compares to its own analytical
reference.

EXIT CODES
----------
    0 — equivalence verified
    1 — at least one check failed
    2 — required input missing (Phase 2 work not yet done)
    3 — internal error in the validator itself

DEPENDENCIES
------------
    - opencv-python-headless (BSD-3) for frame I/O and SSIM
    - numpy (BSD-3) for math
    - pyyaml for writing the test scene
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path


# ---------------------------------------------------------------------------
# Test scene parameters — locked
# ---------------------------------------------------------------------------

CANVAS_W = 640
CANVAS_H = 360
FPS = 30
DURATION_S = 1.0
N_FRAMES = int(FPS * DURATION_S)
PERSPECTIVE_PX = 800.0

# Layer L0 — back, full canvas, solid blue
L0_COLOR_BGR = (200, 80, 40)
L0_Z = 0.0

# Layer L1 — middle, masked. Circular alpha=0 hole inside, alpha=1 outside.
L1_COLOR_BGR = (40, 60, 200)
L1_Z = 0.0
L1_MASK_CENTER_W = (0.0, 0.0)
L1_MASK_RADIUS_W = 100.0

# Layer L2 — front, render_pass=above_mask. Green rect.
# Positioned so the mask circle is only PARTIALLY covered — the left half
# of the mask circle remains uncovered, exposing L0 through the hole.
# Without this overlap-only-partially constraint, the mask compositing
# would not be visibly tested (L2 would occlude the mask hole entirely).
L2_COLOR_BGR = (60, 200, 60)
L2_Z = 200.0
# World rect (10, -50) to (130, 50) projects (scale=4/3) to screen rect
# approximately (333, 113) to (493, 247). This covers the right side of
# the mask circle (centered at 320,180, radius 100) and extends past it
# to the right; the left side of the mask circle is uncovered and L0
# shows through.
L2_RECT_W = (10.0, -50.0, 130.0, 50.0)  # x0, y0, x1, y1

# Tolerances
PASS_MEAN_ABS_ERROR = 3.0
PASS_MAX_ABS_ERROR = 80.0
PASS_GLOBAL_SSIM = 0.97

# Paths
TEST_DIR = Path("workspace/synthetic_test")
SCENE_YAML = TEST_DIR / "scene.yaml"
EXPECTED_DIR = TEST_DIR / "expected"
ENGINE_FRAMES_DIR = TEST_DIR / "frames"
ENGINE_MP4 = TEST_DIR / "out.mp4"
EVIDENCE_DIR = Path("evidence/P2")


# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------

def _import_cv2():
    try:
        import cv2  # type: ignore
        return cv2
    except ImportError:
        print(
            "[validate_portal_equivalence] FAIL: opencv-python-headless not "
            "installed.",
            file=sys.stderr,
        )
        sys.exit(3)


def _import_np():
    try:
        import numpy as np
        return np
    except ImportError:
        print("[validate_portal_equivalence] FAIL: numpy not installed.",
              file=sys.stderr)
        sys.exit(3)


def _import_yaml():
    try:
        import yaml
        return yaml
    except ImportError:
        print("[validate_portal_equivalence] FAIL: pyyaml not installed.",
              file=sys.stderr)
        sys.exit(3)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class Check:
    __slots__ = ("name", "passed", "detail", "data")

    def __init__(self, name: str, passed: bool, detail: str,
                 data: dict | None = None) -> None:
        self.name = name
        self.passed = passed
        self.detail = detail
        self.data = data or {}

    def render(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"  {status}  {self.name}\n        {self.detail}"

    def to_json(self) -> dict:
        return {
            "name": self.name, "passed": self.passed,
            "detail": self.detail, "data": self.data,
        }


# ---------------------------------------------------------------------------
# Test scene generation
# ---------------------------------------------------------------------------

SCENE_DOCSTRING = """\
# Synthetic test scene for Phase 2 architectural validation.
# DO NOT MODIFY. The validator regenerates this file on every run.
# See tools/validate_portal_equivalence.py for the analytical contract.
"""


def write_scene_yaml(yaml, project_dir: Path) -> None:
    """Write the test scene to workspace/synthetic_test/scene.yaml.

    Field names follow the canonical schema in SPEC.md §2.2. If the
    agent's scene loader uses different field names, the agent is wrong
    (the spec is the contract); the agent must fix its loader, not this
    validator."""
    scene = {
        "schema_version": "1.0",
        "name": "synthetic_engine_test",
        "canvas": {"width": CANVAS_W, "height": CANVAS_H},
        "fps": FPS,
        "duration_s": DURATION_S,
        "perspective_px": PERSPECTIVE_PX,
        "seed": 0,
        "camera": {
            "mode": "static",
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
        },
        "layers": [
            {
                "id": "back",
                "z": L0_Z,
                "kind": "solid_rect",
                "color_bgr": list(L0_COLOR_BGR),
                "rect_world": {
                    "x0": -CANVAS_W / 2.0, "y0": -CANVAS_H / 2.0,
                    "x1":  CANVAS_W / 2.0, "y1":  CANVAS_H / 2.0,
                },
                "render_pass": "default",
            },
            {
                "id": "middle_masked",
                "z": L1_Z,
                "kind": "solid_rect",
                "color_bgr": list(L1_COLOR_BGR),
                "rect_world": {
                    "x0": -CANVAS_W / 2.0, "y0": -CANVAS_H / 2.0,
                    "x1":  CANVAS_W / 2.0, "y1":  CANVAS_H / 2.0,
                },
                "render_pass": "default",
                "mask": {
                    "kind": "circle",
                    "anchor": "world",
                    "center": {"x": L1_MASK_CENTER_W[0],
                               "y": L1_MASK_CENTER_W[1]},
                    "radius": L1_MASK_RADIUS_W,
                    "alpha_inside": 0.0,
                    "alpha_outside": 1.0,
                },
            },
            {
                "id": "front_above_mask",
                "z": L2_Z,
                "kind": "solid_rect",
                "color_bgr": list(L2_COLOR_BGR),
                "rect_world": {
                    "x0": L2_RECT_W[0], "y0": L2_RECT_W[1],
                    "x1": L2_RECT_W[2], "y1": L2_RECT_W[3],
                },
                "render_pass": "above_mask",
            },
        ],
        "encode": {"format": "mp4", "codec": "libopenh264", "crf": 18},
    }
    target = project_dir / SCENE_YAML
    target.parent.mkdir(parents=True, exist_ok=True)
    text = SCENE_DOCSTRING + yaml.safe_dump(scene, sort_keys=False, indent=2)
    target.write_text(text)


# ---------------------------------------------------------------------------
# Analytical reference frame generation
# ---------------------------------------------------------------------------

def _project_z_to_scale(z: float) -> float:
    return PERSPECTIVE_PX / (PERSPECTIVE_PX - z)


def _world_rect_to_screen(rect_w, z: float):
    s = _project_z_to_scale(z)
    cx = CANVAS_W / 2.0
    cy = CANVAS_H / 2.0
    x0_w, y0_w, x1_w, y1_w = rect_w
    return (cx + x0_w * s, cy + y0_w * s,
            cx + x1_w * s, cy + y1_w * s)


def _world_circle_to_screen(center_w, radius_w: float, z: float):
    s = _project_z_to_scale(z)
    cx = CANVAS_W / 2.0
    cy = CANVAS_H / 2.0
    return (cx + center_w[0] * s, cy + center_w[1] * s, radius_w * s)


def render_analytical_frame(np):
    """Build the expected BGR image for every frame (camera is static)."""
    img = np.full((CANVAS_H, CANVAS_W, 3), L0_COLOR_BGR, dtype=np.uint8)

    mask_cx_s, mask_cy_s, mask_r_s = _world_circle_to_screen(
        L1_MASK_CENTER_W, L1_MASK_RADIUS_W, L1_Z,
    )
    l2_x0_s, l2_y0_s, l2_x1_s, l2_y1_s = _world_rect_to_screen(L2_RECT_W, L2_Z)

    ys, xs = np.mgrid[0:CANVAS_H, 0:CANVAS_W].astype(np.float64)
    dist = np.sqrt((xs - mask_cx_s) ** 2 + (ys - mask_cy_s) ** 2)
    inside_circle = dist <= mask_r_s
    img[~inside_circle] = L1_COLOR_BGR

    x_lo = max(0, int(round(l2_x0_s)))
    y_lo = max(0, int(round(l2_y0_s)))
    x_hi = min(CANVAS_W, int(round(l2_x1_s)))
    y_hi = min(CANVAS_H, int(round(l2_y1_s)))
    img[y_lo:y_hi, x_lo:x_hi] = L2_COLOR_BGR

    return img


def write_expected_frames(np, cv2, project_dir: Path) -> None:
    target = project_dir / EXPECTED_DIR
    target.mkdir(parents=True, exist_ok=True)
    img = render_analytical_frame(np)
    for i in range(N_FRAMES):
        cv2.imwrite(str(target / f"frame_{i:05d}.png"), img)


# ---------------------------------------------------------------------------
# Engine output reading
# ---------------------------------------------------------------------------

def find_engine_frames(np, cv2, project_dir: Path):
    mp4 = project_dir / ENGINE_MP4
    fdir = project_dir / ENGINE_FRAMES_DIR

    if mp4.exists():
        cap = cv2.VideoCapture(str(mp4))
        if not cap.isOpened():
            return None
        frames = []
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            frames.append(frame)
        cap.release()
        if frames:
            return (frames, f"mp4:{mp4.name}")

    if fdir.exists():
        frames = []
        for i in range(N_FRAMES):
            f = fdir / f"frame_{i:05d}.png"
            if not f.exists():
                break
            img = cv2.imread(str(f), cv2.IMREAD_COLOR)
            if img is None:
                break
            frames.append(img)
        if frames:
            return (frames, f"png:{fdir}")

    return None


# ---------------------------------------------------------------------------
# SSIM
# ---------------------------------------------------------------------------

def _ssim(np, cv2, img1, img2) -> float:
    if img1.ndim == 3:
        img1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
    if img2.ndim == 3:
        img2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
    if img1.shape != img2.shape:
        h = min(img1.shape[0], img2.shape[0])
        w = min(img1.shape[1], img2.shape[1])
        img1 = cv2.resize(img1, (w, h), interpolation=cv2.INTER_AREA)
        img2 = cv2.resize(img2, (w, h), interpolation=cv2.INTER_AREA)
    img1 = img1.astype(np.float64)
    img2 = img2.astype(np.float64)
    K1, K2, L = 0.01, 0.03, 255.0
    C1, C2 = (K1 * L) ** 2, (K2 * L) ** 2
    g = cv2.getGaussianKernel(11, 1.5)
    window = g @ g.T
    mu1 = cv2.filter2D(img1, -1, window)
    mu2 = cv2.filter2D(img2, -1, window)
    mu1_sq, mu2_sq, mu1_mu2 = mu1 * mu1, mu2 * mu2, mu1 * mu2
    sigma1_sq = cv2.filter2D(img1 * img1, -1, window) - mu1_sq
    sigma2_sq = cv2.filter2D(img2 * img2, -1, window) - mu2_sq
    sigma12 = cv2.filter2D(img1 * img2, -1, window) - mu1_mu2
    num = (2 * mu1_mu2 + C1) * (2 * sigma12 + C2)
    den = (mu1_sq + mu2_sq + C1) * (sigma1_sq + sigma2_sq + C2)
    return float((num / den).mean())


# ---------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------

def compare_frames(np, cv2, expected, actual) -> dict:
    if actual.shape != expected.shape:
        h, w = expected.shape[:2]
        actual = cv2.resize(actual, (w, h), interpolation=cv2.INTER_AREA)
    diff = np.abs(expected.astype(np.int16) - actual.astype(np.int16))
    return {
        "mean_abs_error": float(diff.mean()),
        "max_abs_error": float(diff.max()),
        "ssim": _ssim(np, cv2, expected, actual),
    }


# ---------------------------------------------------------------------------
# Top-level checks
# ---------------------------------------------------------------------------

def check_inputs(project_dir: Path) -> Check:
    mp4 = project_dir / ENGINE_MP4
    fdir = project_dir / ENGINE_FRAMES_DIR
    if mp4.exists():
        return Check("inputs", True, f"engine output found: {mp4}")
    if fdir.exists() and any(fdir.glob("frame_*.png")):
        return Check("inputs", True,
                     f"engine output found (PNG sequence): {fdir}")
    return Check(
        "inputs", False,
        f"engine output not found. Expected one of:\n"
        f"          {mp4}\n"
        f"          {fdir}/frame_NNNNN.png\n"
        f"        Run the engine on workspace/synthetic_test/scene.yaml first.",
    )


def check_frame_count(actual_frames, label: str) -> Check:
    n = len(actual_frames)
    if n == N_FRAMES:
        return Check("frame_count", True,
                     f"engine produced {n} frames (expected {N_FRAMES}); "
                     f"source={label}")
    if abs(n - N_FRAMES) <= 1:
        return Check("frame_count", True,
                     f"engine produced {n} frames "
                     f"(expected {N_FRAMES}, ±1 tolerated); source={label}")
    return Check("frame_count", False,
                 f"engine produced {n} frames; expected {N_FRAMES}.")


def check_dimensions(actual_frames) -> Check:
    if not actual_frames:
        return Check("dimensions", False, "no frames")
    h, w = actual_frames[0].shape[:2]
    if (w, h) == (CANVAS_W, CANVAS_H):
        return Check("dimensions", True,
                     f"engine output is {w}x{h} (matches scene.yaml)")
    return Check("dimensions", False,
                 f"engine output is {w}x{h}; expected {CANVAS_W}x{CANVAS_H}")


def check_color_coverage(np, actual_frames) -> Check:
    """Verify the engine's first frame contains pixels of all three expected
    colors (within tolerance). Catches gross layer-compositing bugs that
    pixel-level SSIM might not flag — e.g., if the engine drops a layer
    entirely, SSIM stays high in the regions where remaining layers are
    correct, and coverage is the canary."""
    if not actual_frames:
        return Check("color_coverage", False, "no frames")

    frame = actual_frames[0]
    tol = 30  # per-channel tolerance for matching

    def near_color(target_bgr):
        target = np.array(target_bgr, dtype=np.int16)
        diff = np.abs(frame.astype(np.int16) - target).max(axis=2)
        return int((diff <= tol).sum())

    n_l0 = near_color(L0_COLOR_BGR)
    n_l1 = near_color(L1_COLOR_BGR)
    n_l2 = near_color(L2_COLOR_BGR)

    # Each color must have at least 100 pixels (much less than the analytical
    # counts of ~21k, ~188k, ~21k — gives margin for anti-aliasing edge cases)
    MIN_PIXELS = 100
    missing = []
    if n_l0 < MIN_PIXELS:
        missing.append(f"L0 (back, blue) only {n_l0} pixels — mask hole not visible")
    if n_l1 < MIN_PIXELS:
        missing.append(f"L1 (middle, red) only {n_l1} pixels — middle layer dropped")
    if n_l2 < MIN_PIXELS:
        missing.append(f"L2 (front, green) only {n_l2} pixels — above_mask layer dropped")

    detail = (f"first frame color counts: L0={n_l0}, L1={n_l1}, L2={n_l2} "
              f"(each must be ≥{MIN_PIXELS})")
    if missing:
        detail += "\n        " + "; ".join(missing)

    return Check(
        "color_coverage", len(missing) == 0, detail,
        {"L0_pixels": n_l0, "L1_pixels": n_l1, "L2_pixels": n_l2,
         "min_required": MIN_PIXELS},
    )


def check_pixel_equivalence(np, cv2, expected_frame, actual_frames) -> Check:
    metrics = []
    n_to_check = min(len(actual_frames), N_FRAMES)
    for i in range(n_to_check):
        metrics.append(compare_frames(np, cv2, expected_frame, actual_frames[i]))

    if not metrics:
        return Check("pixel_equivalence", False, "no frames compared")

    mean_errs = [m["mean_abs_error"] for m in metrics]
    max_errs = [m["max_abs_error"] for m in metrics]
    ssims = [m["ssim"] for m in metrics]

    overall_mean = sum(mean_errs) / len(mean_errs)
    worst_max = max(max_errs)
    ssim_min = min(ssims)
    ssim_mean = sum(ssims) / len(ssims)

    failures = []
    if overall_mean > PASS_MEAN_ABS_ERROR:
        failures.append(
            f"mean abs error {overall_mean:.2f} > {PASS_MEAN_ABS_ERROR}")
    if worst_max > PASS_MAX_ABS_ERROR:
        worst_idx = max_errs.index(worst_max)
        failures.append(
            f"max abs error {worst_max:.0f} > {PASS_MAX_ABS_ERROR} "
            f"(frame {worst_idx})")
    if ssim_min < PASS_GLOBAL_SSIM:
        worst_idx = ssims.index(ssim_min)
        failures.append(
            f"min SSIM {ssim_min:.4f} < {PASS_GLOBAL_SSIM} "
            f"(frame {worst_idx})")

    detail = (
        f"compared {n_to_check} frames | "
        f"mean_abs_err={overall_mean:.2f} (≤{PASS_MEAN_ABS_ERROR}) | "
        f"max_abs_err={worst_max:.0f} (≤{PASS_MAX_ABS_ERROR}) | "
        f"SSIM mean={ssim_mean:.4f}, min={ssim_min:.4f} (≥{PASS_GLOBAL_SSIM})"
    )
    if failures:
        detail += "\n        failures: " + "; ".join(failures)

    return Check(
        "pixel_equivalence", len(failures) == 0, detail,
        {
            "n_frames_compared": n_to_check,
            "mean_abs_error_overall": overall_mean,
            "max_abs_error_worst": worst_max,
            "ssim_min": ssim_min,
            "ssim_mean": ssim_mean,
            "thresholds": {
                "mean_abs_error": PASS_MEAN_ABS_ERROR,
                "max_abs_error": PASS_MAX_ABS_ERROR,
                "global_ssim": PASS_GLOBAL_SSIM,
            },
        },
    )


def check_determinism(np, cv2, project_dir: Path) -> Check:
    mp4_redo = project_dir / TEST_DIR / "out_redo.mp4"
    fdir_redo = project_dir / TEST_DIR / "frames_redo"

    redo_frames = None
    if mp4_redo.exists():
        cap = cv2.VideoCapture(str(mp4_redo))
        if cap.isOpened():
            frames = []
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                frames.append(frame)
            cap.release()
            if frames:
                redo_frames = frames
    elif fdir_redo.exists():
        frames = []
        for i in range(N_FRAMES):
            f = fdir_redo / f"frame_{i:05d}.png"
            if not f.exists():
                break
            img = cv2.imread(str(f), cv2.IMREAD_COLOR)
            if img is None:
                break
            frames.append(img)
        if frames:
            redo_frames = frames

    if redo_frames is None:
        return Check(
            "determinism", True,
            "skipped — no second render at out_redo.mp4 or frames_redo/. "
            "To enable, render twice with the same seed.",
            {"skipped": True},
        )

    primary = find_engine_frames(np, cv2, project_dir)
    if primary is None:
        return Check("determinism", False, "primary render missing")

    primary_frames, _ = primary
    if len(redo_frames) != len(primary_frames):
        return Check("determinism", False,
                     f"counts differ: {len(primary_frames)} vs {len(redo_frames)}")

    diffs = []
    for f1, f2 in zip(primary_frames, redo_frames):
        if f1.shape != f2.shape:
            diffs.append(255.0)
        else:
            diffs.append(float(np.abs(f1.astype(np.int16) -
                                      f2.astype(np.int16)).mean()))

    max_diff = max(diffs) if diffs else 0.0
    mean_diff = sum(diffs) / len(diffs) if diffs else 0.0
    passed = max_diff < 1.0
    return Check(
        "determinism", passed,
        f"compared {len(diffs)} frames; "
        f"max mean-abs-diff={max_diff:.4f} (<1.0), "
        f"mean mean-abs-diff={mean_diff:.4f}",
        {"max": max_diff, "mean": mean_diff},
    )


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def write_evidence(checks, project_dir: Path) -> None:
    try:
        ed = project_dir / EVIDENCE_DIR
        ed.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%S")
        out = ed / f"validate_portal_equivalence_{ts}.json"
        out.write_text(json.dumps({
            "validator": "validate_portal_equivalence",
            "timestamp": ts,
            "all_passed": all(c.passed for c in checks),
            "scene_parameters": {
                "canvas": [CANVAS_W, CANVAS_H],
                "fps": FPS,
                "duration_s": DURATION_S,
                "n_frames": N_FRAMES,
                "perspective_px": PERSPECTIVE_PX,
            },
            "thresholds": {
                "mean_abs_error": PASS_MEAN_ABS_ERROR,
                "max_abs_error": PASS_MAX_ABS_ERROR,
                "global_ssim": PASS_GLOBAL_SSIM,
            },
            "checks": [c.to_json() for c in checks],
        }, indent=2))
        (ed / "latest.json").write_text(out.read_text())
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    project_dir = Path(os.environ.get("PARALLAX_PROJECT_DIR", ".")).resolve()
    print(f"[validate_portal_equivalence] project: {project_dir}")
    print("[validate_portal_equivalence] mode: synthetic reference equivalence")
    print()

    yaml = _import_yaml()
    np = _import_np()
    cv2 = _import_cv2()

    try:
        write_scene_yaml(yaml, project_dir)
        write_expected_frames(np, cv2, project_dir)
    except OSError as e:
        print(f"[validate_portal_equivalence] FAIL: {e}", file=sys.stderr)
        return 3

    print(f"[validate_portal_equivalence] wrote test scene to {SCENE_YAML}")
    print(f"[validate_portal_equivalence] wrote {N_FRAMES} expected frames to {EXPECTED_DIR}")
    print()

    checks = []

    c = check_inputs(project_dir)
    checks.append(c)
    print(c.render())
    if not c.passed:
        print()
        print("[validate_portal_equivalence] Phase 2 work not yet done.")
        write_evidence(checks, project_dir)
        return 2

    output = find_engine_frames(np, cv2, project_dir)
    if output is None:
        c = Check("engine_output_readable", False,
                  "engine output exists but could not be read")
        checks.append(c)
        print(c.render())
        write_evidence(checks, project_dir)
        return 1
    actual_frames, label = output

    for c in (
        check_frame_count(actual_frames, label),
        check_dimensions(actual_frames),
        check_color_coverage(np, actual_frames),
        check_pixel_equivalence(np, cv2, render_analytical_frame(np), actual_frames),
        check_determinism(np, cv2, project_dir),
    ):
        checks.append(c)
        print(c.render())

    print()
    n_passed = sum(1 for c in checks if c.passed)
    print(f"[validate_portal_equivalence] {n_passed}/{len(checks)} checks passed")
    write_evidence(checks, project_dir)
    return 0 if all(c.passed for c in checks) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(3)
