# SVG Generation Pipeline — 4-Tier Design

> **Status:** Design only. Implementation pending user review.
> **Author context:** This document is the deliverable for Task 3 of the
> handoff_prompt.md execution. It specifies the architecture that replaces
> the broken `parallax_engine/tools/gen_image.py` (single Haiku call → garbage
> SVGs ~95% of the time, with silent placeholder fallback).
> **Reference points:** SPEC §11.6 (scene designer), §11.7 (casting), §11.13
> (anti-patterns); `parallax_engine/tools/gen_image.py:102-199` (current
> failure mode); `parallax_engine/scene/designer.py:193-257` (current
> scene-designer prompt with drifting Z ranges).

---

## 1. Problem statement

### Current failure mode

`parallax_engine/tools/gen_image.py` is the only path from "asset name + brief
description" to "SVG file on disk." Its prompt template (lines 121-130):

```
"Generate a complete, valid SVG for a 2.5D parallax animation layer.\n"
"The layer depicts: " + prompt + "\n\n"
"Requirements:\n"
"- viewBox: 0 0 {width} {height}\n"
"- Use solid fills and simple geometric shapes; no external images\n"
"- Keep complexity low (fewer than 20 shape elements) for fast rasterisation\n"
"- Output ONLY the SVG XML starting with <svg and ending with </svg>\n"
"- No markdown code fences, no explanation -- raw SVG only"
```

This single one-line description is sent to **claude-haiku-4-5**, which
cannot reliably produce illustrated SVGs. Three failure modes compound:

1. **Wrong model.** Haiku's vector synthesis quality drops sharply past ~8
   shape elements. SPEC §6 reference videos show 30-50+ shapes per layer
   with internal structure (lit facets, atmospheric haze, organic edges) —
   well past Haiku's quality cliff.
2. **Single-shot prompt.** The asset description is the whole brief: "cabin
   with chimney" → indeterminate output. There's no palette enforcement, no
   compositing context, no style anchor, no layer-role guidance.
3. **Silent placeholder fallback.** Lines 175-199: when the API call fails
   *or* the parsed output is invalid SVG, the code writes a magenta colored
   rectangle and returns `ok: True`. The orchestration layer (manager.py)
   logs success and proceeds to QA, which doesn't inspect content — so the
   pipeline reports "all green" while shipping unusable assets. This is the
   single most damaging defect: it masks the failure entirely.

### What success looks like

A render of `examples/forest/scene.yaml` (the SPEC §6.1 forest brief) from
a one-line user prompt produces 6 SVGs visually comparable to the
hand-/Sonnet-authored ones from Task 2 (this session). The bar isn't
photorealism — it's the illustrated-children's-book / Studio Ghibli still
quality demonstrated by `references/sources/portal_transition_footage.mp4`.

### Reference videos as quality bar

`portal_transition_footage.mp4` shows: a cabin with detailed roof, chimney,
and window; mountains with subtle atmospheric haze; tents pitched at
varying angles; yaks with individually-rendered bodies; cliffs with
internal shading. **Each layer is 50-200 individual shape elements.** This
is the floor, not the ceiling.

---

