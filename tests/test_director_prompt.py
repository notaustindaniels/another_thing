"""Tests for parallax_engine.director.prompt.

Covers:
  - director_mode() determinism per §11.9.1
  - build_single_mode_system_blocks() structure + caching
  - build_single_mode_messages() structure
  - build_decomposed_system_prompts() keys + content
  - build_decomposed_user_messages() chaining
  - extract_yaml_block() parsing
  - DirectorBrief validation
"""
from __future__ import annotations

import pytest

from parallax_engine.director.prompt import (
    DirectorBrief,
    build_decomposed_system_prompts,
    build_decomposed_user_messages,
    build_single_mode_messages,
    build_single_mode_system_blocks,
    director_mode,
    extract_yaml_block,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def short_brief() -> DirectorBrief:
    return DirectorBrief(text="A forest drone flythrough at dawn.", target_duration_s=15.0)


@pytest.fixture
def long_brief() -> DirectorBrief:
    return DirectorBrief(text="A long epic journey.", target_duration_s=90.0)


@pytest.fixture
def save_the_cat_brief() -> DirectorBrief:
    return DirectorBrief(
        text="A hero's journey through biomes.",
        target_duration_s=30.0,
        requested_structure="save_the_cat",
    )


@pytest.fixture
def thrift_brief() -> DirectorBrief:
    return DirectorBrief(
        text="Simple 5-second loop.",
        target_duration_s=5.0,
        config_budget="thrift",
    )


# ---------------------------------------------------------------------------
# Test director_mode() — §11.9.1
# ---------------------------------------------------------------------------


class TestDirectorMode:
    """director_mode() must be deterministic and follow §11.9.1 exactly."""

    def test_short_brief_returns_single(self, short_brief: DirectorBrief) -> None:
        assert director_mode(short_brief) == "single"

    def test_long_brief_returns_decomposed(self, long_brief: DirectorBrief) -> None:
        assert director_mode(long_brief) == "decomposed"

    def test_exactly_60s_returns_decomposed(self) -> None:
        brief = DirectorBrief(text="Edge case.", target_duration_s=60.0)
        assert director_mode(brief) == "decomposed"

    def test_59_9s_returns_single(self) -> None:
        brief = DirectorBrief(text="Just under 60s.", target_duration_s=59.9)
        assert director_mode(brief) == "single"

    def test_save_the_cat_returns_decomposed(
        self, save_the_cat_brief: DirectorBrief
    ) -> None:
        assert director_mode(save_the_cat_brief) == "decomposed"

    def test_save_the_cat_short_duration_still_decomposed(self) -> None:
        """save_the_cat overrides the duration check."""
        brief = DirectorBrief(
            text="Short save_the_cat.",
            target_duration_s=10.0,
            requested_structure="save_the_cat",
        )
        assert director_mode(brief) == "decomposed"

    def test_thrift_budget_returns_single(self, thrift_brief: DirectorBrief) -> None:
        assert director_mode(thrift_brief) == "single"

    def test_default_budget_returns_single(self, short_brief: DirectorBrief) -> None:
        assert director_mode(short_brief) == "single"

    def test_premium_budget_short_returns_single(self) -> None:
        brief = DirectorBrief(
            text="Premium short.", target_duration_s=20.0, config_budget="premium"
        )
        assert director_mode(brief) == "single"

    def test_various_structures_short_return_single(self) -> None:
        for struct in ("three_act", "establish_disrupt_resolve", "biome_tour", "portal_reveal"):
            brief = DirectorBrief(
                text="Test.",
                target_duration_s=10.0,
                requested_structure=struct,
            )
            assert director_mode(brief) == "single", f"Expected single for {struct}"

    def test_long_brief_with_thrift_still_decomposed(self) -> None:
        """Duration check takes priority over thrift budget."""
        brief = DirectorBrief(
            text="Long thrift.", target_duration_s=120.0, config_budget="thrift"
        )
        assert director_mode(brief) == "decomposed"

    def test_determinism_same_brief_same_result(self) -> None:
        brief = DirectorBrief(text="Same.", target_duration_s=30.0)
        results = {director_mode(brief) for _ in range(10)}
        assert len(results) == 1, "director_mode() must be deterministic"


# ---------------------------------------------------------------------------
# Test DirectorBrief validation
# ---------------------------------------------------------------------------


class TestDirectorBriefModel:
    def test_minimal_valid(self) -> None:
        b = DirectorBrief(text="Hello world.")
        assert b.target_duration_s == 15.0
        assert b.config_budget == "standard"
        assert b.references == []
        assert b.requested_structure is None

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(Exception):
            DirectorBrief(text="Hi.", unknown_field="bad")  # type: ignore[call-arg]

    def test_target_duration_positive(self) -> None:
        with pytest.raises(Exception):
            DirectorBrief(text="Hi.", target_duration_s=0.0)

    def test_target_duration_max(self) -> None:
        with pytest.raises(Exception):
            DirectorBrief(text="Hi.", target_duration_s=301.0)

    def test_references_list(self) -> None:
        b = DirectorBrief(
            text="Hi.",
            references=["https://example.com/ref1.jpg", "https://example.com/ref2.jpg"],
        )
        assert len(b.references) == 2


# ---------------------------------------------------------------------------
# Test build_single_mode_system_blocks()
# ---------------------------------------------------------------------------


class TestBuildSingleModeSystemBlocks:
    def test_returns_list_of_three(self, short_brief: DirectorBrief) -> None:
        blocks = build_single_mode_system_blocks()
        assert len(blocks) == 3

    def test_all_blocks_have_type_text(self) -> None:
        for block in build_single_mode_system_blocks():
            assert block["type"] == "text"

    def test_all_blocks_have_cache_control(self) -> None:
        for block in build_single_mode_system_blocks():
            assert "cache_control" in block
            assert block["cache_control"]["type"] == "ephemeral"

    def test_block0_contains_director_instructions(self) -> None:
        blocks = build_single_mode_system_blocks()
        assert "Director" in blocks[0]["text"]
        assert "parallax-engine" in blocks[0]["text"]

    def test_block1_contains_schema(self) -> None:
        blocks = build_single_mode_system_blocks()
        # Schema block has JSON content
        assert "json" in blocks[1]["text"].lower() or "schema" in blocks[1]["text"].lower()

    def test_block2_contains_examples(self) -> None:
        blocks = build_single_mode_system_blocks()
        # Examples block should have example content
        assert "example" in blocks[2]["text"].lower() or "yaml" in blocks[2]["text"].lower()

    def test_schema_block_has_json_content(self) -> None:
        blocks = build_single_mode_system_blocks()
        # Schema block should include some JSON fields from schema.json
        assert "storyboard_version" in blocks[1]["text"] or "properties" in blocks[1]["text"]

    def test_examples_block_has_example_a(self) -> None:
        blocks = build_single_mode_system_blocks()
        # Examples block should contain example storyboard content
        assert "example_a" in blocks[2]["text"] or "Through the Pines" in blocks[2]["text"]

    def test_call_is_stable(self) -> None:
        """Same blocks returned across multiple calls (determinism)."""
        blocks1 = build_single_mode_system_blocks()
        blocks2 = build_single_mode_system_blocks()
        assert [b["text"] for b in blocks1] == [b["text"] for b in blocks2]


# ---------------------------------------------------------------------------
# Test build_single_mode_messages()
# ---------------------------------------------------------------------------


class TestBuildSingleModeMessages:
    def test_returns_list_of_one_user_message(self, short_brief: DirectorBrief) -> None:
        messages = build_single_mode_messages(short_brief)
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    def test_user_message_contains_brief_text(self, short_brief: DirectorBrief) -> None:
        messages = build_single_mode_messages(short_brief)
        assert short_brief.text in messages[0]["content"]

    def test_includes_target_duration(self, short_brief: DirectorBrief) -> None:
        messages = build_single_mode_messages(short_brief)
        assert "15.0" in messages[0]["content"]

    def test_includes_requested_structure_when_set(self) -> None:
        brief = DirectorBrief(text="Test.", requested_structure="three_act", target_duration_s=10.0)
        messages = build_single_mode_messages(brief)
        assert "three_act" in messages[0]["content"]

    def test_no_structure_when_none(self, short_brief: DirectorBrief) -> None:
        messages = build_single_mode_messages(short_brief)
        assert "Requested structure" not in messages[0]["content"]

    def test_references_sorted_in_output(self) -> None:
        brief = DirectorBrief(
            text="Test.",
            references=["https://z.com/b.jpg", "https://a.com/c.jpg"],
        )
        messages = build_single_mode_messages(brief)
        content = messages[0]["content"]
        pos_a = content.find("a.com")
        pos_z = content.find("z.com")
        assert pos_a < pos_z, "References should be sorted"

    def test_no_cache_control_in_messages(self, short_brief: DirectorBrief) -> None:
        """User messages must NOT have cache_control (only system blocks do)."""
        messages = build_single_mode_messages(short_brief)
        content = messages[0]["content"]
        assert "cache_control" not in str(content)


# ---------------------------------------------------------------------------
# Test build_decomposed_system_prompts()
# ---------------------------------------------------------------------------


class TestBuildDecomposedSystemPrompts:
    def test_returns_four_keys(self) -> None:
        prompts = build_decomposed_system_prompts()
        assert set(prompts.keys()) == {
            "brief_decomposer",
            "arc_architect",
            "scene_architect",
            "continuity_checker",
        }

    def test_brief_decomposer_mentions_treatment(self) -> None:
        prompts = build_decomposed_system_prompts()
        text = prompts["brief_decomposer"]
        assert "treatment" in text.lower() or "decomposer" in text.lower()

    def test_arc_architect_mentions_arc(self) -> None:
        prompts = build_decomposed_system_prompts()
        assert "arc" in prompts["arc_architect"].lower()

    def test_scene_architect_mentions_scene(self) -> None:
        prompts = build_decomposed_system_prompts()
        assert "scene" in prompts["scene_architect"].lower()

    def test_continuity_checker_mentions_continuity(self) -> None:
        prompts = build_decomposed_system_prompts()
        assert "continuity" in prompts["continuity_checker"].lower()

    def test_all_prompts_are_nonempty_strings(self) -> None:
        for key, text in build_decomposed_system_prompts().items():
            assert isinstance(text, str) and len(text) > 50, f"{key} prompt too short"

    def test_determinism_across_calls(self) -> None:
        p1 = build_decomposed_system_prompts()
        p2 = build_decomposed_system_prompts()
        assert p1 == p2


# ---------------------------------------------------------------------------
# Test build_decomposed_user_messages()
# ---------------------------------------------------------------------------


class TestBuildDecomposedUserMessages:
    def test_returns_four_keys(self, short_brief: DirectorBrief) -> None:
        msgs = build_decomposed_user_messages(short_brief)
        assert set(msgs.keys()) == {
            "brief_decomposer",
            "arc_architect",
            "scene_architect",
            "continuity_checker",
        }

    def test_brief_decomposer_contains_brief(self, short_brief: DirectorBrief) -> None:
        msgs = build_decomposed_user_messages(short_brief)
        assert short_brief.text in msgs["brief_decomposer"]

    def test_arc_architect_contains_treatment(self, short_brief: DirectorBrief) -> None:
        treatment = "title: Test\nlogline: A test brief."
        msgs = build_decomposed_user_messages(short_brief, treatment_yaml=treatment)
        assert treatment in msgs["arc_architect"]

    def test_scene_architect_contains_arc(self, short_brief: DirectorBrief) -> None:
        arc = "structure: three_act\nbeats: []"
        msgs = build_decomposed_user_messages(short_brief, arc_yaml=arc)
        assert arc in msgs["scene_architect"]

    def test_continuity_checker_contains_storyboard(self, short_brief: DirectorBrief) -> None:
        storyboard = "storyboard_version: '1.0'"
        msgs = build_decomposed_user_messages(short_brief, full_storyboard_yaml=storyboard)
        assert storyboard in msgs["continuity_checker"]

    def test_missing_treatment_uses_placeholder(self, short_brief: DirectorBrief) -> None:
        msgs = build_decomposed_user_messages(short_brief)
        # No treatment_yaml given → placeholder
        assert "awaiting" in msgs["arc_architect"].lower()


# ---------------------------------------------------------------------------
# Test extract_yaml_block()
# ---------------------------------------------------------------------------


class TestExtractYamlBlock:
    def test_extracts_simple_block(self) -> None:
        text = "Some text\n```yaml\nfoo: bar\n```\nMore text."
        result = extract_yaml_block(text)
        assert result == "foo: bar"

    def test_raises_on_missing_block(self) -> None:
        with pytest.raises(ValueError, match="no.*yaml"):
            extract_yaml_block("No yaml block here.")

    def test_extracts_multiline(self) -> None:
        text = "```yaml\nstoryboard_version: '1.0'\nproject:\n  title: Test\n```"
        result = extract_yaml_block(text)
        assert "storyboard_version" in result
        assert "title" in result

    def test_strips_surrounding_whitespace(self) -> None:
        text = "```yaml\n  \nfoo: bar\n  \n```"
        result = extract_yaml_block(text)
        assert result == "foo: bar"
