"""
Prompt loader for the parallax-engine build harness.

Two prompts only:
  - prompts/initializer_prompt.md  — used for session 1
  - prompts/coding_prompt.md       — used for sessions 2+

Both are loaded fresh from disk each session so editing the prompt files
between sessions takes effect on the next run without restarting the harness.
"""

from __future__ import annotations

from pathlib import Path


def _prompts_dir() -> Path:
    """Locate the prompts directory relative to this file's parent's parent.

    Layout is:
        runner/prompts.py     <- this file
        prompts/
            initializer_prompt.md
            coding_prompt.md
    """
    return Path(__file__).resolve().parent.parent / "prompts"


def load(name: str) -> str:
    """Load a prompt file by base name (without .md). Raises FileNotFoundError
    with a clear message if missing — the runner aborts the run rather than
    starting a session with an empty prompt."""
    path = _prompts_dir() / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"prompt file not found: {path}\n"
            f"Expected layout: <repo>/prompts/{name}.md\n"
            f"Run setup or check that the harness was launched from the repo root."
        )
    text = path.read_text()
    if not text.strip():
        raise ValueError(f"prompt file is empty: {path}")
    return text


def initializer() -> str:
    """The session-1 prompt. Substantial; lays out the whole build plan."""
    return load("initializer_prompt")


def coding() -> str:
    """The session-2+ prompt. Used for every continuation session."""
    return load("coding_prompt")


def with_warning(prompt: str, warning: str | None) -> str:
    """If the budget module produced a warning string, prepend it to the prompt
    so the agent sees it before reading any other instructions."""
    if not warning:
        return prompt
    return f"## ⚠️ HARNESS WARNING\n\n{warning}\n\n---\n\n{prompt}"
