"""
Session loop for the parallax-engine autonomous build.

One call to run_session() runs one Claude Agent SDK session end-to-end.
The session loop is in autonomous_runner.py.

Per-session responsibilities here:
  - Send the prompt (initializer or coding, possibly with budget warning).
  - Stream the response, printing tool calls / text / errors.
  - Accumulate token usage onto the budget session record.
  - Detect outcome (completed | error | budget) for logging.
  - Catch exceptions so a single bad session doesn't kill the run.
"""

from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Any

from claude_code_sdk import ClaudeSDKClient

from budget import BudgetState, SessionUsage, accumulate_message, save_state


def _extract_usage(msg: Any) -> dict[str, int] | None:
    """Pull token usage off an assistant message, regardless of whether the
    SDK exposes it as a dict, a Pydantic model, or a plain object with
    attributes. Returns None if no usage info is present."""
    if msg is None:
        return None
    usage = getattr(msg, "usage", None)
    if usage is None and isinstance(msg, dict):
        usage = msg.get("usage")
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    # Pydantic model or similar
    out: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens",
                "cache_read_input_tokens", "cache_creation_input_tokens"):
        val = getattr(usage, key, None)
        if val is not None:
            out[key] = int(val)
    return out or None


def _truncate(text: str, n: int = 200) -> str:
    text = str(text)
    return text if len(text) <= n else text[: n - 3] + "..."


async def run_session(
    client: ClaudeSDKClient,
    prompt: str,
    session: SessionUsage,
    project_dir: Path | None = None,
    state: BudgetState | None = None,
    persist_interval_s: float = 30.0,
) -> tuple[str, str]:
    """Run one session. Returns (outcome, response_text).

    outcome is one of: "completed", "error".
    The runner classifies "budget" by checking the budget state separately
    after the session ends.

    If both `project_dir` and `state` are supplied, the budget state is
    persisted to disk every `persist_interval_s` seconds during streaming.
    This protects token accounting against mid-session crashes; without it,
    a Ctrl-C 20 minutes into a session loses all token usage incurred since
    the session started.
    """
    print("[session] sending prompt...\n")
    full_response: list[str] = []
    last_persist = time.time()
    can_persist = project_dir is not None and state is not None

    try:
        await client.query(prompt)

        async for msg in client.receive_response():
            msg_type = type(msg).__name__

            # Pull usage off every message that has it
            usage = _extract_usage(msg)
            if usage:
                accumulate_message(session, usage)

            if msg_type == "AssistantMessage" and hasattr(msg, "content"):
                for block in msg.content:
                    block_type = type(block).__name__
                    if block_type == "TextBlock" and hasattr(block, "text"):
                        full_response.append(block.text)
                        print(block.text, end="", flush=True)
                    elif block_type == "ToolUseBlock" and hasattr(block, "name"):
                        print(f"\n[tool: {block.name}]", flush=True)
                        if hasattr(block, "input"):
                            print(f"   input: {_truncate(block.input)}", flush=True)
                    elif block_type == "ThinkingBlock":
                        # Don't print thinking content; it's noisy. The token
                        # cost is captured via usage already.
                        pass

            elif msg_type == "UserMessage" and hasattr(msg, "content"):
                for block in msg.content:
                    if type(block).__name__ == "ToolResultBlock":
                        result = getattr(block, "content", "")
                        is_error = getattr(block, "is_error", False)
                        if "blocked" in str(result).lower():
                            print(f"   [BLOCKED] {_truncate(result, 300)}", flush=True)
                        elif is_error:
                            print(f"   [error] {_truncate(result, 500)}", flush=True)
                        else:
                            print("   [ok]", flush=True)

            # Periodic budget persist (every 30s by default). Mid-session
            # crashes are infrequent but expensive when they happen.
            if can_persist and (time.time() - last_persist) > persist_interval_s:
                save_state(project_dir, state)
                last_persist = time.time()

        print("\n" + "─" * 70 + "\n")
        if can_persist:
            save_state(project_dir, state)
        return "completed", "".join(full_response)

    except Exception as e:
        print(f"\n[session] error during streaming: {type(e).__name__}: {e}")
        print(traceback.format_exc())
        if can_persist:
            save_state(project_dir, state)
        return "error", str(e)
