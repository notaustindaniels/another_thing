"""Tests for parallax_engine.director.agent.

Covers:
  - DirectorStub: response map, call recording
  - DirectorAgent.run() single mode → valid DirectorResult
  - DirectorAgent.run() decomposed mode → valid DirectorResult
  - Acceptance criteria:
      AC1: Single-mode produces valid Storyboard from brief
      AC2: Decomposed mode triggers for >=60s or save_the_cat
      AC3: System blocks have cache_control (prompt-cached stable prefix)
      AC4: director_mode() is deterministic Python function
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from parallax_engine.director.agent import (
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
    DirectorAgent,
    DirectorResult,
    DirectorStub,
    _models_for_brief,
    _parse_and_validate,
)
from parallax_engine.director.prompt import (
    DirectorBrief,
    build_single_mode_system_blocks,
    director_mode,
)
from parallax_engine.director.schema import Storyboard

# Path to example storyboards for stub responses
_STORYBOARDS_DIR = Path(__file__).parent / "storyboards"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_example_yaml(name: str) -> str:
    return (_STORYBOARDS_DIR / name).read_text("utf-8")


def _wrap_yaml(raw: str) -> str:
    return f"```yaml\n{raw}\n```"


# ---------------------------------------------------------------------------
# Test DirectorStub
# ---------------------------------------------------------------------------


class TestDirectorStub:
    def test_returns_configured_response(self) -> None:
        stub = DirectorStub({"director": "```yaml\nfoo: bar\n```"})
        result = stub.complete(
            model=MODEL_OPUS,
            system=[{"type": "text", "text": "sys"}],
            messages=[{"role": "user", "content": "brief"}],
            step_name="director",
        )
        assert result == "```yaml\nfoo: bar\n```"

    def test_raises_on_missing_step(self) -> None:
        stub = DirectorStub({})
        with pytest.raises(ValueError, match="no response"):
            stub.complete(
                model=MODEL_OPUS,
                system="sys",
                messages=[],
                step_name="director",
            )

    def test_records_calls(self) -> None:
        stub = DirectorStub({"director": "```yaml\nfoo: bar\n```"})
        stub.complete(
            model=MODEL_OPUS,
            system="sys",
            messages=[{"role": "user", "content": "test"}],
            step_name="director",
        )
        assert stub.call_count == 1
        assert stub.calls[0]["step_name"] == "director"
        assert stub.calls[0]["model"] == MODEL_OPUS

    def test_file_path_response(self, tmp_path: Path) -> None:
        """If response value is a path to a YAML file, stub reads and wraps it."""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text("storyboard_version: '1.0'\n", "utf-8")
        stub = DirectorStub({"director": str(yaml_file)})
        result = stub.complete(
            model=MODEL_OPUS,
            system="sys",
            messages=[],
            step_name="director",
        )
        assert "storyboard_version" in result

    def test_multiple_calls_tracked(self) -> None:
        stub = DirectorStub(
            {
                "brief_decomposer": "```yaml\ntitle: T\n```",
                "arc_architect": "```yaml\narc:\n  structure: three_act\n```",
            }
        )
        for step in ("brief_decomposer", "arc_architect"):
            stub.complete(model=MODEL_SONNET, system="sys", messages=[], step_name=step)
        assert stub.call_count == 2


# ---------------------------------------------------------------------------
# AC1: Single-mode produces valid Storyboard from brief
# ---------------------------------------------------------------------------


class TestDirectorAgentSingleMode:
    """Single-mode director: one call → valid Storyboard."""

    @pytest.fixture
    def example_a_response(self) -> str:
        return _wrap_yaml(_load_example_yaml("example_a.yaml"))

    @pytest.fixture
    def agent_with_example_a(self, example_a_response: str) -> DirectorAgent:
        stub = DirectorStub({"director": example_a_response})
        return DirectorAgent(client=stub)

    def test_run_returns_director_result(
        self, agent_with_example_a: DirectorAgent
    ) -> None:
        brief = DirectorBrief(text="Forest flythrough.", target_duration_s=10.0)
        result = agent_with_example_a.run(brief)
        assert isinstance(result, DirectorResult)

    def test_result_mode_is_single(self, agent_with_example_a: DirectorAgent) -> None:
        brief = DirectorBrief(text="Forest flythrough.", target_duration_s=10.0)
        result = agent_with_example_a.run(brief)
        assert result.mode_used == "single"

    def test_result_storyboard_is_valid(
        self, agent_with_example_a: DirectorAgent
    ) -> None:
        brief = DirectorBrief(text="Forest flythrough.", target_duration_s=10.0)
        result = agent_with_example_a.run(brief)
        assert isinstance(result.storyboard, Storyboard)

    def test_storyboard_has_project(self, agent_with_example_a: DirectorAgent) -> None:
        brief = DirectorBrief(text="Forest flythrough.", target_duration_s=10.0)
        result = agent_with_example_a.run(brief)
        assert result.storyboard.project.title

    def test_storyboard_has_scenes(self, agent_with_example_a: DirectorAgent) -> None:
        brief = DirectorBrief(text="Forest flythrough.", target_duration_s=10.0)
        result = agent_with_example_a.run(brief)
        assert len(result.storyboard.scenes) >= 1

    def test_raw_yaml_is_non_empty(self, agent_with_example_a: DirectorAgent) -> None:
        brief = DirectorBrief(text="Forest flythrough.", target_duration_s=10.0)
        result = agent_with_example_a.run(brief)
        assert len(result.raw_yaml) > 0

    def test_stub_called_once_for_single_mode(
        self, example_a_response: str
    ) -> None:
        stub = DirectorStub({"director": example_a_response})
        agent = DirectorAgent(client=stub)
        brief = DirectorBrief(text="Forest flythrough.", target_duration_s=10.0)
        agent.run(brief)
        assert stub.call_count == 1

    def test_all_three_examples_parse(self) -> None:
        """All three canonical §11.4 examples must validate."""
        for fname in ("example_a.yaml", "example_b.yaml", "example_c.yaml"):
            raw = _load_example_yaml(fname)
            stub = DirectorStub({"director": _wrap_yaml(raw)})
            agent = DirectorAgent(client=stub)
            brief = DirectorBrief(text="Test.", target_duration_s=10.0)
            result = agent.run(brief)
            assert isinstance(result.storyboard, Storyboard), f"{fname} failed"

    def test_invalid_yaml_raises_value_error(self) -> None:
        stub = DirectorStub({"director": "```yaml\n{bad: yaml: : :::}\n```"})
        agent = DirectorAgent(client=stub)
        brief = DirectorBrief(text="Test.", target_duration_s=10.0)
        with pytest.raises((ValueError, Exception)):
            agent.run(brief)

    def test_no_yaml_block_raises_value_error(self) -> None:
        stub = DirectorStub({"director": "No YAML block here, just prose."})
        agent = DirectorAgent(client=stub)
        brief = DirectorBrief(text="Test.", target_duration_s=10.0)
        with pytest.raises(ValueError, match="no.*yaml"):
            agent.run(brief)


# ---------------------------------------------------------------------------
# AC2: Decomposed mode triggers for duration >= 60s or save_the_cat
# ---------------------------------------------------------------------------


class TestDirectorAgentDecomposedMode:
    """Decomposed mode: four-step chain for >=60s or save_the_cat briefs."""

    @pytest.fixture
    def example_c_response(self) -> str:
        """Use example C as the continuity-checker's final output."""
        return _wrap_yaml(_load_example_yaml("example_c.yaml"))

    def _make_stub_for_decomposed(self, final_response: str) -> DirectorStub:
        """Build a stub that answers all four decomposed-mode steps."""
        return DirectorStub(
            {
                "brief_decomposer": "```yaml\ntitle: Test\nlogline: A test.\n```",
                "arc_architect": "```yaml\narc:\n  structure: three_act\n  theme: wonder\n  beats: []\nscenes: []\n```",
                "scene_architect": "```yaml\nstoryboard_version: '1.0'\n```",
                "continuity_checker": final_response,
            }
        )

    def test_long_brief_uses_decomposed_mode(self, example_c_response: str) -> None:
        stub = self._make_stub_for_decomposed(example_c_response)
        agent = DirectorAgent(client=stub)
        brief = DirectorBrief(text="Long epic.", target_duration_s=90.0)
        result = agent.run(brief)
        assert result.mode_used == "decomposed"

    def test_save_the_cat_uses_decomposed_mode(self, example_c_response: str) -> None:
        stub = self._make_stub_for_decomposed(example_c_response)
        agent = DirectorAgent(client=stub)
        brief = DirectorBrief(
            text="Hero's journey.",
            target_duration_s=30.0,
            requested_structure="save_the_cat",
        )
        result = agent.run(brief)
        assert result.mode_used == "decomposed"

    def test_decomposed_makes_four_calls(self, example_c_response: str) -> None:
        stub = self._make_stub_for_decomposed(example_c_response)
        agent = DirectorAgent(client=stub)
        brief = DirectorBrief(text="Long epic.", target_duration_s=90.0)
        agent.run(brief)
        assert stub.call_count == 4

    def test_decomposed_result_has_steps(self, example_c_response: str) -> None:
        stub = self._make_stub_for_decomposed(example_c_response)
        agent = DirectorAgent(client=stub)
        brief = DirectorBrief(text="Long epic.", target_duration_s=90.0)
        result = agent.run(brief)
        assert len(result.steps) == 4

    def test_decomposed_step_names_correct(self, example_c_response: str) -> None:
        stub = self._make_stub_for_decomposed(example_c_response)
        agent = DirectorAgent(client=stub)
        brief = DirectorBrief(text="Long epic.", target_duration_s=90.0)
        result = agent.run(brief)
        step_names = [s["step"] for s in result.steps]
        assert step_names == [
            "brief_decomposer",
            "arc_architect",
            "scene_architect",
            "continuity_checker",
        ]

    def test_decomposed_result_storyboard_is_valid(
        self, example_c_response: str
    ) -> None:
        stub = self._make_stub_for_decomposed(example_c_response)
        agent = DirectorAgent(client=stub)
        brief = DirectorBrief(text="Long epic.", target_duration_s=90.0)
        result = agent.run(brief)
        assert isinstance(result.storyboard, Storyboard)

    def test_all_three_examples_as_continuity_output(self) -> None:
        """All three example storyboards work as continuity-checker output."""
        for fname in ("example_a.yaml", "example_b.yaml", "example_c.yaml"):
            final = _wrap_yaml(_load_example_yaml(fname))
            stub = DirectorStub(
                {
                    "brief_decomposer": "```yaml\ntitle: T\n```",
                    "arc_architect": "```yaml\narc:\n  structure: three_act\n```",
                    "scene_architect": "```yaml\nstoryboard_version: '1.0'\n```",
                    "continuity_checker": final,
                }
            )
            agent = DirectorAgent(client=stub)
            brief = DirectorBrief(text="Test.", target_duration_s=90.0)
            result = agent.run(brief)
            assert isinstance(result.storyboard, Storyboard), f"Failed with {fname}"

    def test_short_brief_does_not_use_decomposed(self, example_c_response: str) -> None:
        """Single-mode is used for short brief; four-call stub is NOT needed."""
        stub = DirectorStub({"director": example_c_response})
        agent = DirectorAgent(client=stub)
        brief = DirectorBrief(text="Short.", target_duration_s=10.0)
        result = agent.run(brief)
        assert result.mode_used == "single"
        assert stub.call_count == 1


