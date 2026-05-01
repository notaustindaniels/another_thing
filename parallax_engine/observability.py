"""
parallax_engine.observability
==============================
Pre/post tool hooks and usage logging for the agent harness.

Implements SPEC.md §3.7:
  - pre_tool_hook / post_tool_hook write append-only JSONL to logs/tool_calls.jsonl
  - log_usage writes append-only JSONL to logs/usage.jsonl, deduped by message_id

Both files are NEVER edited or rewritten — only appended.  Callers pass
the workspace root; logs go under workspace/logs/.

Design
------
The hooks are plain functions that accept structured dicts and write to disk.
They are not bound to any specific SDK version so they remain testable without
an Anthropic SDK import.  The agent harness wires them into ClaudeSDKClient
via its pre_tool_hook / post_tool_hook parameters.

Thread-safety
-------------
Each write is a single os.write() call on an O_APPEND-opened fd, which is
atomic on POSIX for writes <= PIPE_BUF (4096 bytes).  JSONL lines are well
under that limit.  No locking is required for single-process use.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_logs_dir(workspace: Path) -> Path:
    """Create workspace/logs/ if it doesn't exist and return the path."""
    logs_dir = workspace / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append a single JSON record as one line to *path* (append-only)."""
    line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
    # Use O_APPEND for atomicity on POSIX.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def _iso_now() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def pre_tool_hook(
    *,
    workspace: Path | str,
    tool_name: str,
    tool_input: dict[str, Any],
    agent_id: str = "",
    turn: int = 0,
) -> None:
    """
    Log a tool invocation *before* it executes.

    Parameters
    ----------
    workspace:
        Root workspace directory (logs go in workspace/logs/).
    tool_name:
        Name of the tool being called (e.g. "Agent", "Read", "Write").
    tool_input:
        The tool's input dict as passed by the SDK.
    agent_id:
        Optional identifier for the calling agent (e.g. "scene-designer").
    turn:
        Optional zero-based turn counter within the agent's conversation.
    """
    workspace = Path(workspace)
    logs_dir = _ensure_logs_dir(workspace)
    record: dict[str, Any] = {
        "event": "pre_tool",
        "ts": _iso_now(),
        "agent_id": agent_id,
        "turn": turn,
        "tool_name": tool_name,
        "tool_input": tool_input,
    }
    _append_jsonl(logs_dir / "tool_calls.jsonl", record)


def post_tool_hook(
    *,
    workspace: Path | str,
    tool_name: str,
    tool_input: dict[str, Any],
    tool_result: Any,
    agent_id: str = "",
    turn: int = 0,
    duration_ms: float = 0.0,
) -> None:
    """
    Log a tool invocation *after* it executes.

    Parameters
    ----------
    workspace:
        Root workspace directory.
    tool_name:
        Name of the tool that was called.
    tool_input:
        The tool's input dict.
    tool_result:
        The tool's result (will be JSON-serialized; non-serialisable objects
        are replaced with their repr()).
    agent_id:
        Optional identifier for the calling agent.
    turn:
        Optional zero-based turn counter.
    duration_ms:
        Wall-clock milliseconds the tool call took.
    """
    workspace = Path(workspace)
    logs_dir = _ensure_logs_dir(workspace)

    # Safe-serialise the result — some tools return raw strings or dicts.
    try:
        result_payload: Any = json.loads(json.dumps(tool_result))
    except (TypeError, ValueError):
        result_payload = repr(tool_result)

    record: dict[str, Any] = {
        "event": "post_tool",
        "ts": _iso_now(),
        "agent_id": agent_id,
        "turn": turn,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_result": result_payload,
        "duration_ms": round(duration_ms, 2),
    }
    _append_jsonl(logs_dir / "tool_calls.jsonl", record)


def log_usage(
    *,
    workspace: Path | str,
    message_id: str,
    model: str = "",
    usage: dict[str, Any],
    agent_id: str = "",
) -> None:
    """
    Log a ResultMessage.usage record to logs/usage.jsonl.

    Deduplication by message_id is the *caller's* responsibility — the
    harness should not call log_usage twice for the same message_id.  If
    it does, two lines will be written (not silently deduplicated) so that
    no data is lost.  The harness is documented to deduplicate in its own
    tracking set before calling this function.

    Parameters
    ----------
    workspace:
        Root workspace directory.
    message_id:
        The unique message ID from ResultMessage (used for deduplication
        by the caller).
    model:
        Model identifier string (e.g. "claude-sonnet-4-5-20251001").
    usage:
        Dict with token counts; expected keys: input_tokens, output_tokens,
        cache_creation_input_tokens, cache_read_input_tokens.
    agent_id:
        Optional identifier for the calling agent.
    """
    workspace = Path(workspace)
    logs_dir = _ensure_logs_dir(workspace)
    record: dict[str, Any] = {
        "ts": _iso_now(),
        "message_id": message_id,
        "agent_id": agent_id,
        "model": model,
        "usage": usage,
    }
    _append_jsonl(logs_dir / "usage.jsonl", record)


# ---------------------------------------------------------------------------
# Convenience class for harness integration
# ---------------------------------------------------------------------------


class ObservabilityHooks:
    """
    Stateful wrapper that tracks per-agent turn counts and seen message IDs
    so the harness can call pre/post hooks without managing counters manually.

    Usage::

        hooks = ObservabilityHooks(workspace=Path("workspace"), agent_id="scene-designer")
        # Before each SDK turn:
        hooks.before_tool(tool_name="Read", tool_input={"file_path": "brief.md"})
        # After each SDK turn:
        hooks.after_tool(tool_name="Read", tool_input={"file_path": "brief.md"},
                         tool_result="...", duration_ms=12.3)
        # After ResultMessage arrives:
        hooks.record_usage(message_id="msg_abc", model="claude-...", usage={...})
    """

    def __init__(self, *, workspace: Path | str, agent_id: str = "") -> None:
        self.workspace = Path(workspace)
        self.agent_id = agent_id
        self._turn: int = 0
        self._seen_message_ids: set[str] = set()

    def before_tool(
        self, *, tool_name: str, tool_input: dict[str, Any]
    ) -> None:
        """Call before each tool invocation."""
        pre_tool_hook(
            workspace=self.workspace,
            tool_name=tool_name,
            tool_input=tool_input,
            agent_id=self.agent_id,
            turn=self._turn,
        )

    def after_tool(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_result: Any,
        duration_ms: float = 0.0,
    ) -> None:
        """Call after each tool invocation; increments the turn counter."""
        post_tool_hook(
            workspace=self.workspace,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_result=tool_result,
            agent_id=self.agent_id,
            turn=self._turn,
            duration_ms=duration_ms,
        )
        self._turn += 1

    def record_usage(
        self,
        *,
        message_id: str,
        model: str = "",
        usage: dict[str, Any],
    ) -> None:
        """Log usage — silently drops duplicate message_ids."""
        if message_id in self._seen_message_ids:
            return
        self._seen_message_ids.add(message_id)
        log_usage(
            workspace=self.workspace,
            message_id=message_id,
            model=model,
            usage=usage,
            agent_id=self.agent_id,
        )
