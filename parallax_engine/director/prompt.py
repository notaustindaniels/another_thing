"""parallax_engine.director.prompt — Prompt building for the Director tier.

Implements SPEC.md §11.5.3 (canonical director prompt) and §11.9.1
(deterministic mode selection).

Prompt caching strategy (SPEC.md §11.12)
-----------------------------------------
The Anthropic Messages API supports ``cache_control: {"type": "ephemeral"}``
on system-prompt content blocks.  Cached blocks must be ≥ 1024 tokens and
appear at a stable position across requests.

We split the system prompt into three stable blocks:

  Block 0 — Director role + 10-point instructions (§11.5.3, ~900 words)
  Block 1 — JSON Schema verbatim from schema.json (~28 KB)
  Block 2 — Three worked examples from tests/storyboards/*.yaml (~12 KB)

All three are marked ``cache_control: ephemeral`` so they are cached on the
first call and billed at the 90%-discount cached rate on subsequent calls.
Only the user message (the brief text) varies per invocation.

Decomposed mode sub-prompts live in prompts/director/ per §11.5.3.
This module also writes them if the directory is missing.
"""
from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent
_PACKAGE_ROOT = _HERE.parent
_PROJECT_ROOT = _PACKAGE_ROOT.parent

_SCHEMA_JSON = _HERE / "schema.json"
_EXAMPLES_DIR = _PROJECT_ROOT / "tests" / "storyboards"
_PROMPTS_DIR = _PROJECT_ROOT / "prompts" / "director"


# ---------------------------------------------------------------------------
# DirectorBrief — the structured input to the director
# ---------------------------------------------------------------------------


class DirectorBrief(BaseModel):
    """User brief fed to the Director agent.

    Fields mirror the deterministic mode-selection rule from §11.9.1.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        description="Free-text brief describing the desired video."
    )
    target_duration_s: float = Field(
        default=15.0,
        gt=0,
        le=300,
        description="Desired total duration in seconds.",
    )
    requested_structure: str | None = Field(
        default=None,
        description=(
            "Optional: one of three_act | save_the_cat | establish_disrupt_resolve "
            "| biome_tour | portal_reveal | custom.  Overrides the director's default "
            "structure selection."
        ),
    )
    config_budget: str = Field(
        default="standard",
        description="Budget tier: thrift | standard | premium | longform.",
    )
    references: list[str] = Field(
        default_factory=list,
        description="List of named reference URIs (strings, not bytes).",
    )


# ---------------------------------------------------------------------------
# director_mode() — §11.9.1
# ---------------------------------------------------------------------------


def director_mode(brief: DirectorBrief) -> str:
    """Return 'single' or 'decomposed' deterministically.

    Rule (SPEC.md §11.9.1):

        def director_mode(brief):
            if brief.target_duration_s >= 60.0: return "decomposed"
            if brief.requested_structure == "save_the_cat": return "decomposed"
            if brief.config_budget == "thrift": return "single"
            return "single"

    This is a pure Python function.  The project manager calls it; the LLM
    director never makes this decision.
    """
    if brief.target_duration_s >= 60.0:
        return "decomposed"
    if brief.requested_structure == "save_the_cat":
        return "decomposed"
    if brief.config_budget == "thrift":
        return "single"
    return "single"


# ---------------------------------------------------------------------------
# Stable-prefix content (cached across all director invocations)
# ---------------------------------------------------------------------------

# System prompt text per §11.5.3 — verbatim except whitespace normalised.
_DIRECTOR_ROLE_INSTRUCTIONS = textwrap.dedent("""\
You are the Director of `parallax-engine`, an automated 2.5D parallax animation pipeline. \
Your sole responsibility is to convert a user brief — sometimes accompanied by mood-board \
reference URIs — into a single `storyboard.yaml` artifact that downstream agents will consume \
to produce a short-form animated MP4.

You are not a renderer. You will never see pixels. You will never see SVG markup. You will \
never produce camera path coordinates, layer Z values, or asset filenames. You produce \
*creative direction*, expressed as structured YAML, and nothing else. The implementation of \
your direction is the job of subordinate agents whose work you will not observe.

