"""Tests for parallax_engine.asset.generator (§11.7 kind dispatch)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml

from parallax_engine.asset.generator import (
    MAX_ASSET_RETRIES_PER_ASSET,
    AssetGeneratorError,
    AssetKind,
    GenerateResult,
    POSITION_HINTS,
    TINT_FILTERS,
    _apply_variant_transform,
    _extract_svg_inner,
    generate,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Return a workspace directory."""
    return tmp_path


@pytest.fixture
def minimal_svg() -> str:
    """A minimal valid SVG."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<rect width="100" height="100" fill="#c84032"/>'
        "</svg>"
    )


@pytest.fixture
def canon_svg(workspace: Path, minimal_svg: str) -> Path:
    """Write a canonical SVG to assets/canon/red_bird.svg."""
    canon_dir = workspace / "assets" / "canon"
    canon_dir.mkdir(parents=True)
    path = canon_dir / "red_bird.svg"
    path.write_text(minimal_svg, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_max_asset_retries(self) -> None:
        assert MAX_ASSET_RETRIES_PER_ASSET == 3

    def test_tint_filters_keys(self) -> None:
        assert "none" in TINT_FILTERS
        assert "cool_shadow" in TINT_FILTERS
        assert "warm_glow" in TINT_FILTERS

    def test_position_hints_keys(self) -> None:
        assert "center" in POSITION_HINTS
        assert "lower_third_left" in POSITION_HINTS
        assert "lower_third_right" in POSITION_HINTS

    def test_position_hint_fractions(self) -> None:
        for name, (fx, fy) in POSITION_HINTS.items():
            assert 0.0 <= fx <= 1.0, f"{name}: fx={fx} out of range"
            assert 0.0 <= fy <= 1.0, f"{name}: fy={fy} out of range"


# ---------------------------------------------------------------------------
# canonical kind — no-op
# ---------------------------------------------------------------------------


class TestCanonical:
    def test_canonical_returns_ok(self, workspace: Path, canon_svg: Path) -> None:
        entry = {
            "kind": "canonical",
            "id": "red_bird",
            "canonical_svg_path": "assets/canon/red_bird.svg",
        }
        result = generate(entry, workspace_dir=workspace)
        assert result["ok"] is True
        assert result["kind"] == "canonical"

    def test_canonical_backend_is_noop(self, workspace: Path, canon_svg: Path) -> None:
        entry = {
            "kind": "canonical",
            "id": "red_bird",
            "canonical_svg_path": "assets/canon/red_bird.svg",
        }
        result = generate(entry, workspace_dir=workspace)
        assert result["backend"] == "no-op"

    def test_canonical_no_svg_generated(self, workspace: Path, canon_svg: Path) -> None:
        """No new files should be created for canonical kind."""
        before = set(workspace.rglob("*.svg"))
        entry = {
            "kind": "canonical",
            "id": "red_bird",
            "canonical_svg_path": "assets/canon/red_bird.svg",
        }
        generate(entry, workspace_dir=workspace)
        after = set(workspace.rglob("*.svg"))
        assert before == after  # no new SVGs

    def test_canonical_missing_path_returns_fail(self, workspace: Path) -> None:
        entry = {"kind": "canonical", "id": "ghost"}
        result = generate(entry, workspace_dir=workspace)
        assert result["ok"] is False

    def test_canonical_path_field_fallback(self, workspace: Path, canon_svg: Path) -> None:
        """Should also accept 'path' field when 'canonical_svg_path' absent."""
        entry = {
            "kind": "canonical",
            "id": "red_bird",
            "path": "assets/canon/red_bird.svg",
        }
        result = generate(entry, workspace_dir=workspace)
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# produce_canonical kind
# ---------------------------------------------------------------------------


class TestProduceCanonical:
    def test_produce_canonical_creates_svg(self, workspace: Path) -> None:
        casting_data = {
            "red_bird": {
                "canonical_description": "A small red bird with black wings",
                "palette_locked": {"body": "#c84032"},
                "forbidden_changes": [],
            }
        }
        entry = {"kind": "produce_canonical", "id": "red_bird"}
        result = generate(
            entry, workspace_dir=workspace, casting_data=casting_data
        )
        # Backend may be placeholder or anthropic; either way ok should be True
        assert result["ok"] is True
        assert result["kind"] == "produce_canonical"
        # SVG must exist at assets/canon/red_bird.svg
        svg_path = workspace / "assets" / "canon" / "red_bird.svg"
        assert svg_path.exists()

    def test_produce_canonical_reads_from_casting_not_scene(
        self, workspace: Path
    ) -> None:
        """canonical_description comes from casting_data, not entry."""
        casting_data = {
            "tree": {
                "canonical_description": "A tall oak tree",
                "palette_locked": {},
            }
        }
        # entry has no canonical_description; should still work via casting_data
        entry = {"kind": "produce_canonical", "id": "tree"}
        result = generate(entry, workspace_dir=workspace, casting_data=casting_data)
        assert result["ok"] is True

    def test_produce_canonical_fails_without_description(
        self, workspace: Path
    ) -> None:
        """No description anywhere → fail."""
        entry = {"kind": "produce_canonical", "id": "unknown_asset"}
        result = generate(entry, workspace_dir=workspace, casting_data={})
        assert result["ok"] is False

    def test_produce_canonical_output_path(self, workspace: Path) -> None:
        casting_data = {
            "big_rock": {
                "canonical_description": "A large grey rock",
                "palette_locked": {"surface": "#808080"},
            }
        }
        entry = {"kind": "produce_canonical", "id": "big_rock"}
        result = generate(entry, workspace_dir=workspace, casting_data=casting_data)
        assert result["ok"] is True
        assert "big_rock" in (result["path"] or "")

    def test_produce_canonical_includes_palette_in_prompt(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """shape_language is forwarded to the prompt."""
        calls: list[str] = []

        def mock_gen_image(prompt, out_path, width=1920, height=1080, seed=42):
            calls.append(prompt)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
            return {"ok": True, "path": str(out_path), "width": width, "height": height,
                    "format": "svg", "backend": "mock", "message": "mock"}

        monkeypatch.setattr(
            "parallax_engine.tools.gen_image.gen_image", mock_gen_image
        )
        casting_data = {
            "cloud": {
                "canonical_description": "A fluffy white cloud",
                "palette_locked": {"body": "#ffffff"},
            }
        }
        entry = {"kind": "produce_canonical", "id": "cloud"}
        generate(
            entry,
            workspace_dir=workspace,
            casting_data=casting_data,
            shape_language="smooth rounded shapes",
        )
        assert len(calls) == 1
        assert "fluffy white cloud" in calls[0]
        assert "smooth rounded shapes" in calls[0]
        assert "#ffffff" in calls[0]


# ---------------------------------------------------------------------------
# local kind
# ---------------------------------------------------------------------------


class TestLocal:
    def test_local_creates_svg(self, workspace: Path) -> None:
        entry = {"kind": "local", "id": "background_sky", "purpose": "A gradient blue sky"}
        result = generate(entry, workspace_dir=workspace)
        assert result["ok"] is True
        assert result["kind"] == "local"

    def test_local_respects_path_field(self, workspace: Path) -> None:
        out = workspace / "custom" / "my_layer.svg"
        entry = {
            "kind": "local",
            "id": "fog",
            "purpose": "Dense morning fog",
            "path": "custom/my_layer.svg",
        }
        result = generate(entry, workspace_dir=workspace)
        assert result["ok"] is True
        assert out.exists()

    def test_local_default_path(self, workspace: Path) -> None:
        entry = {"kind": "local", "id": "moon"}
        result = generate(entry, workspace_dir=workspace)
        assert result["ok"] is True
        assert (workspace / "assets" / "local" / "moon.svg").exists()

    def test_local_shape_language_forwarded(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        def mock_gen_image(prompt, out_path, width=1920, height=1080, seed=42):
            calls.append(prompt)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
            return {"ok": True, "path": str(out_path), "width": width, "height": height,
                    "format": "svg", "backend": "mock", "message": "mock"}

        monkeypatch.setattr("parallax_engine.tools.gen_image.gen_image", mock_gen_image)
        entry = {"kind": "local", "id": "fern", "purpose": "A green fern"}
        generate(entry, workspace_dir=workspace, shape_language="angular geometric")
        assert "angular geometric" in calls[0]


# ---------------------------------------------------------------------------
# variant kind
# ---------------------------------------------------------------------------


class TestVariant:
    def test_variant_creates_svg(
        self, workspace: Path, canon_svg: Path, minimal_svg: str
    ) -> None:
        entry = {
            "kind": "variant",
            "id": "red_bird_scene3",
            "variant_of": "red_bird",
            "scene_index": 3,
            "canonical_svg_path": "assets/canon/red_bird.svg",
            "transformation": {
                "scale": 0.3,
                "lighting_tint": "cool_shadow",
                "position_hint": "lower_third_left",
            },
        }
        result = generate(entry, workspace_dir=workspace)
        assert result["ok"] is True
        assert result["kind"] == "variant"

    def test_variant_output_path_contains_scene_index(
        self, workspace: Path, canon_svg: Path
    ) -> None:
        entry = {
            "kind": "variant",
            "variant_of": "red_bird",
            "scene_index": 7,
            "canonical_svg_path": "assets/canon/red_bird.svg",
            "transformation": {"scale": 0.5},
        }
        result = generate(entry, workspace_dir=workspace)
        assert result["ok"] is True
        assert "scene7" in (result["path"] or "")

    def test_variant_canonical_never_modified(
        self, workspace: Path, canon_svg: Path, minimal_svg: str
    ) -> None:
        """Canonical SVG must remain identical after variant generation."""
        entry = {
            "kind": "variant",
            "variant_of": "red_bird",
            "scene_index": 2,
            "canonical_svg_path": "assets/canon/red_bird.svg",
            "transformation": {"scale": 0.6, "lighting_tint": "warm_glow"},
        }
        generate(entry, workspace_dir=workspace)
        assert canon_svg.read_text(encoding="utf-8") == minimal_svg

    def test_variant_missing_canonical_fails(self, workspace: Path) -> None:
        entry = {
            "kind": "variant",
            "variant_of": "ghost",
            "scene_index": 1,
            "canonical_svg_path": "assets/canon/ghost.svg",
            "transformation": {},
        }
        result = generate(entry, workspace_dir=workspace)
        assert result["ok"] is False

    def test_variant_missing_variant_of_fails(self, workspace: Path) -> None:
        entry = {"kind": "variant", "scene_index": 1}
        result = generate(entry, workspace_dir=workspace)
        assert result["ok"] is False

    def test_variant_svg_contains_transform(
        self, workspace: Path, canon_svg: Path
    ) -> None:
        entry = {
            "kind": "variant",
            "variant_of": "red_bird",
            "scene_index": 5,
            "canonical_svg_path": "assets/canon/red_bird.svg",
            "transformation": {"scale": 0.4, "lighting_tint": "none", "position_hint": "center"},
        }
        result = generate(entry, workspace_dir=workspace)
        assert result["ok"] is True
        svg_text = Path(result["path"]).read_text(encoding="utf-8")
        assert "transform=" in svg_text
        assert "scale(0.4000)" in svg_text

    def test_variant_svg_contains_tint_filter(
        self, workspace: Path, canon_svg: Path
    ) -> None:
        entry = {
            "kind": "variant",
            "variant_of": "red_bird",
            "scene_index": 4,
            "canonical_svg_path": "assets/canon/red_bird.svg",
            "transformation": {"scale": 1.0, "lighting_tint": "cool_shadow"},
        }
        result = generate(entry, workspace_dir=workspace)
        svg_text = Path(result["path"]).read_text(encoding="utf-8")
        assert "filter" in svg_text
        assert "feColorMatrix" in svg_text

    def test_variant_scale_clamped(
        self, workspace: Path, canon_svg: Path
    ) -> None:
        """Absurd scale values are clamped (not rejected)."""
        for scale in (0.0001, 999.0):
            entry = {
                "kind": "variant",
                "variant_of": "red_bird",
                "scene_index": 1,
                "canonical_svg_path": "assets/canon/red_bird.svg",
                "transformation": {"scale": scale},
            }
            result = generate(entry, workspace_dir=workspace)
            assert result["ok"] is True


# ---------------------------------------------------------------------------
# Unknown kind
# ---------------------------------------------------------------------------


class TestUnknownKind:
    def test_unknown_kind_returns_fail(self, workspace: Path) -> None:
        entry = {"kind": "teleport", "id": "ghost"}
        result = generate(entry, workspace_dir=workspace)
        assert result["ok"] is False
        assert "teleport" in result["message"]


# ---------------------------------------------------------------------------
# _extract_svg_inner
# ---------------------------------------------------------------------------


class TestExtractSvgInner:
    def test_extracts_inner_content(self) -> None:
        svg = '<svg viewBox="0 0 100 100"><rect fill="red"/></svg>'
        inner = _extract_svg_inner(svg)
        assert "<rect" in inner
        assert "<svg" not in inner

    def test_handles_multiline(self) -> None:
        svg = '<svg xmlns="...">\n  <g>\n    <path/>\n  </g>\n</svg>'
        inner = _extract_svg_inner(svg)
        assert "<g>" in inner
        assert "<svg" not in inner

    def test_fallback_on_invalid(self) -> None:
        """If regex can't find <svg>...</svg>, return the full text."""
        raw = "not an svg at all"
        inner = _extract_svg_inner(raw)
        assert inner == raw


