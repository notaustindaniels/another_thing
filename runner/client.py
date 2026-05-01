"""
Claude Agent SDK client factory for parallax-engine.

Differences from the autonomous-coding template:

  - No Puppeteer MCP. parallax-engine is a Python project producing MP4s,
    not a web app. Browser automation has no role here.
  - Permissions tightened to the project directory only.
  - Bash allowlist enforced by security.bash_security_hook with the
    parallax-engine-specific rules (forbidden Python/system packages,
    dev-process pkill, +x-only chmod, ./init.sh-only script execution).
  - System prompt names parallax-engine explicitly so the agent has the
    right framing from message 1.
"""

from __future__ import annotations

import json
from pathlib import Path

from claude_code_sdk import ClaudeCodeOptions, ClaudeSDKClient
from claude_code_sdk.types import HookMatcher

from security import bash_security_hook


BUILTIN_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]

SYSTEM_PROMPT = (
    "You are an expert Python engineer building parallax-engine, a commercial "
    "Skill that generates 2.5D multiplane animation MP4s from natural-language "
    "briefs. Read SPEC.md as the source of truth. Licensing constraints are "
    "non-negotiable. Phase gates are enforced. Determinism is required. "
    "Work one milestone per session and end the session cleanly."
)


def create_client(project_dir: Path, model: str) -> ClaudeSDKClient:
    """Build a configured ClaudeSDKClient for one session.

    The caller is responsible for the async context manager lifecycle:

        client = create_client(project_dir, model)
        async with client:
            await client.query(prompt)
            async for msg in client.receive_response():
                ...
    """
    project_dir.mkdir(parents=True, exist_ok=True)

    settings = {
        "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True},
        "permissions": {
            "defaultMode": "acceptEdits",
            "allow": [
                # Project filesystem only. Read tools/ for validators but
                # never write to it.
                "Read(./**)",
                "Write(./**)",
                "Edit(./**)",
                "Glob(./**)",
                "Grep(./**)",
                # Bash is gated by bash_security_hook (see security.py).
                # Granting Bash(*) here grants the *channel*; the hook
                # decides per-command whether to permit it.
                "Bash(*)",
            ],
            "deny": [
                # Hard-deny tools/. The agent must never edit validators.
                "Write(./tools/**)",
                "Edit(./tools/**)",
                # Hard-deny references/. Pre-extracted reference materials
                # are read-only inputs.
                "Write(./references/**)",
                "Edit(./references/**)",
            ],
        },
    }

    settings_file = project_dir / ".claude_settings.json"
    settings_file.write_text(json.dumps(settings, indent=2))

    return ClaudeSDKClient(
        options=ClaudeCodeOptions(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            allowed_tools=list(BUILTIN_TOOLS),
            hooks={
                "PreToolUse": [
                    HookMatcher(matcher="Bash", hooks=[bash_security_hook]),
                ],
            },
            max_turns=1000,
            cwd=str(project_dir.resolve()),
            settings=str(settings_file.resolve()),
        )
    )
