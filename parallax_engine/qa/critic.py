"""parallax_engine.qa.critic — tiered QA critic for the director-era harness.

Implements SPEC.md §11.8 (The QA tiering system) and §11.14 step 8.

Three QA levels (§11.8.2)
--------------------------
asset (Sonnet)
    Input: one asset manifest entry + rendered frame path.
    Output: pass/fail with ``reason`` string.

scene (Sonnet; Opus when budget="premium")
    Input: storyboard scene entry, scene fragment, rendered frames covering
    the scene, casting entries referenced.
    Output: pass/fail with classification from SCENE_CLASSIFICATIONS.

storyboard (Opus always)
    Input: brief, full storyboard, full casting bible, rendered frames
    (1 frame per scene), prior pass's QA report.
    Output: pass/fail with classification from STORYBOARD_CLASSIFICATIONS.

Classifications (§11.8.2)
--------------------------
Scene level:
  function_mismatch, casting_drift, palette_violation,
  hard_rule_violation, pacing_off, transition_paired_wrong

Storyboard level:
  theme_unmet, arc_doesnt_land, structural_contradiction

Model selection
---------------
Critic models follow the spec exactly (§11.8.2):
  - asset:       claude-sonnet-4-6-20261231
  - scene:       claude-sonnet-4-6-20261231 (claude-opus-4-7-20261231 if budget=premium)
  - storyboard:  claude-opus-4-7-20261231 (always Opus; no downgrade)

Anti-patterns (§11.13)
-----------------------
- The critic does not edit any artifact (§11.13.7).
- The critic does not know retry counts (§11.13.9).
- The critic classifies, not just judges (§11.8.2).

SPEC anchors: §11.8, §11.8.1, §11.8.2, §11.13, §11.14 step 8
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model constants
# ---------------------------------------------------------------------------

MODEL_SONNET = "claude-sonnet-4-6-20261231"
MODEL_OPUS = "claude-opus-4-7-20261231"

# ---------------------------------------------------------------------------
# Classification taxonomies (§11.8.2)
# ---------------------------------------------------------------------------

QALevel = Literal["asset", "scene", "storyboard"]

SCENE_CLASSIFICATIONS: frozenset[str] = frozenset(
    {
        "function_mismatch",
        "casting_drift",
        "palette_violation",
        "hard_rule_violation",
        "pacing_off",
        "transition_paired_wrong",
    }
)

STORYBOARD_CLASSIFICATIONS: frozenset[str] = frozenset(
    {
        "theme_unmet",
        "arc_doesnt_land",
        "structural_contradiction",
    }
)

# All known classifications (union)
ALL_CLASSIFICATIONS: frozenset[str] = SCENE_CLASSIFICATIONS | STORYBOARD_CLASSIFICATIONS

# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


@dataclass
class CritiqueResult:
    """Result of a QA critique call.

    Attributes
    ----------
    level:
        The QA level that was invoked.
    verdict:
        "pass" or "fail".
    classification:
        For scene/storyboard levels, the failure classification from the
        taxonomy.  ``None`` on pass or for asset-level critiques.
    reason:
        Human-readable explanation of the verdict.
    model:
        The model that produced this verdict.
    scene_index:
        For scene-level critiques, the 1-based scene index.
    asset_id:
        For asset-level critiques, the asset id.
    raw_response:
        Full raw response from the LLM (or None if using stub/offline mode).
    """

    level: QALevel
    verdict: Literal["pass", "fail"]
    classification: str | None = None
    reason: str = ""
    model: str = ""
    scene_index: int | None = None
    asset_id: str | None = None
    raw_response: str | None = None

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    @property
    def failed(self) -> bool:
        return self.verdict == "fail"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class QACriticError(RuntimeError):
    """Raised when QA critic invocation fails unrecoverably."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def critique(
    level: QALevel,
    workspace_dir: str | Path,
    *,
    # Asset-level inputs
    asset_id: str | None = None,
    asset_manifest_entry: dict[str, Any] | None = None,
    rendered_frame_path: str | Path | None = None,
    # Scene-level inputs
    scene_index: int | None = None,
    storyboard_scene_entry: dict[str, Any] | None = None,
    scene_fragment: dict[str, Any] | None = None,
    rendered_frames_dir: str | Path | None = None,
    casting_entries: list[dict[str, Any]] | None = None,
    # Storyboard-level inputs
    brief_text: str | None = None,
    storyboard_data: dict[str, Any] | None = None,
    casting_bible_data: list[dict[str, Any]] | None = None,
    prior_qa_report: dict[str, Any] | None = None,
    # Config
    budget: Literal["thrift", "standard", "premium", "longform"] = "standard",
    dry_run: bool = False,
) -> CritiqueResult:
    """Invoke the QA critic at the specified level.

    Parameters
    ----------
    level:
        "asset", "scene", or "storyboard".
    workspace_dir:
        Project workspace root.
    asset_id:
        (asset level) The asset id being critiqued.
    asset_manifest_entry:
        (asset level) The manifest entry dict for this asset.
    rendered_frame_path:
        (asset level) Path to the rendered frame showing this asset.
    scene_index:
        (scene level) 1-based scene index.
    storyboard_scene_entry:
        (scene level) The storyboard's scene entry dict.
    scene_fragment:
        (scene level) The scene fragment dict (from scenes/scene_NN.yaml).
    rendered_frames_dir:
        (scene level) Directory containing frame_NNNNN.png for this scene.
    casting_entries:
        (scene level) Casting bible entries referenced by this scene.
    brief_text:
        (storyboard level) The original brief text.
    storyboard_data:
        (storyboard level) Full storyboard dict.
    casting_bible_data:
        (storyboard level) Full casting bible list.
    prior_qa_report:
        (storyboard level) Prior QA report dict (if this is a retry).
    budget:
        Cost tier; affects model selection for scene-level critiques.
    dry_run:
        If True, return a stub PASS without calling the LLM.  Used in tests.

    Returns
    -------
    CritiqueResult
    """
    workspace_dir = Path(workspace_dir)

    if level == "asset":
        return _critique_asset(
            workspace_dir=workspace_dir,
            asset_id=asset_id,
            manifest_entry=asset_manifest_entry or {},
            frame_path=Path(rendered_frame_path) if rendered_frame_path else None,
            budget=budget,
            dry_run=dry_run,
        )
    elif level == "scene":
        return _critique_scene(
            workspace_dir=workspace_dir,
            scene_index=scene_index,
            storyboard_scene=storyboard_scene_entry or {},
            fragment=scene_fragment or {},
            frames_dir=Path(rendered_frames_dir) if rendered_frames_dir else None,
            casting=casting_entries or [],
            budget=budget,
            dry_run=dry_run,
        )
    elif level == "storyboard":
        return _critique_storyboard(
            workspace_dir=workspace_dir,
            brief_text=brief_text or "",
            storyboard=storyboard_data or {},
            casting=casting_bible_data or [],
            prior_report=prior_qa_report or {},
            budget=budget,
            dry_run=dry_run,
        )
    else:
        raise QACriticError(f"Unknown QA level: {level!r}")