## 2. Tier topology

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Tier 1: DIRECTOR                              (claude-opus-4-7)         │
│ Already exists at parallax_engine/director/agent.py                     │
│ UNCHANGED in this proposal.                                             │
│   ↓ storyboard.yaml + casting.yaml                                      │
├─────────────────────────────────────────────────────────────────────────┤
│ Tier 2: SCENE ARCHITECT                       (claude-opus-4-7)         │
│ REPLACES today's scene-designer at the SVG-listing layer.               │
│ Today's scene-designer at parallax_engine/scene/designer.py does TWO    │
│ jobs (camera/layer/mask layout + SVG manifest). This tier strips it     │
│ down to the SVG manifest job and adds explicit per-asset constraints.   │
│ The camera/layer layout work moves to a separate scene-composer agent   │
│ (out of scope for this design, but split is recommended — see §6.4).   │
│   ↓ per-scene SVG manifest                                              │
│      [{id, purpose, spatial_role, palette, plate_size, z_layer,         │
│        compositing_role, style_constraints, casting_ref}, ...]          │
├─────────────────────────────────────────────────────────────────────────┤
│ Tier 3: SVG PROMPT ENGINEER                   (claude-sonnet-4-6)       │
│ NEW per-SVG agent — receives ONE manifest entry, crafts a painstakingly │
│ detailed prompt for Tier 4. Reads casting.yaml for cast members and     │
│ project-level shape_language / palette. Cross-references prior SVGs in  │
│ the same scene (read-only) to keep visual cohesion.                     │
│ Output is a structured prompt with: viewBox, dimensions, palette        │
│ (specific hex), style language, technique notes, compositing context,   │
│ self-check instructions.                                                │
│   ↓ rendered prompt string                                              │
├─────────────────────────────────────────────────────────────────────────┤
│ Tier 4: SVG CRAFTSMAN                         (claude-sonnet-4-6)       │
│ REPLACES parallax_engine/tools/gen_image.py.                            │
│ Single responsibility: receive ONE prompt, produce ONE SVG. Sees only   │
│ the Tier 3 prompt — no other context, no awareness of casting or other  │
│ SVGs. This is intentional: the bottom agent focuses on producing the    │
│ highest-quality SVG possible from the brief, nothing more.              │
│ HARD validation: parses, viewBox correct, ≥N shapes, palette obeyed.    │
│ NO placeholder fallback. Failure propagates upward.                     │
│   ↓ one .svg file                                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

### Why four tiers, not three or five?

- **Three (current)** = Director → Scene-designer → asset-generator. The
  scene-designer is overloaded (camera + layer + manifest + asset
  description) and the asset-generator gets one-line briefs. This is the
  current broken state.
- **Five** would split Tier 3 into "casting context resolver" and "prompt
  composer." Premature: a single Tier 3 agent can comfortably handle
  both responsibilities given that the casting bible and prior SVGs are
  already structured data, not prose to be summarized.
- **Four** isolates the two unsolved problems (per-asset prompt quality
  and per-asset SVG quality) into their own agents with single-purpose
  contracts. Each tier's failure mode is debuggable in isolation.

### What stays

- **Tier 1 (Director):** verified working in the prior session with real
  Opus calls producing thoughtful storyboards. No changes proposed.
- **Casting bible** (`parallax_engine/casting/bible.py`): canonical asset
  registry with `canonical_description`, `palette_locked`,
  `forbidden_changes`, `canonical_svg` path. Lifecycle in SPEC §11.7.
  Tier 3 reads this; Tier 2 may append entries when introducing new cast.
- **Merger** (`parallax_engine/scene/merger.py`): stitches scene fragments
  from the (replaced) scene-composer into a single `scene.yaml`. No
  changes proposed.
- **Renderer** (`parallax_engine/render.py`): unchanged.

---

## 3. Per-tier contracts

### Tier 2 — Scene Architect

**Input** (JSON):
```json
{
  "storyboard": "<full storyboard.yaml>",
  "casting": "<full casting.yaml>",
  "scene_index": 3,
  "prior_scenes": ["<scene_01.yaml>", "<scene_02.yaml>"],
  "renderer_schema": "<excerpt of scene.py Pydantic schema, see §6.5>"
}
```

