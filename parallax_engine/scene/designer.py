"""parallax_engine.scene.designer — Scene Designer agent.

Implements SPEC.md §11.6 (The Scene Designer / Tier 2) and §11.14 step 5.

Role
----
The scene-designer is a **translator**: it takes the storyboard, casting bible,
prior scene fragments (indices < this scene), and a scene index, and returns:
- A ``SceneFragment`` — a validated YAML-serialisable dict for one scene
  (no project-level metadata such as fps, resolution, or seed).
- A list of ``ManifestEntry`` objects — one per SVG asset this scene needs,
  with kind = ``canonical`` | ``produce_canonical`` | ``local``.

The critical invariant for ``produce_canonical`` entries: the
``canonical_description`` is always taken from the casting bible, never from
the LLM's output.  This prevents style drift via re-invented descriptions.

Architecture
------------
SceneDesignerStub
    Drop-in stub for offline tests.  Returns caller-supplied YAML+JSON strings.

SceneDesigner
    Real runner.  Uses either the stub or the Anthropic SDK.  One Sonnet call
    per scene (§11.12.2 cost model).

Prompt caching
--------------
The stable part of the system prompt (role description + schema block) is
cached.  The per-scene user message (storyboard + casting + prior fragments)
is never cached.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from parallax_engine.casting.bible import CastingBible
from parallax_engine.director.schema import CastingEntry, Storyboard

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

MODEL_SONNET = "claude-sonnet-4-6-20261231"

# Keys that belong to the renderer's project/meta block — must NOT appear
# in a scene fragment.
_FORBIDDEN_FRAGMENT_KEYS: frozenset[str] = frozenset(
    {
        "version",
        "fps",
        "resolution",
        "seed",
        "meta",
        "perspective_px",
        "origin",
        "bg_color",
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Manifest entry
# ─────────────────────────────────────────────────────────────────────────────

ManifestKind = Literal["canonical", "produce_canonical", "local"]


class ManifestEntry(BaseModel):
    """One asset manifest entry (§11.6.3).

    kind:
        ``canonical``         — cast member whose SVG already exists; path is known.
        ``produce_canonical`` — first reference to a cast member; asset-generator
                               must produce it.  canonical_description MUST be
                               copied from casting.yaml, not invented by the LLM.
        ``local``             — one-off local asset; not in the casting bible.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Asset id (snake_case)")
    kind: ManifestKind
    purpose: str = Field(description="One-line description of how this asset is used")
    canonical_description: str | None = Field(
        default=None,
        description=(
            "Required when kind == 'produce_canonical'; "
            "populated from casting.yaml by SceneDesigner.run(), not by the LLM"
        ),
    )
    path: str | None = Field(
        default=None,
        description=(
            "Expected SVG path.  For produce_canonical: 'assets/canon/<id>.svg'. "
            "For canonical: the existing path from casting.yaml."
        ),
    )

    @model_validator(mode="after")
    def _require_canonical_description_for_produce(self) -> "ManifestEntry":
        if self.kind == "produce_canonical" and not self.canonical_description:
            raise ValueError(
                "canonical_description is required when kind == 'produce_canonical'"
            )
        return self


# ─────────────────────────────────────────────────────────────────────────────
# Scene fragment
# ─────────────────────────────────────────────────────────────────────────────


class SceneFragment(BaseModel):
    """Scene fragment — one scene's content, no project-level metadata (§11.6.3).

    Required fields: ``scene_index`` and ``duration_s``.
    Forbidden fields: any project/renderer metadata (fps, resolution, seed, etc.).
    Everything else is allowed (stacks, camera, masks, transitions, qa_self_check,
    scene_designer_protest, …).
    """

    model_config = ConfigDict(extra="allow")

    scene_index: int = Field(ge=1, description="1-based scene index this fragment covers")
    duration_s: float = Field(gt=0.0, description="Duration of this scene in seconds")

    @model_validator(mode="before")
    @classmethod
    def _reject_project_keys(cls, data: Any) -> Any:
        """Reject project-level keys that must not appear in a fragment."""
        if not isinstance(data, dict):
            return data
        bad = _FORBIDDEN_FRAGMENT_KEYS & set(data.keys())
        if bad:
            raise ValueError(
                f"Fragment must not contain project-level keys: {sorted(bad)}.  "
                "The merger adds these from the storyboard; the fragment only "
                "describes one scene's content."
            )
        return data