Treat yourself as a director in pre-production. Your closest analogues are the directors who \
develop a film's color script, casting bible, and storyboard before a single frame is animated. \
You have all the constraints of that role: every decision you make must make sense across the \
whole piece, not in isolation; every recurring element must be specified once, canonically, so \
that scene-level interpretations cannot drift; and the dramatic arc must do the work — there \
is no audio, no dialogue, no voiceover, only image, time and motion.

Your output is a single YAML document conforming exactly to the storyboard schema attached \
below. The schema is non-negotiable. You must:

1. **Read the brief carefully**, including any reference URIs. References are URIs only — you \
cannot see image content. Treat them as named visual hints (the user telling you "look at this \
kind of thing") rather than as data. Refer to them by name in `look_references` and your prose.

2. **Decide a structure** before writing scenes. For pieces ≤ 12 s use \
`establish_disrupt_resolve` or a `biome_tour` or a `portal_reveal`. For 12–30 s any of the \
above plus `three_act`. For ≥ 30 s, `three_act`, `save_the_cat`, or a justified `custom`. \
Write the `arc` block first internally before writing scenes.

3. **Decide visual vocabulary globally.** Pick the palette, shape language, density curve, and \
lighting mood for the whole piece. Every scene inherits these unless it has an explicit, \
justified `palette_override`. Resist scene-local invention — the strength of the piece comes \
from constraint.

4. **Cast before you write scenes.** If the same motif, character, or prop is going to appear \
in more than one scene, add it to `casting` *before* writing the scenes that use it. Each \
casting entry's `canonical_description` must be self-sufficient — an asset-generator must be \
able to produce a faithful SVG from that description alone, with no other context.

5. **Write scenes with intent.** Each scene must have an `emotional_function` that contributes \
to the arc; a `camera_intent` chosen for that emotion (close camera moves for intimacy and \
rising action, pulled-back camera for release and scale, static held for breath); transitions \
paired correctly with neighbours; and concrete `motif_features` so the implementation tier \
knows what to put on screen.

6. **Pair transitions correctly.** A `biome_wipe` out of scene N requires a `biome_wipe` into \
scene N+1 with `paired_scene: N`. A `portal_reveal` is one transition spanning two scenes' \
stacks; both scenes must declare it with each other as `paired_scene`. A `match_cut` requires \
the donor and receiver to share a compositional anchor; describe that anchor in the donor's \
`director_notes`.

7. **Specify continuity rules** as `hard_rules`. Phrase them so a downstream QA critic can \
answer yes/no per scene. *"NO character appears"* is checkable; *"the mood should be \
melancholy"* is not.

8. **Capture audio intent** even though no audio will be generated. The pacing of music is \
information about the pacing of image; capturing it forces you to think about pacing.

9. **Don't over-specify.** You are not the scene-designer. Do not write SVG. Do not write Z \
values. Do not write camera paths. Do not write Pillow filter parameters. If you find yourself \
reaching for those, you are doing the wrong job.

10. **Don't ask the user back.** If the brief is ambiguous, pick the most defensible \
interpretation and proceed. State your assumption in `director_notes`. Asking back is reserved \
for cases where the brief is internally contradictory.

When in doubt, reach for restraint. Short-form video that tries to do too many things does none \
of them. Pick a single emotion, give it space, and resolve it.

Your output must be a single YAML document. Wrap it in a single fenced block tagged `yaml`. \
No prose outside the block.