**Output** (JSON):
```json
{
  "scene_index": 3,
  "asset_manifest": [
    {
      "id": "forest_sky_03",
      "purpose": "establish dawn lighting and color palette for the forest stack",
      "spatial_role": "background",
      "compositing_role": "fully_opaque_plate",
      "z_layer": -12000,
      "plate_size": [3840, 2160],
      "viewbox": "0 0 3840 2160",
      "palette": {
        "primary": ["#1b1844", "#f0a36a", "#fbe6a6", "#cc7a72"],
        "accent":  ["#fffae0", "#deb088"]
      },
      "style_constraints": [
        "vertical gradient from deep indigo at top to dusty rose at horizon",
        "5-7 stratus cloud bands; soft edges via overlapping fills",
        "small distant sun disc partially occluded by cloud band"
      ],
      "casting_ref": null,
      "manifest_kind": "local"
    },
    {
      "id": "red_bird_canonical",
      "purpose": "introduce the recurring red bird character",
      "spatial_role": "midground subject",
      "compositing_role": "transparent_subject",
      "z_layer": -3500,
      "plate_size": [3840, 2160],
      "viewbox": "0 0 3840 2160",
      "palette": {
        "primary": ["#c84032"],
        "accent":  ["#000000", "#ffffff"]
      },
      "style_constraints": [
        "small bird centered around (1920, 1080), wingspan ~600 viewbox units",
        "side profile, wing extended in mid-flap pose",
        "obey casting.yaml#red_bird.canonical_description and palette_locked"
      ],
      "casting_ref": "red_bird",
      "manifest_kind": "produce_canonical"
    }
  ]
}
```

**Hard rules** (validation gate):

- Every entry's `z_layer` must be drawn from the SPEC's canonical depth
  range: `[-12000, -500]` for typical multi-plane scenes, with the spread
  matching one of §6's worked examples.
- `plate_size` must be `[3840, 2160]` for v1 (the renderer raster cache
  expects landscape master plates).
- `viewbox` must be `"0 0 3840 2160"`. Mismatched viewBoxes between the
  SVG and the plate are a SPEC §6.4 portal-mechanic killer.
- `palette` must reference `casting.yaml#palette_locked` for any
  `casting_ref`-bearing entry.
- `compositing_role` ∈ {`fully_opaque_plate`, `transparent_subject`,
  `transparent_silhouette`, `transparent_foliage_edges`, `mask_silhouette`,
  `mask_hole`}. Tier 4 uses this to enforce "sky fills the plate; other
  layers are sparse."

**Model:** `claude-opus-4-7` (Opus). The architect's job is reasoning
about composition, depth budgeting, and scene continuity — high-stakes
planning, not text production. Opus at long-context is right.

**Retries:** max 2 on schema-validation failures. After cap, escalate to
the scene-composer to revise the layer layout.

**Cost ceiling per call:** ~$0.30 (8K input, 4K output Opus).

### Tier 3 — SVG Prompt Engineer

**Input** (JSON, one call per asset entry from Tier 2):
```json
{
  "asset": { "<one entry from Tier 2's asset_manifest>" },
  "casting_entry": "<casting.yaml entry if casting_ref set, else null>",
  "project_shape_language": "<storyboard.visual_vocabulary>",
  "scene_context": {
    "scene_index": 3,
    "scene_purpose": "<from storyboard.scenes[3].camera_intent>",
    "adjacent_layer_ids": ["forest_sky_03", "mountains_03", "trees_far_03"],
    "adjacent_layer_palettes": [...]
  }
}
```

**Output** (string — the rendered prompt for Tier 4):

Structured, multi-section prompt using the template in §4. Returns the
string directly; no JSON wrapping, no commentary.

**Model:** `claude-sonnet-4-6` (Sonnet). Prompt-engineering quality is
mid-tier reasoning + prose composition — Sonnet is right.

**Retries:** max 1 on output-length failures (minimum 800 chars, maximum
6000 chars).

**Cost ceiling per call:** ~$0.04 (1.5K input, 0.8K output).

### Tier 4 — SVG Craftsman

**Input** (string): the prompt from Tier 3, verbatim.

**Output** (string): raw SVG XML, validated.

**Model:** `claude-sonnet-4-6` (Sonnet). **Not Haiku.** Per handoff §5.5:
"DO NOT use Haiku for SVG generation." Sonnet's vector synthesis quality
at 30-200 shape elements is the only path to reference-video aesthetic.

**Validation gate** (hard, no placeholder fallback):