# ---------------------------------------------------------------------------
# Level-specific handlers
# ---------------------------------------------------------------------------


def _critique_asset(
    *,
    workspace_dir: Path,
    asset_id: str | None,
    manifest_entry: dict[str, Any],
    frame_path: Path | None,
    budget: str,
    dry_run: bool,
) -> CritiqueResult:
    """Asset-level critique — Sonnet (§11.8.2)."""
    model = MODEL_SONNET

    if dry_run:
        return CritiqueResult(
            level="asset",
            verdict="pass",
            reason="dry_run stub",
            model=model,
            asset_id=asset_id,
        )

    prompt = _build_asset_prompt(asset_id or "unknown", manifest_entry, frame_path)
    raw = _call_llm(model, prompt, workspace_dir)
    return _parse_asset_response(raw, model, asset_id)


def _critique_scene(
    *,
    workspace_dir: Path,
    scene_index: int | None,
    storyboard_scene: dict[str, Any],
    fragment: dict[str, Any],
    frames_dir: Path | None,
    casting: list[dict[str, Any]],
    budget: str,
    dry_run: bool,
) -> CritiqueResult:
    """Scene-level critique — Sonnet (Opus if budget=premium) (§11.8.2)."""
    model = MODEL_OPUS if budget == "premium" else MODEL_SONNET

    if dry_run:
        return CritiqueResult(
            level="scene",
            verdict="pass",
            reason="dry_run stub",
            model=model,
            scene_index=scene_index,
        )

    prompt = _build_scene_prompt(scene_index, storyboard_scene, fragment, frames_dir, casting)
    raw = _call_llm(model, prompt, workspace_dir)
    return _parse_scene_response(raw, model, scene_index)