Below: the formal schema. Below that: three worked examples. Below that: the user's brief and \
any references.
""")


def _load_schema_text() -> str:
    """Load schema.json as formatted text for the system prompt."""
    return _SCHEMA_JSON.read_text(encoding="utf-8")


def _load_examples_text() -> str:
    """Concatenate all three example YAML files into one block for the prompt."""
    parts: list[str] = []
    # Deterministic order: a, b, c
    for name in ("example_a.yaml", "example_b.yaml", "example_c.yaml"):
        path = _EXAMPLES_DIR / name
        if path.exists():
            parts.append(f"### Example: {name}\n\n```yaml\n{path.read_text('utf-8').strip()}\n```")
    return "\n\n---\n\n".join(parts)


def _schema_block() -> dict[str, Any]:
    """Return the schema system-prompt block with cache_control."""
    return {
        "type": "text",
        "text": (
            "## Storyboard Schema (JSON)\n\n"
            "```json\n"
            + _load_schema_text()
            + "\n```"
        ),
        "cache_control": {"type": "ephemeral"},
    }


def _examples_block() -> dict[str, Any]:
    """Return the examples system-prompt block with cache_control."""
    return {
        "type": "text",
        "text": "## Worked Examples\n\n" + _load_examples_text(),
        "cache_control": {"type": "ephemeral"},
    }


def _role_block() -> dict[str, Any]:
    """Return the role+instructions system-prompt block with cache_control."""
    return {
        "type": "text",
        "text": _DIRECTOR_ROLE_INSTRUCTIONS,
        "cache_control": {"type": "ephemeral"},
    }


# ---------------------------------------------------------------------------
# Public prompt-building API
# ---------------------------------------------------------------------------


def build_single_mode_system_blocks() -> list[dict[str, Any]]:
    """Build the system-prompt content blocks for single-mode director call.

    Returns a list of three content blocks, each marked ``cache_control:
    ephemeral``.  The order is:

      [0] role + 10-point instructions
      [1] JSON schema
      [2] three worked examples

    These are stable across all director invocations.  Pass them as the
    ``system`` parameter to ``anthropic.Anthropic().messages.create()``.
    """
    return [_role_block(), _schema_block(), _examples_block()]


def build_single_mode_messages(brief: DirectorBrief) -> list[dict[str, Any]]:
    """Build the full messages list for a single-mode director call.

    The system prompt (stable, cached) is returned separately via
    ``build_single_mode_system_blocks()``.  This function returns only the
    ``messages`` list (user turn), which contains only the variable brief
    text and is NOT cached.

    Usage::

        system = build_single_mode_system_blocks()
        messages = build_single_mode_messages(brief)
        response = client.messages.create(
            model=MODEL_OPUS,
            system=system,
            messages=messages,
            max_tokens=8192,
        )
    """
    user_text = _format_brief_for_user_turn(brief)
    return [{"role": "user", "content": user_text}]


def _format_brief_for_user_turn(brief: DirectorBrief) -> str:
    """Format brief + references as the user-turn text."""
    lines: list[str] = ["## User Brief\n"]
    lines.append(brief.text.strip())
    lines.append("")
    if brief.target_duration_s:
        lines.append(f"**Target duration:** {brief.target_duration_s} s")
    if brief.requested_structure:
        lines.append(f"**Requested structure:** {brief.requested_structure}")
    if brief.references:
        lines.append("")
        lines.append("**References (URIs only — treat as named visual hints):**")
        for ref in sorted(brief.references):
            lines.append(f"- {ref}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Decomposed mode sub-prompts
# ---------------------------------------------------------------------------

_BRIEF_DECOMPOSER_SYSTEM = textwrap.dedent("""\
You are the Brief Decomposer for `parallax-engine` (director-tier, step 1 of 4).
Your job is to read a free-text brief and convert it into a structured project \
treatment block for the next agent (the Arc Architect).

Output a single YAML document with these top-level keys:
  title:         string
  logline:       string (≤ 25 words)
  theme:         string (one word)
  summary:       string (2-4 sentences)
  intended_platform: enum (youtube_short | instagram_reel | tiktok | loop | web | custom)
  aspect_ratio:  string (e.g. "9:16")
  target_fps:    integer (24 or 30)
  total_duration_s: float
  look_references: list of {name: str, note: str}

Wrap in ```yaml ... ```.  No prose outside the block.
""")

_ARC_ARCHITECT_SYSTEM = textwrap.dedent("""\
You are the Arc Architect for `parallax-engine` (director-tier, step 2 of 4).
You receive a project treatment (from the Brief Decomposer) and produce the \
`arc` block plus a skeletal scenes list.

Output a YAML document with:
  arc:
    structure: <enum>
    theme: <string>
    beats: [{name, emotional_function, target_time_s}, ...]
  scenes:
    - index: 0
      duration_s: float
      emotional_function: string
      logline: string
    ...

Wrap in ```yaml ... ```.  No prose outside the block.
""")

_SCENE_ARCHITECT_SYSTEM = textwrap.dedent("""\
You are the Scene Architect for `parallax-engine` (director-tier, step 3 of 4).
You receive the full arc + skeletal scenes and flesh out every per-scene field.

