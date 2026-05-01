"""
tests/test_subagents.py — Tests for parallax_engine.subagents (P3.M03).

Verifies:
  - All five AgentDefinitions exist with the expected names
  - No subagent has the 'Agent' tool (§9.6)
  - Allowed tool lists match §3.3 contract exactly
  - Stub prompts contain expected return-value patterns (§3.3)
  - AgentDefinition raises on 'Agent' in allowed_tools
  - SUBAGENT_BY_NAME lookup is consistent with ALL_SUBAGENTS
"""
from __future__ import annotations

import re
import pytest

from parallax_engine.subagents import (
    AgentDefinition,
    ALL_SUBAGENTS,
    ASSET_GENERATOR,
    CAMERA_PATHER,
    HAIKU,
    MASK_AUTHOR,
    QA_CRITIC,
    SCENE_DESIGNER,
    SONNET,
    SUBAGENT_BY_NAME,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_NAMES = {
    "scene-designer",
    "asset-generator",
    "mask-author",
    "camera-pather",
    "qa-critic",
}


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_five_subagents_defined(self) -> None:
        assert len(ALL_SUBAGENTS) == 5

    def test_all_expected_names_present(self) -> None:
        names = {s.name for s in ALL_SUBAGENTS}
        assert names == _EXPECTED_NAMES

    def test_subagent_by_name_consistent(self) -> None:
        """SUBAGENT_BY_NAME and ALL_SUBAGENTS refer to the same objects."""
        for s in ALL_SUBAGENTS:
            assert SUBAGENT_BY_NAME[s.name] is s

    def test_subagent_by_name_complete(self) -> None:
        assert set(SUBAGENT_BY_NAME.keys()) == _EXPECTED_NAMES

    def test_all_subagents_are_agent_definitions(self) -> None:
        for s in ALL_SUBAGENTS:
            assert isinstance(s, AgentDefinition)


# ---------------------------------------------------------------------------
# §9.6: No subagent has the Agent tool
# ---------------------------------------------------------------------------

class TestNoAgentTool:
    def test_no_subagent_has_agent_tool(self) -> None:
        for s in ALL_SUBAGENTS:
            assert "Agent" not in s.allowed_tools, (
                f"{s.name} must not have 'Agent' in allowed_tools (§9.6)"
            )

    def test_agent_definition_raises_on_agent_tool(self) -> None:
        """AgentDefinition.__post_init__ raises ValueError if 'Agent' is included."""
        with pytest.raises(ValueError, match="must not include 'Agent'"):
            AgentDefinition(
                name="bad-agent",
                model=SONNET,
                description="bad",
                allowed_tools=("Read", "Agent", "Write"),
                system_prompt="stub",
            )


# ---------------------------------------------------------------------------
# Tool lists match §3.3 contract
# ---------------------------------------------------------------------------

class TestToolLists:
    def test_scene_designer_tools(self) -> None:
        assert set(SCENE_DESIGNER.allowed_tools) == {"Read", "Write"}

    def test_asset_generator_tools(self) -> None:
        expected = {
            "Read", "Write", "Bash",
            "mcp__parallax_render__gen_image",
            "mcp__parallax_masks__autosegment",
        }
        assert set(ASSET_GENERATOR.allowed_tools) == expected

    def test_mask_author_tools(self) -> None:
        expected = {"Read", "Write", "mcp__parallax_masks__alpha_refine"}
        assert set(MASK_AUTHOR.allowed_tools) == expected

    def test_camera_pather_tools(self) -> None:
        assert set(CAMERA_PATHER.allowed_tools) == {"Read", "Write"}

    def test_qa_critic_tools(self) -> None:
        expected = {
            "Read", "Write", "Glob",
            "mcp__parallax_qa__diff_frames",
            "mcp__parallax_qa__ssim_score",
        }
        assert set(QA_CRITIC.allowed_tools) == expected


# ---------------------------------------------------------------------------
# Model assignments match §3.1
# ---------------------------------------------------------------------------

class TestModels:
    def test_scene_designer_uses_sonnet(self) -> None:
        assert SCENE_DESIGNER.model == SONNET

    def test_asset_generator_uses_haiku(self) -> None:
        assert ASSET_GENERATOR.model == HAIKU

    def test_mask_author_uses_haiku(self) -> None:
        assert MASK_AUTHOR.model == HAIKU

    def test_camera_pather_uses_sonnet(self) -> None:
        assert CAMERA_PATHER.model == SONNET

    def test_qa_critic_uses_sonnet(self) -> None:
        assert QA_CRITIC.model == SONNET


# ---------------------------------------------------------------------------
# Stub prompts contain §3.3 return-value patterns
# ---------------------------------------------------------------------------

class TestStubPrompts:
    def test_scene_designer_prompt_has_return_pattern(self) -> None:
        """Prompt must show 'scene written: N layers, M masks, duration Ts'."""
        assert "scene written:" in SCENE_DESIGNER.system_prompt

    def test_asset_generator_prompt_has_return_pattern(self) -> None:
        """Prompt must show 'ok: assets/<layer>.svg' pattern."""
        assert "ok: assets/" in ASSET_GENERATOR.system_prompt

    def test_mask_author_prompt_has_return_pattern(self) -> None:
        """Prompt must show 'ok: silhouette + hole paths added to assets/<file>.svg'."""
        assert "silhouette + hole paths added to assets/" in MASK_AUTHOR.system_prompt

    def test_camera_pather_prompt_has_return_pattern(self) -> None:
        """Prompt must show 'camera path written' pattern."""
        assert "camera path written:" in CAMERA_PATHER.system_prompt

    def test_qa_critic_prompt_has_pass_pattern(self) -> None:
        """Prompt must show 'PASS' return option."""
        assert "PASS" in QA_CRITIC.system_prompt

    def test_qa_critic_prompt_has_fail_pattern(self) -> None:
        """Prompt must show 'FAIL:' return option."""
        assert "FAIL:" in QA_CRITIC.system_prompt

    def test_all_prompts_non_empty(self) -> None:
        for s in ALL_SUBAGENTS:
            assert len(s.system_prompt.strip()) > 0, f"{s.name} has empty prompt"


# ---------------------------------------------------------------------------
# AgentDefinition is frozen (immutable)
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_agent_definition_is_frozen(self) -> None:
        """AgentDefinition fields are immutable once created."""
        with pytest.raises((AttributeError, TypeError)):
            SCENE_DESIGNER.name = "mutated"  # type: ignore[misc]

    def test_allowed_tools_is_tuple(self) -> None:
        """allowed_tools must be a tuple (hashable, immutable)."""
        for s in ALL_SUBAGENTS:
            assert isinstance(s.allowed_tools, tuple), (
                f"{s.name}.allowed_tools must be a tuple"
            )


# ---------------------------------------------------------------------------
# Descriptions are non-empty
# ---------------------------------------------------------------------------

class TestDescriptions:
    def test_all_descriptions_non_empty(self) -> None:
        for s in ALL_SUBAGENTS:
            assert len(s.description.strip()) > 0, (
                f"{s.name} has empty description"
            )
