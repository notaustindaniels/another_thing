# Scene Architecture — Composing Scenes for the Parallax Engine

This document specifies how a scene is **composed** for the `parallax-engine` renderer. It is the ground truth that supersedes the worked examples in `SPEC.md` §6 (which used six full-canvas plates and produced flat, slideshow-like output).

The renderer itself — projection, drone camera, mask compositing, encoder — is unchanged. What changes is the **number and granularity of layers** and the **process** by which their positions and the camera path are chosen.

---

## 1. The principle

A 2.5D parallax scene reads as three-dimensional only when many objects sit at many distinct depths. The illusion comes from **differential parallax** — near objects rushing past while far objects barely move — and that differential exists only between layers, not within a layer.

A single plate containing 16 trees has *zero* internal parallax: all 16 trees grow, shrink, and slide together as one rigid painting. The fix is structural: **every visible object becomes its own layer**, with its own Z, its own lateral (X, Y) offset, and its own plate dimensions. A forest is 30–50 layers, not 6.

---

## 2. Object categories and placement rules

Every object placed in a scene falls into one of five categories. The category determines its Z range, plate-size range, and the count expected per scene.

| Category | Z range | Plate size (px) | Count per scene | Purpose |
|---|---|---|---|---|
| **Sky / atmosphere** | z < −15000 | ≥ 6000 × 4000 | 1–2 | Effectively infinite backdrop. Camera never reaches it. Full-fill SVG (gradients, soft cloud bands) — the only category that fills its plate. |
| **Distant backdrop** | −15000 ≤ z < −8000 | ~4000 × 3000 | 3–5 | Mountain ranges, horizon treelines, distant city silhouettes. Camera barely affects them. |
| **Mid scenery** | −8000 ≤ z < −3000 | ~2000 × 2000 to 3000 × 2500 | 8–12 | Main parallax zone. Trees, rocks, structures. The camera passes through this zone over the bulk of the shot. |
| **Near objects** | −3000 ≤ z < −500 | 800 × 1200 to 1500 × 1800 | 10–15 | Speed-selling objects. Individual trees, branches, fog wisps. Each enters from an edge and rushes past the camera. |
| **Frame elements** | −500 ≤ z < −100 | 400 × 600 to ~600 × 900 | 5–8 | Peripheral motion. Leaves, particles, very-close fog. They cull within the first half of the shot — that's their job. |

**Hard minimums**: at least **30 total layers**, at least **3 per category**, **no two objects sharing the same Z** (jitter to enforce uniqueness on a deterministic grid).

**Plate-size memory budget**: a near or frame layer at `z = z_obj` reaches scale `s_max = perspective_px / (perspective_px − z_obj_at_closest_approach)` before culling. Its rasterized plate occupies `s_max × plate_w × s_max × plate_h × 4` bytes. Sum across all layers; total must stay under **4 GB** for a single render. This is why frame elements cap at 600×900 and not larger — at z = −150 with `perspective_px = 1200`, `s_max ≈ 8` and a 600×900 plate already rasterizes to ~17 MB.

---

## 3. Parallax verification — the gate

Before any SVG is generated, the set dresser computes a **parallax verification table** for the planned camera path. The table answers, for each layer: *how much does this object grow relative to itself over the shot?*

### 3.1 Math

Given `perspective_px` (default 1200) and the camera's world Z position `z_cam_world` at time `t`, a layer at world depth `z_obj` has camera-frame depth `z_cam = z_obj − z_cam_world` and screen scale:

```
s(t) = perspective_px / (perspective_px − z_cam(t))
     = perspective_px / (perspective_px − (z_obj − z_cam_world(t)))
```

When `z_cam ≥ perspective_px − 720` (i.e., `≥ 480` for the default), the renderer's near-cull begins to fade the layer out; full cull at `z_cam ≥ perspective_px − 300` (`≥ 900` for the default). See [`parallax_engine/projection.py`](parallax_engine/projection.py).

Because spring lag means the camera never quite reaches the bezier endpoints, the table must be computed against the **actual sampled camera track** (using `parallax_engine.camera`), not the bezier control points.

### 3.2 Table format

```
id            cat        z       s_min   s_max    ratio   notes
sky           backdrop  -20000   0.057   0.060    1.05x   ✓ (camera barely affects)
mountains_01  backdrop  -14000   0.079   0.085    1.08x   ✓
trees_mid_03  mid       -5500    0.179   0.300    1.68x   ✓
tree_near_07  near      -1200    0.500   ∞        cull@t=0.74s   ✓ (passes camera)
leaf_fg_02    frame     -300     0.800   ∞        cull@t=0.21s   ✓ (passes camera early)
```

