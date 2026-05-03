# SVG Generation Pipeline — 6-Tier Design

> **Status:** Design only. Implementation pending user review.
> **Author context:** This document is the deliverable for Task 3 of the
> handoff_prompt.md execution. It specifies the architecture that replaces
> the broken `parallax_engine/tools/gen_image.py` (single Haiku call → garbage
> SVGs ~95% of the time, with silent placeholder fallback).
>
> **2026-05-03 revision:** Two new agents added between Tier 2 and Tier 3 —
> **Set Dresser (Tier 2.5)** and **Camera Path Designer (Tier 2.7)** — to fix
> the scene-composition flaw exposed by the dawn-forest renders: scenes
> were six full-canvas plates each containing many objects (16 trees crammed
> onto one plate at one Z), so all objects on a plate shared parallax and
> the result read as a slideshow. The new agents place 30–50 individual
> objects (one per layer) and design the camera path AROUND those objects.
> See [`scene_architecture.md`](scene_architecture.md) for the underlying spec.
> A working proof-of-concept is `scripts/gen_forest_scene.py` (40+ layers).
>
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

A render of a dawn-forest scene from a one-line user prompt produces a
**30–50 layer scene** (one SVG per individual object), with each tree at
its own Z, demonstrating real differential parallax — comparable to the
proof-of-concept output at [`examples/forest_v2/`](examples/forest_v2/)
and `/tmp/forest_40layer.mp4` produced this session. The bar isn't
photorealism — it's the illustrated-children's-book / Studio Ghibli still
quality demonstrated by `references/sources/portal_transition_footage.mp4`,
with the depth feel of `references/sources/2.5D_Drone_footage.mp4`
(individual-tree parallax, not slideshow).

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
│ Per scene: subject inventory, palette, mood, duration, camera intent.   │
│ Names the cast (8 trees, 1 portal mountain, etc.) and the spatial roles │
│ (backdrop, mid, near, frame) but does NOT pin per-object Z/X/Y/plate    │
│ — that is the Set Dresser's job.                                        │
│   ↓ scene_brief                                                         │
│      { scene_index, palette, mood, duration_s, camera_speed_intent,     │
│        subject_inventory: [{role, kind, count, casting_ref?}, ...] }    │
├─────────────────────────────────────────────────────────────────────────┤
│ Tier 2.5: SET DRESSER                         (claude-sonnet-4-6)  NEW  │
│ Receives scene_brief, places 30–50 INDIVIDUAL objects in 3D volume:     │
│ each gets its own Z, X, Y, plate_w, plate_h, category, description.     │
│ Computes the parallax verification table (against the camera intent).   │
│ HARD GATE: backdrop ratio < 1.5x, near reaches s>=3.0, frame culls in   │
│ shot, total raster memory < 4 GB. If gates fail, re-place violators     │
│ (max 2 retries). No SVG generation proceeds until table passes.         │
│   ↓ object_manifest + parallax_table                                    │
│      [{id, category, z, x, y, plate_w, plate_h, description}, ...]      │
├─────────────────────────────────────────────────────────────────────────┤
│ Tier 2.7: CAMERA PATH DESIGNER                (claude-sonnet-4-6)  NEW  │
│ Reads object_manifest. Designs the drone-camera Bezier path (4+        │
│ controls), spring halflife, noise (z_amp/xy_amp/hz), bank, POI         │
│ lookahead — choosing controls that WEAVE BETWEEN near objects rather    │
│ than clipping through them. Verifies no control point sits within       │
│ 200 Z-units of a near object's Z at the same lateral cell.              │
│   ↓ camera_block (matches scene.yaml drone-camera schema)               │
├─────────────────────────────────────────────────────────────────────────┤
│ Tier 3: SVG PROMPT ENGINEER                   (claude-sonnet-4-6)       │
│ NEW per-SVG agent — receives ONE manifest entry from Tier 2.5, crafts   │
│ a painstakingly detailed prompt for Tier 4. Reads casting.yaml for      │
│ cast members and project-level shape_language / palette. Output is a    │
│ structured prompt with: viewBox = plate_size, dimensions, palette       │
│ (specific hex), style language, compositing context, self-check.        │
│   ↓ rendered prompt string                                              │
├─────────────────────────────────────────────────────────────────────────┤
│ Tier 4: SVG CRAFTSMAN                         (claude-sonnet-4-6)       │
│ REPLACES parallax_engine/tools/gen_image.py.                            │
│ Single responsibility: receive ONE prompt, produce ONE SVG depicting    │
│ ONE OBJECT on a TRANSPARENT background (sky exception: full-fill).      │
│ HARD validation: parses, viewBox matches per-asset value, ≥N shapes,    │
│ palette obeyed. NO placeholder fallback. Failure propagates upward.     │
│   ↓ one .svg file                                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