```python
def validate_svg(content: str, expected_viewbox: str, min_shapes: int,
                 allowed_palette: set[str]) -> str:
    content = content.strip()
    if not (content.startswith("<svg") and content.endswith("</svg>")):
        raise InvalidSVGError("not bracketed by <svg>...</svg>")
    root = ET.fromstring(content)        # ValueError if XML invalid
    if root.attrib.get("viewBox", "").strip() != expected_viewbox:
        raise InvalidSVGError("viewBox mismatch")
    n_shapes = sum(1 for el in root.iter()
                   if el.tag.split("}")[-1] in
                      ("path","polygon","rect","circle","ellipse"))
    if n_shapes < min_shapes:
        raise InvalidSVGError(f"only {n_shapes} shapes (need ≥{min_shapes})")
    fills = {el.attrib.get("fill", "").lower() for el in root.iter()}
    fills.discard("")
    fills.discard("none")
    out_of_palette = fills - {c.lower() for c in allowed_palette}
    if out_of_palette:
        raise InvalidSVGError(f"fills not in palette: {out_of_palette}")
    return content
```

**Retries:** max 2 on validation failure. Each retry includes the
specific failure reason in the prompt as a correction. After 3rd failure,
**hard-fail the entire pipeline** — the orchestrator must surface the
failure to the user, not silently degrade. (Fixes manager.py bug 7.)

**Cost ceiling per call:** ~$0.10 (3K input, 5K output).

---

## 4. Tier 4 prompt template (the most important part)

The Tier 4 prompt is the place where quality is won or lost. The template
below is what Tier 3 produces. Variables in `{{ }}` are filled by Tier 3.

### System block (cacheable across all Tier 4 calls in a render)

```
You are an expert vector illustrator producing SVG layers for a 2.5D
parallax animation engine called parallax-engine.

OUTPUT CONTRACT — every rule is non-negotiable:
- Output ONLY raw SVG XML. No markdown code fences. No prose before or
  after.
- Start with `<svg` and end with `</svg>`.
- Use absolute path commands (M, L, C, Q, H, V, Z) — no relative variants.
- Allowed elements: <svg>, <defs>, <linearGradient>, <radialGradient>,
  <stop>, <path>, <polygon>, <rect>, <circle>, <ellipse>, <g>.
  Disallowed: <image>, <text>, <foreignObject>, <style>, <script>,
  embedded raster, CSS classes.
- Solid fills or gradients only. Specify `fill="#hex"` directly on each
  shape — no fill-via-CSS.

COMPOSITING ROLE — this layer is ONE PLANE in a multi-plane composite.
Other layers exist BEHIND and IN FRONT of this one. Respect the role
specified in the user prompt:
- fully_opaque_plate (sky): fills the entire 3840×2160 viewBox; no
  transparent regions.
- transparent_silhouette (mountains, distant trees): silhouette in the
  bottom or middle of the plate; top portion is fully transparent so
  upstream layers show through.
- transparent_foliage_edges (foreground leaves): detail at the EDGES;
  large central region is fully transparent so the layer behind shows
  through.
- transparent_subject (a character or single object): subject occupies
  a small portion of the plate at a specified location; rest is
  transparent.
- mask_silhouette (portal_tree silhouette path): the visible tree shape;
  shares its viewBox with the corresponding mask_hole.
- mask_hole (portal_tree hole path): the portal opening; painted
  pure white (#ffffff) so the renderer's mask raster picks it up.

AESTHETIC: detailed, painterly vector illustrations. Reference quality:
illustrated children's-book stills / Studio Ghibli backgrounds /
Polyfjord-style motion graphics. NOT geometric primitives, flat icons,
or stick figures. Trees are detailed with branches, foliage clusters,
and light variation — not triangles on rectangles.

SELF-CHECK before responding:
1. Output starts with <svg and ends with </svg>, with NOTHING else?
2. ViewBox matches the user-prompt's `viewBox` value?
3. At least the user-prompt's `min_shapes` shape elements?
4. Compositing role respected (e.g., transparent regions where
   compositing_role demands)?
5. Every fill is a #hex color from the user-prompt's allowed `palette`?

Respond with the SVG and nothing else.
```

