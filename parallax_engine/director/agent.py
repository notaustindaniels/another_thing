"""parallax_engine.director.agent — Director agent runner.

Implements SPEC.md §11.5.1 (single + decomposed modes), §11.5.2 (model selection),
§11.5.3 (canonical prompt), and §11.9.1 (deterministic mode selection).

Architecture
------------
- ``DirectorStub``  — drop-in stub for offline tests; accepts a response map.
- ``DirectorAgent`` — real runner; uses either the stub or the Anthropic SDK.

The Anthropic SDK (``anthropic``) is imported lazily so this module is
importable without the package installed.  Tests always inject a stub.

Prompt caching (§11.12)
-----------------------
For single-mode calls, the stable system-prompt blocks (role + schema + examples)
are passed with ``cache_control: {"type": "ephemeral"}`` so the Anthropic API
caches them.  The user message (the brief) is never cached.

For decomposed-mode calls, each sub-agent receives a model-appropriate system
prompt; the first call (brief-decomposer) may cache the instructions block.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from parallax_engine.director.prompt import (
    DirectorBrief,
    build_decomposed_system_prompts,
    build_decomposed_user_messages,
    build_single_mode_messages,
    build_single_mode_system_blocks,
    director_mode,
    extract_yaml_block,
)
from parallax_engine.director.schema import Storyboard, load_storyboard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model constants (§11.5.2)
# ---------------------------------------------------------------------------

MODEL_OPUS = "claude-opus-4-7-20261231"
MODEL_SONNET = "claude-sonnet-4-6-20261231"
MODEL_HAIKU = "claude-haiku-4-5-20261231"

# Per-mode model selection table
_MODE_MODELS: dict[str, dict[str, str]] = {
    "single": {
        "director": MODEL_OPUS,
    },
    "decomposed": {
        "brief_decomposer": MODEL_SONNET,
        "arc_architect": MODEL_OPUS,
        "scene_architect": MODEL_OPUS,
        "continuity_checker": MODEL_SONNET,
    },
    "thrift": {
        "brief_decomposer": MODEL_HAIKU,
        "arc_architect": MODEL_SONNET,
        "scene_architect": MODEL_SONNET,
        "continuity_checker": MODEL_HAIKU,
    },
}


def _models_for_brief(brief: DirectorBrief) -> dict[str, str]:
    """Return model map for this brief's mode + budget."""
    mode = director_mode(brief)
    if mode == "single":
        return _MODE_MODELS["single"]
    if brief.config_budget == "thrift":
        return _MODE_MODELS["thrift"]
    return _MODE_MODELS["decomposed"]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class DirectorResult:
    """Result of a director invocation."""

    storyboard: Storyboard
    """Validated Pydantic model."""

    mode_used: str
    """'single' or 'decomposed'."""

    raw_yaml: str
    """Raw YAML string returned by the LLM (for debugging)."""

    steps: list[dict[str, Any]] = field(default_factory=list)
    """Decomposed-mode intermediates: list of {step, model, raw_response}."""


# ---------------------------------------------------------------------------
# Stub — for offline tests
# ---------------------------------------------------------------------------


class DirectorStub:
    """Drop-in stub LLM client for offline testing.

    Pass ``response_map`` keyed on step name:
      - ``"director"``         — single-mode response
      - ``"brief_decomposer"`` — decomposed step 1
      - ``"arc_architect"``    — decomposed step 2
      - ``"scene_architect"``  — decomposed step 3
      - ``"continuity_checker"``  — decomposed step 4

    Each value is either:
      - a plain string (the LLM text to return), OR
      - a path-like string to a YAML file (read and wrapped in ```yaml``` block)
    """

    def __init__(self, response_map: dict[str, str] | None = None) -> None:
        self._map: dict[str, str] = response_map or {}
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        model: str,
        system: list[dict[str, Any]] | str,
        messages: list[dict[str, Any]],
        step_name: str = "director",
        max_tokens: int = 8192,
    ) -> str:
        """Simulate a completion call; return the stub response text."""
        self.calls.append(
            {
                "model": model,
                "step_name": step_name,
                "n_messages": len(messages),
            }
        )
        raw = self._map.get(step_name, "")
        if not raw:
            raise ValueError(
                f"DirectorStub has no response for step={step_name!r}. "
                f"Available: {sorted(self._map)}"
            )
        # If the raw value looks like a file path, read it and wrap
        path = Path(raw)
        if path.suffix in (".yaml", ".yml") and path.exists():
            content = path.read_text("utf-8")
            return f"```yaml\n{content}\n```"
        return raw

    @property
    def call_count(self) -> int:
        return len(self.calls)