For categories where the camera passes the object (near, frame), `s_max → ∞` is expected at the cull moment; the report shows `cull@t=<time>s` instead of a numeric ratio.

### 3.3 Hard gates

The set dresser must adjust placements until **every** entry passes:

- **Backdrop**: `ratio < 1.5×`. If ≥ 1.5×, the object grows visibly during the shot — push it deeper.
- **Distant backdrop / mid**: `ratio` reported but ungated; these are the visual interest, ratios of 1.2× to 3× are fine.
- **Near**: must achieve `s ≥ 3.0` at some point during the shot. If not — either the camera doesn't get close enough or the object is too far back; bring it closer or push the camera farther.
- **Frame**: must cull within `duration_s` (i.e., `cull_at_t < duration_s`). If a frame element never culls, it's not actually close enough to be a frame element — recategorize as near.
- **Memory**: sum of `s_max² × plate_w × plate_h × 4` across all layers must be `< 4 GB`.

If any gate fails, the set dresser adjusts and recomputes. The verification table must pass before any SVG is generated.

---

## 4. The set-dressing algorithm

Pseudocode. Inputs: `brief` (palette, mood, subject), `duration_s`, `camera_speed_intent`, RNG `seed`. Output: a `manifest` (list of object dicts) plus a passing parallax verification table.

```
1. Choose camera Z travel from intent:
     "gentle drift"      → 3000 units
     "moderate push"     → 6000 units
     "fast FPV drone"    → 10000 units
     "extreme speed"     → 20000 units
   (Note: keep the back half of the shot populated. The camera should
    end before passing the deepest mid-scenery object, otherwise the
    last second of the shot is mostly sky.)

2. Place sky/atmosphere at z = -(camera_travel_distance × 2 to 3),
   plate ≥ 6000×4000, x = y = 0.

3. Place distant backdrop (3–5 objects) at z ∈ [-15000, -8000],
   plates 4000×3000, lateral spread x ∈ [±200, ±600].

4. Place mid scenery (8–12 objects) evenly across z ∈ [-8000, -3000],
   plates 2000×2000 to 3000×2500, lateral spread x ∈ [±300, ±1000].

5. Place near objects (10–15 objects) clustered in the first 40% of
   camera travel, z ∈ [-3000, -500], plates 800×1200 to 1500×1800,
   lateral spread x ∈ [±500, ±2000].

6. Place frame elements (5–8 objects) in the nearest 10% of Z,
   z ∈ [-500, -100], plates 400×600 to 600×900, lateral spread
   x ∈ [±800, ±1500].

7. Enforce: all Z unique (jitter on deterministic grid); no two layers
   within 200 Z AND 400 lateral units (visual collision); every layer's
   viewBox aspect equals its plate aspect.

8. Compute parallax verification table (§3) against the sampled camera
   track. Adjust violators:
     - backdrop ratio ≥ 1.5×  → push z deeper
     - near s_max < 3.0       → bring z closer or extend camera Z range
     - frame never culls      → bring z closer or recategorize as near
     - total memory ≥ 4 GB    → shrink the largest plates

9. Re-verify until table passes. If 2 retries still fail, abort and
   report — the brief itself may be incoherent with the engine's
   physics.
```

---

## 5. Camera path design — strictly after set dressing

The camera path is **never** designed before objects are placed. Object positions inform path control points; otherwise the camera flies through walls or past nothing.

Drone-camera config (per [`SPEC.md`](SPEC.md) §2.3 and the Blender FPV reference at [`references/transcripts/fpv_drone_blender_transcript.md`](references/transcripts/fpv_drone_blender_transcript.md)):

- **Bezier path**: 4 control points minimum.
  - P0: `[0, 0, 0]` (camera start at world origin).
  - P1, P2: lateral offsets at the 1/3 and 2/3 marks creating a gentle S-curve that *weaves between* near objects.
  - P3: `[0, 0, −camera_travel_distance]`, with `camera_travel_distance` chosen so deep mid scenery is still ahead of the camera at the end of the shot.
