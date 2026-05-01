"""
parallax_engine/tools/autosegment.py -- MCP tool for background removal / segmentation.

Provides autosegment, the tool that removes the background from a raster image
and returns a PNG with an alpha channel.  The asset-generator subagent calls
this through the harness as mcp__parallax_masks__autosegment.

Implementation
--------------
Backend priority:

1. rembg (MIT licensed, uses u2net ONNX model): highest quality background
   removal.  Optional import; falls back gracefully if not installed.

2. OpenCV GrabCut (Apache 2.0): reasonable quality, no extra dependencies.
   Assumes the 10% border region is background.

3. Otsu threshold (OpenCV + numpy): simplest fallback; works best for images
   with a solid light or dark background.

Output is always an RGBA PNG with transparent background pixels.

SPEC anchors: section 3.3, section 5.1
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def autosegment(
    input_path: str | Path,
    output_path: str | Path,
    method: str = "auto",
) -> dict[str, Any]:
    """
    Remove background from an image and produce a masked RGBA PNG.

    Parameters
    ----------
    input_path:
        Path to the input image (PNG, JPEG, etc.).
    output_path:
        Path where the masked RGBA PNG will be written.
    method:
        Backend selection: "auto" (try rembg then grabcut), "rembg",
        "grabcut", or "otsu".  Default: "auto".

    Returns
    -------
    dict with keys:
        ok : bool
        path : str  (absolute path to written file)
        width, height : int
        n_foreground_pixels : int  (pixels with alpha > 128)
        backend : str  ("rembg", "grabcut", or "otsu")
        message : str
    """
    import cv2  # Apache 2.0; already in conda env

    in_p = Path(input_path).resolve()
    out_p = Path(output_path).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    if not in_p.exists():
        return {
            "ok": False,
            "path": str(out_p),
            "width": 0,
            "height": 0,
            "n_foreground_pixels": 0,
            "backend": "none",
            "message": "Input file not found: " + str(in_p),
        }

    img_bgr = cv2.imread(str(in_p), cv2.IMREAD_COLOR)
    if img_bgr is None:
        return {
            "ok": False,
            "path": str(out_p),
            "width": 0,
            "height": 0,
            "n_foreground_pixels": 0,
            "backend": "none",
            "message": "cv2 could not read image: " + str(in_p),
        }

    if method in ("auto", "rembg"):
        result = _try_rembg(in_p, out_p)
        if result["ok"]:
            return result
        if method == "rembg":
            return result  # explicit rembg request; no fallback

    if method in ("auto", "grabcut"):
        return _grabcut_segment(img_bgr, out_p)

    return _otsu_segment(img_bgr, out_p)


def _try_rembg(
    in_p: Path,
    out_p: Path,
) -> dict[str, Any]:
    """Try rembg (MIT) background removal.  Returns ok=False if unavailable."""
    try:
        from rembg import remove  # optional MIT dependency
        from PIL import Image
    except ImportError:
        return {"ok": False, "message": "rembg not installed"}

    try:
        input_data = in_p.read_bytes()
        output_data = remove(input_data)
        out_p.write_bytes(output_data)

        img = Image.open(out_p)
        w, h = img.size
        if img.mode == "RGBA":
            alpha = np.array(img)[:, :, 3]
        else:
            alpha = np.ones((h, w), dtype=np.uint8) * 255
        n_fg = int((alpha > 128).sum())

        logger.info("autosegment: rembg wrote %s (%dx%d, %d fg px)", out_p, w, h, n_fg)
        return {
            "ok": True,
            "path": str(out_p),
            "width": w,
            "height": h,
            "n_foreground_pixels": n_fg,
            "backend": "rembg",
            "message": "Background removed via rembg; " + str(n_fg) + " foreground pixels",
        }
    except Exception as exc:
        logger.warning("autosegment: rembg backend failed: %s", exc)
        return {"ok": False, "message": "rembg error: " + str(exc)}


def _grabcut_segment(
    img_bgr: np.ndarray,
    out_p: Path,
) -> dict[str, Any]:
    """
    GrabCut-based background removal (cv2, Apache 2.0).

    Assumes the 10% border margin is background; the central 80% contains the
    subject of interest.
    """
    import cv2

    h, w = img_bgr.shape[:2]
    margin_y = max(1, int(h * 0.10))
    margin_x = max(1, int(w * 0.10))
    rect = (margin_x, margin_y, w - 2 * margin_x, h - 2 * margin_y)

    mask = np.zeros((h, w), dtype=np.uint8)
    bgd_model = np.zeros((1, 65), dtype=np.float64)
    fgd_model = np.zeros((1, 65), dtype=np.float64)

    try:
        cv2.grabCut(img_bgr, mask, rect, bgd_model, fgd_model, 5, cv2.GC_INIT_WITH_RECT)
    except Exception as exc:
        logger.warning("autosegment: grabcut failed (%s), falling back to otsu", exc)
        return _otsu_segment(img_bgr, out_p)

    # GrabCut mask values: 0=BG, 1=FG, 2=PR_BG, 3=PR_FG
    fg_mask = np.where(
        (mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0
    ).astype(np.uint8)

    return _write_rgba(img_bgr, fg_mask, out_p, "grabcut")


def _otsu_segment(
    img_bgr: np.ndarray,
    out_p: Path,
) -> dict[str, Any]:
    """
    Otsu threshold on grayscale -- simplest fallback, good for solid backgrounds.
    """
    import cv2

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # If the border is mostly white in the thresholded image, the background is
    # the bright region, so invert so that foreground == 255.
    border = np.concatenate(
        [thresh[0, :], thresh[-1, :], thresh[:, 0], thresh[:, -1]]
    )
    if border.mean() > 128:
        thresh = cv2.bitwise_not(thresh)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    fg_mask = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

    return _write_rgba(img_bgr, fg_mask, out_p, "otsu")


def _write_rgba(
    img_bgr: np.ndarray,
    alpha_mask: np.ndarray,
    out_p: Path,
    backend: str,
) -> dict[str, Any]:
    """Convert a BGR image + binary mask to an RGBA PNG and write to disk."""
    from PIL import Image

    h, w = img_bgr.shape[:2]
    img_rgb = img_bgr[:, :, ::-1].copy()  # BGR -> RGB

    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, :3] = img_rgb
    rgba[:, :, 3] = alpha_mask

    Image.fromarray(rgba, mode="RGBA").save(str(out_p))

    n_fg = int((alpha_mask > 128).sum())
    logger.info("autosegment: %s wrote %s (%dx%d, %d fg px)", backend, out_p, w, h, n_fg)

    return {
        "ok": True,
        "path": str(out_p),
        "width": w,
        "height": h,
        "n_foreground_pixels": n_fg,
        "backend": backend,
        "message": "Background segmented via " + backend + "; " + str(n_fg) + " foreground pixels",
    }