For each scene you must specify: camera_intent, transition_in, transition_out, \
pacing, motif_features, must_feature, palette_override (if applicable), \
director_notes.

Also produce visual_vocabulary and casting blocks (cast entries that appear in \
more than one scene).

Wrap in ```yaml ... ```.  No prose outside the block.
""")

_CONTINUITY_CHECKER_SYSTEM = textwrap.dedent("""\
You are the Continuity Checker for `parallax-engine` (director-tier, step 4 of 4).
You receive the assembled storyboard YAML and validate it.

Check:
  - transition pairing (biome_wipe, portal_reveal must reference each other)
  - casting first_appearance_scene values are correct
  - callback_to_scene indices are backward-only
  - all paired_scene indices exist

Then produce a final storyboard.yaml adding a `continuity` block with \
hard_rules derived from the storyboard constraints.

Output the complete, final storyboard YAML. Wrap in ```yaml ... ```. \
No prose outside the block. If you find structural errors, note them in \
director_notes fields.
""")


def build_decomposed_system_prompts() -> dict[str, str]:
    """Return system prompts for each decomposed-mode sub-agent.

    Keys: 'brief_decomposer', 'arc_architect', 'scene_architect',
          'continuity_checker'.
    """
    return {
        "brief_decomposer": _BRIEF_DECOMPOSER_SYSTEM,
        "arc_architect": _ARC_ARCHITECT_SYSTEM,
        "scene_architect": _SCENE_ARCHITECT_SYSTEM,
        "continuity_checker": _CONTINUITY_CHECKER_SYSTEM,
    }


def build_decomposed_user_messages(
    brief: DirectorBrief,
    *,
    treatment_yaml: str | None = None,
    arc_yaml: str | None = None,
    full_storyboard_yaml: str | None = None,
) -> dict[str, str]:
    """Return user-turn messages for each decomposed-mode step.

    Pass previously-produced YAML strings to chain the steps together.
    Missing intermediates produce placeholder messages (for testing).

    Keys: 'brief_decomposer', 'arc_architect', 'scene_architect',
          'continuity_checker'.
    """
    brief_text = _format_brief_for_user_turn(brief)
    return {
        "brief_decomposer": brief_text,
        "arc_architect": (
            "## Project Treatment\n\n```yaml\n"
            + (treatment_yaml or "(awaiting brief-decomposer output)")
            + "\n```\n\nProduce the arc + skeletal scenes list."
        ),
        "scene_architect": (
            "## Arc + Skeletal Scenes\n\n```yaml\n"
            + (arc_yaml or "(awaiting arc-architect output)")
            + "\n```\n\nFlesh out per-scene fields, visual_vocabulary, and casting."
        ),
        "continuity_checker": (
            "## Assembled Storyboard (draft)\n\n```yaml\n"
            + (full_storyboard_yaml or "(awaiting scene-architect output)")
            + "\n```\n\nValidate and emit the final storyboard with continuity block."
        ),
    }


# ---------------------------------------------------------------------------
# Prompt file persistence (used at runtime to write prompts/director/ dir)
# ---------------------------------------------------------------------------


def ensure_prompts_directory() -> Path:
    """Write decomposed sub-prompts to prompts/director/ if missing.

    Returns the path to the prompts/director/ directory.
    """
    _PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in build_decomposed_system_prompts().items():
        path = _PROMPTS_DIR / f"{name}.md"
        if not path.exists():
            path.write_text(text, encoding="utf-8")
    return _PROMPTS_DIR


# ---------------------------------------------------------------------------
# YAML extraction helper
# ---------------------------------------------------------------------------


def extract_yaml_block(text: str) -> str:
    """Extract the first fenced ```yaml ... ``` block from text.

    Raises ValueError if no YAML fence is found.
    Used to parse director LLM responses.
    """
    match = re.search(r"```yaml\s*\n(.*?)```", text, re.DOTALL)
    if not match:
        raise ValueError(
            "Director response contained no ```yaml ... ``` block. "
            f"Response snippet: {text[:200]!r}"
        )
    return match.group(1).strip()
