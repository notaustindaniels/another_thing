"""
tests/test_autosegment.py -- Tests for parallax_engine.tools.autosegment (P4.M02).

Covers:
  - autosegment produces an RGBA PNG from a valid input image
  - Missing input file returns ok=False without raising
  - Result dict keys are correct
  - grabcut and otsu backends produce valid output
  - n_foreground_pixels is > 0 for a non-trivial image
  - Output is RGBA (4-channel PNG)
"""
from __future__ import annotations

import pathlib

import cv2
import numpy as np
import pytest
from PIL import Image

from parallax_engine.tools.autosegment import autosegment, _otsu_segment, _grabcut_segment


def _make_test_image(tmp_path: pathlib.Path, name: str = "input.png") -> pathlib.Path:
    """Write a 64x64 test image: white background with a black circle in the center."""
    img = np.full((64, 64, 3), 255, dtype=np.uint8)
    cv2.circle(img, (32, 32), 20, (0, 0, 0), -1)
    p = tmp_path / name
    cv2.imwrite(str(p), img)
    return p


class TestAutosegmentMissingFile:
    def test_missing_input_returns_error(self, tmp_path: pathlib.Path) -> None:
        result = autosegment(
            tmp_path / "nonexistent.png",
            tmp_path / "out.png",
        )
        assert result["ok"] is False
        assert "not found" in result["message"].lower() or "input" in result["message"].lower()

    def test_missing_input_no_exception(self, tmp_path: pathlib.Path) -> None:
        # Must not raise -- always returns a dict
        result = autosegment(tmp_path / "ghost.png", tmp_path / "out.png")
        assert isinstance(result, dict)


class TestAutosegmentResultShape:
    def test_result_keys_present(self, tmp_path: pathlib.Path) -> None:
        in_p = _make_test_image(tmp_path)
        result = autosegment(in_p, tmp_path / "out.png", method="otsu")
        expected = {"ok", "path", "width", "height", "n_foreground_pixels", "backend", "message"}
        assert expected <= set(result.keys())

    def test_ok_true_for_valid_image(self, tmp_path: pathlib.Path) -> None:
        in_p = _make_test_image(tmp_path)
        result = autosegment(in_p, tmp_path / "out.png", method="otsu")
        assert result["ok"] is True

    def test_output_file_exists(self, tmp_path: pathlib.Path) -> None:
        in_p = _make_test_image(tmp_path)
        out_p = tmp_path / "out.png"
        autosegment(in_p, out_p, method="otsu")
        assert out_p.exists()

    def test_output_is_rgba(self, tmp_path: pathlib.Path) -> None:
        in_p = _make_test_image(tmp_path)
        out_p = tmp_path / "out.png"
        autosegment(in_p, out_p, method="otsu")
        img = Image.open(out_p)
        assert img.mode == "RGBA", "output must be RGBA"

    def test_dimensions_match_input(self, tmp_path: pathlib.Path) -> None:
        in_p = _make_test_image(tmp_path)
        out_p = tmp_path / "out.png"
        result = autosegment(in_p, out_p, method="otsu")
        assert result["width"] == 64
        assert result["height"] == 64

    def test_n_foreground_pixels_positive(self, tmp_path: pathlib.Path) -> None:
        in_p = _make_test_image(tmp_path)
        result = autosegment(in_p, tmp_path / "out.png", method="otsu")
        assert result["n_foreground_pixels"] > 0

    def test_result_is_json_serialisable(self, tmp_path: pathlib.Path) -> None:
        import json
        in_p = _make_test_image(tmp_path)
        result = autosegment(in_p, tmp_path / "out.png", method="otsu")
        json.dumps(result)  # must not raise


class TestAutosegmentGrabcut:
    def test_grabcut_produces_output(self, tmp_path: pathlib.Path) -> None:
        in_p = _make_test_image(tmp_path)
        out_p = tmp_path / "out.png"
        result = autosegment(in_p, out_p, method="grabcut")
        assert result["ok"] is True
        assert out_p.exists()
        img = Image.open(out_p)
        assert img.mode == "RGBA"

    def test_grabcut_backend_label(self, tmp_path: pathlib.Path) -> None:
        in_p = _make_test_image(tmp_path)
        result = autosegment(in_p, tmp_path / "out.png", method="grabcut")
        # grabcut may fall back to otsu on tiny images; either is acceptable
        assert result["backend"] in ("grabcut", "otsu")


class TestAutosegmentOtsu:
    def test_otsu_backend_label(self, tmp_path: pathlib.Path) -> None:
        in_p = _make_test_image(tmp_path)
        result = autosegment(in_p, tmp_path / "out.png", method="otsu")
        assert result["backend"] == "otsu"

    def test_otsu_direct_function(self, tmp_path: pathlib.Path) -> None:
        img_bgr = cv2.imread(str(_make_test_image(tmp_path)))
        out_p = tmp_path / "direct_otsu.png"
        result = _otsu_segment(img_bgr, out_p)
        assert result["ok"] is True
        assert result["backend"] == "otsu"


class TestAutosegmentAuto:
    def test_auto_falls_back_when_rembg_missing(self, tmp_path: pathlib.Path) -> None:
        in_p = _make_test_image(tmp_path)
        result = autosegment(in_p, tmp_path / "out.png", method="auto")
        # rembg is not installed; should fall back to grabcut or otsu
        assert result["ok"] is True
        assert result["backend"] in ("rembg", "grabcut", "otsu")

    def test_parent_dirs_created(self, tmp_path: pathlib.Path) -> None:
        in_p = _make_test_image(tmp_path)
        out_p = tmp_path / "deep" / "nested" / "out.png"
        result = autosegment(in_p, out_p, method="otsu")
        assert out_p.exists()
        assert result["ok"] is True
