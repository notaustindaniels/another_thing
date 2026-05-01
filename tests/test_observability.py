"""
Tests for parallax_engine.observability

Covers:
  - pre_tool_hook writes to logs/tool_calls.jsonl
  - post_tool_hook appends to logs/tool_calls.jsonl
  - log_usage writes to logs/usage.jsonl
  - ObservabilityHooks wrapper deduplicates usage by message_id
  - Files are created on first write
  - Lines are valid JSONL
  - Non-serialisable results are repr()'d
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from parallax_engine.observability import (
    ObservabilityHooks,
    log_usage,
    post_tool_hook,
    pre_tool_hook,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    """Return a fresh temporary workspace directory."""
    return tmp_path


# ---------------------------------------------------------------------------
# pre_tool_hook
# ---------------------------------------------------------------------------


class TestPreToolHook:
    def test_creates_logs_dir(self, ws):
        pre_tool_hook(workspace=ws, tool_name="Read", tool_input={"file_path": "x.md"})
        assert (ws / "logs").is_dir()

    def test_creates_tool_calls_jsonl(self, ws):
        pre_tool_hook(workspace=ws, tool_name="Read", tool_input={"file_path": "x.md"})
        assert (ws / "logs" / "tool_calls.jsonl").exists()

    def test_record_is_valid_json(self, ws):
        pre_tool_hook(workspace=ws, tool_name="Write", tool_input={"file_path": "y.md"})
        lines = (ws / "logs" / "tool_calls.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "pre_tool"
        assert record["tool_name"] == "Write"

    def test_tool_input_is_preserved(self, ws):
        inp = {"file_path": "scene.yaml", "content": "hello"}
        pre_tool_hook(workspace=ws, tool_name="Write", tool_input=inp)
        lines = (ws / "logs" / "tool_calls.jsonl").read_text().strip().splitlines()
        record = json.loads(lines[0])
        assert record["tool_input"] == inp

    def test_agent_id_and_turn_recorded(self, ws):
        pre_tool_hook(
            workspace=ws,
            tool_name="Glob",
            tool_input={"pattern": "*.svg"},
            agent_id="asset-generator",
            turn=3,
        )
        record = json.loads((ws / "logs" / "tool_calls.jsonl").read_text().strip())
        assert record["agent_id"] == "asset-generator"
        assert record["turn"] == 3

    def test_appends_multiple_calls(self, ws):
        for i in range(5):
            pre_tool_hook(workspace=ws, tool_name="Read", tool_input={"n": i})
        lines = (ws / "logs" / "tool_calls.jsonl").read_text().strip().splitlines()
        assert len(lines) == 5


# ---------------------------------------------------------------------------
# post_tool_hook
# ---------------------------------------------------------------------------


class TestPostToolHook:
    def test_event_is_post_tool(self, ws):
        post_tool_hook(
            workspace=ws,
            tool_name="Read",
            tool_input={"file_path": "a.md"},
            tool_result="content here",
        )
        record = json.loads((ws / "logs" / "tool_calls.jsonl").read_text().strip())
        assert record["event"] == "post_tool"

    def test_duration_recorded(self, ws):
        post_tool_hook(
            workspace=ws,
            tool_name="Bash",
            tool_input={"command": "ls"},
            tool_result="file.txt\n",
            duration_ms=42.5,
        )
        record = json.loads((ws / "logs" / "tool_calls.jsonl").read_text().strip())
        assert record["duration_ms"] == 42.5

    def test_non_serialisable_result_is_repr(self, ws):
        class Unserializable:
            pass

        obj = Unserializable()
        post_tool_hook(
            workspace=ws,
            tool_name="Agent",
            tool_input={"description": "x"},
            tool_result=obj,
        )
        record = json.loads((ws / "logs" / "tool_calls.jsonl").read_text().strip())
        assert isinstance(record["tool_result"], str)
        assert "Unserializable" in record["tool_result"]

    def test_pre_and_post_interleave_in_same_file(self, ws):
        pre_tool_hook(workspace=ws, tool_name="Read", tool_input={})
        post_tool_hook(workspace=ws, tool_name="Read", tool_input={}, tool_result="ok")
        lines = (ws / "logs" / "tool_calls.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2
        events = [json.loads(l)["event"] for l in lines]
        assert events == ["pre_tool", "post_tool"]


# ---------------------------------------------------------------------------
# log_usage
# ---------------------------------------------------------------------------


class TestLogUsage:
    def test_creates_usage_jsonl(self, ws):
        log_usage(
            workspace=ws,
            message_id="msg_001",
            model="claude-sonnet-4-5",
            usage={"input_tokens": 100, "output_tokens": 50},
        )
        assert (ws / "logs" / "usage.jsonl").exists()

    def test_record_fields(self, ws):
        log_usage(
            workspace=ws,
            message_id="msg_abc",
            model="claude-haiku-3-5",
            usage={"input_tokens": 200, "output_tokens": 80},
            agent_id="qa-critic",
        )
        record = json.loads((ws / "logs" / "usage.jsonl").read_text().strip())
        assert record["message_id"] == "msg_abc"
        assert record["model"] == "claude-haiku-3-5"
        assert record["usage"]["input_tokens"] == 200
        assert record["agent_id"] == "qa-critic"

    def test_multiple_messages_appended(self, ws):
        for i in range(3):
            log_usage(
                workspace=ws,
                message_id=f"msg_{i:03d}",
                model="claude-sonnet-4-5",
                usage={"input_tokens": i * 10},
            )
        lines = (ws / "logs" / "usage.jsonl").read_text().strip().splitlines()
        assert len(lines) == 3


# ---------------------------------------------------------------------------
# ObservabilityHooks wrapper
# ---------------------------------------------------------------------------


class TestObservabilityHooks:
    def test_before_tool_writes_pre_event(self, ws):
        hooks = ObservabilityHooks(workspace=ws, agent_id="scene-designer")
        hooks.before_tool(tool_name="Read", tool_input={"file_path": "brief.md"})
        record = json.loads((ws / "logs" / "tool_calls.jsonl").read_text().strip())
        assert record["event"] == "pre_tool"
        assert record["agent_id"] == "scene-designer"

    def test_after_tool_increments_turn(self, ws):
        hooks = ObservabilityHooks(workspace=ws)
        for i in range(3):
            hooks.before_tool(tool_name="Read", tool_input={"n": i})
            hooks.after_tool(tool_name="Read", tool_input={"n": i}, tool_result="ok")
        lines = (ws / "logs" / "tool_calls.jsonl").read_text().strip().splitlines()
        records = [json.loads(l) for l in lines]
        # Turn should increment: 0, 0, 1, 1, 2, 2 (pre and post share the same turn value)
        pre_turns = [r["turn"] for r in records if r["event"] == "pre_tool"]
        assert pre_turns == [0, 1, 2]

    def test_record_usage_deduplicates_by_message_id(self, ws):
        hooks = ObservabilityHooks(workspace=ws, agent_id="director")
        usage = {"input_tokens": 100, "output_tokens": 50}
        hooks.record_usage(message_id="msg_dup", model="m", usage=usage)
        hooks.record_usage(message_id="msg_dup", model="m", usage=usage)
        hooks.record_usage(message_id="msg_new", model="m", usage=usage)
        lines = (ws / "logs" / "usage.jsonl").read_text().strip().splitlines()
        # msg_dup written once, msg_new written once → 2 lines total
        assert len(lines) == 2
        ids = [json.loads(l)["message_id"] for l in lines]
        assert "msg_dup" in ids
        assert "msg_new" in ids

    def test_no_cross_agent_contamination(self, ws):
        hooks_a = ObservabilityHooks(workspace=ws, agent_id="agent-a")
        hooks_b = ObservabilityHooks(workspace=ws, agent_id="agent-b")
        # Same message_id in separate hooks instances → both write (independent dedup sets)
        usage = {"input_tokens": 10}
        hooks_a.record_usage(message_id="msg_shared", model="m", usage=usage)
        hooks_b.record_usage(message_id="msg_shared", model="m", usage=usage)
        lines = (ws / "logs" / "usage.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2

    def test_tool_calls_jsonl_is_append_only(self, ws):
        hooks = ObservabilityHooks(workspace=ws)
        hooks.before_tool(tool_name="A", tool_input={})
        hooks.after_tool(tool_name="A", tool_input={}, tool_result="r1")
        hooks.before_tool(tool_name="B", tool_input={})
        hooks.after_tool(tool_name="B", tool_input={}, tool_result="r2")
        lines = (ws / "logs" / "tool_calls.jsonl").read_text().strip().splitlines()
        # 2 pre + 2 post = 4 lines; never overwritten
        assert len(lines) == 4