### User block (per asset)

```
viewBox: {{ viewbox }}                      # e.g., "0 0 3840 2160"
plate_size: [{{ plate_w }}, {{ plate_h }}]  # rasterization target
compositing_role: {{ compositing_role }}    # e.g., "transparent_silhouette"
min_shapes: {{ min_shapes }}                # e.g., 12
palette:
  primary: {{ palette_primary }}            # e.g., ["#1f3a26", "#54663e"]
  accent:  {{ palette_accent }}             # e.g., ["#a59caa", "#2a3225"]
  forbidden: {{ palette_forbidden }}        # e.g., ["#ff00ff"]  (used by Tier 4 to fail loudly on placeholder colors)

style_language: {{ project_shape_language }}
                                            # e.g., "warm illustrated palette,
                                            # rounded organic shapes, no hard
                                            # geometric edges, gentle gradients"

scene_context:
  scene_index: {{ scene_index }}
  scene_purpose: {{ scene_purpose }}
                                            # e.g., "establish dawn forest;
                                            # camera will fly through canopy
                                            # toward the heart of the scene"
  adjacent_layers_at_lower_z: {{ adj_lower }}
                                            # layers at higher z (further away);
                                            # this layer renders on top of these
  adjacent_layers_at_higher_z: {{ adj_higher }}
                                            # layers at lower z (closer);
                                            # those render on top of this layer

== ASSET BRIEF ==
{{ asset_purpose }}
                                            # one paragraph from Tier 2

== STRUCTURAL CONSTRAINTS ==
{{ style_constraints }}                     # bulleted list from Tier 2:
                                            # spatial layout, count
                                            # requirements, specific features

== CASTING REFERENCE ==
{{ casting_section }}                       # if casting_ref set, full
                                            # canonical_description block
                                            # from casting.yaml; otherwise
                                            # "(none — this is a local
                                            #  asset, not a cast member)"

== EXAMPLES (style anchors, do not copy) ==
{{ examples_section }}                      # 1-2 brief style descriptions
                                            # of similar layers in OTHER
                                            # scenes, to anchor consistency
                                            # without prescribing the exact
                                            # output

Produce the SVG now. Begin with `<svg` and end with `</svg>`.
```

### Why this works

- **Strict output contract** kills the "model writes prose around the
  SVG" failure that the current Haiku prompt half-addresses.
- **Compositing role** kills the "everything is a full-plate rectangle"
  failure that wastes pixels and ruins parallax.
- **Palette spec with hex codes** kills the "model picks plausible-but-
  wrong colors" failure and lets validation enforce it programmatically.
- **Self-check block** has been empirically valuable in this session:
  the Tier 4 prototype script `scripts/gen_assets_oneshot.py` includes a
  similar block and Sonnet's outputs reliably opened with `<svg` and
  closed with `</svg>` (one truncation issue when output exceeded 8K
  tokens — fixed by raising max_tokens to 16K).

---

## 5. Casting bible feed (§11.7 integration)

For an asset entry with `casting_ref: "red_bird"` and `manifest_kind:
"produce_canonical"`, Tier 3 looks up `casting.yaml`:

```yaml
casting:
  - id: red_bird
    canonical_description: |
      A small red songbird with a black face mask, white throat patch,
      and orange-red body. Rendered in side profile, wings positioned
      mid-flap. Tail short, feet small with two visible toes. Eye
      depicted as a single black dot with a white catch-light.
    palette_locked:
      body: "#c84032"
      face_mask: "#000000"
      throat: "#ffffff"
      eye_catch: "#ffffff"
    forbidden_changes:
      - "color must always be exactly #c84032 — no shade variants"
      - "must always be in side profile, never frontal or 3/4"
    canonical_svg: null    # to be filled in after first production
    first_appearance_scene: 3
```

Tier 3 bakes this into the user-block's `== CASTING REFERENCE ==` section,
prepending the contract:

