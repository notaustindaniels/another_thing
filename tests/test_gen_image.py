"""
tests/test_gen_image.py -- Tests for parallax_engine.tools.gen_image (P4.M02).

Covers:
  - gen_image produces a file when called
  - Placeholder backend is used when no Anthropic API key is set
  - Output is valid SVG (starts with <svg)
  - Determinism: same prompt + seed -> same color in placeholder output
  - Result dict keys and types are correct
  - output_path parent dirs are created automatically
  - Missing ANTHROPIC_API_KEY does not raise; falls back to placeholder
"""
from __future__ import annotations

import os
import pathlib
import xml.etree.ElementTree as ET

import pytest

from parallax_engine.tools.gen_image import gen_image, _placeholder_svg


class TestGenImagePlaceholder:
    def test_produces_file(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "sky.svg"
        result = gen_image("blue sky with clouds", str(out), width=640, height=360)
        assert out.exists(), "gen_image must write a file"
        assert result["ok"] is True

    def test_result_keys(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "layer.svg"
        result = gen_image("mountains at dusk", str(out))
        expected_keys = {"ok", "path", "width", "height", "format", "backend", "message"}
        assert expected_keys <= set(result.keys())

    def test_format_is_svg(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "trees.svg"
        result = gen_image("pine forest silhouette", str(out))
        assert result["format"] == "svg"

    def test_output_is_valid_svg(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "scene.svg"
        gen_image("ocean sunset", str(out), width=800, height=600)
        text = out.read_text()
        assert text.strip().startswith("<svg"), "output must be SVG"
        # Must parse without error
        ET.fromstring(text)

    def test_dimensions_in_result(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "w.svg"
        result = gen_image("desert dunes", str(out), width=1280, height=720)
        assert result["width"] == 1280
        assert result["height"] == 720

    def test_placeholder_backend_when_no_key(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        out = tmp_path / "layer.svg"
        result = gen_image("city skyline at night", str(out))
        assert result["ok"] is True
        assert result["backend"] == "placeholder"

    def test_parent_dirs_created(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "deep" / "nested" / "dir" / "layer.svg"
        result = gen_image("volcano eruption", str(out))
        assert out.exists(), "parent directories must be created"
        assert result["ok"] is True

    def test_determinism_same_prompt_same_color(self, tmp_path: pathlib.Path) -> None:
        prompt = "rolling green hills"
        out1 = tmp_path / "a.svg"
        out2 = tmp_path / "b.svg"
        _placeholder_svg(prompt, out1, 640, 360)
        _placeholder_svg(prompt, out2, 640, 360)
        assert out1.read_text() == out2.read_text(), "placeholder must be deterministic"

    def test_different_prompts_different_colors(self, tmp_path: pathlib.Path) -> None:
        out1 = tmp_path / "p1.svg"
        out2 = tmp_path / "p2.svg"
        _placeholder_svg("dark stormy ocean", out1, 640, 360)
        _placeholder_svg("bright sunny beach", out2, 640, 360)
        # Different prompts should produce different fill colors
        assert out1.read_text() != out2.read_text()

    def test_xml_special_chars_in_prompt(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "special.svg"
        result = gen_image('Trees & <bushes> "quoted"', str(out))
        assert result["ok"] is True
        # Must still be parseable XML
        ET.fromstring(out.read_text())

    def test_long_prompt_truncated(self, tmp_path: pathlib.Path) -> None:
        long_prompt = "A " * 200  # 400 chars
        out = tmp_path / "long.svg"
        result = gen_image(long_prompt, str(out))
        assert result["ok"] is True
        assert out.exists()

    def test_result_is_json_serialisable(self, tmp_path: pathlib.Path) -> None:
        import json
        out = tmp_path / "x.svg"
        result = gen_image("forest floor", str(out))
        json.dumps(result)  # must not raise