# ─────────────────────────────────────────────────────────────────────────────
# Input / Output dataclasses
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SceneDesignerInput:
    """Inputs for a single scene-designer invocation."""

    storyboard: Storyboard
    casting: CastingBible
    prior_fragments: list[dict[str, Any]]
    """Raw dicts (already parsed YAML) for all scene fragments with index < scene_index."""
    scene_index: int
    """1-based index of the scene to design."""


@dataclass
class SceneDesignerOutput:
    """Result of a scene-designer run."""

    fragment: SceneFragment
    """Validated scene fragment (Pydantic model)."""

    manifest: list[ManifestEntry]
    """Asset manifest entries for this scene."""

    raw_fragment_yaml: str
    """Raw YAML string from the LLM (for debugging)."""

    raw_manifest_json: str
    """Raw JSON string from the LLM (for debugging)."""


# ─────────────────────────────────────────────────────────────────────────────
# Prompt construction
# ─────────────────────────────────────────────────────────────────────────────

# Verbatim system prompt from §11.6.6
_SYSTEM_PROMPT = """\
You are a Scene Designer for `parallax-engine`. Your job is to translate one \
scene of a director's storyboard into one fragment of `scene.yaml` that the \
renderer can execute. You are a translator and a craftsperson, not a director \
and not a renderer.

You will be given:
- The full `storyboard.yaml` (read it carefully — every field affects you).
- The casting bible `casting.yaml` (treat every entry as canonical; never re-describe).
- All prior scenes' fragments, in order. Read them. Your scene must visually \
flow from scene N-1 and into scene N+1.
- The `scene.yaml` schema as canonical reference for your output.
- The integer index of the scene you are to build.

Your output is a YAML fragment for one scene plus an asset manifest. You do not \
write the project block, the music track, the global palette, or anything that is \
not specifically your scene. The merger will assemble fragments after you.

Your translation responsibilities:

1. **Camera.** Convert `camera_intent` into a concrete `camera` block per the \
schema. The translation table is authoritative; consult it. Pick `drone.path`, \
`parallax.pan_axis`, `parallax.dolly`, or `portal.mask_path` parameters that \
realise the storyboard's intent. You have latitude in *parameters* but not in \
*behaviour*: a `cinematic_pan_right` always becomes `camera.mode: parallax` \
with rightward pan.

2. **Layers.** Decide how many planes (Z values) the scene needs. Use the \
storyboard's `density_curve` and the per-scene `pacing` to size: `sparse` and \
`held` mean ≤ 4 planes; `steady` means 5–7; `dense` and `rising` mean 8–12. \
Each plane has a Z value, a parallax factor (closer planes move faster), and a \
list of layer assets.

3. **Assets.** For each layer asset, decide whether it is canonical (cast member: \
reference by id) or local (one-off: invent a description). For canonical assets \
that have not yet been produced, write `kind: produce_canonical` in the manifest. \
Never write inline SVG. Never specify colors that contradict the storyboard's \
palette unless the scene has an explicit `palette_override`.

4. **Masks.** If the scene's `transition_out.type` is `portal_reveal` or \
`biome_wipe_donor`, define the mask geometry here. The mask is world-anchored, \
not screen-anchored.

5. **Timing.** Your scene's `duration_s` is given by the storyboard. Distribute \
it: typically 60% in the steady camera state, 20% ramp-in, 20% ramp-out. Pacing \
modifies this — `held` is 100% steady, `rising` ends with the camera still \
accelerating.

6. **Hard rules.** Read `continuity.hard_rules`. Each is a constraint your scene \
must satisfy. State in your scene's `qa_self_check` block which rules apply and \
how you've satisfied them.

What you do NOT do:
- You do not change the storyboard. If you think the storyboard is wrong, write a \
`scene_designer_protest` field at the bottom of your fragment with your reasoning.
- You do not invent recurring cast. If a motif appears in your scene that is not \
in casting and is not in `must_feature`, treat it as local and per-scene.
- You do not produce SVG. You produce paths and descriptions; asset-generator does \
the SVG.
- You do not edit prior scenes' fragments.
- You do not call any other agent.

Your output is a single YAML document for the scene fragment, followed by a single \
JSON document for the manifest. Both wrapped in fenced blocks. No prose outside.
"""

