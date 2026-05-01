"""
parallax_engine/tools/qa.py -- MCP tool wrappers for QA comparisons.

Provides:
  ``diff_frames(dir_a, dir_b)``  -- per-frame max absolute pixel difference
  ``ssim_score(dir_a, dir_b)``   -- mean SSIM across frame pairs

The qa-critic subagent calls these through the harness as
``mcp__parallax_qa__diff_frames`` and ``mcp__parallax_qa__ssim_score``
(ss3.3).

Implementation notes
--------------------
- SSIM uses a Gaussian-windowed formula matching the one in
  tools/validate_portal_equivalence.py (11x11 window, sigma=1.5,
  K1=0.01, K2=0.03) to guarantee consistent scores across the codebase.
- Implemented with cv2 + numpy only (no skimage dependency).
- Both functions return plain JSON-serialisable dicts.
- Frame pairs are matched by filename (``frame_NNNNN.png``); unmatched
  files are silently skipped.

SPEC anchors: ss3.1, ss3.3
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2  # type: ignore
import numpy as np


# ---------------------------------------------------------------------------
# Internal SSIM helper
# ---------------------------------------------------------------------------

def _ssim_grayscale(img1: np.ndarray, img2: np.ndarray) -> float:
    """
    Gaussian-windowed SSIM for two uint8 grayscale images.

    Uses the same formula as ``tools/validate_portal_equivalence.py``
    so scores are comparable across the codebase:
      window = 11x11 Gaussian, sigma=1.5
      K1=0.01, K2=0.03, L=255
    """
    f1 = img1.astype(np.float64)
    f2 = img2.astype(np.float64)
    K1, K2, L = 0.01, 0.03, 255.0
    C1, C2 = (K1 * L) ** 2, (K2 * L) ** 2
    g = cv2.getGaussianKernel(11, 1.5)
    window = g @ g.T
    mu1 = cv2.filter2D(f1, -1, window)
    mu2 = cv2.filter2D(f2, -1, window)
    mu1_sq = mu1 * mu1
    mu2_sq = mu2 * mu2
    mu1_mu2 = mu1 * mu2
    sigma1_sq = cv2.filter2D(f1 * f1, -1, window) - mu1_sq
    sigma2_sq = cv2.filter2D(f2 * f2, -1, window) - mu2_sq
    sigma12 = cv2.filter2D(f1 * f2, -1, window) - mu1_mu2
    num = (2.0 * mu1_mu2 + C1) * (2.0 * sigma12 + C2)
    den = (mu1_sq + mu2_sq + C1) * (sigma1_sq + sigma2_sq + C2)
    return float((num / den).mean())


def _load_pair_gray(
    path_a: Path, path_b: Path, target_hw: tuple[int, int] | None
) -> tuple[np.ndarray, np.ndarray] | None:
    """Load two BGR images, convert to grayscale, resize to target if needed."""
    img_a = cv2.imread(str(path_a), cv2.IMREAD_COLOR)
    img_b = cv2.imread(str(path_b), cv2.IMREAD_COLOR)
    if img_a is None or img_b is None:
        return None
    if img_a.shape != img_b.shape:
        h = min(img_a.shape[0], img_b.shape[0])
        w = min(img_a.shape[1], img_b.shape[1])
        img_a = cv2.resize(img_a, (w, h), interpolation=cv2.INTER_AREA)
        img_b = cv2.resize(img_b, (w, h), interpolation=cv2.INTER_AREA)
    elif target_hw is not None and img_a.shape[:2] != target_hw:
        h, w = target_hw
        img_a = cv2.resize(img_a, (w, h), interpolation=cv2.INTER_AREA)
        img_b = cv2.resize(img_b, (w, h), interpolation=cv2.INTER_AREA)
    gray_a = cv2.cvtColor(img_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(img_b, cv2.COLOR_BGR2GRAY)
    return gray_a, gray_b


def _load_pair_bgr(
    path_a: Path, path_b: Path
) -> tuple[np.ndarray, np.ndarray] | None:
    """Load two BGR images, resize b to match a if shapes differ."""
    img_a = cv2.imread(str(path_a), cv2.IMREAD_COLOR)
    img_b = cv2.imread(str(path_b), cv2.IMREAD_COLOR)
    if img_a is None or img_b is None:
        return None
    if img_a.shape != img_b.shape:
        h, w = img_a.shape[:2]
        img_b = cv2.resize(img_b, (w, h), interpolation=cv2.INTER_AREA)
    return img_a, img_b


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

def diff_frames(
    dir_a: str | Path,
    dir_b: str | Path,
) -> dict[str, Any]:
    """
    Compare two frame directories, returning per-frame pixel differences.

    Frames are matched by filename (``frame_NNNNN.png``).  Files present in
    ``dir_a`` but absent from ``dir_b`` (or vice versa) are skipped.

    Parameters
    ----------
    dir_a, dir_b:
        Directories containing ``frame_NNNNN.png`` sequences.

    Returns
    -------
    dict with keys:

    n_frames : int
        Number of frame pairs compared.
    overall_max : float
        Maximum single-pixel absolute difference across all frames (0..255).
    overall_mean : float
        Mean of per-frame mean absolute differences.
    frames : list[dict]
        Per-frame records: ``{file, max_abs_diff, mean_abs_diff}``.
    """
    dir_a = Path(dir_a)
    dir_b = Path(dir_b)

    frames_a = sorted(dir_a.glob("frame_*.png"))
    records: list[dict[str, Any]] = []

    for fa in frames_a:
        fb = dir_b / fa.name
        if not fb.exists():
            continue
        pair = _load_pair_bgr(fa, fb)
        if pair is None:
            continue
        img_a, img_b = pair
        diff = np.abs(img_a.astype(np.int16) - img_b.astype(np.int16))
        records.append(
            {
                "file": fa.name,
                "max_abs_diff": float(diff.max()),
                "mean_abs_diff": float(diff.mean()),
            }
        )

    if not records:
        return {
            "n_frames": 0,
            "overall_max": 0.0,
            "overall_mean": 0.0,
            "frames": [],
        }

    overall_max = float(max(r["max_abs_diff"] for r in records))
    overall_mean = float(
        sum(r["mean_abs_diff"] for r in records) / len(records)
    )
    return {
        "n_frames": len(records),
        "overall_max": overall_max,
        "overall_mean": overall_mean,
        "frames": records,
    }


def ssim_score(
    dir_a: str | Path,
    dir_b: str | Path,
) -> dict[str, Any]:
    """
    Compute mean SSIM across matched frame pairs in two directories.

    Uses a Gaussian-windowed SSIM (11x11, sigma=1.5) on grayscale
    conversions of each frame pair, matching the formula used in
    ``tools/validate_portal_equivalence.py``.

    Parameters
    ----------
    dir_a, dir_b:
        Directories containing ``frame_NNNNN.png`` sequences.

    Returns
    -------
    dict with keys:

    n_frames : int
        Number of frame pairs compared.
    mean_ssim : float
        Mean SSIM across all pairs (0..1; 1 = identical).
    min_ssim : float
        Minimum SSIM across all pairs.
    frames : list[dict]
        Per-frame records: ``{file, ssim}``.
    """
    dir_a = Path(dir_a)
    dir_b = Path(dir_b)

    frames_a = sorted(dir_a.glob("frame_*.png"))
    records: list[dict[str, Any]] = []

    for fa in frames_a:
        fb = dir_b / fa.name
        if not fb.exists():
            continue
        pair = _load_pair_gray(fa, fb, target_hw=None)
        if pair is None:
            continue
        gray_a, gray_b = pair
        s = _ssim_grayscale(gray_a, gray_b)
        records.append({"file": fa.name, "ssim": s})

    if not records:
        return {
            "n_frames": 0,
            "mean_ssim": 0.0,
            "min_ssim": 0.0,
            "frames": [],
        }

    ssims = [r["ssim"] for r in records]
    return {
        "n_frames": len(records),
        "mean_ssim": float(sum(ssims) / len(ssims)),
        "min_ssim": float(min(ssims)),
        "frames": records,
    }