# ---------------------------------------------------------------------------
# AC3: Schema + examples are prompt-cached (stable prefix)
# ---------------------------------------------------------------------------


class TestPromptCachingStructure:
    """The stable prefix (system blocks) must have cache_control: ephemeral."""

    def test_system_blocks_count(self) -> None:
        blocks = build_single_mode_system_blocks()
        assert len(blocks) == 3

    def test_all_blocks_have_ephemeral_cache_control(self) -> None:
        for block in build_single_mode_system_blocks():
            assert block.get("cache_control", {}).get("type") == "ephemeral"

    def test_schema_block_is_stable(self) -> None:
        """Multiple calls return identical schema block text."""
        b1 = build_single_mode_system_blocks()[1]["text"]
        b2 = build_single_mode_system_blocks()[1]["text"]
        assert b1 == b2

    def test_examples_block_is_stable(self) -> None:
        """Multiple calls return identical examples block text."""
        b1 = build_single_mode_system_blocks()[2]["text"]
        b2 = build_single_mode_system_blocks()[2]["text"]
        assert b1 == b2

    def test_schema_block_contains_valid_json(self) -> None:
        import json
        blocks = build_single_mode_system_blocks()
        schema_text = blocks[1]["text"]
        # Extract the JSON portion from the fenced block
        import re
        m = re.search(r"```json\s*\n(.*?)```", schema_text, re.DOTALL)
        assert m, "Schema block should contain a ```json fence"
        json.loads(m.group(1))  # must parse without error

    def test_examples_block_contains_all_three_examples(self) -> None:
        blocks = build_single_mode_system_blocks()
        examples_text = blocks[2]["text"]
        assert "example_a" in examples_text
        assert "example_b" in examples_text
        assert "example_c" in examples_text


