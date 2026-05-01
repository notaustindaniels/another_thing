"""
tests/test_cli_harness.py — Tests for the 'harness' CLI subcommand (P3.M05).

Validates:
  - python -m parallax_engine harness --workspace <dir> --brief <text> exits 0
  - All five stub subagents are invoked and checkpoint their completion
  - workspace/logs/tool_calls.jsonl is non-empty with entries for each agent
  - workspace/logs/usage.jsonl is non-empty
  - Repeat runs (checkpoints already exist) also exit 0

SPEC anchors: §3.2, §3.7, §3.8, §8.3
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from parallax_engine.cli import build_parser, cmd_harness, main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fresh_workspace() -> Generator[Path, None, None]:
    """Provide a fresh temporary workspace directory for each test."""
    with tempfile.TemporaryDirectory(prefix="parallax_harness_test_") as tmp:
        yield Path(tmp)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_harness(workspace: Path, brief: str = "test") -> int:
    """Run cmd_harness via main() and return the exit code."""
    return main(["harness", "--workspace", str(workspace), "--brief", brief])


def _load_jsonl(path: Path) -> list[dict]:
    """Load all JSON records from a JSONL file."""
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


# ---------------------------------------------------------------------------
# Core functionality tests
# ---------------------------------------------------------------------------


class TestHarnessExitCode:
    """Harness subcommand must exit 0 on any stub run."""

    def test_exits_zero_fresh_workspace(self, fresh_workspace: Path) -> None:
        """Fresh workspace with no state: harness exits 0."""
        rc = _run_harness(fresh_workspace, brief="test")
        assert rc == 0

    def test_exits_zero_with_pre_existing_workspace(self, fresh_workspace: Path) -> None:
        """Run twice in the same workspace; both exits should be 0."""
        rc1 = _run_harness(fresh_workspace, brief="first run")
        rc2 = _run_harness(fresh_workspace, brief="second run")
        assert rc1 == 0
        assert rc2 == 0

    def test_exits_zero_with_long_brief(self, fresh_workspace: Path) -> None:
        """Harness handles a longer brief string without error."""
        brief = "Create a 10-second parallax animation of a foggy forest with mountains at golden hour"
        rc = _run_harness(fresh_workspace, brief=brief)
        assert rc == 0

    def test_cli_parser_recognises_harness(self) -> None:
        """build_parser() includes the harness subcommand."""
        parser = build_parser()
        args = parser.parse_args(["harness", "--workspace", "/tmp/ws", "--brief", "x"])
        assert args.command == "harness"
        assert args.workspace == "/tmp/ws"
        assert args.brief == "x"

    def test_harness_defaults(self) -> None:
        """--workspace and --brief have sensible defaults."""
        parser = build_parser()
        args = parser.parse_args(["harness"])
        assert args.workspace == "workspace"
        assert args.brief == "test scene"


class TestAllFiveAgentsInvoked:
    """All five stub subagents must appear in tool_calls.jsonl."""

    _EXPECTED_AGENTS = {
        "scene-designer",
        "asset-generator",
        "mask-author",
        "camera-pather",
        "qa-critic",
    }

    def _agent_names_in_log(self, workspace: Path) -> set[str]:
        """Return set of agent names seen in post_tool events."""
        records = _load_jsonl(workspace / "logs" / "tool_calls.jsonl")
        agents: set[str] = set()
        for rec in records:
            if rec.get("event") == "post_tool":
                # tool_name is "Agent:<agent-name>"
                tool_name: str = rec.get("tool_name", "")
                if tool_name.startswith("Agent:"):
                    agents.add(tool_name[len("Agent:"):])
        return agents

    def test_all_five_agents_appear_in_tool_calls(self, fresh_workspace: Path) -> None:
        """All five subagent names must appear in tool_calls.jsonl after a run."""
        _run_harness(fresh_workspace)
        found = self._agent_names_in_log(fresh_workspace)
        assert found == self._EXPECTED_AGENTS, (
            f"Missing agents: {self._EXPECTED_AGENTS - found}; "
            f"Extra agents: {found - self._EXPECTED_AGENTS}"
        )

    def test_each_agent_has_both_pre_and_post_events(self, fresh_workspace: Path) -> None:
        """Every agent should have a pre_tool and post_tool event."""
        _run_harness(fresh_workspace)
        records = _load_jsonl(workspace / "logs" / "tool_calls.jsonl")
        pre_agents: set[str] = set()
        post_agents: set[str] = set()
        for rec in records:
            tool_name: str = rec.get("tool_name", "")
            if tool_name.startswith("Agent:"):
                name = tool_name[len("Agent:"):]
                if rec.get("event") == "pre_tool":
                    pre_agents.add(name)
                elif rec.get("event") == "post_tool":
                    post_agents.add(name)
        assert pre_agents == self._EXPECTED_AGENTS
        assert post_agents == self._EXPECTED_AGENTS

    # Fixture override — need fresh_workspace in scope for the inner method
    def test_each_agent_has_both_pre_and_post_events(self, fresh_workspace: Path) -> None:  # noqa: F811
        """Every agent should have a pre_tool and post_tool event."""
        workspace = fresh_workspace
        _run_harness(workspace)
        records = _load_jsonl(workspace / "logs" / "tool_calls.jsonl")
        pre_agents: set[str] = set()
        post_agents: set[str] = set()
        for rec in records:
            tool_name: str = rec.get("tool_name", "")
            if tool_name.startswith("Agent:"):
                name = tool_name[len("Agent:"):]
                if rec.get("event") == "pre_tool":
                    pre_agents.add(name)
                elif rec.get("event") == "post_tool":
                    post_agents.add(name)
        assert pre_agents == self._EXPECTED_AGENTS
        assert post_agents == self._EXPECTED_AGENTS


class TestCheckpoints:
    """All pipeline phases must be checkpointed after a run."""

    _EXPECTED_PHASES = {
        "manifest",
        "assets-done",
        "masks-done",
        "camera-done",
        "render-done",
        "qa-pass-1",
    }

    def test_checkpoint_file_exists(self, fresh_workspace: Path) -> None:
        """checkpoints/state.json is created after a run."""
        _run_harness(fresh_workspace)
        checkpoint = fresh_workspace / "checkpoints" / "state.json"
        assert checkpoint.exists(), "checkpoints/state.json not created"

    def test_all_phases_checkpointed(self, fresh_workspace: Path) -> None:
        """All expected phases appear in the checkpoint."""
        _run_harness(fresh_workspace)
        checkpoint = fresh_workspace / "checkpoints" / "state.json"
        state = json.loads(checkpoint.read_text())
        completed = set(state.get("phases", {}).keys())
        assert self._EXPECTED_PHASES.issubset(completed), (
            f"Missing phases: {self._EXPECTED_PHASES - completed}"
        )

    def test_checkpoint_timestamps_are_iso8601(self, fresh_workspace: Path) -> None:
        """Phase timestamps must look like ISO-8601 strings."""
        _run_harness(fresh_workspace)
        checkpoint = fresh_workspace / "checkpoints" / "state.json"
        state = json.loads(checkpoint.read_text())
        for phase, ts in state["phases"].items():
            assert isinstance(ts, str) and "T" in ts, (
                f"Phase {phase!r} timestamp {ts!r} is not ISO-8601"
            )


class TestObservabilityLogs:
    """tool_calls.jsonl and usage.jsonl must be non-empty."""

    def test_tool_calls_jsonl_non_empty(self, fresh_workspace: Path) -> None:
        """workspace/logs/tool_calls.jsonl must exist and have content."""
        _run_harness(fresh_workspace)
        path = fresh_workspace / "logs" / "tool_calls.jsonl"
        assert path.exists(), "tool_calls.jsonl not created"
        records = _load_jsonl(path)
        assert len(records) >= 5, (
            f"Expected >= 5 records (one per agent), got {len(records)}"
        )

    def test_usage_jsonl_non_empty(self, fresh_workspace: Path) -> None:
        """workspace/logs/usage.jsonl must exist and have at least one record."""
        _run_harness(fresh_workspace)
        path = fresh_workspace / "logs" / "usage.jsonl"
        assert path.exists(), "usage.jsonl not created"
        records = _load_jsonl(path)
        assert len(records) >= 1, "usage.jsonl must have at least one entry"

    def test_tool_calls_records_are_valid_json(self, fresh_workspace: Path) -> None:
        """Every line in tool_calls.jsonl must be valid JSON."""
        _run_harness(fresh_workspace)
        path = fresh_workspace / "logs" / "tool_calls.jsonl"
        raw = path.read_text(encoding="utf-8")
        for i, line in enumerate(raw.splitlines()):
            line = line.strip()
            if line:
                record = json.loads(line)  # raises if invalid
                assert "event" in record, f"Line {i}: missing 'event' key"
                assert "ts" in record, f"Line {i}: missing 'ts' key"

    def test_usage_records_have_required_fields(self, fresh_workspace: Path) -> None:
        """usage.jsonl records must have message_id, model, usage, ts."""
        _run_harness(fresh_workspace)
        path = fresh_workspace / "logs" / "usage.jsonl"
        records = _load_jsonl(path)
        for rec in records:
            assert "message_id" in rec, f"usage record missing message_id: {rec}"
            assert "ts" in rec, f"usage record missing ts: {rec}"
            assert "usage" in rec, f"usage record missing usage: {rec}"

    def test_tool_calls_appended_on_second_run(self, fresh_workspace: Path) -> None:
        """Running harness twice appends to tool_calls.jsonl (not truncates)."""
        _run_harness(fresh_workspace, brief="run 1")
        path = fresh_workspace / "logs" / "tool_calls.jsonl"
        count_after_1 = len(_load_jsonl(path))
        assert count_after_1 >= 5

        # Second run: checkpoints already exist → all phases skipped
        _run_harness(fresh_workspace, brief="run 2")
        count_after_2 = len(_load_jsonl(path))

        # usage.jsonl gets a new entry each run
        usage_path = fresh_workspace / "logs" / "usage.jsonl"
        usage_count = len(_load_jsonl(usage_path))
        assert usage_count >= 2, (
            "usage.jsonl should have at least 2 entries after two runs"
        )


class TestWorkspaceStructure:
    """Workspace directory structure must match §3.2."""

    _REQUIRED_DIRS = ["assets", "masks", "frames", "qa", "logs", "checkpoints"]

    def test_workspace_dirs_created(self, fresh_workspace: Path) -> None:
        """All required workspace subdirectories must exist after a run."""
        _run_harness(fresh_workspace)
        for subdir in self._REQUIRED_DIRS:
            path = fresh_workspace / subdir
            assert path.is_dir(), f"Missing workspace directory: {subdir}/"

    def test_brief_written_to_disk(self, fresh_workspace: Path) -> None:
        """brief.md must exist in the workspace after a run."""
        _run_harness(fresh_workspace, brief="my test brief")
        brief_path = fresh_workspace / "brief.md"
        assert brief_path.exists(), "brief.md not written"
        content = brief_path.read_text(encoding="utf-8")
        assert "my test brief" in content

    def test_scene_yaml_seeded(self, fresh_workspace: Path) -> None:
        """scene.yaml must be created in the workspace (smoke-test seed)."""
        _run_harness(fresh_workspace)
        scene_yaml = fresh_workspace / "scene.yaml"
        assert scene_yaml.exists(), "scene.yaml not seeded"
        # Must be valid YAML with stacks and at least one layer
        import yaml  # type: ignore[import]
        doc = yaml.safe_load(scene_yaml.read_text())
        assert "stacks" in doc, "scene.yaml missing 'stacks'"


class TestSmokeSceneYAML:
    """The built-in smoke-test scene.yaml must have the right structure."""

    def test_smoke_scene_yaml_has_one_layer(self) -> None:
        """Smoke scene has exactly 1 layer so asset-generator is dispatched once."""
        from parallax_engine.cli import _SMOKE_SCENE_YAML
        import yaml  # type: ignore[import]
        doc = yaml.safe_load(_SMOKE_SCENE_YAML)
        stacks = doc.get("stacks", {})
        layer_ids = []
        for stack_val in stacks.values():
            if isinstance(stack_val, list):
                for layer in stack_val:
                    if isinstance(layer, dict) and "id" in layer:
                        layer_ids.append(layer["id"])
        assert len(layer_ids) >= 1, "Smoke scene must have at least 1 layer"

    def test_smoke_scene_yaml_has_one_mask(self) -> None:
        """Smoke scene has exactly 1 mask so mask-author is dispatched once."""
        from parallax_engine.cli import _SMOKE_SCENE_YAML
        import yaml  # type: ignore[import]
        doc = yaml.safe_load(_SMOKE_SCENE_YAML)
        masks = doc.get("masks", [])
        assert len(masks) >= 1, "Smoke scene must have at least 1 mask"