# Fragment schema hint included in the user message
_FRAGMENT_SCHEMA_HINT = """\
## Scene Fragment Schema (your YAML output must conform to this)

Required fields:
  scene_index: <int>       # must match the scene index you are building
  duration_s: <float>      # must match storyboard.scenes[i].duration_s

Optional content fields (include what this scene needs):
  stacks:
    <stack_name>:
      layers:
        - id: <str>
          src: <str>                    # path to SVG; use assets/canon/<id>.svg for casting
          scene_xyz: [x, y, z]          # z is depth; more negative = farther away
          plate_size: [width, height]
          anchor: "center" | "top-left" | [px, py]
  camera:
    mode: parallax | drone | keyframe
    parallax:
      pan_axis: x | y
      speed: <float>
      dolly: <float>
    drone:
      path:
        kind: bezier | linear
        controls: [[x,y,z], ...]
        duration_s: <float>
      spring_halflife_s: <float>
  masks:
    - id: <str>
      anchor: screen | world
      src_stack: <stack_name>
      dest_stack: <stack_name>
      matte: alpha
      growth:
        kind: radius | wipe
        t0: <float>
        t1: <float>
        r0: <float>
        r1: <float>
        feather_px: <float>
  transitions:
    transition_in:
      kind: <TransitionType>
      duration: <float>
    transition_out:
      kind: <TransitionType>
      duration: <float>
  qa_self_check:
    hard_rules_checked: [...]

DO NOT include: version, fps, resolution, seed, meta, perspective_px, origin, bg_color
(These are project-level fields; the merger adds them.)
"""

# Manifest schema hint
_MANIFEST_SCHEMA_HINT = """\
## Asset Manifest Schema (your JSON output must conform to this)

A JSON array of objects, one per SVG asset the scene needs:

[
  {
    "id": "<str>",
    "kind": "canonical" | "produce_canonical" | "local",
    "purpose": "<one-line description of use in this scene>",
    "path": "<expected SVG path, or null for local>"
  }
]

kind rules:
  canonical         → casting entry with canonical_svg already set; reuse the path.
  produce_canonical → casting entry with canonical_svg = null; asset-generator will
                      produce it; reserve path = "assets/canon/<id>.svg".
  local             → one-off asset not in casting; path = "assets/local/<id>.svg".

NOTE: Do NOT include a "canonical_description" field in your output — it will be
populated automatically from casting.yaml for produce_canonical entries.
"""