# ---------------------------------------------------------------------------
# Real LLM client (lazy import)
# ---------------------------------------------------------------------------


class _AnthropicClient:
    """Thin wrapper around anthropic.Anthropic().messages.create().

    Imported lazily; raises ImportError with a helpful message if the
    ``anthropic`` package is not installed.
    """

    def __init__(self) -> None:
        try:
            import anthropic  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "parallax-engine director: 'anthropic' package is not installed. "
                "Install it with: pip install anthropic"
            ) from exc
        self._client = anthropic.Anthropic()  # type: ignore[attr-defined]

    def complete(
        self,
        *,
        model: str,
        system: list[dict[str, Any]] | str,
        messages: list[dict[str, Any]],
        step_name: str = "director",
        max_tokens: int = 8192,
    ) -> str:
        """Call the Anthropic Messages API and return the response text."""
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
        )
        if isinstance(system, str):
            kwargs["system"] = system
        else:
            kwargs["system"] = system  # list of content blocks with cache_control
        resp = self._client.messages.create(**kwargs)
        # Extract text content
        for block in resp.content:
            if hasattr(block, "text"):
                return block.text
        return ""


# ---------------------------------------------------------------------------
# DirectorAgent — main runner
# ---------------------------------------------------------------------------


class DirectorAgent:
    """Runs the director in single or decomposed mode.

    Parameters
    ----------
    client:
        An object that implements ``.complete(model, system, messages, ...)``
        and returns a string.  Defaults to the real Anthropic client.
        Pass a ``DirectorStub`` for testing.
    """

    def __init__(self, client: Any | None = None) -> None:
        self._client = client  # None → lazy-create real client on first call

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = _AnthropicClient()
        return self._client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, brief: DirectorBrief) -> DirectorResult:
        """Invoke the director and return a validated DirectorResult.

        Selects single or decomposed mode via ``director_mode()`` (§11.9.1).
        """
        mode = director_mode(brief)
        logger.info("director_mode=%s for brief: %s", mode, brief.text[:60])
        if mode == "single":
            return self._run_single(brief)
        return self._run_decomposed(brief)

    # ------------------------------------------------------------------
    # Single mode
    # ------------------------------------------------------------------

    def _run_single(self, brief: DirectorBrief) -> DirectorResult:
        """One Opus call → storyboard YAML."""
        client = self._get_client()
        model = _models_for_brief(brief)["director"]
        system_blocks = build_single_mode_system_blocks()
        messages = build_single_mode_messages(brief)

        logger.debug("Director single-mode call: model=%s", model)
        raw = client.complete(
            model=model,
            system=system_blocks,
            messages=messages,
            step_name="director",
            max_tokens=8192,
        )
        raw_yaml = extract_yaml_block(raw)
        storyboard = _parse_and_validate(raw_yaml)
        return DirectorResult(
            storyboard=storyboard,
            mode_used="single",
            raw_yaml=raw_yaml,
        )

    # ------------------------------------------------------------------
    # Decomposed mode
    # ------------------------------------------------------------------

    def _run_decomposed(self, brief: DirectorBrief) -> DirectorResult:
        """Four-step chain: brief-decomposer → arc-architect → scene-architect
        → continuity-checker.

        Per §11.5.1:
          Step 1 — Brief-decomposer (Sonnet)
          Step 2 — Arc-architect (Opus)
          Step 3 — Scene-architect (Opus)
          Step 4 — Continuity-checker (Sonnet) — emits final storyboard YAML
        """
        client = self._get_client()
        models = _models_for_brief(brief)
        sys_prompts = build_decomposed_system_prompts()
        steps: list[dict[str, Any]] = []

        # Step 1 — Brief decomposer
        user_msgs_step1 = build_decomposed_user_messages(brief)
        raw1 = client.complete(
            model=models["brief_decomposer"],
            system=sys_prompts["brief_decomposer"],
            messages=[{"role": "user", "content": user_msgs_step1["brief_decomposer"]}],
            step_name="brief_decomposer",
            max_tokens=2048,
        )
        treatment_yaml = _try_extract_yaml(raw1)
        steps.append({"step": "brief_decomposer", "model": models["brief_decomposer"], "raw": raw1})
        logger.debug("brief_decomposer done, treatment_yaml length=%d", len(treatment_yaml))

        # Step 2 — Arc architect
        user_msgs_step2 = build_decomposed_user_messages(brief, treatment_yaml=treatment_yaml)
        raw2 = client.complete(
            model=models["arc_architect"],
            system=sys_prompts["arc_architect"],
            messages=[{"role": "user", "content": user_msgs_step2["arc_architect"]}],
            step_name="arc_architect",
            max_tokens=4096,
        )
        arc_yaml = _try_extract_yaml(raw2)
        steps.append({"step": "arc_architect", "model": models["arc_architect"], "raw": raw2})
        logger.debug("arc_architect done, arc_yaml length=%d", len(arc_yaml))

        # Step 3 — Scene architect
        user_msgs_step3 = build_decomposed_user_messages(brief, arc_yaml=arc_yaml)
        raw3 = client.complete(
            model=models["scene_architect"],
            system=sys_prompts["scene_architect"],
            messages=[{"role": "user", "content": user_msgs_step3["scene_architect"]}],
            step_name="scene_architect",
            max_tokens=8192,
        )
        full_yaml = _try_extract_yaml(raw3)
        steps.append({"step": "scene_architect", "model": models["scene_architect"], "raw": raw3})
        logger.debug("scene_architect done, full_yaml length=%d", len(full_yaml))

        # Step 4 — Continuity checker → final storyboard
        user_msgs_step4 = build_decomposed_user_messages(brief, full_storyboard_yaml=full_yaml)
        raw4 = client.complete(
            model=models["continuity_checker"],
            system=sys_prompts["continuity_checker"],
            messages=[{"role": "user", "content": user_msgs_step4["continuity_checker"]}],
            step_name="continuity_checker",
            max_tokens=8192,
        )
        final_yaml = extract_yaml_block(raw4)
        steps.append({"step": "continuity_checker", "model": models["continuity_checker"], "raw": raw4})
        logger.debug("continuity_checker done, final_yaml length=%d", len(final_yaml))

        storyboard = _parse_and_validate(final_yaml)
        return DirectorResult(
            storyboard=storyboard,
            mode_used="decomposed",
            raw_yaml=final_yaml,
            steps=steps,
        )


# ---------------------------------------------------------------------------
# Parse + validate helpers
# ---------------------------------------------------------------------------


def _parse_and_validate(raw_yaml: str) -> Storyboard:
    """Parse YAML and validate against the Storyboard Pydantic schema.

    Raises ValueError (with details) on parse failure.
    Raises pydantic.ValidationError on schema mismatch.
    """
    try:
        data = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise ValueError(f"Director output is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"Director output parsed as {type(data).__name__}, expected dict."
        )
    return load_storyboard(data)


def _try_extract_yaml(text: str) -> str:
    """Extract YAML block if present, else return raw text."""
    try:
        return extract_yaml_block(text)
    except ValueError:
        return text