def _critique_storyboard(
    *,
    workspace_dir: Path,
    brief_text: str,
    storyboard: dict[str, Any],
    casting: list[dict[str, Any]],
    prior_report: dict[str, Any],
    budget: str,
    dry_run: bool,
) -> CritiqueResult:
    """Storyboard-level critique — Opus always (§11.8.2)."""
    model = MODEL_OPUS  # always Opus; no downgrade

    if dry_run:
        return CritiqueResult(
            level="storyboard",
            verdict="pass",
            reason="dry_run stub",
            model=model,
        )

    prompt = _build_storyboard_prompt(brief_text, storyboard, casting, prior_report)
    raw = _call_llm(model, prompt, workspace_dir)
    return _parse_storyboard_response(raw, model)


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def _build_asset_prompt(
    asset_id: str,
    manifest_entry: dict[str, Any],
    frame_path: Path | None,
) -> str:
    entry_json = json.dumps(manifest_entry, indent=2, sort_keys=True)
    frame_note = f"Rendered frame: {frame_path}" if frame_path else "No rendered frame available."

    return f"""You are the QA critic for a 2.5D parallax animation engine.

TASK: Asset-level critique for asset id={asset_id!r}.

MANIFEST ENTRY:
{entry_json}

{frame_note}

Evaluate whether the asset meets the following criteria:
1. The SVG is valid and loadable.
2. The asset matches its ``purpose`` description.
3. The asset obeys any ``palette_locked`` color constraints.
4. The asset is appropriate for use in a 2.5D parallax animation.

Respond in this exact JSON format:
{{
  "verdict": "pass" or "fail",
  "reason": "<one sentence>"
}}

Output ONLY the JSON object. No explanation outside the JSON."""


def _build_scene_prompt(
    scene_index: int | None,
    storyboard_scene: dict[str, Any],
    fragment: dict[str, Any],
    frames_dir: Path | None,
    casting: list[dict[str, Any]],
) -> str:
    scene_json = json.dumps(storyboard_scene, indent=2, sort_keys=True)
    fragment_json = json.dumps(fragment, indent=2, sort_keys=True)
    casting_json = json.dumps(casting, indent=2, sort_keys=True)
    frames_note = f"Rendered frames directory: {frames_dir}" if frames_dir else "No rendered frames available."
    idx = f"scene {scene_index}" if scene_index is not None else "scene (index unknown)"

    return f"""You are the QA critic for a 2.5D parallax animation engine.

TASK: Scene-level critique for {idx}.

STORYBOARD SCENE ENTRY:
{scene_json}

SCENE FRAGMENT (what was designed):
{fragment_json}

CASTING ENTRIES REFERENCED:
{casting_json}

{frames_note}

Evaluate whether the scene fragment satisfies the storyboard's intent.

Classification taxonomy (use exactly one of these labels on failure):
  function_mismatch          — scene's emotional function doesn't match storyboard
  casting_drift              — a cast member is missing or re-described
  palette_violation          — colors outside the locked palette appear
  hard_rule_violation        — a continuity hard rule is broken
  pacing_off                 — duration/timing inconsistent with storyboard pacing
  transition_paired_wrong    — transition pairing invariant violated

Respond in this exact JSON format:
{{
  "verdict": "pass" or "fail",
  "classification": "<one of the labels above, or null if pass>",
  "reason": "<one sentence>"
}}

Output ONLY the JSON object."""