> The canonical_description below is BINDING. Every variant of this cast
> member, in this and future scenes, will be derived from your output.
> Do not deviate from palette_locked or forbidden_changes.
>
> [verbatim canonical_description]
> [verbatim palette_locked, formatted as a key→hex table]
> [verbatim forbidden_changes as a numbered list]

For subsequent scenes (`manifest_kind: "canonical"`), Tier 3 short-
circuits: it looks up `casting.yaml#red_bird.canonical_svg`, returns the
existing path to the orchestrator, and skips Tier 4 entirely. Cost: $0.

For per-scene variants (`manifest_kind: "variant"`), Tier 3 builds a
**transformation** prompt that points at the canonical SVG path and
specifies what changes (scale, lighting tint, position). Tier 4 receives
the canonical SVG content as input alongside the transformation spec
and produces a derived SVG that obeys all `forbidden_changes`. Variant
production is a constrained edit, not a regeneration.

---

## 6. Removal of failure modes

### 6.1 — Silent placeholder fallback (manager.py bug 7)

**Today:** `gen_image.py:175-199` writes a magenta `<rect>` and returns
`ok: True`. `manager.py:_run_asset_generators` sees ok=True and proceeds.
QA doesn't check.

**Fix:** Tier 4 raises `InvalidSVGError` after retry cap. The orchestrator
catches the error, marks the asset as failed in the asset manifest, and
escalates per `MAX_ASSET_RETRIES_PER_ASSET = 3` (rerun Tier 3 with a
revised prompt) → `MAX_SCENE_REDESIGNS_PER_SCENE = 2` (rerun Tier 2 with
the failed entry described as "previously failed asset") →
`MAX_STORYBOARD_REGENERATIONS = 1` (full director rerun). After all caps
exhausted, the pipeline **fails**, prints the error, and exits non-zero.
**No magenta rectangle ever lands on disk.**

### 6.2 — Wrong-model assignment (handoff §3, §5.5)

**Today:** `gen_image.py:132` calls `claude-haiku-4-5`. Stale model id
`claude-sonnet-4-6-20261231` in `director/agent.py:50` and `scene/
designer.py:54` (404s on the API; discovered this session by attempting
to use it).

**Fix:** Pin model IDs to `claude-opus-4-7`, `claude-sonnet-4-6`,
`claude-haiku-4-5-20251001` (the IDs the API actually accepts). Add a
unit test that calls `client.models.list()` and asserts the engine's
configured IDs are in the response.

### 6.3 — QA vacuity (manager.py bug 6)

**Today:** `_run_asset_qa` and `_run_scene_qa` pass on empty inputs
because they only validate that present items meet criteria, never that
expected items are present.

**Fix:** Tier-level QA receives the Tier 2 manifest as the *source of
truth* for what should exist. Each manifest entry has a corresponding
SVG file on disk; QA enumerates manifest entries, asserts the file
exists, opens it, and runs the same `validate_svg` gate from Tier 4.
Empty manifest fails QA (it can't be empty if the storyboard mandates a
scene).

### 6.4 — Scene-designer overload (latent in current scene/designer.py)

**Today:** `parallax_engine/scene/designer.py:193-257` system prompt does
both layer/camera/mask layout AND asset manifest writing. Drift between
the prompt and the renderer's Pydantic schema (claude-progress.txt §
"latent risks discovered in cycle 7") is the root cause of the
`_split_to_single_scene` bridge in manager.py.

**Recommended fix (out of scope for this design but flagged):** Split
the scene-designer into two sequential agents:

1. **Scene Composer** (Opus) — outputs `scene_NN.yaml` fragment with
   layer Z values, camera block, mask block. Receives the renderer's
   Pydantic schema as its system prompt prefix (auto-generated from
   `scene.py` to prevent drift).