# ---------------------------------------------------------------------------
# AC4: director_mode() is deterministic
# ---------------------------------------------------------------------------


class TestDirectorModeDeterminism:
    """Redundant with TestDirectorMode but focused on the AC4 guarantee."""

    def test_same_brief_same_result_100_times(self) -> None:
        brief = DirectorBrief(text="Test.", target_duration_s=30.0)
        results = [director_mode(brief) for _ in range(100)]
        assert len(set(results)) == 1

    def test_no_side_effects(self) -> None:
        brief = DirectorBrief(text="Test.", target_duration_s=30.0)
        before = brief.model_dump()
        director_mode(brief)
        after = brief.model_dump()
        assert before == after


# ---------------------------------------------------------------------------
# Test _models_for_brief()
# ---------------------------------------------------------------------------


class TestModelsForBrief:
    def test_single_mode_uses_opus(self) -> None:
        brief = DirectorBrief(text="Short.", target_duration_s=10.0)
        models = _models_for_brief(brief)
        assert models["director"] == MODEL_OPUS

    def test_decomposed_mode_brief_decomposer_is_sonnet(self) -> None:
        brief = DirectorBrief(text="Long.", target_duration_s=90.0)
        models = _models_for_brief(brief)
        assert models["brief_decomposer"] == MODEL_SONNET

    def test_decomposed_mode_arc_architect_is_opus(self) -> None:
        brief = DirectorBrief(text="Long.", target_duration_s=90.0)
        models = _models_for_brief(brief)
        assert models["arc_architect"] == MODEL_OPUS

    def test_thrift_budget_decomposed_uses_haiku_and_sonnet(self) -> None:
        brief = DirectorBrief(
            text="Long thrift.", target_duration_s=90.0, config_budget="thrift"
        )
        models = _models_for_brief(brief)
        assert models["brief_decomposer"] == MODEL_HAIKU
        assert models["arc_architect"] == MODEL_SONNET

    def test_thrift_budget_single_uses_opus(self) -> None:
        """Even for thrift, single-mode director is still Opus."""
        brief = DirectorBrief(text="Short thrift.", target_duration_s=5.0, config_budget="thrift")
        models = _models_for_brief(brief)
        assert models["director"] == MODEL_OPUS