# ---------------------------------------------------------------------------
# _apply_variant_transform
# ---------------------------------------------------------------------------


class TestApplyVariantTransform:
    def test_output_is_valid_svg_wrapper(self) -> None:
        svg = '<svg viewBox="0 0 100 100"><rect/></svg>'
        out = _apply_variant_transform(svg, 0.5, "none", "center", 1920, 1080)
        assert out.startswith("<svg")
        assert "</svg>" in out

    def test_scale_appears_in_transform(self) -> None:
        svg = '<svg viewBox="0 0 100 100"><rect/></svg>'
        out = _apply_variant_transform(svg, 0.25, "none", "center", 1920, 1080)
        assert "scale(0.2500)" in out

    def test_tint_filter_in_output(self) -> None:
        svg = '<svg viewBox="0 0 100 100"><rect/></svg>'
        out = _apply_variant_transform(svg, 1.0, "warm_glow", "center", 1920, 1080)
        assert "feColorMatrix" in out
        assert "parallax_tint" in out

    def test_no_filter_when_tint_none(self) -> None:
        svg = '<svg viewBox="0 0 100 100"><rect/></svg>'
        out = _apply_variant_transform(svg, 1.0, "none", "center", 1920, 1080)
        assert "feColorMatrix" not in out

    def test_position_hint_translate(self) -> None:
        svg = '<svg viewBox="0 0 100 100"><rect/></svg>'
        out = _apply_variant_transform(svg, 1.0, "none", "lower_third_left", 1920, 1080)
        # lower_third_left = (0.15 * 1920, 0.67 * 1080) = (288, 723.6)
        assert "translate(288.00,723.60)" in out

    def test_canonical_rect_preserved_in_inner(self) -> None:
        svg = '<svg viewBox="0 0 100 100"><rect fill="#c84032"/></svg>'
        out = _apply_variant_transform(svg, 1.0, "none", "center", 1920, 1080)
        # Original content must survive in the wrapper
        assert 'fill="#c84032"' in out