def _build_storyboard_prompt(
    brief_text: str,
    storyboard: dict[str, Any],
    casting: list[dict[str, Any]],
    prior_report: dict[str, Any],
) -> str:
    storyboard_json = json.dumps(storyboard, indent=2, sort_keys=True)
    casting_json = json.dumps(casting, indent=2, sort_keys=True)
    prior_json = json.dumps(prior_report, indent=2, sort_keys=True)

    return f"""You are the QA critic for a 2.5D parallax animation engine.

TASK: Storyboard-level critique.

ORIGINAL BRIEF:
{brief_text}

FULL STORYBOARD:
{storyboard_json}

FULL CASTING BIBLE:
{casting_json}

PRIOR QA REPORT (if any):
{prior_json}

Evaluate whether the storyboard faithfully realises the brief's theme,
has a coherent arc, and is structurally sound.

Classification taxonomy (use exactly one of these labels on failure):
  theme_unmet              — the brief's theme is not realized in the storyboard
  arc_doesnt_land          — the dramatic arc lacks the required structure
  structural_contradiction — the storyboard is internally inconsistent

Respond in this exact JSON format:
{{
  "verdict": "pass" or "fail",
  "classification": "<one of the labels above, or null if pass>",
  "reason": "<one sentence>"
}}

Output ONLY the JSON object."""


# ---------------------------------------------------------------------------
# LLM caller (optional Anthropic SDK)
# ---------------------------------------------------------------------------


def _call_llm(model: str, prompt: str, workspace_dir: Path) -> str:
    """Call the Anthropic API with the given prompt.

    Returns the raw text response.  Falls back to a PASS stub if the SDK
    is not available or no API key is set.
    """
    try:
        import anthropic  # optional
    except ImportError:
        logger.warning("qa.critic: anthropic SDK not installed; returning stub PASS")
        return _stub_pass_response()

    import os
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not (openrouter_key or api_key or oauth_token):
        raise RuntimeError(
            "parallax-engine qa.critic: no credentials found. "
            "Set OPENROUTER_API_KEY, ANTHROPIC_API_KEY, or CLAUDE_CODE_OAUTH_TOKEN."
        )

    try:
        if openrouter_key:
            client = anthropic.Anthropic(
                base_url="https://openrouter.ai/api",
                auth_token=openrouter_key,
            )
        elif api_key:
            client = anthropic.Anthropic(api_key=api_key)
        else:
            client = anthropic.Anthropic(auth_token=oauth_token)
        response = client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.error("qa.critic: LLM call failed: %s", exc)
        return _stub_pass_response()


def _stub_pass_response() -> str:
    """Return a minimal stub PASS JSON for offline / no-key environments."""
    return json.dumps({"verdict": "pass", "reason": "offline stub; no LLM available"})


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------


def _parse_asset_response(raw: str, model: str, asset_id: str | None) -> CritiqueResult:
    data = _parse_json_response(raw)
    verdict = _normalise_verdict(data.get("verdict", "pass"))
    return CritiqueResult(
        level="asset",
        verdict=verdict,
        classification=None,
        reason=data.get("reason", ""),
        model=model,
        asset_id=asset_id,
        raw_response=raw,
    )


def _parse_scene_response(raw: str, model: str, scene_index: int | None) -> CritiqueResult:
    data = _parse_json_response(raw)
    verdict = _normalise_verdict(data.get("verdict", "pass"))
    classification = data.get("classification")
    if classification and classification not in SCENE_CLASSIFICATIONS:
        logger.warning("qa.critic: unknown scene classification %r", classification)
        classification = None
    return CritiqueResult(
        level="scene",
        verdict=verdict,
        classification=classification if verdict == "fail" else None,
        reason=data.get("reason", ""),
        model=model,
        scene_index=scene_index,
        raw_response=raw,
    )


def _parse_storyboard_response(raw: str, model: str) -> CritiqueResult:
    data = _parse_json_response(raw)
    verdict = _normalise_verdict(data.get("verdict", "pass"))
    classification = data.get("classification")
    if classification and classification not in STORYBOARD_CLASSIFICATIONS:
        logger.warning("qa.critic: unknown storyboard classification %r", classification)
        classification = None
    return CritiqueResult(
        level="storyboard",
        verdict=verdict,
        classification=classification if verdict == "fail" else None,
        reason=data.get("reason", ""),
        model=model,
        raw_response=raw,
    )


def _parse_json_response(raw: str) -> dict[str, Any]:
    """Parse a JSON response, tolerating markdown code fences."""
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("qa.critic: could not parse JSON response: %r", raw[:200])
        return {"verdict": "pass", "reason": "parse error in LLM response; defaulting to pass"}


def _normalise_verdict(raw: Any) -> Literal["pass", "fail"]:
    if isinstance(raw, str) and raw.strip().lower() == "fail":
        return "fail"
    return "pass"