# ---------------------------------------------------------------------------
# Test _parse_and_validate()
# ---------------------------------------------------------------------------


class TestParseAndValidate:
    def test_parses_example_a(self) -> None:
        raw = _load_example_yaml("example_a.yaml")
        sb = _parse_and_validate(raw)
        assert isinstance(sb, Storyboard)

    def test_parses_example_b(self) -> None:
        raw = _load_example_yaml("example_b.yaml")
        sb = _parse_and_validate(raw)
        assert isinstance(sb, Storyboard)

    def test_parses_example_c(self) -> None:
        raw = _load_example_yaml("example_c.yaml")
        sb = _parse_and_validate(raw)
        assert isinstance(sb, Storyboard)

    def test_raises_on_invalid_yaml(self) -> None:
        with pytest.raises((ValueError, Exception)):
            _parse_and_validate("not: valid: : yaml: : :")

    def test_raises_on_non_dict(self) -> None:
        with pytest.raises(ValueError, match="expected dict"):
            _parse_and_validate("- list_item_1\n- list_item_2")

    def test_raises_on_missing_required_field(self) -> None:
        # Valid YAML but missing required storyboard fields
        with pytest.raises(Exception):  # pydantic ValidationError
            _parse_and_validate("foo: bar\nbaz: qux")