def build_scene_designer_messages(
    storyboard: Storyboard,
    casting: CastingBible,
    prior_fragments: list[dict[str, Any]],
    scene_index: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build (system_blocks, messages) for the scene-designer call.

    Returns
    -------
    system_blocks:
        List of content blocks suitable for Anthropic's ``system`` parameter.
        The first block (stable) is cache-controlled.
    messages:
        A single user turn containing storyboard + casting + prior fragments +
        schema hints + the scene index.
    """
    # System: stable cacheable part + schema hints
    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": _SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": _FRAGMENT_SCHEMA_HINT + "\n\n" + _MANIFEST_SCHEMA_HINT,
            "cache_control": {"type": "ephemeral"},
        },
    ]

    # Serialise storyboard to YAML
    storyboard_dict = storyboard.model_dump(mode="json", exclude_none=False)
    storyboard_yaml = yaml.dump(storyboard_dict, default_flow_style=False, sort_keys=True)

    # Serialise casting entries to YAML
    casting_entries = casting.all_entries()
    casting_list = [e.model_dump(mode="json", exclude_none=False) for e in casting_entries]
    casting_yaml = yaml.dump(
        {"casting": casting_list}, default_flow_style=False, sort_keys=True
    )

    # Prior fragments as YAML
    if prior_fragments:
        prior_text_parts = []
        for i, frag in enumerate(prior_fragments):
            frag_idx = frag.get("scene_index", i + 1)
            prior_text_parts.append(
                f"### Prior fragment — scene {frag_idx}\n"
                f"```yaml\n{yaml.dump(frag, default_flow_style=False, sort_keys=True)}```"
            )
        prior_text = "\n\n".join(prior_text_parts)
    else:
        prior_text = "(No prior scene fragments — this is scene 1.)"

    # Find the scene entry for this scene_index
    scene_entry_dict: dict[str, Any] = {}
    for scene in storyboard.scenes:
        if scene.index == scene_index:
            scene_entry_dict = scene.model_dump(mode="json", exclude_none=False)
            break

    user_content = (
        f"## Storyboard\n\n```yaml\n{storyboard_yaml}```\n\n"
        f"## Casting Bible (casting.yaml)\n\n```yaml\n{casting_yaml}```\n\n"
        f"## Prior Scenes (read for visual continuity)\n\n{prior_text}\n\n"
        f"## Your Assignment\n\n"
        f"Produce the scene fragment and asset manifest for **scene {scene_index}**.\n\n"
        f"Scene {scene_index} entry from storyboard:\n"
        f"```yaml\n{yaml.dump(scene_entry_dict, default_flow_style=False, sort_keys=True)}```\n\n"
        "Output your YAML fragment in a ```yaml block, then your JSON manifest in a "
        "```json block. No prose outside the blocks."
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    return system_blocks, messages


# ─────────────────────────────────────────────────────────────────────────────
# Output parsing
# ─────────────────────────────────────────────────────────────────────────────


def extract_yaml_block(text: str) -> str:
    """Extract the first ```yaml ... ``` block from *text*.

    Raises
    ------
    ValueError
        If no YAML block is found.
    """
    pattern = r"```yaml\s*\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if not match:
        raise ValueError(
            f"No ```yaml block found in scene-designer response.\n"
            f"Response (first 400 chars): {text[:400]}"
        )
    return match.group(1).strip()


def extract_json_block(text: str) -> str:
    """Extract the first ```json ... ``` block from *text*.

    Raises
    ------
    ValueError
        If no JSON block is found.
    """
    pattern = r"```json\s*\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if not match:
        raise ValueError(
            f"No ```json block found in scene-designer response.\n"
            f"Response (first 400 chars): {text[:400]}"
        )
    return match.group(1).strip()


def parse_fragment(raw_yaml: str) -> SceneFragment:
    """Parse and validate a scene fragment YAML string.

    Raises
    ------
    yaml.YAMLError
        If *raw_yaml* is not valid YAML.
    pydantic.ValidationError
        If the parsed dict fails SceneFragment validation.
    """
    data: Any = yaml.safe_load(raw_yaml)
    if not isinstance(data, dict):
        raise ValueError(
            f"Scene fragment YAML must be a mapping; got {type(data).__name__}"
        )
    return SceneFragment.model_validate(data)


def _resolve_manifest_entry(
    raw: dict[str, Any],
    casting: CastingBible,
) -> ManifestEntry:
    """Resolve one raw manifest dict into a validated ManifestEntry.

    For ``produce_canonical`` entries:
        - Overwrites ``canonical_description`` from the casting bible (never from LLM).
        - Sets ``path`` to ``assets/canon/<id>.svg`` if not already set.

    For ``canonical`` entries:
        - Sets ``path`` from the casting bible if not already set.

    For ``local`` entries:
        - Sets ``path`` to ``assets/local/<id>.svg`` if not set.
    """
    entry = dict(raw)  # shallow copy; don't mutate caller's dict
    asset_id: str = str(entry.get("id", ""))
    kind: str = str(entry.get("kind", "local"))

    if kind == "produce_canonical":
        # ALWAYS source canonical_description from casting, not from the LLM.
        cast_entry: CastingEntry | None = casting.read_casting_entry(asset_id)
        if cast_entry is not None:
            entry["canonical_description"] = cast_entry.canonical_description
        else:
            # Unknown cast id — keep whatever the LLM wrote (will be validated by Pydantic)
            pass
        # Reserve the expected path
        if not entry.get("path"):
            entry["path"] = f"assets/canon/{asset_id}.svg"

    elif kind == "canonical":
        # Look up the existing path from the casting bible
        cast_entry = casting.read_casting_entry(asset_id)
        if cast_entry is not None and cast_entry.canonical_svg and not entry.get("path"):
            entry["path"] = cast_entry.canonical_svg

    elif kind == "local":
        if not entry.get("path"):
            entry["path"] = f"assets/local/{asset_id}.svg"

    return ManifestEntry.model_validate(entry)


def parse_manifest(
    raw_json: str,
    casting: CastingBible,
) -> list[ManifestEntry]:
    """Parse and validate a manifest JSON string.

    Args:
        raw_json: JSON string containing a list of manifest entry dicts.
        casting: CastingBible for resolving canonical descriptions and paths.

    Returns:
        Validated list of ManifestEntry objects.

    Raises:
        json.JSONDecodeError: If *raw_json* is not valid JSON.
        pydantic.ValidationError: If any entry fails validation.
    """
    data: Any = json.loads(raw_json)
    if not isinstance(data, list):
        raise ValueError(
            f"Manifest JSON must be a list; got {type(data).__name__}"
        )
    entries: list[ManifestEntry] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(
                f"Manifest entry {i} must be a mapping; got {type(item).__name__}"
            )
        entries.append(_resolve_manifest_entry(item, casting))
    return entries


# ─────────────────────────────────────────────────────────────────────────────
# Stub — for offline tests
# ─────────────────────────────────────────────────────────────────────────────


class SceneDesignerStub:
    """Drop-in stub LLM client for offline testing.

    Pass ``response_map`` keyed on scene_index (int → str):
        Each value is a string that should contain:
        - A ```yaml ... ``` block (the fragment)
        - A ```json ... ``` block (the manifest)

    ``calls`` is populated on each call, enabling assertion on call count
    and parameters.

    Example::

        stub = SceneDesignerStub({
            1: "```yaml\\nscene_index: 1\\nduration_s: 5.0\\n```\\n"
               "```json\\n[{...}]\\n```"
        })
        designer = SceneDesigner(client=stub)
        out = designer.run(storyboard, casting, [], 1)
    """

    def __init__(self, response_map: dict[int, str] | None = None) -> None:
        self._map: dict[int, str] = response_map or {}
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        model: str,
        system: list[dict[str, Any]] | str,
        messages: list[dict[str, Any]],
        step_name: str = "scene_designer",
        max_tokens: int = 8192,
        scene_index: int = 0,
    ) -> str:
        """Simulate a completion call; return the stub response text."""
        self.calls.append(
            {
                "model": model,
                "step_name": step_name,
                "scene_index": scene_index,
                "n_messages": len(messages),
            }
        )
        response = self._map.get(scene_index, "")
        if not response:
            raise ValueError(
                f"SceneDesignerStub has no response for scene_index={scene_index}. "
                f"Available: {sorted(self._map)}"
            )
        return response

    @property
    def call_count(self) -> int:
        return len(self.calls)


# ─────────────────────────────────────────────────────────────────────────────
# Real LLM client (lazy import)
# ─────────────────────────────────────────────────────────────────────────────


class _AnthropicClient:
    """Thin wrapper around anthropic.Anthropic().messages.create().

    Imported lazily so this module is importable without the ``anthropic``
    package installed.  Tests always inject a stub.
    """

    def __init__(self) -> None:
        try:
            import anthropic  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "parallax-engine scene-designer: 'anthropic' package is not installed. "
                "Install it with: pip install anthropic"
            ) from exc
        import os
        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        if openrouter_key:
            self._client = anthropic.Anthropic(  # type: ignore[attr-defined]
                base_url="https://openrouter.ai/api",
                auth_token=openrouter_key,
            )
        elif api_key:
            self._client = anthropic.Anthropic(api_key=api_key)  # type: ignore[attr-defined]
        elif oauth_token:
            self._client = anthropic.Anthropic(auth_token=oauth_token)  # type: ignore[attr-defined]
        else:
            raise RuntimeError(
                "parallax-engine scene-designer: no credentials found. "
                "Set OPENROUTER_API_KEY, ANTHROPIC_API_KEY, or CLAUDE_CODE_OAUTH_TOKEN."
            )

    def complete(
        self,
        *,
        model: str,
        system: list[dict[str, Any]] | str,
        messages: list[dict[str, Any]],
        step_name: str = "scene_designer",
        max_tokens: int = 8192,
        scene_index: int = 0,
    ) -> str:
        """Call the Anthropic Messages API and return the response text."""
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
        )
        kwargs["system"] = system
        resp = self._client.messages.create(**kwargs)
        for block in resp.content:
            if hasattr(block, "text"):
                return block.text
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# SceneDesigner — main runner
# ─────────────────────────────────────────────────────────────────────────────


class SceneDesigner:
    """Runs the scene-designer for one scene.

    One Sonnet call per scene (§11.12.2).  Sequential invocation (scene N sees
    all fragments for indices 1..N-1) is enforced by the caller (project manager).

    Parameters
    ----------
    client:
        An object implementing ``.complete(model, system, messages, ...)``.
        Defaults to the real Anthropic client.  Pass a ``SceneDesignerStub``
        for testing.
    model:
        Override the default Sonnet model.  Normally left as the default.
    """

    def __init__(
        self,
        client: Any | None = None,
        model: str = MODEL_SONNET,
    ) -> None:
        self._client = client
        self._model = model

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = _AnthropicClient()
        return self._client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        storyboard: Storyboard,
        casting: CastingBible,
        prior_fragments: list[dict[str, Any]],
        scene_index: int,
    ) -> SceneDesignerOutput:
        """Invoke the scene-designer for one scene.

        Parameters
        ----------
        storyboard:
            Validated Storyboard from the director.
        casting:
            The casting bible (CastingBible instance).  Used to resolve
            ``canonical_description`` for ``produce_canonical`` manifest entries
            and existing paths for ``canonical`` entries.
        prior_fragments:
            List of raw dicts for all scene fragments with index < scene_index.
            Pass [] for scene 1.  These are shown to the LLM for visual continuity.
        scene_index:
            1-based index of the scene to design.

        Returns
        -------
        SceneDesignerOutput
            Contains the validated SceneFragment, the resolved manifest, and
            the raw strings from the LLM for debugging.

        Raises
        ------
        ValueError
            If the LLM response does not contain the expected YAML and JSON blocks,
            or if the fragment / manifest fails validation.
        """
        client = self._get_client()
        system_blocks, messages = build_scene_designer_messages(
            storyboard=storyboard,
            casting=casting,
            prior_fragments=prior_fragments,
            scene_index=scene_index,
        )

        logger.info(
            "scene_designer: scene_index=%d model=%s n_prior=%d",
            scene_index,
            self._model,
            len(prior_fragments),
        )

        raw_response = client.complete(
            model=self._model,
            system=system_blocks,
            messages=messages,
            step_name="scene_designer",
            max_tokens=8192,
            scene_index=scene_index,
        )

        # Parse and validate the two output blocks
        raw_yaml = extract_yaml_block(raw_response)
        raw_json = extract_json_block(raw_response)

        fragment = parse_fragment(raw_yaml)
        manifest = parse_manifest(raw_json, casting)

        # Validate scene_index matches
        if fragment.scene_index != scene_index:
            raise ValueError(
                f"Fragment scene_index={fragment.scene_index} does not match "
                f"requested scene_index={scene_index}"
            )

        # Validate duration_s matches storyboard
        expected_duration: float | None = None
        for scene in storyboard.scenes:
            if scene.index == scene_index:
                expected_duration = scene.duration_s
                break
        if expected_duration is not None and abs(fragment.duration_s - expected_duration) > 0.001:
            raise ValueError(
                f"Fragment duration_s={fragment.duration_s} does not match "
                f"storyboard duration_s={expected_duration} for scene {scene_index}"
            )

        logger.info(
            "scene_designer: scene %d → fragment OK, %d manifest entries",
            scene_index,
            len(manifest),
        )

        return SceneDesignerOutput(
            fragment=fragment,
            manifest=manifest,
            raw_fragment_yaml=raw_yaml,
            raw_manifest_json=raw_json,
        )