### Why six tiers, not four or three?

- **Three (current)** = Director → Scene-designer → asset-generator. The
  scene-designer is overloaded (camera + layer + manifest + asset
  description) and the asset-generator gets one-line briefs. This is the
  current broken state.
- **Four (prior version of this doc)** introduced a Prompt Engineer and a
  Craftsman split, but kept Scene Architect responsible for both
  per-object placement AND camera path. That single-agent placement
  quietly produced 6-layer scenes (one full-canvas plate per Z), and the
  camera path was authored before knowing where the objects were — so
  the renders read as flat slideshows. Six tiers split those concerns.
- **Six** isolates four distinct problems into single-purpose agents:
  what's in the scene (Tier 2), where each object sits in 3D (Tier 2.5),
  how the camera flies through them (Tier 2.7), and how each individual
  SVG looks (Tiers 3 + 4). The parallax verification gate after Tier 2.5
  catches placement errors before any SVG is generated — the most
  expensive failure to recover from.

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

- Tier 2 produces a *subject inventory* (kinds and rough counts), NOT
  per-object placement. Z, X, Y, plate_w, plate_h, and viewBox are
  decided downstream by the **Set Dresser (Tier 2.5)** based on the
  parallax verification math in
  [`scene_architecture.md`](scene_architecture.md). Tier 2 manifest
  entries that include speculative z_layer / plate_size are HINTS for
  the Set Dresser and may be revised.
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

### Tier 2.5 — Set Dresser  *(new — 2026-05-03)*

**Role.** Place 30–50 individual objects in the 3D volume that the
camera will fly through. Each object becomes one layer with its own
Z, X, Y, plate width, plate height, and category. Compute the
parallax verification table and adjust placement until every gate
passes — only then is the manifest sent downstream.

This is the agent that was missing from the prior 4-tier design. Without
it, scenes degenerated into 6 full-canvas plates with multiple objects
per plate; on a flythrough the renders read as a slideshow because
every object on a plate shared parallax. The Set Dresser breaks each
plate into individual objects across distinct Z values.

**Input** (JSON):
```json
{
  "scene_brief": "<Tier 2 output for this scene>",
  "casting": "<full casting.yaml>",
  "renderer_constants": {
    "perspective_px": 1200,
    "cull_begin_z_cam": 480,
    "cull_full_z_cam": 900
  }
}
```

**Output** (JSON):
```json
{
  "scene_index": 3,
  "object_manifest": [
    {
      "id": "near_tree_07",
      "category": "near",
      "z": -1857,
      "x": 1320,
      "y": -120,
      "plate_w": 1000,
      "plate_h": 1500,
      "subject": "single fir, slight rightward lean, full trunk visible",
      "palette_hint": "deep moss green crown #1f3322 with warm rim #bf9050",
      "casting_ref": null
    }
    /* ...30+ more entries... */
  ],
  "parallax_table": [
    {"id": "sky", "category": "sky", "z": -28000,
     "s_min": 0.041, "s_max_pre": 0.053, "s_renderer": 0.053,
     "ratio_or_cull": "1.28x"},
    {"id": "near_tree_07", "category": "near", "z": -1857,
     "s_min": 0.393, "s_max_pre": 1.651, "s_renderer": 3.912,
     "ratio_or_cull": "cull@t=2.80s"}
    /* ...one row per object... */
  ],
  "camera_speed_intent": "moderate push",
  "camera_travel_z": 6500
}
```