2. **Scene Architect** (this proposal's Tier 2) — outputs the asset
   manifest only. Receives the Scene Composer's output as input.

This split is beyond the SVG pipeline's scope but the SVG pipeline's
quality is bottlenecked by it: if the scene-composer produces wrong Z
values, the SVG architect can't fix them downstream.

---

## 7. Cost estimate

Reference render: a typical 5-scene storyboard with 6 SVGs/scene = 30
SVGs/render. Assume 5 of those 30 are cast-member canonical productions
(first occurrence) and another 5 are variants of those cast members in
later scenes; the remaining 20 are local (non-cast) assets.

| Stage         | Calls | Tokens (in/out)     | Cost (Sonnet 4.6 / Opus 4.7)     |
|---------------|-------|---------------------|----------------------------------|
| Tier 1 Director  | 1   | 12K / 6K  (Opus)     | $0.18 + $0.45 = **$0.63**      |
| Tier 2 Architect | 5   | 8K / 4K each (Opus)  | (5 × $0.12 + 5 × $0.30) = **$2.10** |
| Tier 3 Engineer  | 30  | 1.5K / 0.8K each (Sonnet) | (30 × $0.0045 + 30 × $0.012) = **$0.50** |
| Tier 4 Craftsman | 25  | 3K / 5K each (Sonnet) | (25 × $0.009 + 25 × $0.075) = **$2.10** |
| Tier 4 Retries (~10% of calls fail validation once) | ~2.5 | same as Tier 4 | **$0.21** |
| Variants (skip Tier 4 — derived from canonical) | 5 | 4K / 3K each (Sonnet) | (5 × $0.012 + 5 × $0.045) = **$0.29** |
| **Total per render** |     |                     | **≈ $5.83**                     |

With prompt caching on the Tier 4 system block (~600 tokens, cached
across all Tier 4 calls in a single render): saves ~$0.03 on Tier 4 in.
Negligible on the total.

**Compare to current:** ~$0.05/render with Haiku producing unusable
output. The 100× cost increase is the price of usable assets. For a
production tier where outputs ship to end-users, this is right-sized.

For development / iteration, a `--cheap` flag that drops Tier 3 (Tier 2
output goes directly to Tier 4 with a static prompt template) would
bring cost back to ~$2.50/render with reduced quality. Recommended only
for CI smoke tests.

---

## 8. Migration sequencing

### Phase 1 — additive

1. Add `parallax_engine/svg_pipeline/tier4_craftsman.py` — `claude-sonnet-4-6`,
   `validate_svg` gate, retry loop, no fallback. Public API:
   `produce_svg(prompt: str, contract: ValidationContract) -> Path`.
2. Add `parallax_engine/svg_pipeline/tier3_engineer.py` — Sonnet,
   builds the Tier 4 prompt from a manifest entry + casting + scene
   context. Public API: `craft_prompt(asset_entry, casting, project,
   scene_context) -> str`.
3. Add `parallax_engine/svg_pipeline/tier2_architect.py` — Opus,
   produces the per-scene asset manifest. Public API:
   `architect_scene_assets(storyboard, casting, scene_index, prior_scenes)
   -> list[ManifestEntry]`.
4. **Do NOT modify** `parallax_engine/tools/gen_image.py` yet.
5. Add `parallax_engine/svg_pipeline/__init__.py` with a
   `produce_assets_for_scene(...)` entry point that orchestrates 2→3→4.
6. Add tests with mocked Anthropic clients (golden-fixture-style).

### Phase 2 — cutover behind a feature flag

1. Add a `PARALLAX_USE_NEW_SVG_PIPELINE` environment variable
   (defaulting to false).
2. In `manager.py:_run_asset_generators`, dispatch to the new pipeline
   when the flag is set, else keep calling `gen_image.py`.
3. Run end-to-end on `examples/forest/scene.yaml` with the flag set,
   compare visual quality against this session's hand-/Sonnet-authored
   outputs.

### Phase 3 — replace

1. Once cutover is validated, default the flag to `true` in
   `parallax_engine/settings.py`.
2. Mark `parallax_engine/tools/gen_image.py` as deprecated; keep as a
   shim that calls the new pipeline.
3. After 1 release cycle of soak time, delete the shim.

---

## 9. Verification plan

When implemented, success criterion is:

> **Render `examples/forest/scene.yaml` from a one-line user brief
> ("a drone flies through a dawn forest at golden hour") through the new
> pipeline. The 6 SVGs produced are visually comparable to the
> Sonnet-authored ones from this session.**

Comparable means: when the same scene.yaml geometry is rendered with the
two asset sets side-by-side (this session's vs. pipeline's), a human
reviewer cannot reliably distinguish which is which without close
inspection. No SSIM target — this is a judgment call.

Secondary success criteria:

- A render with one cast member (red_bird) referenced in 3 scenes
  produces 1 canonical SVG + 2 variants. The variants share the same
  `body: #c84032` fill and pose. Inspecting the SVG diffs shows only
  scale / lighting changes, not full re-generation.
- A render where Tier 4 cannot produce a valid SVG after 3 retries
  fails the pipeline (exit code 1) with a clear error message naming
  the failed asset. **No magenta placeholder lands on disk.**
- Render cost under $7 per full storyboard end-to-end.

---

## 10. What this document does NOT design

- The **Scene Composer** split (§6.4). Real fix for prompt/schema drift
  but separable.
- **Stylesheet caching** for repeated palettes. Premature optimization;
  prompt caching on the system block is enough for v1.
- **Concurrent Tier 4 calls.** The Anthropic SDK supports it; manager.py
  currently runs assets sequentially. Recommended: parallelize within
  one scene's manifest. Out of scope here — a deployment-time concern.
- **Tier 4 with extended thinking.** Sonnet 4.6's extended thinking mode
  could improve SVG quality at higher cost. Worth A/B testing once the
  baseline pipeline is in place.
- **Image-input feedback loop** (Sonnet sees the rendered SVG and
  iterates). A future direction; needs the renderer to produce
  small-format previews for the model to consume. Not v1.

---

## 11. Files to be created (when implementation is authorized)

```
parallax_engine/svg_pipeline/
├── __init__.py                  # public entry point: produce_assets_for_scene
├── tier2_architect.py           # Opus — manifest production
├── tier3_engineer.py            # Sonnet — prompt crafting
├── tier4_craftsman.py           # Sonnet — SVG synthesis with hard validation
├── validation.py                # ValidationContract + validate_svg()
└── prompts/
    ├── tier2_system.md          # Scene Architect system prompt
    ├── tier3_system.md          # SVG Prompt Engineer system prompt
    └── tier4_system.md          # SVG Craftsman system prompt (text in §4)

parallax_engine/manager.py       # MODIFIED to dispatch to new pipeline
                                 # behind PARALLAX_USE_NEW_SVG_PIPELINE flag
parallax_engine/tools/gen_image.py  # UNCHANGED until Phase 3
parallax_engine/settings.py      # ADD flag default

tests/svg_pipeline/
├── test_tier4_validation.py     # validate_svg gate tests
├── test_tier3_prompt_format.py  # prompt structure tests with mocked clients
├── test_tier2_manifest.py       # manifest schema validation tests
└── fixtures/
    ├── golden_storyboard.yaml
    └── golden_casting.yaml
```

---

## 12. Decisions to confirm before implementation

1. **Approve 4-tier topology** as designed, or push to 3 or 5 tiers?
2. **Approve cost ceiling** ~$5.80 per full render? (vs current $0.05 for
   garbage output, vs an opt-in `--cheap` mode at ~$2.50.)
3. **Approve hard-fail on validation** with no placeholder fallback?
   This is non-negotiable from my POV but worth user-confirming.
4. **Approve Tier 1 (Director) staying unchanged?** It's working in the
   current system; this design assumes no change.
5. **Approve scope**: SVG pipeline only. The Scene Composer split (§6.4)
   is recommended but separable.

If yes to all, implementation is Phase 1+2+3 across roughly 1500-2500
lines of code spread over the files in §11.
