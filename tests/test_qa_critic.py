"""Tests for parallax_engine.qa.critic (§11.8 tiered QA system)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from parallax_engine.qa.critic import (
    ALL_CLASSIFICATIONS,
    MODEL_OPUS,
    MODEL_SONNET,
    SCENE_CLASSIFICATIONS,
    STORYBOARD_CLASSIFICATIONS,
    CritiqueResult,
    QACriticError,
    _normalise_verdict,
    _parse_json_response,
    _stub_pass_response,
    critique,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestClassificationTaxonomies:
    def test_scene_classifications(self) -> None:
        expected = {
            "function_mismatch",
            "casting_drift",
            "palette_violation",
            "hard_rule_violation",
            "pacing_off",
            "transition_paired_wrong",
        }
        assert SCENE_CLASSIFICATIONS == expected

    def test_storyboard_classifications(self) -> None:
        expected = {
            "theme_unmet",
            "arc_doesnt_land",
            "structural_contradiction",
        }
        assert STORYBOARD_CLASSIFICATIONS == expected

    def test_all_classifications_is_union(self) -> None:
        assert ALL_CLASSIFICATIONS == SCENE_CLASSIFICATIONS | STORYBOARD_CLASSIFICATIONS

    def test_model_selection(self) -> None:
        assert "opus" in MODEL_OPUS
        assert "sonnet" in MODEL_SONNET
        # Opus must be a different (higher tier) model
        assert MODEL_OPUS != MODEL_SONNET


# ---------------------------------------------------------------------------
# CritiqueResult dataclass
# ---------------------------------------------------------------------------


class TestCritiqueResult:
    def test_pass_verdict_properties(self) -> None:
        r = CritiqueResult(level="asset", verdict="pass", reason="ok")
        assert r.passed is True
        assert r.failed is False

    def test_fail_verdict_properties(self) -> None:
        r = CritiqueResult(level="scene", verdict="fail", classification="pacing_off")
        assert r.passed is False
        assert r.failed is True

    def test_has_classification_field(self) -> None:
        r = CritiqueResult(
            level="storyboard",
            verdict="fail",
            classification="theme_unmet",
            reason="theme not delivered",
        )
        assert r.classification == "theme_unmet"

    def test_default_classification_none(self) -> None:
        r = CritiqueResult(level="asset", verdict="pass")
        assert r.classification is None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class TestNormaliseVerdict:
    def test_pass_string(self) -> None:
        assert _normalise_verdict("pass") == "pass"

    def test_fail_string(self) -> None:
        assert _normalise_verdict("fail") == "fail"

    def test_case_insensitive_fail(self) -> None:
        assert _normalise_verdict("FAIL") == "fail"

    def test_unknown_defaults_to_pass(self) -> None:
        assert _normalise_verdict("maybe") == "pass"

    def test_non_string_defaults_to_pass(self) -> None:
        assert _normalise_verdict(None) == "pass"
        assert _normalise_verdict(0) == "pass"


class TestParseJsonResponse:
    def test_valid_json(self) -> None:
        raw = '{"verdict": "pass", "reason": "all good"}'
        data = _parse_json_response(raw)
        assert data["verdict"] == "pass"

    def test_fail_json(self) -> None:
        raw = '{"verdict": "fail", "classification": "theme_unmet", "reason": "no theme"}'
        data = _parse_json_response(raw)
        assert data["verdict"] == "fail"
        assert data["classification"] == "theme_unmet"

    def test_markdown_fenced_json(self) -> None:
        raw = "```json\n{\"verdict\": \"pass\", \"reason\": \"ok\"}\n```"
        data = _parse_json_response(raw)
        assert data["verdict"] == "pass"

    def test_invalid_json_returns_default_pass(self) -> None:
        data = _parse_json_response("not json at all")
        assert data["verdict"] == "pass"


class TestStubPassResponse:
    def test_returns_valid_json(self) -> None:
        raw = _stub_pass_response()
        data = json.loads(raw)
        assert data["verdict"] == "pass"


# ---------------------------------------------------------------------------
# critique() — asset level (dry_run)
# ---------------------------------------------------------------------------


class TestCritiqueAsset:
    def test_dry_run_returns_pass(self, tmp_path: Path) -> None:
        result = critique(
            "asset",
            workspace_dir=tmp_path,
            asset_id="red_bird",
            asset_manifest_entry={"id": "red_bird", "kind": "canonical"},
            dry_run=True,
        )
        assert result.verdict == "pass"
        assert result.level == "asset"
        assert result.asset_id == "red_bird"

    def test_dry_run_uses_sonnet(self, tmp_path: Path) -> None:
        result = critique("asset", workspace_dir=tmp_path, dry_run=True)
        assert result.model == MODEL_SONNET

    def test_result_type(self, tmp_path: Path) -> None:
        result = critique("asset", workspace_dir=tmp_path, dry_run=True)
        assert isinstance(result, CritiqueResult)


# ---------------------------------------------------------------------------
# critique() — scene level (dry_run)
# ---------------------------------------------------------------------------


class TestCritiqueScene:
    def test_dry_run_returns_pass(self, tmp_path: Path) -> None:
        result = critique(
            "scene",
            workspace_dir=tmp_path,
            scene_index=2,
            storyboard_scene_entry={"scene_index": 2, "duration_s": 6.0},
            dry_run=True,
        )
        assert result.verdict == "pass"
        assert result.level == "scene"
        assert result.scene_index == 2

    def test_dry_run_standard_uses_sonnet(self, tmp_path: Path) -> None:
        result = critique("scene", workspace_dir=tmp_path, budget="standard", dry_run=True)
        assert result.model == MODEL_SONNET

    def test_dry_run_premium_uses_opus(self, tmp_path: Path) -> None:
        result = critique("scene", workspace_dir=tmp_path, budget="premium", dry_run=True)
        assert result.model == MODEL_OPUS

    def test_classification_none_on_pass(self, tmp_path: Path) -> None:
        result = critique("scene", workspace_dir=tmp_path, dry_run=True)
        assert result.classification is None


# ---------------------------------------------------------------------------
# critique() — storyboard level (dry_run)
# ---------------------------------------------------------------------------


class TestCritiqueStoryboard:
    def test_dry_run_returns_pass(self, tmp_path: Path) -> None:
        result = critique(
            "storyboard",
            workspace_dir=tmp_path,
            brief_text="A 10-second forest flythrough",
            dry_run=True,
        )
        assert result.verdict == "pass"
        assert result.level == "storyboard"

    def test_storyboard_always_uses_opus(self, tmp_path: Path) -> None:
        """Storyboard level must always use Opus (§11.8.2), never downgraded."""
        for budget in ("thrift", "standard", "premium"):
            result = critique(
                "storyboard", workspace_dir=tmp_path, budget=budget, dry_run=True
            )
            assert result.model == MODEL_OPUS, f"budget={budget!r}: expected Opus, got {result.model}"

    def test_classification_none_on_pass(self, tmp_path: Path) -> None:
        result = critique("storyboard", workspace_dir=tmp_path, dry_run=True)
        assert result.classification is None


# ---------------------------------------------------------------------------
# critique() — no credentials (no API key, no dry_run) raises RuntimeError
# ---------------------------------------------------------------------------


class TestCritiqueOfflineStub:
    def test_asset_raises_when_no_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When neither credential env var is set, critique() raises RuntimeError."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="no credentials"):
            critique("asset", workspace_dir=tmp_path, dry_run=False)

    def test_scene_raises_when_no_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="no credentials"):
            critique("scene", workspace_dir=tmp_path, dry_run=False)

    def test_storyboard_raises_when_no_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="no credentials"):
            critique("storyboard", workspace_dir=tmp_path, dry_run=False)


# ---------------------------------------------------------------------------
# Unknown level raises QACriticError
# ---------------------------------------------------------------------------


class TestCritiqueUnknownLevel:
    def test_raises_on_unknown_level(self, tmp_path: Path) -> None:
        with pytest.raises(QACriticError, match="Unknown QA level"):
            critique("pixel_level", workspace_dir=tmp_path)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# critic does not edit artifacts (§11.13.7)
# ---------------------------------------------------------------------------


class TestCriticNoEdits:
    def test_asset_critique_writes_no_files(
        self, tmp_path: Path
    ) -> None:
        """QA critic must not write to workspace artifacts."""
        before = set(tmp_path.rglob("*"))
        critique("asset", workspace_dir=tmp_path, dry_run=True)
        after = set(tmp_path.rglob("*"))
        # No new files should be created (logs dir may appear from manager, but not here)
        # The critic itself creates nothing
        assert before == after

    def test_storyboard_critique_writes_no_files(
        self, tmp_path: Path
    ) -> None:
        before = set(tmp_path.rglob("*"))
        critique("storyboard", workspace_dir=tmp_path, dry_run=True)
        after = set(tmp_path.rglob("*"))
        assert before == after