**Hard rules** (validation gates — must pass before downstream tiers run):

- ≥ 30 objects total, ≥ 3 in each category (sky, distant, mid, near, frame).
- All Z-values unique; no two objects within 200 Z AND 400 lateral units
  (visual collision).
- For each object: `viewBox = "0 0 plate_w plate_h"` (set by the SVG
  Craftsman; the Set Dresser provides the plate dims).
- **Backdrop** (sky + distant): `s_max / s_min < 1.5` over the camera's
  sampled track. If not, the object grows visibly during the shot —
  push it deeper.
- **Near**: must reach `s_renderer >= 3.0` at some point in the shot.
  Auto-pass if the camera passes the object (cull_at_t set).
- **Frame**: must cull within `duration_s` (the camera passes it).
- **Memory budget**: `Σ (s_renderer² × plate_w × plate_h × 4) < 4 GB`
  across all layers, where `s_renderer` matches the renderer's
  pre-build algorithm in
  [`parallax_engine/render.py:243-261`](parallax_engine/render.py#L243-L261)
  (cull-fade-aware max scale).

The math used to compute `s_renderer` and the table is the same
projection equation in [`parallax_engine/projection.py`](parallax_engine/projection.py):

```
z_cam(t)        = z_obj − z_cam_world(t)            # camera world Z sampled per frame
s(t)            = perspective_px / (perspective_px − z_cam(t))
s_renderer      = max{ s(t) : compute_near_cull(z_cam(t)) > 0.001 }
ratio (backdrop)= max(s) / min(s)  over t∈[0, duration_s]
```

The track `z_cam_world(t)` MUST be the actual sampled drone-camera
output (from `parallax_engine.camera.drone_camera_track`) — not
just the bezier endpoints. Spring lag means the camera never quite
reaches the bezier P3, and the table must reflect that.

**Model:** `claude-sonnet-4-6` (Sonnet). Mid-tier reasoning + careful
arithmetic + steady self-correction. Opus would also work; Sonnet is
chosen for cost.

**Retries:** max 2 on gate failures. The retry prompt includes the
specific failures (e.g., "distant_treeline_a backdrop ratio 1.83x — push
z deeper") so the agent can correct surgically rather than re-rolling.
After cap, escalate by failing the scene — the brief itself may be
incoherent with engine physics (e.g., asking for "fast FPV camera
across an open field with no foreground" — there's no near-tier content
to parallax against).

**Cost ceiling per call:** ~$0.15 (4K input, 6K output Sonnet).

**Reference implementation:** the proof-of-concept
[`scripts/gen_forest_scene.py`](scripts/gen_forest_scene.py) implements
the Set Dresser as a deterministic Python placement function rather than
an LLM call. For a production pipeline the LLM agent provides the
creative variation (which species, which lateral lanes, which subject
poses); the math (Z uniqueness, parallax table, gates) stays as pure
Python that the agent calls or that wraps the agent's output.

---

### Tier 2.7 — Camera Path Designer  *(new — 2026-05-03)*

**Role.** Given the placed object_manifest, design the drone-camera
Bezier path + spring + noise + bank parameters that fly through the
scene. The camera path is *strictly downstream* of object placement —
designing it independently produces flythroughs that clip through near
objects or fly past empty volume.

**Input** (JSON):
```json
{
  "object_manifest": "<Tier 2.5 output>",
  "duration_s": 8.0,
  "fps": 30,
  "camera_intent": "moderate push, gentle S-curve",
  "perspective_px": 1200
}
```

**Output** (JSON — drops directly into `scene.camera` per
[`parallax_engine/scene.py`](parallax_engine/scene.py)):
```json
{
  "mode": "drone",
  "drone": {
    "path": {
      "kind": "bezier",
      "controls": [[0, 0, 0], [200, 30, -2200], [-250, -20, -4500], [0, 0, -6500]],
      "duration_s": 8.0
    },
    "poi_lookahead_s": 0.55,
    "spring_halflife_s": 0.18,
    "noise": {"z_amp": 22, "xy_amp": 8, "hz": 0.7},
    "bank_from_velocity": 0.40
  }
}
```

**Hard rules** (collision-avoidance gate):

- Camera Z travel must end *before* passing all mid scenery — otherwise
  the back half of the shot is mostly sky. As a rough rule of thumb,
  `|P3.z| < |min(z_obj for o in mid)| - 1500`.
- For each middle Bezier control point and each near/frame object: if
  `|control.z − z_obj| < 200` AND `|control.x − x_obj| < 400`, nudge
  control.x by 300 in the direction away from `x_obj`. Iterate until no
  collision (max 30 iterations); abort if it can't be resolved.
- `spring_halflife_s ∈ [0.15, 0.25]`. Setting to 0 produces the rigid
  rail "Final Cut keyframe" feel that the engine was specifically built
  to avoid (see [`references/transcripts/fpv_drone_blender_transcript.md`](references/transcripts/fpv_drone_blender_transcript.md)).
- Noise `z_amp` scales with intended speed (slow drift 5–10, moderate
  push 15–25, fast FPV 30–50); `xy_amp ≈ z_amp / 3`; `hz ∈ [0.5, 0.8]`.
- `bank_from_velocity ∈ [0.3, 0.6]`.

**Model:** `claude-sonnet-4-6` (Sonnet). Geometric reasoning + parameter
selection from a small space; Sonnet is sufficient.

**Retries:** max 1 on collision-resolution failure (the heuristic
nudge usually succeeds on the first try; failure means the brief asks
for an impossible camera path through object placement).

**Cost ceiling per call:** ~$0.05 (2K input, 1K output Sonnet).

---

### Tier 3 — SVG Prompt Engineer

**Input** (JSON, one call per asset entry from Tier 2.5):
```json
{
  "asset": { "<one entry from Tier 2.5's object_manifest>" },
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

**Fix (now in scope — 2026-05-03 revision):** The 6-tier topology
splits the old scene-designer's responsibilities into three single-
purpose agents:

1. **Scene Architect (Tier 2)** — subject inventory + scene brief
   (palette, mood, duration, camera intent). No per-object Z/X/Y/plate.
2. **Set Dresser (Tier 2.5)** — places 30–50 individual objects with
   Z/X/Y/plate, runs the parallax verification gate.
3. **Camera Path Designer (Tier 2.7)** — bezier/spring/noise/bank, with
   collision avoidance against near-tier objects.

Each agent has its own system prompt that includes the renderer's
Pydantic schema excerpt for the section it produces (auto-generated
from `scene.py` to prevent drift). The old scene-designer file is
deprecated as part of Phase 3.

---

## 7. Cost estimate

Reference render: a typical 5-scene storyboard. **Layer count per
scene jumps from ~6 (4-tier design) to ~40 (6-tier design)** because
each individual object now gets its own SVG. So 5 scenes × 40 SVGs =
~200 SVGs/render. Assume ~10% are cast-member canonical productions
and another ~10% are variants; the remaining ~160 are local assets.

| Stage         | Calls | Tokens (in/out)     | Cost (Sonnet 4.6 / Opus 4.7)     |
|---------------|-------|---------------------|----------------------------------|
| Tier 1 Director           | 1   | 12K / 6K  (Opus)            | $0.18 + $0.45 = **$0.63** |
| Tier 2 Architect          | 5   | 8K / 4K each (Opus)         | 5 × $0.42 = **$2.10** |
| Tier 2.5 Set Dresser      | 5   | 4K / 6K each (Sonnet)       | 5 × $0.10 = **$0.50** |
| Tier 2.7 Camera Path      | 5   | 2K / 1K each (Sonnet)       | 5 × $0.02 = **$0.08** |
| Tier 3 Engineer           | 200 | 1.5K / 0.8K each (Sonnet)   | 200 × $0.017 = **$3.30** |
| Tier 4 Craftsman          | 180 | 3K / 5K each (Sonnet)       | 180 × $0.084 = **$15.12** |
| Tier 4 retries (~10%)     | ~18 | same as Tier 4              | **$1.51** |
| Variants (skip Tier 4)    | 20  | 4K / 3K each (Sonnet)       | 20 × $0.057 = **$1.14** |
| **Total per render**      |     |                             | **≈ $24.40** |

With prompt caching on the Tier 4 system block (~1200 tokens, cached
across all 180 Tier 4 calls): saves ~$0.65. Total **≈ $23.75**.

**Compare to current:** ~$0.05/render with Haiku producing unusable
output. The ~500× cost increase reflects both the per-call quality
upgrade (Sonnet vs Haiku) and the layer-count increase (~30× more
SVGs per scene). For a production tier where outputs ship to
end-users, this is the price of getting actual depth in the
flythroughs.

For development / iteration, a `--cheap` flag would drop Tier 3 (Tier
2.5 output goes directly to Tier 4 with a static prompt template) and
optionally reduce per-scene object count from ~40 to ~20, bringing
cost to ~$10/render with reduced quality. Recommended only for CI
smoke tests and prompt iteration. The 41-layer proof-of-concept run
of [`scripts/gen_forest_scene.py`](scripts/gen_forest_scene.py) cost
~$2 (single scene, no Director/Architect/Engineer overhead — only
direct Tier 4 calls).

---

## 8. Migration sequencing

### Phase 1 — additive

1. Add `parallax_engine/svg_pipeline/parallax_verification.py` — pure
   Python; replicates `render.py:243-261` cull-fade-aware s_max walk,
   computes the per-layer table, applies the four hard gates. Used by
   Tier 2.5 and by tests. Public API:
   `compute_table(manifest, scene_dict) -> (rows, track)` and
   `assert_gates(rows, manifest)`.
2. Add `parallax_engine/svg_pipeline/tier4_craftsman.py` — `claude-sonnet-4-6`,
   `validate_svg` gate (with **per-asset viewBox parameter** — do NOT
   hardcode `0 0 3840 2160`), retry loop, no fallback. Public API:
   `produce_svg(prompt: str, contract: ValidationContract) -> Path`.
3. Add `parallax_engine/svg_pipeline/tier3_engineer.py` — Sonnet,
   builds the Tier 4 prompt from a Tier 2.5 manifest entry + casting +
   scene context. Public API: `craft_prompt(asset_entry, casting,
   project, scene_context) -> str`.
4. Add `parallax_engine/svg_pipeline/tier2_7_camera_path.py` — Sonnet,
   bezier/spring/noise from manifest; uses
   `parallax_verification.collision_check`. Public API:
   `design_camera(manifest, intent) -> dict`.
5. Add `parallax_engine/svg_pipeline/tier2_5_set_dresser.py` — Sonnet,
   places objects, calls `parallax_verification.assert_gates`, retries
   on gate failure with violations in the prompt. Public API:
   `dress_scene(brief, casting) -> (manifest, table)`.
6. Add `parallax_engine/svg_pipeline/tier2_architect.py` — Opus,
   produces the subject inventory (no per-object placement). Public
   API: `architect_scene(storyboard, casting, scene_index, prior_scenes)
   -> SceneBrief`.
7. **Do NOT modify** `parallax_engine/tools/gen_image.py` yet.
8. Add `parallax_engine/svg_pipeline/__init__.py` with a
   `produce_assets_for_scene(...)` entry point that orchestrates
   2 → 2.5 → 2.7 → 3 → 4 (gate after 2.5).
9. Add tests with mocked Anthropic clients (golden-fixture-style).
   Critically: a test where the Set Dresser's first attempt fails
   gates (e.g., backdrop ratio 1.8x) and the retry produces a passing
   manifest. Asserts gates run before any Tier 3/4 call.

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

> **From a one-line user brief ("a drone flies through a dawn forest at
> golden hour"), the pipeline produces a 30–50 layer scene whose render
> shows individual trees at different depths with real differential
> parallax — near trees rush past, distant trees barely move, sky stays
> infinite — comparable to
> [`examples/forest_v2/`](examples/forest_v2/) and the rendered MP4 at
> `/tmp/forest_40layer.mp4` produced by the proof-of-concept this session.**

Comparable means: when the pipeline-generated scene is rendered, a human
reviewer watching it back-to-back with the proof-of-concept render sees
the same depth quality (individual-object parallax, not slideshow). No
SSIM target — this is a judgment call.

Secondary success criteria:

- The Set Dresser's parallax verification table is saved to disk
  alongside the scene.yaml and shows all gates passing.
- A render where the Set Dresser's first attempt fails gates (e.g.,
  backdrop ratio 1.8x) shows the retry succeeding with the violations
  in the prompt — i.e., the gate-failure feedback loop is wired through.
- A render with one cast member (red_bird) referenced in 3 scenes
  produces 1 canonical SVG + 2 variants. The variants share the same
  `body: #c84032` fill and pose.
- A render where Tier 4 cannot produce a valid SVG after 3 retries
  fails the pipeline (exit code 1) with a clear error message naming
  the failed asset. **No magenta placeholder lands on disk.**
- Render cost under $25 per full 5-scene storyboard end-to-end (see §7).

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
├── tier2_architect.py           # Opus — subject inventory + scene brief
├── tier2_5_set_dresser.py       # Sonnet — 30-50 object placement + parallax table
├── tier2_7_camera_path.py       # Sonnet — bezier/spring/noise from manifest
├── parallax_verification.py     # pure-Python: project, gates, memory check
├── tier3_engineer.py            # Sonnet — prompt crafting
├── tier4_craftsman.py           # Sonnet — SVG synthesis with hard validation
├── validation.py                # ValidationContract + validate_svg()
└── prompts/
    ├── tier2_system.md          # Scene Architect system prompt
    ├── tier2_5_system.md        # Set Dresser system prompt
    ├── tier2_7_system.md        # Camera Path Designer system prompt
    ├── tier3_system.md          # SVG Prompt Engineer system prompt
    └── tier4_system.md          # SVG Craftsman system prompt (text in §4)

parallax_engine/manager.py       # MODIFIED to dispatch to new pipeline
                                 # behind PARALLAX_USE_NEW_SVG_PIPELINE flag
parallax_engine/tools/gen_image.py  # UNCHANGED until Phase 3
parallax_engine/settings.py      # ADD flag default

tests/svg_pipeline/
├── test_tier4_validation.py     # validate_svg gate tests (per-asset viewBox)
├── test_tier3_prompt_format.py  # prompt structure tests with mocked clients
├── test_tier2_brief.py          # Scene Architect brief schema tests
├── test_tier2_5_set_dresser.py  # set dressing + parallax gates pass/fail
├── test_tier2_7_camera_path.py  # collision-avoidance nudging tests
├── test_parallax_verification.py # pure-Python: matches render.py s_max walk
└── fixtures/
    ├── golden_storyboard.yaml
    ├── golden_casting.yaml
    └── golden_object_manifest.yaml
```

---

## 12. Decisions to confirm before implementation

1. **Approve 6-tier topology** as designed (Director / Scene Architect /
   Set Dresser / Camera Path Designer / SVG Prompt Engineer / SVG
   Craftsman)?
2. **Approve cost ceiling** ~$24 per full 5-scene render? (vs current
   $0.05 for garbage output, vs an opt-in `--cheap` mode at ~$10 with
   reduced object counts.)
3. **Approve hard-fail on validation** with no placeholder fallback?
   This is non-negotiable from my POV but worth user-confirming.
4. **Approve hard-fail on parallax-gate** failure (max 2 retries on
   Set Dresser, then escalate)? Same principle as #3 applied to
   placement: silent acceptance of a backdrop ratio of 2.5x produces
   the same slideshow effect this redesign exists to fix.
5. **Approve Tier 1 (Director) staying unchanged?** It's working in the
   current system; this design assumes no change.
6. **Approve scope**: SVG pipeline + scene composition (Set Dresser +
   Camera Path Designer). The Scene Composer split (§6.4) is now
   subsumed by Tier 2.5 + Tier 2.7.

If yes to all, implementation is Phase 1+2+3 across roughly 2500-3500
lines of code spread over the files in §11 (the Set Dresser and
Camera Path Designer add ~1000 lines beyond the prior 4-tier estimate).