- **Avoidance**: no control point may sit within **200 Z-units** of a near/frame object's Z **at the same lateral cell**. If collision detected, nudge laterally by 300+ units.
- **Spring**: `spring_halflife_s ∈ [0.15, 0.25]` — gives the camera weight. Setting this to 0 produces the rigid-rail "Final Cut Pro keyframe" feel that the engine was specifically built to avoid.
- **Noise**: `z_amp` scales with intended speed (slow drift → 5–10, moderate push → 15–25, fast FPV → 30–50). `xy_amp` is roughly `z_amp / 3`. `hz ∈ [0.5, 0.8]` gives organic wobble.
- **Bank from velocity**: 0.3–0.6. The roll-with-curve effect that sells "this is a drone, not a slider rig."
- **POI lookahead**: 0.4–0.7s. Camera looks ahead on the path so its yaw leads the curve.

The Blender FPV transcript's central insight: **speed naturally emerges from curves**. Don't author keyframed velocity ramps. Place objects and let the spring + noise + bezier-curvature interaction produce the right motion. Long straight sections feel fast; tight curves slow the camera (because the spring lags more on direction changes); the wobble breathes life into static moments.

---

## 6. SVG-per-object contract

Each layer's SVG depicts **exactly one object** on a **transparent background**. The object is centered in the SVG's viewBox, and the **viewBox dimensions match the layer's `plate_size`** so the artist's units map 1:1 to plate pixels.

| Field | Rule |
|---|---|
| `viewBox` | `"0 0 <plate_w> <plate_h>"` — must match `plate_size` exactly |
| `width`, `height` | Optional; if present must equal viewBox dims |
| Background | Transparent. **No** full-canvas `<rect fill=...>`. (Sky exception below.) |
| Subject placement | Centered (`anchor: "center"` is the default for `LayerSpec` — see [`parallax_engine/scene.py`](parallax_engine/scene.py)) |
| Aspect ratio | `viewBox` aspect must equal `plate_size` aspect. rsvg-convert silently stretches mismatches. |
| Shape count | ≥ 8 paths/polygons/circles/ellipses (validation gate) |
| Style | Painterly, organic silhouettes — match the quality bar of [`examples/portal/assets/`](examples/portal/assets/) |

**Sky / atmosphere exception**: this is the only category that fills its full viewBox (gradients, soft cloud bands, atmospheric haze). It is the scene's infinite backdrop, never composited *behind* anything else.

**One object per SVG, always**:
- A *single tree* — not a treeline of 16 trees.
- A *single mountain peak* — not a mountain range. (A "distant mountain range" is 3–5 separate single-mountain SVGs at slightly different Z values.)
- A *single fog wisp* — not a fog band.
- A *single branch cluster* — not a wall of leaves.

Putting multiple objects on one plate erases the parallax differential between them and is exactly the failure mode this architecture corrects.

---

## 7. Renderer integration

Nothing in the renderer changes. The `Scene` Pydantic schema in [`parallax_engine/scene.py`](parallax_engine/scene.py) already accepts arbitrary numbers of layers, per-layer `plate_size`, per-layer `scene_xyz`, and the drone camera block. A 41-layer scene is structurally identical to a 6-layer scene — there are simply more entries under `stacks.<name>.layers`.

**Performance considerations** (defer to measurement):
- Per-frame projection: 41 layers × 240 frames = ~10K projections. Numpy-fast.
- Rasterization: each layer rasterizes **once** at its `s_max × plate_size`. That cache is the dominant memory cost; the §3.3 memory gate is what protects it.
- Compositing: 41 alpha blends per frame at 960×540 — well within real-time.

If a 41-layer render runs slow or OOMs, the levers are: shrink frame-element plates further, reduce near-object count, lower resolution. The renderer code itself does not need optimization for this layer count.

---

## 8. Process summary (and pipeline integration)

```
Director (Tier 1)         → storyboard: scenes, palette, mood
Scene Architect (Tier 2)   → per-scene subject inventory, spatial roles
Set Dresser (Tier 2.5)     → 30–50 object manifest with Z/X/Y/plate
                             + parallax verification table  ── HARD GATE
Camera Path Designer       → bezier controls, spring, noise, bank
  (Tier 2.7)                 (computed from manifest, avoiding near objects)
SVG Prompt Engineer        → per-SVG art brief (single object)
  (Tier 3)
SVG Craftsman (Tier 4)     → one SVG per call, transparent background,
                             viewBox = plate_size
Renderer                   → existing parallax_engine, no changes
```

The two new agents — Set Dresser (Tier 2.5) and Camera Path Designer (Tier 2.7) — are documented in [`svg_pipeline_design.md`](svg_pipeline_design.md). The parallax verification table is the gate between set dressing and SVG generation; nothing downstream runs until it passes.
