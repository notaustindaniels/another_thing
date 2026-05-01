#!/usr/bin/env python3
"""
validate_projection.py
======================

Phase 1 acceptance gate for the parallax-engine projection core.

This script verifies that `parallax_engine.projection.project_points` correctly
implements the canonical projection equation from SPEC.md §2:

    s         = perspective_px / (perspective_px - z_cam)
    X_screen  = O_x + x_centered * s
    Y_screen  = O_y + y_centered * s

where (O_x, O_y) is the canvas center, (x_centered, y_centered) are world
coordinates relative to that center, and z_cam is the camera-frame z (i.e.,
the value AFTER any world-to-camera transformation has been applied).

This script DOES NOT test world-to-camera transformations, camera roll,
or per-layer parallax factor scaling. Those have their own validators
(validate_camera.py, validate_roll.py, validate_parallax.py).

API CONTRACT (locked — the implementation must match):

    parallax_engine.projection.project_points(
        x_centered: np.ndarray,    # shape (N,), float
        y_centered: np.ndarray,    # shape (N,), float
        z_cam:      np.ndarray,    # shape (N,), float
        perspective_px: float,     # > 0
        canvas_width:  int,        # > 0
        canvas_height: int,        # > 0
    ) -> tuple[np.ndarray, np.ndarray]
        # returns (x_screen, y_screen), each shape (N,), same dtype as input

Behavior at z_cam = perspective_px (singularity) is implementation-defined,
but must be deterministic and must not return zero or other plausible-looking
finite numbers. Acceptable handling: return +inf, return NaN, or raise a
documented exception. The test verifies that whatever the implementation
does, it is consistent and detectable.

Behavior for z_cam > perspective_px (point behind viewer) must produce s < 0,
which the renderer's culling layer is responsible for filtering. This script
verifies the math; it does not verify culling.

EXIT CODES:
    0 — all tests passed
    1 — at least one test failed
    2 — could not import parallax_engine.projection (Phase 1 not yet implemented)
    3 — internal error in the validator itself
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PERSPECTIVE_PX = 800.0   # canonical CSS-equivalent perspective distance
CANVAS_W = 1920          # default test canvas
CANVAS_H = 1080
ABS_TOL = 1e-6           # absolute tolerance for analytical-comparison tests
PERF_THRESHOLD_MS = 50.0 # 100k points must project in under this
PERF_N = 100_000

EVIDENCE_DIR = Path("evidence/P1.M02")  # this validator gates milestone P1.M02


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------

class TestFailure(AssertionError):
    """Raised by individual tests on failure."""


def _import_target():
    """Import the function under test. Exit with code 2 if not yet implemented."""
    try:
        mod = importlib.import_module("parallax_engine.projection")
    except ImportError as e:
        print(
            f"[validate_projection] FAIL: cannot import parallax_engine.projection: {e}\n"
            f"[validate_projection] Phase 1 milestone P1.M02 not yet implemented.",
            file=sys.stderr,
        )
        sys.exit(2)

    if not hasattr(mod, "project_points"):
        print(
            "[validate_projection] FAIL: parallax_engine.projection has no attribute "
            "'project_points'. The API contract requires this function name.",
            file=sys.stderr,
        )
        sys.exit(2)

    return mod.project_points


def _np():
    """Lazy numpy import with a clearer error than the default."""
    try:
        import numpy as np
        return np
    except ImportError:
        print(
            "[validate_projection] FAIL: numpy is not installed in the active environment.",
            file=sys.stderr,
        )
        sys.exit(3)


# ---------------------------------------------------------------------------
# Individual tests
# ---------------------------------------------------------------------------

def test_identity_z_zero(project, np) -> None:
    """At z_cam = 0, s = 1 and screen = origin + centered, exactly."""
    x_c = np.array([0.0, 100.0, -250.0, 480.0], dtype=np.float64)
    y_c = np.array([0.0,  50.0,  120.0, -360.0], dtype=np.float64)
    z   = np.zeros_like(x_c)

    xs, ys = project(x_c, y_c, z, PERSPECTIVE_PX, CANVAS_W, CANVAS_H)

    expected_x = x_c + CANVAS_W / 2.0
    expected_y = y_c + CANVAS_H / 2.0

    if not np.allclose(xs, expected_x, atol=ABS_TOL):
        raise TestFailure(f"identity x mismatch: got {xs}, expected {expected_x}")
    if not np.allclose(ys, expected_y, atol=ABS_TOL):
        raise TestFailure(f"identity y mismatch: got {ys}, expected {expected_y}")


def test_magnification_positive_z(project, np) -> None:
    """At z_cam = p/2, s = 2; offsets from center should double."""
    x_c = np.array([100.0, -50.0, 0.0], dtype=np.float64)
    y_c = np.array([200.0,  75.0, 0.0], dtype=np.float64)
    z   = np.full_like(x_c, PERSPECTIVE_PX / 2.0)

    xs, ys = project(x_c, y_c, z, PERSPECTIVE_PX, CANVAS_W, CANVAS_H)

    s = 2.0
    expected_x = CANVAS_W / 2.0 + x_c * s
    expected_y = CANVAS_H / 2.0 + y_c * s

    if not np.allclose(xs, expected_x, atol=ABS_TOL):
        raise TestFailure(f"magnification x mismatch: got {xs}, expected {expected_x}")
    if not np.allclose(ys, expected_y, atol=ABS_TOL):
        raise TestFailure(f"magnification y mismatch: got {ys}, expected {expected_y}")


def test_minification_negative_z(project, np) -> None:
    """At z_cam = -p, s = 0.5; offsets from center should halve."""
    x_c = np.array([400.0, -200.0, 800.0], dtype=np.float64)
    y_c = np.array([100.0,  300.0, -150.0], dtype=np.float64)
    z   = np.full_like(x_c, -PERSPECTIVE_PX)

    xs, ys = project(x_c, y_c, z, PERSPECTIVE_PX, CANVAS_W, CANVAS_H)

    s = 0.5
    expected_x = CANVAS_W / 2.0 + x_c * s
    expected_y = CANVAS_H / 2.0 + y_c * s

    if not np.allclose(xs, expected_x, atol=ABS_TOL):
        raise TestFailure(f"minification x mismatch: got {xs}, expected {expected_x}")
    if not np.allclose(ys, expected_y, atol=ABS_TOL):
        raise TestFailure(f"minification y mismatch: got {ys}, expected {expected_y}")


def test_origin_point_invariant(project, np) -> None:
    """A point at (0, 0) must map to canvas center for any finite, non-singular z."""
    x_c = np.zeros(7, dtype=np.float64)
    y_c = np.zeros(7, dtype=np.float64)
    # Use a spread of z values that avoid the singularity at z = perspective_px
    z = np.array([
        -2 * PERSPECTIVE_PX,
        -PERSPECTIVE_PX,
        -1.0,
        0.0,
        1.0,
        PERSPECTIVE_PX / 2.0,
        PERSPECTIVE_PX * 0.99,
    ], dtype=np.float64)

    xs, ys = project(x_c, y_c, z, PERSPECTIVE_PX, CANVAS_W, CANVAS_H)

    cx = CANVAS_W / 2.0
    cy = CANVAS_H / 2.0
    if not np.allclose(xs, cx, atol=ABS_TOL):
        raise TestFailure(f"origin x not invariant under z: got {xs}, expected {cx}")
    if not np.allclose(ys, cy, atol=ABS_TOL):
        raise TestFailure(f"origin y not invariant under z: got {ys}, expected {cy}")


def test_canonical_formula_random(project, np) -> None:
    """1000 random points: vectorized output must match scalar formula point-by-point."""
    rng = np.random.default_rng(seed=0xCAFEBABE)
    n = 1000

    x_c = rng.uniform(-2000.0, 2000.0, size=n)
    y_c = rng.uniform(-2000.0, 2000.0, size=n)
    # Avoid the singularity neighborhood
    z   = rng.uniform(-PERSPECTIVE_PX * 4, PERSPECTIVE_PX - 1.0, size=n)

    xs, ys = project(x_c, y_c, z, PERSPECTIVE_PX, CANVAS_W, CANVAS_H)

    s = PERSPECTIVE_PX / (PERSPECTIVE_PX - z)
    expected_x = CANVAS_W / 2.0 + x_c * s
    expected_y = CANVAS_H / 2.0 + y_c * s

    if not np.allclose(xs, expected_x, atol=ABS_TOL, rtol=1e-9):
        max_err = float(np.max(np.abs(xs - expected_x)))
        raise TestFailure(f"random x mismatch: max abs error {max_err}")
    if not np.allclose(ys, expected_y, atol=ABS_TOL, rtol=1e-9):
        max_err = float(np.max(np.abs(ys - expected_y)))
        raise TestFailure(f"random y mismatch: max abs error {max_err}")


def test_singularity_is_detectable(project, np) -> None:
    """At z = perspective_px, the result must be detectable as non-finite or
    the function must raise. The choice is the implementation's, but it must
    be deterministic and not return finite plausible numbers."""
    x_c = np.array([100.0], dtype=np.float64)
    y_c = np.array([50.0], dtype=np.float64)
    z   = np.array([PERSPECTIVE_PX], dtype=np.float64)

    try:
        xs, ys = project(x_c, y_c, z, PERSPECTIVE_PX, CANVAS_W, CANVAS_H)
    except (ZeroDivisionError, FloatingPointError, ValueError) as e:
        # Acceptable: the implementation chose to raise.
        return

    # Did NOT raise — outputs must be non-finite (inf or nan).
    if np.all(np.isfinite(xs)) and np.all(np.isfinite(ys)):
        raise TestFailure(
            f"singularity at z={PERSPECTIVE_PX} returned finite values "
            f"x={xs}, y={ys}; expected inf/nan or raised exception"
        )


def test_behind_viewer_negative_scale(project, np) -> None:
    """For z_cam > perspective_px (behind viewer), scale s must be negative.
    The renderer's culling layer is responsible for filtering; we only verify
    the math is consistent."""
    x_c = np.array([100.0, 200.0, -50.0], dtype=np.float64)
    y_c = np.array([100.0, -200.0, 50.0], dtype=np.float64)
    z   = np.array([PERSPECTIVE_PX * 1.5, PERSPECTIVE_PX * 2.0, PERSPECTIVE_PX * 10.0],
                   dtype=np.float64)

    xs, ys = project(x_c, y_c, z, PERSPECTIVE_PX, CANVAS_W, CANVAS_H)

    cx, cy = CANVAS_W / 2.0, CANVAS_H / 2.0
    # When s < 0, (xs - cx) and x_c should have opposite signs (assuming x_c != 0)
    for i in range(len(x_c)):
        x_off = x_c[i]
        screen_off = xs[i] - cx
        if x_off != 0.0 and not np.isnan(screen_off):
            if (x_off > 0) == (screen_off > 0):
                raise TestFailure(
                    f"behind-viewer at z={z[i]}: x_offset={x_off}, "
                    f"screen_offset={screen_off}; signs should differ "
                    f"(s should be negative)"
                )


def test_vectorized_performance(project, np) -> None:
    """100k points must project in under 50ms — the function MUST be vectorized."""
    rng = np.random.default_rng(seed=0xDEADBEEF)
    x_c = rng.uniform(-2000.0, 2000.0, size=PERF_N)
    y_c = rng.uniform(-2000.0, 2000.0, size=PERF_N)
    z   = rng.uniform(-PERSPECTIVE_PX * 4, PERSPECTIVE_PX - 1.0, size=PERF_N)

    # Warm up (one call to JIT/cache anything that needs warming)
    project(x_c[:1000], y_c[:1000], z[:1000], PERSPECTIVE_PX, CANVAS_W, CANVAS_H)

    t0 = time.perf_counter()
    project(x_c, y_c, z, PERSPECTIVE_PX, CANVAS_W, CANVAS_H)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    if elapsed_ms > PERF_THRESHOLD_MS:
        raise TestFailure(
            f"performance: {PERF_N:,} points took {elapsed_ms:.1f}ms, "
            f"threshold is {PERF_THRESHOLD_MS}ms. "
            f"Implementation is likely not vectorized — no Python loops over points."
        )


def test_determinism_byte_identical(project, np) -> None:
    """Same input must produce byte-identical output across runs."""
    rng = np.random.default_rng(seed=0xABCDEF)
    x_c = rng.uniform(-2000.0, 2000.0, size=500)
    y_c = rng.uniform(-2000.0, 2000.0, size=500)
    z   = rng.uniform(-PERSPECTIVE_PX * 2, PERSPECTIVE_PX - 1.0, size=500)

    xs1, ys1 = project(x_c, y_c, z, PERSPECTIVE_PX, CANVAS_W, CANVAS_H)
    xs2, ys2 = project(x_c, y_c, z, PERSPECTIVE_PX, CANVAS_W, CANVAS_H)

    if xs1.tobytes() != xs2.tobytes():
        raise TestFailure("determinism: x output bytes differ across two identical calls")
    if ys1.tobytes() != ys2.tobytes():
        raise TestFailure("determinism: y output bytes differ across two identical calls")


def test_dtype_preservation(project, np) -> None:
    """float32 input must yield float32 output; float64 must yield float64."""
    for dtype in (np.float32, np.float64):
        x_c = np.array([100.0, 0.0, -50.0], dtype=dtype)
        y_c = np.array([50.0, 0.0, 25.0], dtype=dtype)
        z   = np.array([0.0, 100.0, -200.0], dtype=dtype)

        xs, ys = project(x_c, y_c, z, PERSPECTIVE_PX, CANVAS_W, CANVAS_H)

        if xs.dtype != dtype:
            raise TestFailure(
                f"dtype: input was {dtype}, x output is {xs.dtype}"
            )
        if ys.dtype != dtype:
            raise TestFailure(
                f"dtype: input was {dtype}, y output is {ys.dtype}"
            )


def test_input_shape_validation(project, np) -> None:
    """Mismatched input shapes should raise, not silently broadcast or truncate."""
    x_c = np.zeros(5, dtype=np.float64)
    y_c = np.zeros(3, dtype=np.float64)  # wrong length
    z   = np.zeros(5, dtype=np.float64)

    try:
        project(x_c, y_c, z, PERSPECTIVE_PX, CANVAS_W, CANVAS_H)
    except (ValueError, AssertionError):
        return  # expected
    raise TestFailure(
        "shape validation: function accepted mismatched input shapes silently"
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS: list[tuple[str, Callable]] = [
    ("identity_z_zero",            test_identity_z_zero),
    ("magnification_positive_z",   test_magnification_positive_z),
    ("minification_negative_z",    test_minification_negative_z),
    ("origin_point_invariant",     test_origin_point_invariant),
    ("canonical_formula_random",   test_canonical_formula_random),
    ("singularity_is_detectable",  test_singularity_is_detectable),
    ("behind_viewer_negative_scale", test_behind_viewer_negative_scale),
    ("vectorized_performance",     test_vectorized_performance),
    ("determinism_byte_identical", test_determinism_byte_identical),
    ("dtype_preservation",         test_dtype_preservation),
    ("input_shape_validation",     test_input_shape_validation),
]


def _write_evidence(results: list[dict]) -> None:
    """Write evidence JSON for the harness to confirm validation ran."""
    try:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        out = EVIDENCE_DIR / "validate_projection.json"
        out.write_text(json.dumps({
            "validator": "validate_projection",
            "perspective_px": PERSPECTIVE_PX,
            "canvas": [CANVAS_W, CANVAS_H],
            "results": results,
            "all_passed": all(r["passed"] for r in results),
        }, indent=2))
    except OSError:
        # Do not fail the validator because evidence couldn't be written;
        # the harness will notice the missing file separately.
        pass


def main() -> int:
    project = _import_target()
    np = _np()

    print(f"[validate_projection] running {len(TESTS)} tests")
    print(f"[validate_projection] perspective_px={PERSPECTIVE_PX} canvas={CANVAS_W}x{CANVAS_H}")
    print()

    results: list[dict] = []
    for name, fn in TESTS:
        t0 = time.perf_counter()
        try:
            fn(project, np)
            ms = (time.perf_counter() - t0) * 1000.0
            print(f"  PASS  {name}  ({ms:.1f}ms)")
            results.append({"name": name, "passed": True, "elapsed_ms": ms})
        except TestFailure as e:
            ms = (time.perf_counter() - t0) * 1000.0
            print(f"  FAIL  {name}  ({ms:.1f}ms)")
            print(f"        {e}")
            results.append({
                "name": name, "passed": False, "elapsed_ms": ms, "reason": str(e),
            })
        except Exception as e:
            # Unexpected failure inside a test — implementation likely raised
            # something we did not explicitly handle. Treat as a failure.
            ms = (time.perf_counter() - t0) * 1000.0
            print(f"  FAIL  {name}  ({ms:.1f}ms)  unexpected exception:")
            print(f"        {type(e).__name__}: {e}")
            print("        " + "\n        ".join(traceback.format_exc().splitlines()[-6:]))
            results.append({
                "name": name, "passed": False, "elapsed_ms": ms,
                "reason": f"unexpected {type(e).__name__}: {e}",
            })

    _write_evidence(results)

    n_passed = sum(1 for r in results if r["passed"])
    n_total = len(results)
    print()
    print(f"[validate_projection] {n_passed}/{n_total} tests passed")
    return 0 if n_passed == n_total else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(3)
