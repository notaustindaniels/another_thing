# `parallax-engine` — Canonical Specification

> **Read this first.** This document is the source of truth for the project. Any decision Claude Code makes that conflicts with this document is wrong by default. If a conflict surfaces a real ambiguity, fix the spec, commit it, then proceed.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture: The Unified Engine](#2-architecture-the-unified-engine)
3. [Agent Harness](#3-agent-harness)
4. [Skill Packaging](#4-skill-packaging)
5. [Stack and Licensing](#5-stack-and-licensing)
6. [Worked Examples](#6-worked-examples)
7. [Determinism Contract](#7-determinism-contract)
8. [Build Phase Plan](#8-build-phase-plan)
9. [Cross-Cutting Invariants](#9-cross-cutting-invariants)
10. [Glossary](#10-glossary)
11. [The Director Tier: Storyboard-Driven Creative Orchestration](#section-11--the-director-tier-storyboard-driven-creative-orchestration)

---

## 1. Project Overview

### 1.1 What this is

`parallax-engine` is a Python project that produces 2.5D multiplane camera animations as MP4 files from natural-language briefs. It is the engine — renderer plus director tier plus orchestration. It ships internally as three layers:

- **The renderer:** a deterministic Python pipeline (Pillow + skia-python + NumPy + FFmpeg/libopenh264) that takes a `scene.yaml` and emits a video.
- **The implementation harness:** parallel-wave agents (asset-generator, mask-author, camera-pather, qa-critic) that produce assets and metadata for the renderer.
- **The director tier:** sequential creative agents (director → scene-designers → project-manager) that translate a written brief into the `scene.yaml` and casting bible the implementation harness consumes.

The engine is exposed to end users via `parallax-skill`, an Anthropic Skill — a thin `SKILL.md` + `scripts/run.sh` shim that lets Claude (in claude.ai or the API) invoke `parallax-engine` from a user brief. The Skill is the storefront; the engine is the factory. See §4.

### 1.2 Naming conventions (read carefully — these names are not interchangeable)

This project distinguishes three things that are easy to confuse:

| Name | What it refers to | Where it lives |
|---|---|---|
| `parallax-engine` (PyPI dist) / `parallax_engine` (Python import) / `parallax-engine` (CLI) | The engine itself: renderer + agent harness + director tier. The Python codebase. | `parallax_engine/` package directory; eventually `pip install parallax-engine` |
| `parallax-skill` | The Anthropic Skill wrapper. A small `SKILL.md` + `scripts/run.sh` shim that lets Claude (in claude.ai or via the API) invoke the engine. | `parallax-skill/` directory inside the Phase 5 deliverable; this is what gets shipped to the Skill marketplace |
| The `parallax-skill/` build directory | The repo root: contains the engine, the Skill wrapper, the build harness, the spec, the validators. | The directory you unpacked the tarball into. |

When this spec says "the engine," "Director," "scene-designer," etc., it always means components of `parallax-engine`. When it says "the Skill" it always means the `parallax-skill` wrapper. The build directory is mentioned only in setup contexts.

This distinction matters because the engine is sellable as three product surfaces (the Skill, a Python CLI, the renderer alone) and the names need to support that decomposition. See §4 (Skill Packaging) for the wrapper details and §5 (Stack and Licensing) for distribution.

### 1.3 What it produces

The renderer reproduces three families of behavior **as configurations of one engine**, not as three separate code paths:

1. **Drone-FPV flythrough.** Camera follows a Bezier path through a stack of 2D plates with critically-damped spring follow, organic noise wobble, and roll-from-velocity bank. Visual reference: Polyfjord's "Realistic FPV Drone Footage in Blender" tutorial, but with 2D SVG plates instead of 3D meshes. Aesthetic target: classic Disney multiplane camera (Bambi, Pinocchio) with high-speed FPV camera energy.
2. **Heygen-style 2.5D parallax with biome reveals.** Camera pans/zooms across multiple layer stacks, with masked transitions (wipes, irises, gradient sweeps, displaced edges) between consecutive stacks.
3. **Portal transitions.** Two stacks running simultaneously; a world-anchored mask on one layer cuts the source stack to reveal the destination stack underneath. As the camera approaches the mask layer, the hole grows by perspective scale alone (no time animation needed). At the moment the layer near-culls, the mask flips fully open and the destination stack takes the whole frame.

These are the same engine. A "drone flythrough" is `camera.mode = drone, masks = []`. A "biome reveal" is `multiple stacks, mask anchor = screen, growth = radius(t)`. A "portal" is `two stacks, mask anchor = world, growth = perspective`. See §6 for full worked examples.

### 1.4 What this is not

- **Not a 3D engine.** No meshes, no shaders, no GPU pipeline. All projection math is one perspective-divide formula evaluated in NumPy.
- **Not a real-time renderer.** Headless, deterministic, frame-by-frame, encode-to-MP4.
- **Not a fork of any existing tool.** Not Hyperframes, not Remotion, not Three.js. Inspired by the user's prior CSS-3D Remotion implementation but ported wholesale to Python.
- **Not a chat-driven authoring tool.** The harness is the authoring layer; the user provides briefs, not scene files.

### 1.5 Why each layer exists

| Layer | Why it exists |
|---|---|
| Renderer (deterministic Python) | Byte-identical output across runs is required for testing, regression, and customer trust. LLMs cannot produce deterministic pixel output; pure Python can. |
| Harness (multi-agent system) | Translating a one-paragraph brief into a 200-line scene file with masks, camera paths, and asset prompts is irreducibly creative; LLMs are the right tool. The orchestrator-worker pattern (Anthropic's Research feature) is the proven topology. |
| Skill (`SKILL.md` + shim) | Users invoke Claude with natural language, not Python CLIs. The Skill is the entry point Claude recognizes; everything else is downstream of it. |

### 1.6 Commercial intent

The Skill is commercial. License diligence is non-negotiable: every dependency is permissive (MIT, Apache 2.0, BSD, HPND, LGPL-with-dynamic-linking) and the FFmpeg build is LGPL with libopenh264 (no GPL contamination, no H.264 patent royalties owed downstream). See §5.

---

## 2. Architecture: The Unified Engine

### 2.1 The projection model

**Coordinate system.** Right-handed. `+X` right, `+Y` down (matches image space), `+Z` away from camera. Camera looks down `−Z` by default.

**Pose.** The camera has six degrees of freedom: `(cx, cy, cz, yaw, pitch, roll)`. Position in scene units (interpret as pixels for plate-aligned scenes). Yaw rotates around the camera's local Y, pitch around X, roll around Z. Compose intrinsic-Z-X-Y so roll applies last.

**The single projection equation.** For a scene-space point `p = (x, y, z)`:

1. Transform to camera frame: `p_c = Rᵀ · (p − c)` where `R = R_z(roll) · R_x(pitch) · R_y(yaw)`.
2. Perspective project:
   ```
   s     = perspective_px / (perspective_px − z_c)
   X_scr = O_x + x_c · s
   Y_scr = O_y + y_c · s
   ```

`perspective_px` is the focal-length-equivalent in pixels (default 1200, matching the user's existing Remotion config). `(O_x, O_y)` is the principal point — the pixel through which the optic axis exits the screen. Default: image center `(W/2, H/2)`.

**Equivalence with CSS.** When the camera is at origin with no rotation, `z_c = z`, and the formula reduces to `s = perspective_px / (perspective_px − z)` — identical to CSS `transform: translate3d(x, y, z); perspective: <perspective_px>px`. A scene authored in the user's existing Remotion repo can be losslessly ported by copying `(initial_z + camera_z)` per plate to `scene_z` and setting the camera at `(0, 0, 0)`.

**Vectorized implementation.** Every projection in the renderer goes through this function:

```python
import numpy as np

def project_points(points_xyz: np.ndarray,
                   cam: dict,
                   perspective_px: float,
                   origin_xy: tuple[float, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Project an (N,3) array of scene-space points through the camera.
    Returns (s: (N,), screen_xy: (N,2), z_cam: (N,)).
    z_cam negative means in front of the camera (further down -Z).
    """
    cx, cy, cz = cam["cx"], cam["cy"], cam["cz"]
    yaw, pit, rol = cam["yaw"], cam["pitch"], cam["roll"]
    cy_, sy_ = np.cos(yaw), np.sin(yaw)
    cp_, sp_ = np.cos(pit), np.sin(pit)
    cr_, sr_ = np.cos(rol), np.sin(rol)
    Ry = np.array([[ cy_, 0, sy_], [ 0, 1, 0], [-sy_, 0, cy_]])
    Rx = np.array([[1, 0, 0], [0, cp_, -sp_], [0, sp_, cp_]])
    Rz = np.array([[cr_, -sr_, 0], [sr_, cr_, 0], [0, 0, 1]])
    R = Rz @ Rx @ Ry
    p = points_xyz - np.array([cx, cy, cz], dtype=np.float64)
    pc = p @ R                                        # equivalent to Rᵀ · pᵀ for row-vectors
    xc, yc, zc = pc[:, 0], pc[:, 1], pc[:, 2]
    denom = perspective_px - zc
    denom = np.where(denom < 1.0, 1.0, denom)         # near-plane epsilon (see §2.4)
    s = perspective_px / denom
    X = origin_xy[0] + xc * s
    Y = origin_xy[1] + yc * s
    return s, np.stack([X, Y], axis=1), zc
```

**This function is the entire 3D math of the engine.** Layers, mask paths, portal silhouettes, sub-objects — everything goes through `project_points`. There is no other projection code anywhere.

### 2.2 The scene schema

**One YAML document describes everything a render needs.** It is parsed into Python dataclasses; the dataclasses are the only types the renderer consumes. The YAML is authored either by hand or by the harness's `scene-designer` subagent; the renderer treats both sources identically.

```yaml
# parallax-scene v1
version: 1
meta:
  duration_s: 10.0
  fps: 30
  resolution: [1920, 1080]
  perspective_px: 1200
  origin: [960, 540]            # principal point (Ox, Oy) in pixels
  bg_color: "#000000"
  seed: 1234                    # the only randomness root for the entire scene

stacks:                         # named layer stacks ("biomes")
  forest:
    layers:
      - id: sky
        src: "assets/sky.svg"
        scene_xyz: [0, 0, -12000]
        plate_size: [3840, 2160]   # native plate size BEFORE perspective
        anchor: center             # center | top-left | (px, py) tuple
        post:
          dof_blur_px: 0
          depth_fade: 0.5
      - id: trees_far
        src: "assets/trees_far.svg"
        scene_xyz: [0, 0, -8500]
        plate_size: [3840, 2160]
        post: { dof_blur_px: 5, depth_fade: 0.35 }
      # ...
  city:
    layers:
      - { id: city_sky, src: "assets/city_sky.svg", scene_xyz: [0,0,-10500], plate_size: [3840,2160] }
      # ...

camera:
  mode: drone                   # drone | keyframed
  drone:                        # only present if mode == drone
    path:
      kind: bezier
      controls: [[0,0,0], [180,30,-3000], [-150,-25,-7000], [0,0,-11500]]
      duration_s: 10.0
    poi_lookahead_s: 0.55       # seconds the look-target runs ahead of the rig
    spring_halflife_s: 0.18     # critically-damped spring halflife
    noise:
      z_amp: 22                 # vertical wobble amplitude in scene units
      xy_amp: 6                 # horizontal jitter amplitude
      hz: 0.7                   # noise frequency
    bank_from_velocity: 0.40    # roll = -k * tanh(lateral_velocity / 200)
  keyframed:                    # only present if mode == keyframed
    - { t: 0.0, x: 0,    y: 0, z: 0,     yaw: 0.0,  pitch: 0, roll: 0, ease: "easeInOutCubic" }
    - { t: 6.0, x: 300,  y: 0, z: -1500, yaw: 0.10, pitch: 0, roll: 0 }
    # ...

masks:                          # zero or more
  - id: portal
    path_svg: "assets/portal_tree.svg"      # MUST be the same SVG used by the silhouette layer
    silhouette_id_in_svg: "silhouette"      # <path id="silhouette"> paints the visible tree
    path_id_in_svg: "hole"                  # <path id="hole"> defines the mask hole
    attached_to_layer: "forest.portal_tree" # qualified <stack>.<layer_id>
    anchor: world                           # world | screen | layer-plane
    src_stack: forest
    dest_stack: city
    matte: alpha                            # alpha | luminance
    invert: false
    growth:
      kind: perspective                     # perspective | radius | gradient | matte_seq | displaced_edge

post:
  global:
    vignette: { strength: 0.45, radius: 0.85 }
    grain:    { sigma: 4 }
    light_leaks: { sprite: "assets/leak1.png", opacity: 0.18, blend: "screen" }
    fisheye:  { k1: 0.18, k2: 0.06 }
    color_grade: { lut: "assets/cinematic.cube" }
```

**Schema rules (opinionated; non-negotiable):**

- The schema is **versioned** (`version: 1`). Future versions are additive; breaking changes require a new major version.
- **Layers are flat plates.** No mesh geometry. Plates are billboards always facing the camera; their visual rotation comes from the camera's roll only (until per-layer rotation is added in v2).
- **Stacks are first-class.** A scene with no masks has one stack named `default` or any single name.
- **Masks are top-level objects**, not nested in layers. A mask references stacks by name and a layer by qualified `<stack>.<layer_id>` name.
- **Per-layer post is local; global post applies after all stacks are composited.**
- **`meta.seed` is the only randomness root.** Every other RNG in the system derives from it via `numpy.random.SeedSequence.spawn`.
- **Asset paths are relative to the workspace root.** Never absolute. The renderer resolves them against `workspace/`.
- **Coordinates are in pixels** for the default scale (1920×1080 frame, perspective_px=1200). Authors can use a different unit if they're consistent.

### 2.3 The drone-physics camera

**Goal.** Reproduce the Polyfjord cinematic-FPV signature — fast on straights, lagging-then-whipping through corners, organic vertical wobble, leaning into turns — using a deterministic discrete-time integrator that produces byte-identical output across machines.

**Decomposition.** The Blender method has four conceptual pieces; we port each to a Python primitive:

| Blender concept | Python primitive |
|---|---|
| Bezier curve in scene space | NumPy Bernstein-polynomial evaluator |
| POI empty advancing along the curve | Lookahead time offset on the same curve |
| Camera as rigid body chasing POI via force field | Critically-damped spring (Holden's exact integrator) |
| Z-noise modifier for vertical wobble | Seeded simplex/Perlin noise on (x, y, z) |
| (Implicit: bank into corners from velocity) | `roll = -k * tanh(lateral_velocity / 200)` |

**The integrator (definitive form).** The exact-discrete critically-damped spring guarantees stability at any `dt` and bit-identical reproduction.

```python
def drone_camera_track(scene, T: float, fps: int) -> np.ndarray:
    """
    Returns an (n, 6) array of (x, y, z, yaw, pitch, roll) per frame,
    with n = int(T * fps).
    """
    n = int(T * fps)
    dt = 1.0 / fps
    path = BezierPath(scene.camera.drone.path.controls, scene.camera.drone.path.duration_s)
    look_dt = scene.camera.drone.poi_lookahead_s
    h = scene.camera.drone.spring_halflife_s
    k_bank = scene.camera.drone.bank_from_velocity
    n_amp = scene.camera.drone.noise

    # Deterministic noise seeded from meta.seed
    ss = np.random.SeedSequence(scene.meta.seed)
    noise = SeededNoise(ss.spawn(1)[0])     # vendored simplex noise; see §5

    omega = np.log(2) / h
    x = np.array(path.eval(0.0), dtype=np.float64)
    v = np.zeros(3, dtype=np.float64)

    poses = np.empty((n, 6), dtype=np.float64)
    for i in range(n):
        t = i * dt
        tgt = np.array(path.eval(min(t + look_dt, T)))
        nx = n_amp.xy_amp * noise.sample(t * n_amp.hz, axis=0)
        ny = n_amp.xy_amp * noise.sample(t * n_amp.hz, axis=1)
        nz = n_amp.z_amp  * noise.sample(t * n_amp.hz, axis=2)
        tgt_n = tgt + np.array([nx, ny, nz])

        # Holden critically-damped spring step
        e  = np.exp(-omega * dt)
        j0 = x - tgt_n
        j1 = v + j0 * omega
        x  = e * (j0 + j1 * dt) + tgt_n
        v  = e * (v - j1 * omega * dt)

        # Look-at: a point further along the curve
        look_pt = np.array(path.eval(min(t + look_dt + 0.25, T)))
        fwd = look_pt - x
        yaw   = np.arctan2(fwd[0], -fwd[2])
        pitch = np.arctan2(-fwd[1], np.hypot(fwd[0], fwd[2]))

        # Bank from lateral velocity
        v_lat = v[0] * np.cos(yaw) - v[2] * np.sin(yaw)
        roll  = -k_bank * np.tanh(v_lat / 200.0)

        poses[i] = (x[0], x[1], x[2], yaw, pitch, roll)
    return poses
```

**The keyframed mode.** Direct per-segment easing, no spline through keyframes. Simpler, hand-authorable, predictable.

```python
def kf_camera_track(scene, T, fps):
    kfs = sorted(scene.camera.keyframed, key=lambda k: k["t"])
    n = int(T * fps)
    out = np.empty((n, 6), dtype=np.float64)
    for i in range(n):
        t = i / fps
        a, b = bracket(kfs, t)
        u = ease(a.get("ease", "linear"), (t - a["t"]) / max(b["t"] - a["t"], 1e-9))
        for j, key in enumerate(("x", "y", "z", "yaw", "pitch", "roll")):
            out[i, j] = (1 - u) * a.get(key, 0.0) + u * b.get(key, 0.0)
    return out
```

Easings supported in v1: `linear`, `easeInOutCubic`, `easeOutQuint`, `easeInOutSine`. More are additive.

### 2.4 The mask system

**Compositing rule (definitive).** For every mask `m`:

```
F = D · M + S · (1 − M)
```

Where `S` is the source-stack composite, `D` is the destination-stack composite, and `M` is the mask alpha (rasterized at frame resolution, in [0, 1]). All three buffers are premultiplied-alpha RGBA `float32`.

**Building `M`** depends on `mask.anchor` and `mask.growth.kind`:

| anchor | growth.kind | How M is built |
|---|---|---|
| `world` | `perspective` | Project the mask path's vertices through `project_points` using the attached layer's `(x, y, z)` and the current camera. Rasterize the projected polygon to a frame-resolution alpha matte. The matte grows automatically as the layer approaches the camera (this is the portal). |
| `world` | `radius(t)` or `matte_seq` | Project as above, then additionally animate (radius scaling or matte-sequence lookup). |
| `screen` | `radius(t)` | Path is in screen-pixel coordinates. Animate radius from `r0` at `t0` to `r1` at `t1`. Optional `feather_px` Gaussian-blurs the matte. |
| `screen` | `gradient` | A gradient sweep along an axis, animated from t0→t1. Useful for wipes. |
| `screen` | `displaced_edge` | A diagonal mask edge displaced by a noise/displacement map; for organic transitions. |
| `layer-plane` | any | Path is in the attached layer's local 2D coordinate system, but the layer's `z` is constant. Used when a pre-computed matte rides along with a 2D-translated layer. |

**The "near-cull handoff" rule (this is what makes the portal feel passable).**

When the mask layer's near-cull opacity drops to zero (i.e., the layer has dissolved because it got too close to the camera), the mask flips to fully open: `M = 1.0` everywhere. The destination stack takes the entire frame. This is the "pass through the portal" moment.

```python
def build_mask_alpha(mask, scene, cam, frame_resolution, t):
    layer = scene.find_layer(mask.attached_to_layer)
    s, screen_xy, z_cam = project_points(layer.scene_xyz[None, :], cam,
                                         scene.meta.perspective_px,
                                         scene.meta.origin)
    cull_opacity = compute_near_cull(z_cam[0])

    if cull_opacity < 0.001:
        # The mask layer has dissolved. Open the portal fully.
        return np.ones(frame_resolution, dtype=np.float32)

    if mask.anchor == "world":
        path_polygon = project_path_through_camera(mask.path_id_in_svg,
                                                   mask.path_svg,
                                                   layer.scene_xyz, cam,
                                                   scene.meta.perspective_px,
                                                   scene.meta.origin)
        M = rasterize_polygon_to_alpha(path_polygon, frame_resolution)
    elif mask.anchor == "screen":
        M = build_screen_anchored_matte(mask, t, frame_resolution)
    else:
        M = build_layer_plane_matte(mask, layer, cam, frame_resolution)

    if mask.growth.get("feather_px"):
        M = scipy.ndimage.gaussian_filter(M, mask.growth["feather_px"])
    if mask.invert:
        M = 1.0 - M
    return M.astype(np.float32)
```

**The same SVG defines both silhouette and hole.** This is non-negotiable. The portal layer's source SVG must contain two paths with stable IDs (`silhouette_id_in_svg`, `path_id_in_svg`), both inside the same `<svg viewBox>`. The renderer paints the silhouette via the layer pipeline and the mask via the projection pipeline, and they cannot drift because they share the viewBox. **Two separate SVGs are forbidden** — that was the bug in the user's prior repo.

**The "in-front-of-mask" rule (generalized L2 trick).** After computing `F = D·M + S·(1−M)` for a mask, identify all source-stack layers whose camera-space Z is in front of the mask layer (i.e., closer to the camera). Re-project and alpha-over them onto `F` *unmasked*. This makes a leaf nearer than the portal correctly occlude the seam.

```python
def composite_with_mask(scene, mask, S, D, cam, frame_resolution, t):
    M = build_mask_alpha(mask, scene, cam, frame_resolution, t)
    F = D * M[..., None] + S * (1.0 - M[..., None])
    layer = scene.find_layer(mask.attached_to_layer)
    _, _, z_layer = project_points(layer.scene_xyz[None, :], cam,
                                   scene.meta.perspective_px, scene.meta.origin)
    src_layers = scene.stacks[mask.src_stack].layers
    for L in src_layers:
        _, _, z_L = project_points(L.scene_xyz[None, :], cam,
                                   scene.meta.perspective_px, scene.meta.origin)
        if z_L[0] > z_layer[0]:                 # closer to camera
            sprite = render_layer(L, cam, scene)
            alpha_over(F, sprite)
    return F
```

This is auto-applied to every mask in the scene.

### 2.5 The renderer pipeline

**Per-frame algorithm** (execute in order, exactly once per output frame):

1. Compute camera pose from camera mode (drone or keyframed). Look up the precomputed pose for this frame index.
2. For each stack `s` in the scene, in order of declaration: composite all of `s`'s layers into a frame-sized RGBA buffer `buf[s]`:
   - Sort layers by `z_cam` descending (back to front).
   - For each layer:
     - Project the layer's center → `(s, X, Y, z_cam)`.
     - If `z_cam <= near_cull_threshold`: skip (or alpha-fade if in the cull window).
     - Look up the cached SVG raster at the appropriate target size.
     - Apply per-layer post: DOF blur (Gaussian σ from `dof_blur_px` and z), depth-fade (lerp toward fog color), motion blur (optional).
     - Alpha-over the sprite onto `buf[s]` at `(X − w/2, Y − h/2)`.
3. For each mask `m` in declaration order:
   - Build `M` per §2.4.
   - Compute `F = buf[dest] · M + buf[src] · (1 − M)`.
   - Apply the in-front-of-mask rule.
   - Replace `buf[dest]` with `F` (mask consumes the dest stack downstream).
4. The final output is the last remaining stack buffer (typically the last destination of the last mask, or the only stack if no masks).
5. Apply global post: vignette → grain → light leaks → fisheye/lens distortion → color grade. Order matters; this is the pinned order.
6. Convert to `uint8` RGBA and write to FFmpeg's stdin.

**SVG rasterization is cached.** Before the render loop starts, walk the camera track for every layer, compute the maximum projected size required, rasterize the SVG once at that size via `skia-python`'s `SVGDOM`, and store as a premultiplied `float32` array. Per-frame work is `cv2.resize` (Lanczos for shrink, area for grow) — SIMD-fast.

**The `over` operator is premultiplied.** Always. Straight alpha is forbidden anywhere outside of file I/O boundaries.

```python
def alpha_over(dst: np.ndarray, src: np.ndarray, dx: int, dy: int) -> None:
    """In-place premultiplied 'over' with src placed at (dx, dy) in dst."""
    h, w = src.shape[:2]
    H, W = dst.shape[:2]
    x0, y0 = max(int(dx), 0), max(int(dy), 0)
    x1, y1 = min(int(dx) + w, W), min(int(dy) + h, H)
    if x1 <= x0 or y1 <= y0:
        return
    sx0, sy0 = x0 - int(dx), y0 - int(dy)
    sx1, sy1 = sx0 + (x1 - x0), sy0 + (y1 - y0)
    a = src[sy0:sy1, sx0:sx1, 3:4]
    dst[y0:y1, x0:x1] = src[sy0:sy1, sx0:sx1] + dst[y0:y1, x0:x1] * (1.0 - a)
```

### 2.6 Per-layer post

| Effect | Definition |
|---|---|
| **DOF blur** | `sigma = dof_blur_px * (1 + abs(z_cam − focal_z) / focal_falloff)`. Apply `scipy.ndimage.gaussian_filter` on RGB and A separately (RGB is already premultiplied, so blur is correct; A independently). |
| **Depth fade** | Lerp toward fog color: `rgb = lerp(rgb, fog_rgb, fade(z_cam))` where `fade(z) = clip((|z|−fog_near)/(fog_far−fog_near), 0, 1) * layer.post.depth_fade`. |
| **Near-cull** | Linearly fade alpha from 1 → 0 over `z_cam ∈ [cull_start, cull_end]`. Default: `cull_start = perspective_px − 720`, `cull_end = perspective_px − 300`. The exact values are scene-tunable but defaults are fixed. |
| **Motion blur** | Optional. Directional Gaussian along the projected velocity vector of the layer center. Off by default. |

### 2.7 Global post

| Effect | Definition |
|---|---|
| **Vignette** | `rgb *= 1 − strength * smoothstep(radius, 1.0, distance_from_center_normalized)`. |
| **Grain** | Add Gaussian noise of σ in luma; seeded from `meta.seed` plus frame index. |
| **Light leaks** | Alpha-over a sprite with screen blend: `out = 1 − (1−dst) * (1−src*opacity)`. Sprite is sampled at frame resolution. |
| **Lens distortion (fisheye)** | Backward-warp via precomputed pixel map `r_undist = r * (1 + k1·r² + k2·r⁴)`. Resample with `cv2.remap(..., INTER_LANCZOS4)`. |
| **Color grade** | 3D LUT lookup. `.cube` files supported. |

Pinned order: vignette → grain → light leaks → fisheye → color grade.

### 2.8 Encoding

FFmpeg LGPL build with libopenh264, frames piped via `image2pipe`:

```python
ff = subprocess.Popen([
    "ffmpeg", "-y",
    "-f", "rawvideo", "-pix_fmt", "rgba",
    "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
    "-c:v", "libopenh264",
    "-b:v", "8M", "-g", str(fps * 2),
    "-pix_fmt", "yuv420p",
    "-movflags", "+faststart",
    "-threads", "1",                 # required for byte-identical output
    out_path,
], stdin=subprocess.PIPE)
```

The `-threads 1` is non-negotiable for the determinism contract (§7).

---

## 3. Agent Harness

> **⚠️ SUPERSEDED IN PART BY SECTION 11.** The topology described in this section is the v0 design: a flat orchestrator-worker harness where `scene-designer` is a one-shot agent that produces `scene.yaml` directly from the brief. **Section 11 supersedes this.** In the canonical v1 architecture, the harness has three tiers (Director → Scene Designers → Implementation), `scene-designer` is a sequential per-scene translator (not a one-shot), and the lead orchestrator is renamed to `project-manager` with its creative responsibilities removed. **Read Section 11 first, then return here for the parts that are still canonical**: §3.2 (filesystem-as-shared-state pattern), §3.5 (auth swap), §3.6 (budget controls), §3.7 (observability), §3.8 (checkpointing). The agent topology in §3.1, the subagent contracts in §3.3, and the lead behavior in §3.4 are replaced by §11.1, §11.6, and §11.9 respectively.

### 3.1 Topology

The harness mirrors Anthropic's Research feature: lead orchestrator + scoped subagents communicating via the filesystem. Heavy-subagent for creative work (design, generation), in-process tools for deterministic work (rendering).

```
ParallaxLead (ClaudeSDKClient, model: claude-sonnet-4-5)
  Tools: Agent (delegate), Read, Write, Glob,
         mcp__parallax_render__render_scene,
         mcp__parallax_qa__diff_frames
  Job: brief → scene.yaml → delegate asset+mask+camera work →
       render → QA loop → emit final MP4

  Subagents (delegated via Agent tool):
    scene-designer   (sonnet, one-shot)
    asset-generator  (haiku, parallel per layer)
    mask-author      (haiku, parallel per mask)
    camera-pather    (sonnet, one-shot)
    qa-critic        (sonnet, iterative; capped at 3 passes in Python)
```

### 3.2 Filesystem-as-shared-state

All subagents communicate via a workspace directory. Subagents pass back lightweight status strings + filesystem paths, never large blobs.

```
workspace/
├── brief.md
├── scene.yaml                    # canonical, written by scene-designer
├── assets/
│   ├── sky.svg
│   ├── trees_far.svg
│   └── ...
├── masks/                        # if any masks need standalone files
├── camera_path.json              # optional intermediate output
├── frames/                       # rendered frame cache (cleaned at end)
├── qa/
│   ├── pass_01_report.md
│   └── pass_01_overlays/
├── checkpoints/state.json        # phase markers for resumption
├── logs/
│   ├── transcript.txt
│   ├── tool_calls.jsonl
│   └── usage.jsonl               # per-message cost tracking
└── out.mp4
```

### 3.3 Subagent contracts

Each subagent has a strict input/output contract written into its system prompt and reflected in its `description`. The lead matches against the description when deciding which subagent to dispatch.

| Subagent | Tools | Reads | Writes | Returns |
|---|---|---|---|---|
| `scene-designer` | Read, Write | `brief.md` | `scene.yaml` (manifest only; no assets yet) | "scene written: N layers, M masks, duration Ts" |
| `asset-generator` | Read, Write, Bash, `mcp__parallax_render__gen_image`, `mcp__parallax_masks__autosegment` | `scene.yaml` (one layer) | `assets/<layer>.svg`, `assets/<layer>.meta.json` | "ok: assets/<layer>.svg" |
| `mask-author` | Read, Write, `mcp__parallax_masks__alpha_refine` | `assets/<silhouette>.svg` | `assets/<silhouette>.svg` (with `id="silhouette"` and `id="hole"` paths in the same SVG) | "ok: silhouette + hole paths added to assets/<file>.svg" |
| `camera-pather` | Read, Write | `scene.yaml`, `brief.md` | updates `scene.yaml` with `camera:` block | "camera path written: M keyframes / drone path with K control points" |
| `qa-critic` | Read, Write, Glob, `mcp__parallax_qa__diff_frames`, `mcp__parallax_qa__ssim_score` | `frames/`, `scene.yaml`, `brief.md` | `qa/pass_NN_report.md` | "PASS" or "FAIL: <issues>, see qa/pass_NN_report.md" |

**Subagents do not delegate to other subagents.** The Claude Agent SDK enforces this; do not give a subagent the `Agent` tool.

**Subagents return short status strings + paths, never large blobs.** Workspace is the bus.

### 3.4 Lead orchestrator behavior

The lead is the only loop with multi-step decision-making. Its prompt includes effort-scaling rules to prevent runaway spawning:

```
For a 4–7 layer scene with N biomes:
  - Dispatch one scene-designer (one-shot)
  - Dispatch one asset-generator per layer in PARALLEL (single tool-use wave)
  - Dispatch one mask-author per mask in PARALLEL after assets exist
  - Dispatch one camera-pather (one-shot)
  - Call render_scene tool (in-process, deterministic)
  - Dispatch qa-critic; on FAIL, fix targeted assets/masks/camera and re-render
  - Cap QA passes at 3 (enforced by orchestrator code, not by prompting)
```

**The QA loop counter is a Python integer in the orchestrator, not a value the LLM is asked to track.** Telling an LLM "stop after 3 passes" via prompt is a coin flip; a `for i in range(3):` is a guarantee.

### 3.5 Auth swap (`CLAUDE_CODE_OAUTH_TOKEN` ↔ `ANTHROPIC_API_KEY`)

```python
def configure_credentials() -> str:
    """
    Resolve credentials. ANTHROPIC_API_KEY is the production path
    (required for commercial distribution per Anthropic's SDK terms).
    CLAUDE_CODE_OAUTH_TOKEN is the dev path (Pro/Max subscription).
    """
    if any(os.getenv(k) for k in
           ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")):
        return "cloud-provider"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "api-key"
    if os.getenv("CLAUDE_CODE_OAUTH_TOKEN"):
        # Map OAuth token to API key envvar so the SDK picks it up.
        os.environ["ANTHROPIC_API_KEY"] = os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
        return "oauth"
    sys.exit("No credentials. Set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN.")
```

The shipped Skill requires `ANTHROPIC_API_KEY`. The dev path supports `CLAUDE_CODE_OAUTH_TOKEN` for the maintainer's local development.

### 3.6 Budget and safety controls

Every `ClaudeSDKClient` call sets:

- `max_turns=80`
- `max_budget_usd=2.50` (tunable per render)
- `permission_mode="acceptEdits"` (lead writes scene.yaml, etc., without prompting)
- `allowed_tools` lists strict per-subagent tool surfaces

On `error_max_budget_usd` or `error_max_turns`, the orchestrator catches the result and produces a salvage output (last successful render or partial) rather than failing.

### 3.7 Observability

Hooks log every tool call to `logs/tool_calls.jsonl` and every `ResultMessage.usage` (deduped by `message_id`) to `logs/usage.jsonl`. Both files are append-only; the harness never edits or rewrites them.

### 3.8 Checkpointing

After each major phase completion (manifest, assets-done, masks-done, camera-done, render-done, qa-pass-N), write `checkpoints/state.json`. On startup, read the checkpoint and skip completed phases. **Do not rely on the SDK's session resume across machines** — Anthropic's docs warn against it for production.

---

## 4. Skill Packaging

### 4.1 Directory layout

```
parallax-skill/                  # the Anthropic Skill wrapper directory
├── SKILL.md
└── scripts/
    └── run.sh                   # thin shim: invoke parallax-engine CLI
```

The `parallax_engine` Python package lives separately at the repo root and is installed (via `pip install .` or `uvx`) on the user's machine before the Skill is registered.

### 4.2 SKILL.md (verbatim template)

```markdown
---
name: parallax-video
description: |
  Generate 2.5D multiplane camera animations as MP4 from a written brief.
  Use whenever the user asks for a parallax animation, layered 2D scene
  video, drone-FPV-through-illustration flythrough, biome-reveal explainer,
  After-Effects-style 2.5D parallax video, masked layer transition, or
  portal transition between two illustrated worlds. Even when the user
  doesn't say "parallax" — invoke this skill for any request that
  describes stacked illustrated layers with a moving camera, a multiplane-
  camera-like flythrough, or a transition that reveals one scene through
  a shape cut out of another.
---

# Parallax Video Skill

Generates an MP4 from a written brief. The skill invokes a multi-agent
harness that designs the scene, generates SVG assets, plans the camera
path, renders deterministically, and runs a QA loop.

## Usage

1. If the user has not yet provided one, ask for a brief covering: target
   duration, visual style references (forest, city, surreal, etc.), the
   kind of motion (drone-FPV, cinematic pan, biome reveals, portal),
   and any narrative beats. Write the brief to `workspace/brief.md`.
2. Invoke the harness:
   ```bash
   bash scripts/run.sh ./workspace
   ```
3. Stream progress to the user. The harness prints one line per phase.
4. The final MP4 is at `workspace/out.mp4`.

## Notes

- The harness spawns its own multi-agent system; do not delegate to other
  Anthropic features (Research, etc.) for this work.
- Default budget cap is $2.50/render. Override with
  `bash scripts/run.sh ./workspace --budget 5.00`.
- If the harness fails three QA passes, it emits the best partial result
  and a list of residual issues. Surface those to the user verbatim.

## Examples

- "Make a 10-second drone flythrough of a redwood forest at golden hour"
- "Create a 2.5D explainer that travels through 4 biomes (mountain, river,
  city, desert)"
- "Show a portal in a tree opening into a neon city"
```

### 4.3 scripts/run.sh (verbatim)

```bash
#!/usr/bin/env bash
set -euo pipefail
WORKSPACE="${1:-./workspace}"
shift || true
python -m parallax_engine.cli --workspace "$WORKSPACE" "$@"
```

### 4.4 The Skill ↔ Harness boundary

> The Skill is a thin shim over the harness, the way Anthropic's Research feature isn't a Skill — it's a harness exposed via the claude.ai product surface.

`SKILL.md` only orients Claude to invoke the Bash command. It does **not** carry multi-agent prompts, scene schema details, the renderer, or SVG generation logic. Those live in the Python package, where they are testable, versioned, and deterministic.

---

## 5. Stack and Licensing

### 5.1 Pinned dependencies

| Package | Version | License | Role |
|---|---|---|---|
| `claude-agent-sdk` | latest stable | MIT | Multi-agent harness |
| `Pillow` | 10.x | HPND (MIT-style) | PNG I/O, layer compositing primitives |
| `skia-python` | 87.x | BSD (binding); BSD (Skia) | SVG rasterization, advanced 2D blends |
| `numpy` | 1.26+ | BSD | All projection/composite math |
| `scipy` | 1.11+ | BSD | `gaussian_filter`, `ndimage` |
| `opencv-python-headless` | 4.x | Apache 2.0 | `resize`, `remap` (lens distortion), Lanczos |
| `pyyaml` | 6.x | MIT | Scene file parsing |
| `pydantic` | 2.x | MIT | Schema validation |
| FFmpeg (LGPL) | 6.x | LGPL-2.1+ | Encoder |
| `libopenh264` (Cisco) | 2.4+ | BSD-2-Clause + Cisco binary license | H.264 codec, royalty-paid by Cisco |
| `noise` or vendored simplex | latest | MIT | Deterministic Perlin/simplex noise |

**Forbidden dependencies** (license-incompatible with commercial resale):
- `python-lottie` (AGPL-3.0+) — use `lottie-web` in headless Chrome instead, or `rlottie` (LGPL, dynamically linked).
- Any library with a non-OSI license.
- Any library that statically links GPL code.

### 5.2 FFmpeg + H.264 commercial recipe

This is the most-tripped landmine in commercial video products. The recipe:

1. Ship an **LGPL-only build of FFmpeg** (no `--enable-gpl`, no x264, no x265, no xvid, no GPL filters).
2. Pull `libopenh264` as Cisco's precompiled binary at install time (via `pip install` on `ciscobinary.openh264.org`).
3. Encode with `-c:v libopenh264`. Cisco pays MPEG-LA royalties for users of the precompiled binary; downstream commercial users are covered.
4. LGPL compliance: dynamically link to FFmpeg, allow user replacement, distribute LGPL text and the standard "this software uses code of FFmpeg licensed under the LGPLv2.1" attribution.
5. Reproduce OpenH264's "OpenH264 Video Codec provided by Cisco Systems, Inc." in the EULA.

**Alternative codecs** (also LGPL-clean):
- `libaom-av1` (AOMedia license, royalty-free per AOMedia patent grant)
- `libvpx` (BSD, royalty-free)

H.264 stays the default for compatibility.

### 5.3 SVG library choice

`skia-python` over `cairosvg`/`pycairo`:

- `pycairo` is LGPL-2.1; safe via dynamic linking but adds compliance burden.
- `cairosvg` depends on `cairocffi` which is LGPL-3.0+.
- `skia-python` is BSD; cleaner license story for commercial resale.

If `skia-python`'s SVG support proves insufficient (it has known partial-spec gaps), fall back to invoking `rsvg-convert` as an external CLI — no linking, no derivative-work concerns.

### 5.4 Python version

3.10+ required (uses `match`/`case` and modern type hints). Tested on 3.10, 3.11, 3.12.

---

## 6. Worked Examples

### 6.1 Drone forest flight (Polyfjord-style on SVG plates)

```yaml
version: 1
meta:
  duration_s: 10
  fps: 30
  resolution: [1920, 1080]
  perspective_px: 1200
  origin: [960, 540]
  bg_color: "#000000"
  seed: 4242

stacks:
  forest:
    layers:
      - { id: sky,         src: assets/sky_dawn.svg,    scene_xyz: [0,0,-12000], plate_size: [3840,2160] }
      - { id: mountains,   src: assets/mountains.svg,   scene_xyz: [0,0,-9000],  plate_size: [3840,2160], post: { dof_blur_px: 5, depth_fade: 0.4 } }
      - { id: trees_far,   src: assets/trees_far.svg,   scene_xyz: [0,0,-6500],  plate_size: [3840,2160], post: { dof_blur_px: 3, depth_fade: 0.25 } }
      - { id: trees_mid,   src: assets/trees_mid.svg,   scene_xyz: [0,0,-4500],  plate_size: [3840,2160] }
      - { id: trees_near,  src: assets/trees_near.svg,  scene_xyz: [0,0,-2500],  plate_size: [3840,2160] }
      - { id: foreground,  src: assets/leaves_fg.svg,   scene_xyz: [0,0,-500],   plate_size: [3840,2160] }

camera:
  mode: drone
  drone:
    path:
      kind: bezier
      controls: [[0,0,0], [180,30,-3000], [-150,-25,-7000], [0,0,-11500]]
      duration_s: 10
    poi_lookahead_s: 0.55
    spring_halflife_s: 0.18
    noise: { z_amp: 22, xy_amp: 6, hz: 0.7 }
    bank_from_velocity: 0.40

masks: []

post:
  global:
    vignette: { strength: 0.5, radius: 0.85 }
    grain:    { sigma: 4 }
    fisheye:  { k1: 0.20, k2: 0.04 }
```

### 6.2 SVG parallax with 4 biome reveals

```yaml
version: 1
meta:
  duration_s: 24
  fps: 30
  resolution: [1920, 1080]
  perspective_px: 1200
  origin: [960, 540]
  seed: 7

stacks:
  mountains:
    layers:
      - { id: bg, src: assets/m_bg.svg, scene_xyz: [0,0,-7000], plate_size: [3840,2160] }
      - { id: mg, src: assets/m_mg.svg, scene_xyz: [0,0,-4000], plate_size: [3840,2160] }
      - { id: fg, src: assets/m_fg.svg, scene_xyz: [0,0,-1500], plate_size: [3840,2160] }
  river:
    layers:
      - { id: bg, src: assets/r_bg.svg, scene_xyz: [0,0,-7000], plate_size: [3840,2160] }
      - { id: mg, src: assets/r_mg.svg, scene_xyz: [0,0,-4000], plate_size: [3840,2160] }
  lanterns:
    layers:
      - { id: bg, src: assets/l_bg.svg, scene_xyz: [0,0,-7000], plate_size: [3840,2160] }
  desert:
    layers:
      - { id: bg, src: assets/d_bg.svg, scene_xyz: [0,0,-7000], plate_size: [3840,2160] }
  night:
    layers:
      - { id: bg, src: assets/n_bg.svg, scene_xyz: [0,0,-7000], plate_size: [3840,2160] }

camera:
  mode: keyframed
  keyframed:
    - { t: 0,  x: 0,    y: 0, z: 0,     yaw: 0,    pitch: 0, roll: 0, ease: easeInOutCubic }
    - { t: 6,  x: 300,  y: 0, z: -1500, yaw: 0.10 }
    - { t: 12, x: -200, y: 0, z: -3000, yaw: -0.05 }
    - { t: 18, x: 200,  y: 0, z: -4500, yaw: 0.05 }
    - { t: 24, x: 0,    y: 0, z: -6000, yaw: 0 }

masks:
  - { id: w1, src_stack: mountains, dest_stack: river,    anchor: screen, growth: { kind: radius,    t0: 5.0,  t1: 6.0,  r0: 0, r1: 1400, feather_px: 60 },  matte: alpha }
  - { id: w2, src_stack: river,     dest_stack: lanterns, anchor: screen, growth: { kind: radius,    t0: 11.0, t1: 12.0, r0: 0, r1: 1400, feather_px: 200 }, matte: alpha }
  - { id: w3, src_stack: lanterns,  dest_stack: desert,   anchor: screen, growth: { kind: gradient,  t0: 17,   t1: 18,   axis: x }, matte: luminance }
  - { id: w4, src_stack: desert,    dest_stack: night,    anchor: screen, growth: { kind: displaced_edge, t0: 22, t1: 23, displacement_map: assets/disp.png, amp: 80 }, matte: alpha }

post:
  global:
    vignette: { strength: 0.3 }
    grain:    { sigma: 2 }
```

### 6.3 The portal (forest → city)

```yaml
version: 1
meta:
  duration_s: 8
  fps: 30
  resolution: [1920, 1080]
  perspective_px: 1200
  origin: [960, 540]
  seed: 1

stacks:
  forest:
    layers:
      - { id: sky,         src: assets/sky.svg,         scene_xyz: [0,0,-10500], plate_size: [3840,2160] }
      - { id: trees_far,   src: assets/trees_far.svg,   scene_xyz: [0,0,-8500],  plate_size: [3840,2160] }
      - { id: trees_mid,   src: assets/trees_mid.svg,   scene_xyz: [0,0,-6500],  plate_size: [3840,2160] }
      - { id: portal_tree, src: assets/portal_tree.svg, scene_xyz: [0,0,-4500],  plate_size: [3840,2160], svg_paint_id: silhouette }
      - { id: leaves_mid,  src: assets/leaves_mid.svg,  scene_xyz: [0,0,-2500],  plate_size: [3840,2160] }
      - { id: leaves_fg,   src: assets/leaves_fg.svg,   scene_xyz: [0,0,-500],   plate_size: [3840,2160] }
  city:
    layers:
      - { id: city_sky,  src: assets/city_sky.svg,  scene_xyz: [0,0,-10500], plate_size: [3840,2160] }
      - { id: city_far,  src: assets/city_far.svg,  scene_xyz: [0,0,-7000],  plate_size: [3840,2160] }
      - { id: city_near, src: assets/city_near.svg, scene_xyz: [0,0,-3500],  plate_size: [3840,2160] }

camera:
  mode: drone
  drone:
    path:
      kind: bezier
      controls: [[0,0,0],[40,10,-2000],[-30,-10,-3500],[0,0,-4400]]
      duration_s: 8
    poi_lookahead_s: 0.5
    spring_halflife_s: 0.20
    noise: { z_amp: 18, xy_amp: 5, hz: 0.6 }
    bank_from_velocity: 0.30

masks:
  - id: portal
    path_svg: assets/portal_tree.svg
    silhouette_id_in_svg: silhouette
    path_id_in_svg: hole
    attached_to_layer: forest.portal_tree
    anchor: world
    src_stack: forest
    dest_stack: city
    matte: alpha
    growth: { kind: perspective }

post:
  global:
    vignette: { strength: 0.45 }
    grain:    { sigma: 3 }
    fisheye:  { k1: 0.18 }
```

### 6.4 The portal SVG (silhouette + hole, single file)

```svg
<svg viewBox="0 0 3840 2160" xmlns="http://www.w3.org/2000/svg">
  <path id="silhouette" fill="#0a1f12"
        d="M1820,2160 L1820,1100 C1500,880 1380,520 1920,260
           C2460,520 2340,880 2020,1100 L2020,2160 Z"/>
  <path id="hole" fill="#ffffff"
        d="M1880,1280 C1760,1180 1760,1020 1920,940
           C2080,1020 2080,1180 1960,1280 Z"/>
</svg>
```

The silhouette and the hole share `viewBox="0 0 3840 2160"`. They cannot drift.

---

## 7. Determinism Contract

A scene produces a byte-identical MP4 if and only if:

1. Same `meta.seed`.
2. Same renderer commit.
3. Same FFmpeg version (pinned via wheel).
4. Same `libopenh264` version (pinned via Cisco binary).
5. Same Python interpreter major/minor version.
6. Same NumPy version (pinned).
7. `-threads 1` on the FFmpeg encode.

**What this guarantees:**

- `tests/test_render_golden.py` can compare a render against a known-good MP4 byte-for-byte.
- A customer support ticket with a `scene.yaml` and a `meta.seed` is fully reproducible.
- Regression tests are reliable.

**What breaks determinism:**

- Multi-threaded FFmpeg encoding (different bitstream).
- Multi-process frame rendering without per-process seeded RNGs.
- Wall-clock-derived randomness anywhere.
- Floating-point math reduction order changes (e.g., `numpy.sum` over multiple axes in different orders).
- GPU-accelerated paths (non-deterministic at IEEE-754 level).

The renderer is single-threaded in the encode stage by default. Per-frame rendering can be parallelized via `multiprocessing` only if every worker derives its RNG from `SeedSequence(scene.meta.seed).spawn(frame_index)`.

**Cache key derivation.** The SVG raster cache is keyed on `(svg_bytes_sha256, target_size_xy, color_management_version)`. A renderer change that touches color management bumps the version constant, invalidating caches.

---

## 8. Build Phase Plan

Six phases. Each ends in something runnable. Each phase is one Claude Code session. Commit between phases.

### Phase 1 — Project scaffold and renderer core

**Goal.** Render `scene.yaml` → `out.mp4` deterministically, with hand-authored test scenes. No agents.

**Deliverables:**
- `pyproject.toml` with pinned deps from §5.1.
- `parallax_engine/projection.py` — implements `project_points` from §2.1.
- `parallax_engine/camera.py` — `drone_camera_track` and `kf_camera_track` from §2.3.
- `parallax_engine/masks.py` — `build_mask_alpha` and `composite_with_mask` from §2.4.
- `parallax_engine/render.py` — the per-frame loop from §2.5.
- `parallax_engine/encode.py` — FFmpeg wrapper from §2.8.
- `parallax_engine/scene.py` — Pydantic models + YAML loader from §2.2.
- `parallax_engine/cli.py` — argparse entry point with `render` subcommand.
- `tests/scenes/forest.yaml` — minimal forest scene with checked-in placeholder SVGs.
- `tests/scenes/portal.yaml` — minimal portal scene (matches §6.3).
- `tests/test_projection.py` — unit tests verifying CSS-equivalence with hand-computed expected values.
- `tests/test_camera.py` — unit tests for the spring integrator and Bezier evaluator.
- `tests/test_render_smoke.py` — `python -m parallax_engine render tests/scenes/forest.yaml --out /tmp/test.mp4` succeeds.

**Stop criteria:**
- `pytest` passes.
- `python -m parallax_engine render tests/scenes/forest.yaml --out test.mp4` produces a valid MP4.
- `python -m parallax_engine render tests/scenes/portal.yaml --out test.mp4` produces a valid MP4.

**Risks:** Phase 1 is the riskiest technically. Get it bulletproof before moving on. Specifically: validate the projection math against hand-computed expected values; validate the spring integrator against a known-good trajectory; validate determinism by running the same scene twice and `cmp`-ing the outputs.

### Phase 2 — Port the portal as regression test

**Goal.** Prove the engine reproduces the user's existing CSS-3D portal.

**Deliverables:**
- `tests/scenes/portal_regression.yaml` — translation of `SceneFPVForestToCity.tsx` to YAML using §6.3 as the template.
- The SVGs from the user's prior repo, cleaned up so silhouette + hole share one SVG file (per §2.4).
- A side-by-side comparison HTML or video proving visual equivalence to within JPEG-difference tolerance.
- `tests/test_render_golden.py` — byte-identical regression test for `portal_regression.yaml`.

**Stop criteria:**
- The portal renders.
- It looks indistinguishable from the user's prior Remotion output to a casual eye.
- The golden-render test passes deterministically.

### Phase 3 — Agent harness skeleton

**Goal.** The orchestrator can call subagents; subagents can write to the workspace; the SDK plumbing works. No real intelligence yet.

**Deliverables:**
- `parallax_engine/lead.py` — `ParallaxLead` orchestrator using `ClaudeSDKClient`.
- `parallax_engine/subagents.py` — `AgentDefinition`s for all five subagents (with stub prompts).
- `parallax_engine/auth.py` — credential resolver from §3.5.
- `parallax_engine/observability.py` — pre/post tool hooks logging to JSONL.
- `parallax_engine/state.py` — checkpoint read/write.
- `parallax_engine/tools/render.py` — `@tool render_scene` wrapping the Phase 1 renderer.
- `parallax_engine/tools/qa.py` — `@tool diff_frames` and `@tool ssim_score`.
- `parallax_engine/cli.py` — extended with `harness` subcommand.
- A "smoke test" run that takes a trivial brief, writes a hand-coded scene.yaml verbatim, renders, and exits. No real subagent intelligence yet — they return canned strings.

**Stop criteria:**
- `python -m parallax_engine harness --workspace ./tmp --brief "test"` runs end-to-end without errors.
- All five subagents are invoked, log their tool calls, and write their checkpoint markers.
- `tool_calls.jsonl` and `usage.jsonl` are populated.

### Phase 4 — Subagent prompts and asset generation

**Goal.** Real subagents that produce real output. This is where the prompts get tuned iteratively.

**Deliverables:**
- `parallax_engine/prompts/lead.md` — full lead orchestrator prompt with effort-scaling rules from §3.4.
- `parallax_engine/prompts/scene_designer.md` — produces `scene.yaml` from `brief.md`.
- `parallax_engine/prompts/asset_generator.md` — produces one SVG layer at a time.
- `parallax_engine/prompts/mask_author.md` — adds silhouette + hole paths to an existing SVG.
- `parallax_engine/prompts/camera_pather.md` — produces the `camera:` block in `scene.yaml`.
- `parallax_engine/prompts/qa_critic.md` — reviews rendered frames and reports.
- Image generation integration (Imagen/SDXL/whatever): `mcp__parallax_render__gen_image` tool.
- Auto-segmentation tool: `mcp__parallax_masks__autosegment` (rembg or similar).
- `tests/test_harness_e2e.py` — runs three end-to-end briefs (forest, biomes, portal) and validates outputs.

**Stop criteria:**
- All three reference behaviors (forest flythrough, biome reveals, portal) can be produced from natural-language briefs without manual intervention.
- QA loop bounded at 3 passes.
- Costs stay under $2.50/render on average.

### Phase 5 — Skill packaging

**Goal.** The Skill is installable and Claude recognizes the trigger.

**Deliverables:**
- `skill/SKILL.md` per §4.2.
- `skill/scripts/run.sh` per §4.3.
- Installation instructions in `README.md`.
- A test invocation from a Claude Code session that has the skill installed.

**Stop criteria:**
- The Skill triggers correctly on the example prompts in §4.2.
- An end-to-end run from a Claude Code session produces an MP4.

### Phase 6 — Polish and commercial readiness

**Goal.** Ship-ready.

**Deliverables:**
- LUT support (color grading).
- Light leaks library (sample sprites).
- Fisheye/lens distortion polished.
- Vignette, grain, motion blur tuned.
- License audit document with every dependency listed.
- EULA template.
- Distribution: `pip install parallax-engine` works.
- README with installation, examples, troubleshooting.
- A demo reel of 3–5 produced videos.

**Stop criteria:**
- License audit signed off.
- Installation tested on macOS, Linux, Windows.
- Demo reel exists.
- README is complete.

---

## 9. Cross-Cutting Invariants

These hold across every phase. Violations are bugs regardless of which file they live in.

### 9.1 Premultiplied alpha everywhere

All RGBA buffers used in compositing are premultiplied `float32`. Straight alpha exists only at file I/O boundaries (PNG decode, SVG raster output → premultiply immediately). The `over` operator is the premultiplied form in §2.5.

### 9.2 One projection function

`project_points` in `projection.py` is the only place perspective math lives. Layers, masks, sub-objects, debug overlays — everything routes through it.

### 9.3 One randomness source

`scene.meta.seed` is the only seed authored into the scene file. Every other RNG (noise, grain, light leaks, asset generation) derives from it via `numpy.random.SeedSequence.spawn(channel_id)`. Channel IDs are stable integers documented in `parallax_engine/seeds.py`.

### 9.4 Same SVG for silhouette and hole

When a layer's SVG is also a mask source, the silhouette path and the hole path live in the same `<svg viewBox>`. Two separate SVG files for the same logical object are forbidden.

### 9.5 Workspace as bus

Subagents communicate via files in `workspace/`, not by passing payloads through the lead's context. Returns are short status strings + paths.

### 9.6 No subagent re-delegation

Subagents never have the `Agent` tool. Only the lead delegates.

### 9.7 Counters live in Python, not in prompts

QA pass count, retry count, budget — all enforced by Python integers in the orchestrator. Prompts contain hints; orchestrator code is the guarantee.

### 9.8 Cache invalidation is explicit

The SVG raster cache version constant lives in `parallax_engine/render.py`. Any change to color management, premultiplication, or rasterization parameters bumps it.

### 9.9 LGPL FFmpeg only

Build configuration permits `--enable-shared` and excludes `--enable-gpl`. CI fails if a GPL FFmpeg is detected. Library load asserts FFmpeg is LGPL at startup.

### 9.10 Schema version is sacred

Every `scene.yaml` includes `version: 1`. Loader rejects mismatched versions. Version bumps require a migration in `parallax_engine/migrations/`.

---

## 10. Glossary

| Term | Definition |
|---|---|
| **Plate** | A flat 2D layer (SVG or PNG) at a fixed scene-space `(x, y, z)`, billboarded to the camera. |
| **Stack** | A named collection of plates rendered together; e.g., "forest", "city", "mountains". |
| **Mask** | A path that splits two stacks into a composite via `F = D·M + S·(1−M)`. |
| **Anchor** | How a mask path moves with the camera: `world` (projected through perspective), `screen` (fixed in pixels), `layer-plane` (in a specific layer's local 2D space). |
| **Growth** | How the mask animates: `perspective` (grows from camera approach), `radius(t)` (animated radius), `gradient` (axis sweep), `displaced_edge` (organic noise edge), `matte_seq` (per-frame matte sprites). |
| **Near-cull** | Per-layer alpha fade as `z_cam` approaches `perspective_px`. The layer dissolves before the projection math goes singular. |
| **Near-cull handoff** | The portal-passable behavior: when the mask's anchor layer near-culls, the mask flips to fully open and the destination stack takes the full frame. |
| **In-front-of-mask rule** | After applying a mask, all source-stack layers nearer than the mask layer are re-rendered unmasked on top, so close foreground correctly occludes the mask seam. |
| **POI lookahead** | The drone camera's look-target advances along the Bezier path by `poi_lookahead_s` seconds, so the camera looks where it's heading. |
| **Spring halflife** | The `h` parameter in the critically-damped spring; smaller = snappier camera, larger = laggier. |
| **Bank from velocity** | `roll = -k * tanh(lateral_velocity / 200)`; the camera leans into turns proportionally to its sideways speed. |
| **Lead** | The orchestrator agent in the harness. Delegates, integrates, runs the QA loop. |
| **Workspace** | The directory where the harness writes all artifacts: `brief.md`, `scene.yaml`, `assets/`, `frames/`, `out.mp4`, `logs/`, `checkpoints/`. |
| **Determinism contract** | The seven conditions in §7 under which the same `scene.yaml` produces a byte-identical MP4. |

---

<!-- BEGIN SECTION 11 -->
## Section 11 — The Director Tier: Storyboard-Driven Creative Orchestration

> **Status:** Canonical specification. This section supersedes any prior documentation that describes `scene-designer` as a one-shot agent. Read in conjunction with §2 (renderer / unified engine), §6 (worked scene examples), §3 (legacy harness — partially superseded), and §4 (Skill packaging).
>
> **Audience:** Claude Code, when implementing the director tier; human reviewers, when evaluating creative output; downstream agents, when consuming `storyboard.yaml` and `casting.yaml`.

---

## 11.0 Why this tier exists

The v0 harness in §3 conflates two jobs that professional animation studios have kept separate for almost a century: **deciding what the film should be** and **building the shot.** A brief like *"a 30-second video about loneliness in a city"* requires creative decisions a single one-shot LLM call cannot make well — the dramatic arc, the per-scene emotional function, the visual vocabulary across the whole piece, which transitions belong where (a portal mask reveal, a biome wipe, and a hard cut have completely different emotional implications), recurring motifs and casting across scenes, and continuity rules that only make sense globally.

The animation industry has solved this through a strict pre‑production pipeline: **treatment → script → storyboard → animatic → layout → animation.** Pixar formalises it further with the **color script** — a sequence of small painted thumbnails that map the emotional and chromatic arc of the entire film before a single shot is rendered. Brad Bird's Simpsons-era directives are a useful North Star: *"We can't spend a lot of money on elaborate animation, but we can have sophisticated filmmaking."* The same principle applies here. We can't spend Sora‑level compute per second, but we can have sophisticated direction.

State‑of‑the‑art AI video systems in late 2025 / early 2026 — Sora 2 Pro Storyboard, Runway Gen‑4.5 Director Mode, Veo 3.1 Scenebuilder, Pika 2.5 Pikascenes, Kling 3.0 Multi‑Shot — have all converged on the same insight: **a per-shot prompt is not enough; the model needs an upstream document that pins palette, characters, props, lighting and pacing across shots.** Sora's "storyboard cards with reference images," Runway's "single-image identity anchor," Veo's "ingredients to video," and Pika's "scene ingredients" are all different syntaxes for the same idea — a casting bible that survives between shots. Academic work has codified this further: VideoDirectorGPT (2023), Anim‑Director (2024), MovieAgent (2025), FilmAgent (2025) and UniMAGE (2025) all implement an explicit *director* tier whose only job is to plan, never to render.

`parallax-engine` adopts this pattern. The director tier sits **above** the existing implementation harness, produces a `storyboard.yaml` artifact, never touches pixels, and never spawns subagents. Every downstream agent reads the same storyboard. Casting is a file, not a prompt.

The rest of this section specifies that tier exhaustively.

---

## 11.1 Three‑tier topology and the project manager's narrowed role

```
┌──────────────────────────────────────────────────────────────────────┐
│                    PROJECT MANAGER (lead orchestrator)               │
│  - Coordinates between tiers; never makes creative decisions         │
│  - Owns budget counters, retry counters, workspace I/O               │
│  - Python invariants enforced outside the prompt                     │
└──────────────────────────────────────────────────────────────────────┘
                                  │
            ┌─────────────────────┼─────────────────────┐
            ▼                     ▼                     ▼
   ┌────────────────┐    ┌────────────────┐    ┌──────────────────┐
   │  TIER 1        │    │  TIER 2        │    │  TIER 3          │
   │  DIRECTOR      │ →  │  SCENE         │ →  │  IMPLEMENTATION  │
   │  (creative)    │    │  DESIGNERS     │    │  (mechanical)    │
   │                │    │  (translator)  │    │                  │
   │  Sequential,   │    │  Sequential,   │    │  Parallel waves  │
   │  one Opus call │    │  scene-by-     │    │  per scene:      │
   │  (or chain of  │    │  scene, reads  │    │  asset-generator │
   │  4 sub-calls   │    │  storyboard +  │    │  mask-author     │
   │  for >60 s)    │    │  prior scenes' │    │  camera-pather   │
   │                │    │  fragments.    │    │  qa-critic       │
   │  Writes:       │    │                │    │                  │
   │  storyboard.yaml│    │  Writes:      │    │  Reads/writes:   │
   │  casting.yaml  │    │  scenes/NN.yaml│    │  assets/*.svg    │
   │                │    │  Updates       │    │  frames/*.png    │
   │                │    │  casting.yaml  │    │  out.mp4         │
   └────────────────┘    └────────────────┘    └──────────────────┘
            ▲                     ▲                     ▲
            │                     │                     │
            └────── shared workspace (filesystem blackboard) ─────────┘
```

**Why this shape and not a flat orchestrator‑worker.** Anthropic's published orchestrator‑worker pattern (the engine behind Claude's research feature) is a one‑tier hierarchy: a lead spawns parallel workers and synthesises. That works for breadth‑first information gathering where subtasks are independent. It fails for video direction because **scene 4 depends on scene 3's casting decisions, which depend on the storyboard's palette progression, which depends on the brief's emotional arc.** These are sequential dependencies that fan out into parallel implementation only at the leaves. The right pattern is therefore **sequential‑then‑parallel**, sometimes called the *pipeline of agents* in LangGraph, or *sequential process feeding hierarchical sub‑process* in CrewAI. The director and scene‑designers are sequential; only the implementation tier fans out.

**Why the project manager is not the director.** Mixing coordination (deterministic, budget‑bound, retries, file I/O) with creative judgement (open‑ended, expensive, irreducibly subjective) is the canonical anti‑pattern that ruins multi‑agent systems. Anthropic's own cookbook is explicit: *"Consider using Claude Opus for the orchestrator and Claude Haiku for workers to optimise cost vs. quality."* That formulation only works when the orchestrator's job is genuinely orchestration. In our case the orchestrator must *also* invent a dramatic arc — which is the most expensive single decision in the whole pipeline. We therefore split the role: the **project manager** stays cheap and deterministic; the **director** is a separate, expensive, creative agent that the project manager invokes exactly once (or once per re‑run) and never confuses with anything else.

**The filesystem as a blackboard.** The classic blackboard architecture (Erman 1980; Hayes‑Roth 1985; recently re‑validated for LLM multi‑agent systems by Han & Zhang 2025) maps naturally onto our existing filesystem‑based communication. `workspace/` *is* the blackboard. Every agent reads what it needs and writes its contribution. The project manager is the control unit that selects which agent runs next. We do not introduce a new IPC mechanism; we keep the existing one and formalise its semantics.

---

## 11.2 Workspace layout (revised)

```
workspace/
├── brief.md                  # user-supplied free-text brief (input)
├── references/               # optional user-supplied mood-board images (input)
│   ├── ref_01.png
│   └── ref_02.jpg
├── storyboard.yaml           # ★ NEW: director's creative output (Tier 1)
├── casting.yaml              # ★ NEW: casting bible (written by director,
│                             #         appended to by scene-designers as
│                             #         canonical SVGs are produced)
├── scenes/                   # ★ NEW: per-scene fragments (Tier 2)
│   ├── scene_01.yaml
│   ├── scene_02.yaml
│   └── …
├── scene.yaml                # final merged scene.yaml (Tier 2 output, fed to renderer)
├── assets/                   # SVGs (Tier 3)
│   ├── canon/                #   canonical casting SVGs (referenced by id)
│   │   ├── red_bird.svg
│   │   └── lonely_man.svg
│   └── scene_03/             #   per-scene non-canonical assets
│       └── streetlamp.svg
├── frames/                   # rendered PNGs (renderer output)
├── qa/                       # QA reports per pass, per tier
│   ├── pass_01_asset.json
│   ├── pass_01_scene.json
│   └── pass_01_storyboard.json
├── logs/                     # full agent transcripts, token counts, costs
└── out.mp4                   # final
```

The two new top‑level artifacts (`storyboard.yaml` and `casting.yaml`) are **first‑class**: a user can hand‑edit either and re‑run from that point. This is the single most important user‑experience win of adding the director tier — it turns the system from *"edit the brief, hope"* into *"edit the script, re‑render."*

---

## 11.3 The `storyboard.yaml` schema

This is the largest artefact in the section because it is the contract between Tier 1 and Tier 2. Every field is specified with type, validation, authoring agent, and translation rule into `scene.yaml`.

### 11.3.1 Top‑level structure

```yaml
storyboard_version: "1.0"           # string, required, semver of THIS schema
project:                            # mapping, required
  title: string                     # required, ≤ 80 chars
  logline: string                   # required, 1 sentence, ≤ 200 chars
  theme: string                     # required, 1–3 words ("loneliness", "wonder")
  summary: string                   # required, 1 paragraph, 60–400 words
  intended_platform: enum           # required: tiktok | reels | youtube_short |
                                    #          square_social | wide_landscape |
                                    #          custom
  aspect_ratio: enum                # required: "9:16" | "1:1" | "16:9" | "4:5" | "2.39:1"
  target_fps: int                   # required, 24 | 30 | 60
  total_duration_s: float           # required, 0 < x ≤ 300
  audience_note: string             # optional, free text
visual_vocabulary: { … }            # mapping, required, see §11.3.2
arc: { … }                          # mapping, required, see §11.3.3
casting: [ { … }, … ]               # list, required (may be empty), see §11.3.4
scenes: [ { … }, … ]                # list, required, ≥ 1, see §11.3.5
continuity: { … }                   # mapping, optional, see §11.3.6
audio_intent: { … }                 # mapping, optional, see §11.3.7
director_notes: string              # optional, free text, ≤ 2000 chars
```

**Authoring contract:** every field above is written by the **director**. Scene‑designers are read‑only with respect to `storyboard.yaml`; they may only *append* canonical SVG paths to `casting.yaml`. The QA critic is also read‑only with respect to `storyboard.yaml`; if it determines the storyboard itself is wrong, it raises a storyboard‑level failure (§11.9) and the project manager re‑invokes the director.

**Validation:** the schema is enforced by a Pydantic v2 model (`parallax_engine.director.schema.Storyboard`). Pydantic's YAML support via `pydantic-yaml` gives us round‑trip parsing, validation errors with line numbers, and JSON Schema export for prompt grounding. Validation runs (a) immediately after the director call, and (b) immediately before the first scene‑designer call. Any validation failure aborts the run and is logged with the malformed YAML for inspection.

### 11.3.2 `visual_vocabulary` — the visual grammar of the piece

This block is what Pixar would call the "color script in YAML form" plus the production designer's shape language and lighting bible. It is the single most important block for cross‑scene consistency.

```yaml
visual_vocabulary:
  palette:                          # required
    primary: ["#1a2238", "#9daaf2"] # 2–4 hex colors, dominant tones
    secondary: ["#ff6a3d"]          # 1–3 hex colors, accents
    neutrals: ["#f4db7d", "#000000"]# 1–4 hex colors, used for text/whitespace
    forbidden: ["#ff0000"]          # optional: colors that must NEVER appear
                                    #          (e.g. saving red for one motif)
  palette_progression: enum         # required: static | warming | cooling |
                                    #          desaturating | saturating |
                                    #          custom_described
  palette_progression_note: string  # required if "custom_described"
  shape_language: enum              # required: angular | rounded | organic |
                                    #          geometric | mixed
  shape_language_note: string       # optional, ≤ 200 chars
                                    # e.g. "circles for character, triangles
                                    #       for environment threats"
  density_curve: enum               # required: sparse | medium | dense |
                                    #          rising | falling | rising_then_falling
                                    # Controls how busy the frame is.
  lighting_mood: enum               # required: high_key | low_key | natural |
                                    #          neon | golden_hour | overcast |
                                    #          chiaroscuro | flat
  time_of_day: enum                 # required: dawn | morning | noon | afternoon |
                                    #          dusk | night | abstract
  weather: enum                     # optional: clear | overcast | rain | snow |
                                    #          fog | wind | none_specified
  look_references:                  # optional, list of named visual styles
    - name: string                  # e.g. "Studio Ghibli pastoral"
      note: string                  # 1 sentence
    - name: "Heygen explainer"
      note: "Bold flat shapes, clean parallax planes, high contrast"
  reference_image_uris:             # optional, paths under workspace/references/
    - "references/ref_01.png"       # may be consumed by director and surfaced
                                    # to scene-designers as URIs (NOT pixels)
```

**Translation rules:**

- `palette.primary[0]` becomes `scene.palette.background_base` for every scene unless the per‑scene `palette_override` is set.
- `palette_progression: warming` is a deterministic instruction to scene‑designers: scene N's palette is interpolated from `primary` toward warmer hues as `N / total_scenes` increases. The scene‑designer applies the interpolation; the director merely picks the policy.
- `shape_language` propagates as a hard constraint into the asset‑generator's prompts (Tier 3): every SVG produced for this project must obey it.
- `density_curve` determines per‑scene `layer_count` budgets in the bridge (§11.10).
- `lighting_mood` affects the renderer's atmospheric overlays (haze opacity, vignette strength, gradient direction). It does **not** affect SVG generation.
- `forbidden` colors are passed as a negative constraint to the asset‑generator; the QA critic spot‑checks rendered frames against them.
- `reference_image_uris` are passed by URI only. **The director never receives image bytes for references.** The scene‑designer never sees them either. They exist purely to be human‑readable annotations and (in a future v2) to be embedded by a vision‑capable casting agent.

### 11.3.3 `arc` — dramatic structure

```yaml
arc:
  structure: enum                   # required: three_act | save_the_cat |
                                    #          establish_disrupt_resolve |
                                    #          biome_tour | portal_reveal |
                                    #          custom
  structure_note: string            # required if "custom", ≤ 200 chars
  beats:                            # required, list of 2–8 beats
    - id: string                    # required, snake_case, unique
      label: string                 # required, ≤ 40 chars ("opening_image",
                                    #          "catalyst", "midpoint", "climax",
                                    #          "denouement")
      function: enum                # required: hook | establish | rising |
                                    #          turning_point | midpoint |
                                    #          escalation | climax | release |
                                    #          coda
      narrative_note: string        # required, 1 sentence, what this beat does
      target_time_s: float          # required, when this beat lands (seconds
                                    #          from start)
      target_scenes: [int]          # required, scene indices this beat covers
                                    #          (1‑based, inclusive)
```

**Why structure is an enum, not a free string.** Snyder's *Save the Cat* maps poorly to sub‑30‑second pieces — there isn't room for 15 beats. Short‑form video has its own grammar. We pick a small set of named structures that map cleanly to our renderer's three behaviours:

- `three_act` — generic, falls back to setup/confrontation/resolution; works for any duration ≥ 15 s.
- `save_the_cat` — only for ≥ 60 s; selectively uses Snyder's beats.
- `establish_disrupt_resolve` — the canonical short‑form arc, ~10 s minimum.
- `biome_tour` — designed for explainers: each scene is one biome, transitions are biome wipes; emotional arc is *curiosity → recognition*.
- `portal_reveal` — a single dramatic mask reveal in the middle, before/after pairing; emotional arc is *anticipation → reveal → consequence*.
- `custom` — escape hatch; the director must justify in `structure_note`.

Each beat carries a `function`, which is the canonical taxonomy used by the QA critic to decide whether a scene fulfilled its job. The function vocabulary is deliberately small (9 values) so it can be reliably reasoned about.

### 11.3.4 `casting` — recurring visual elements

This is the casting bible (§11.7). Each entry is a recurring motif, character, or prop that more than one scene references.

```yaml
casting:
  - id: string                      # required, snake_case, unique within project
                                    # e.g. "red_bird", "lonely_man", "lantern"
    kind: enum                      # required: character | prop | motif |
                                    #          environment_element
    canonical_description: string   # required, 2–4 sentences, fully self-
                                    #          contained, no references to
                                    #          other entries
    role_in_story: string           # required, 1 sentence
    palette_locked: { … }           # optional, see below
    allowed_variations:             # optional, list of strings
      - "may be lit warmly or coolly"
      - "may be partially obscured"
      - "may be near or distant"
    forbidden_changes:              # optional, list of strings
      - "color must always include #c84032"
      - "must always have wings closed"
    canonical_svg: path | null      # null at director time; filled by scene-
                                    # designer/asset-generator when first
                                    # produced. Path is "assets/canon/<id>.svg".
    first_appearance_scene: int     # required, 1‑based scene index
    appearance_evolution: string    # optional, 1 sentence
                                    # e.g. "starts small and dim, grows brighter"
  palette_locked:                   # mapping inside a casting entry
    body: "#c84032"                 # required colors that must appear
    accent: "#f4db7d"
    forbidden: ["#0000ff"]          # may not appear
```

**Authoring rules:**

- The director writes every casting entry and its `canonical_description`. The description must be **self-contained** so that asset‑generator can produce a faithful SVG without reading any other file.
- `canonical_svg` is **null at director time**. The first scene‑designer that needs the asset triggers asset‑generator to produce it; the resulting path is then written into `casting.yaml` and **all subsequent scenes reference it by id**, never by re‑describing.
- A casting entry must be referenced by ≥ 2 scenes. If it's a one‑off, it doesn't belong in casting; it belongs in that scene's local asset manifest.

### 11.3.5 `scenes` — the scene list

```yaml
scenes:
  - index: int                      # required, 1-based, dense, no gaps
    duration_s: float               # required, > 0
    logline: string                 # required, 1 sentence, ≤ 160 chars
    emotional_function: enum        # required: establishing | rising |
                                    #          escalation | reveal |
                                    #          turning | climax | release |
                                    #          denouement | coda | bridge
    camera_intent: enum             # required, see §11.3.5.1
    transition_in: { … }            # required, see §11.3.5.2
    transition_out: { … }           # required (omitted on last scene)
    must_feature: [string]          # optional, casting ids that must appear
    must_avoid: [string]            # optional, casting ids that must NOT appear
                                    #          (forces a clean slate)
    motif_features: [string]        # optional, list of non-cast motifs
                                    #          ("rain on glass", "long shadow")
    pacing: enum                    # required: slow_burn | steady |
                                    #          rising | rapid | held | breath
    palette_override:               # optional, mapping
      primary: ["#…"]
      secondary: ["#…"]
      reason: string                # required if override present
    callback_to_scene: int | null   # optional, 1-based, must be < this index
                                    # signals "this scene visually echoes scene N"
    director_notes: string          # optional, free text, ≤ 600 chars
```

#### 11.3.5.1 `camera_intent` enum

This is **abstract** — it does not specify drone path coordinates. The bridge converts it to concrete `scene.yaml` camera fields.

| Value | Meaning | Maps to renderer behaviour |
|---|---|---|
| `drone_fpv_forward` | Diving forward through depth | `camera.mode: drone`, push‑in path |
| `drone_fpv_orbit` | Circling a focal point | `camera.mode: drone`, arc path |
| `drone_fpv_pullout` | Retreating from intimate to vast | `camera.mode: drone`, pull‑out path |
| `cinematic_pan_left` / `cinematic_pan_right` | Lateral discovery | `camera.mode: parallax`, `pan` axis |
| `cinematic_truck_in` | Slow push‑in, parallax planes separate | `camera.mode: parallax`, `dolly` axis |
| `cinematic_pullback` | Reveal of context | `camera.mode: parallax`, reverse `dolly` |
| `static_held` | Locked frame, subject moves only | `camera.mode: parallax`, zero motion |
| `iris_in` / `iris_out` | Circular reveal/conceal | special pre/post layer in renderer |
| `match_cut_setup` | Composition designed to cut into next scene's `match_cut_resolve` | metadata only |
| `portal_reveal` | World‑anchored mask divides foreground/background stacks | `camera.mode: portal` |
| `biome_wipe_donor` | This scene is the source of an outgoing biome wipe | metadata; renderer composites |
| `biome_wipe_receiver` | This scene is the destination of an incoming biome wipe | metadata; renderer composites |

#### 11.3.5.2 `transition_in` / `transition_out`

```yaml
transition_in:                      # mapping
  type: enum                        # required: hard_cut | fade_from_black |
                                    #          fade_from_white | dissolve |
                                    #          biome_wipe | portal_reveal |
                                    #          iris_in | match_cut |
                                    #          j_cut | l_cut
  duration_s: float                 # required for non-cuts, 0.1 ≤ x ≤ 2.0
  emotional_intent: string          # optional, 1 phrase, ≤ 80 chars
                                    # e.g. "soft surrender", "violent rupture"
  paired_scene: int | null          # optional; for match_cut, biome_wipe,
                                    #          portal_reveal, the scene this
                                    #          transition is paired with
```

**Hard rules the director must obey:**

- `transition_out` of scene N must be compatible with `transition_in` of scene N+1. Specifically: if scene N's `transition_out.type` is `biome_wipe`, scene N+1's `transition_in.type` must also be `biome_wipe` and `paired_scene` must point to N. The renderer needs both endpoints to composite the wipe.
- `portal_reveal` is one transition consuming two scenes' worth of stacks; the receiving scene's `transition_in.type` is also `portal_reveal`, and `paired_scene` points to the donor.
- `match_cut` requires the donor's last frame and the receiver's first frame to share a compositional anchor; the bridge enforces this by passing the donor's anchor descriptor into the receiver's scene‑designer prompt.

### 11.3.6 `continuity` — global rules

```yaml
continuity:
  palette_progression_explicit:     # optional, overrides visual_vocabulary
    - scene_index: 1
      palette_shift: "cool, desaturated"
    - scene_index: 4
      palette_shift: "warm, saturated"
  motif_evolution:                  # optional
    - cast_id: "red_bird"
      stages:
        - scenes: [1, 2]
          state: "small, distant, dim"
        - scenes: [3]
          state: "close, vivid, central"
        - scenes: [4, 5]
          state: "absent, but its silhouette echoes in clouds"
  callbacks: [int]                  # optional, list of scene indices that
                                    #          should each have callback_to_scene
                                    #          set; sanity check
  hard_rules: [string]              # free-text constraints, all-caps preferred
                                    # e.g.
                                    # - "NEVER show the man's face directly"
                                    # - "RAIN must be present whenever red_bird is absent"
```

The QA critic uses `hard_rules` directly: each rule is restated as a yes/no check that the critic must answer per scene.

### 11.3.7 `audio_intent` — captured but not generated in v1

```yaml
audio_intent:
  music_arc: enum                   # optional: ambient | building | sparse |
                                    #          dense | silent | call_response
  per_scene:                        # optional
    - scene_index: 1
      mood: "subdued, single piano"
    - scene_index: 4
      mood: "swell, full strings"
  sfx_notes: [string]               # optional, free text
  vo_intent: enum                   # optional: none | sparse_narration |
                                    #          dialogue | textless
```

We do not synthesise audio in v1. We capture intent so that (a) downstream timing decisions can be made (a `held` scene with `swell` music probably wants longer duration), (b) future v2 work can hook in TTS / music gen without retrofitting the schema.

---

## 11.4 Three fully‑worked storyboard examples

### 11.4.1 Example A — 10‑second drone forest flythrough

```yaml
storyboard_version: "1.0"
project:
  title: "Through the Pines"
  logline: "A breathless forward dive through a sun-shafted pine forest at dawn."
  theme: "wonder"
  summary: >
    A single sustained drone-FPV push through a stylised pine forest. No characters,
    no narrative beats in the traditional sense — the dramatic interest is the
    progressive revelation of depth as parallax planes part to disclose a clearing
    of golden light. Starts dim and tight, ends bright and open. One emotional
    arc: anticipation resolving into awe.
  intended_platform: youtube_short
  aspect_ratio: "9:16"
  target_fps: 30
  total_duration_s: 10.0

visual_vocabulary:
  palette:
    primary: ["#0e1a14", "#1f3a2e", "#3a6b4f"]
    secondary: ["#f4c860", "#fff3c2"]
    neutrals: ["#0a0a0a"]
    forbidden: ["#ff0000", "#ff00ff"]
  palette_progression: warming
  shape_language: organic
  shape_language_note: "Soft asymmetric pine silhouettes; no hard geometry."
  density_curve: rising_then_falling
  lighting_mood: chiaroscuro
  time_of_day: dawn
  weather: fog
  look_references:
    - name: "Studio Ghibli forest sequences"
      note: "Layered foliage planes, god-rays, restrained color shifts."

arc:
  structure: establish_disrupt_resolve
  beats:
    - id: enter
      label: "entering darkness"
      function: hook
      narrative_note: "We push through dense, near-silhouette foliage."
      target_time_s: 0.0
      target_scenes: [1]
    - id: depth
      label: "discovering depth"
      function: rising
      narrative_note: "Parallax planes part; light begins to filter through."
      target_time_s: 4.0
      target_scenes: [1]
    - id: release
      label: "the clearing"
      function: release
      narrative_note: "Forest opens into a sun-flooded clearing."
      target_time_s: 8.0
      target_scenes: [1]

casting: []        # no recurring characters; single sustained shot

scenes:
  - index: 1
    duration_s: 10.0
    logline: "Drone-FPV forward push through pine forest from gloom to clearing."
    emotional_function: establishing
    camera_intent: drone_fpv_forward
    transition_in:
      type: fade_from_black
      duration_s: 0.5
      emotional_intent: "gentle awakening"
    pacing: rising
    motif_features: ["god-rays", "drifting fog", "parted branches"]
    director_notes: >
      This is one continuous shot; do not subdivide. Layer count should peak
      around mid-flight (≥ 9 planes) and reduce to ≤ 4 at the clearing.

continuity:
  hard_rules:
    - "NO character or animal appears."
    - "THE final frame must be at least 60% covered by warm light pixels."

audio_intent:
  music_arc: building
  per_scene:
    - scene_index: 1
      mood: "low drone rising into airy strings"
  vo_intent: none
```

### 11.4.2 Example B — 24‑second 4‑biome explainer

```yaml
storyboard_version: "1.0"
project:
  title: "Four Climates"
  logline: "A small bird flies through four biomes — desert, forest, tundra, ocean — and each transition is a wipe."
  theme: "interconnection"
  summary: >
    A 24-second educational vignette. Four 5-second scenes, each in a distinct
    biome, joined by 1-second biome wipes. A single recurring character — a
    small red bird — flies through all four. The piece's argument is visual:
    the same creature persists through wildly different worlds. Cool, calm,
    pedagogical tone.
  intended_platform: tiktok
  aspect_ratio: "9:16"
  target_fps: 30
  total_duration_s: 24.0

visual_vocabulary:
  palette:
    primary: ["#e8d8a8", "#3a6b4f", "#cfe6f0", "#1a3a5a"]
    secondary: ["#c84032"]                 # reserved for the bird
    neutrals: ["#0a0a0a", "#fafafa"]
    forbidden: []
  palette_progression: custom_described
  palette_progression_note: >
    Each scene has its own dominant primary; the bird's #c84032 persists
    unchanged across all four for visual anchoring.
  shape_language: rounded
  shape_language_note: "Friendly, non-threatening; geometric primitives ok for terrain, organic for living things."
  density_curve: steady
  lighting_mood: natural
  time_of_day: noon
  weather: clear
  look_references:
    - name: "Heygen explainer"
      note: "Bold flat shapes, clean parallax planes, high contrast."

arc:
  structure: biome_tour
  beats:
    - id: introduce
      label: "desert"
      function: establish
      narrative_note: "The bird enters frame in arid landscape."
      target_time_s: 0.0
      target_scenes: [1]
    - id: green
      label: "forest"
      function: rising
      narrative_note: "Wipe to lush forest; same bird, new context."
      target_time_s: 6.0
      target_scenes: [2]
    - id: cold
      label: "tundra"
      function: turning
      narrative_note: "Wipe to icy tundra; bird is small against vastness."
      target_time_s: 12.0
      target_scenes: [3]
    - id: blue
      label: "ocean"
      function: release
      narrative_note: "Wipe to ocean; bird is reflected in water and ascends."
      target_time_s: 18.0
      target_scenes: [4]

casting:
  - id: red_bird
    kind: character
    canonical_description: >
      A small songbird, simplified to two overlapping rounded shapes for body
      and head. Solid #c84032 fill across the entire silhouette. Black dot eye.
      Wing rendered as two short curved strokes on the side. No beak detail,
      no feet. Designed for clean visibility against any biome background.
    role_in_story: "The thread that connects all four biomes."
    palette_locked:
      body: "#c84032"
      accent: "#000000"
      forbidden: ["#0000ff", "#00ff00"]
    allowed_variations:
      - "may be larger or smaller in frame"
      - "may be reflected in water (scene 4)"
    forbidden_changes:
      - "color must always be exactly #c84032; no shading, no gradients"
      - "must always be a solid silhouette; never an outline"
    canonical_svg: null
    first_appearance_scene: 1
    appearance_evolution: "starts mid-frame in scene 1; grows progressively smaller in scenes 2–3; grows large again in scene 4 ascent"

scenes:
  - index: 1
    duration_s: 5.0
    logline: "Red bird crosses a sand-dune horizon."
    emotional_function: establishing
    camera_intent: cinematic_pan_right
    transition_in:
      type: fade_from_white
      duration_s: 0.5
      emotional_intent: "bright, neutral arrival"
    transition_out:
      type: biome_wipe
      duration_s: 1.0
      emotional_intent: "geographic transit"
      paired_scene: 2
    must_feature: ["red_bird"]
    pacing: steady
    motif_features: ["heat shimmer", "long shadow"]

  - index: 2
    duration_s: 5.0
    logline: "Red bird threads between forest canopies."
    emotional_function: rising
    camera_intent: cinematic_pan_right
    transition_in:
      type: biome_wipe
      duration_s: 1.0
      paired_scene: 1
    transition_out:
      type: biome_wipe
      duration_s: 1.0
      paired_scene: 3
    must_feature: ["red_bird"]
    pacing: steady
    motif_features: ["dappled light", "leaf flutter"]

  - index: 3
    duration_s: 5.0
    logline: "Red bird, tiny, against tundra vastness."
    emotional_function: turning
    camera_intent: cinematic_pullback
    transition_in:
      type: biome_wipe
      duration_s: 1.0
      paired_scene: 2
    transition_out:
      type: biome_wipe
      duration_s: 1.0
      paired_scene: 4
    must_feature: ["red_bird"]
    pacing: held
    motif_features: ["snow drift", "low sun"]
    callback_to_scene: null

  - index: 4
    duration_s: 5.0
    logline: "Red bird ascends from ocean, doubled by its reflection."
    emotional_function: release
    camera_intent: drone_fpv_pullout
    transition_in:
      type: biome_wipe
      duration_s: 1.0
      paired_scene: 3
    must_feature: ["red_bird"]
    pacing: rising
    motif_features: ["water reflection", "rising arc"]
    callback_to_scene: 1

continuity:
  motif_evolution:
    - cast_id: red_bird
      stages:
        - scenes: [1, 2]
          state: "mid-frame, ~10% of frame height"
        - scenes: [3]
          state: "tiny, ~3% of frame height, lost in scale"
        - scenes: [4]
          state: "growing, ascending arc, doubled by reflection"
  hard_rules:
    - "RED_BIRD must appear in every scene."
    - "RED_BIRD's color must be exactly #c84032 in every frame."
    - "NO other red object may appear anywhere in the project."

audio_intent:
  music_arc: ambient
  vo_intent: none
```

### 11.4.3 Example C — 8‑second portal transition

```yaml
storyboard_version: "1.0"
project:
  title: "The Door"
  logline: "A figure walks into a doorway and emerges in a different world."
  theme: "transformation"
  summary: >
    Eight seconds, two scenes, one portal mask. Scene 1 is a grey corridor;
    a silhouette walks toward a tall doorway. The doorway's interior is the
    portal — a world-anchored mask that, as the figure passes through, reveals
    the second stack: a sunlit field. The transition is not a wipe; it is an
    in-frame mask reveal. Scene 2 is the figure on the other side, in a wholly
    different palette and shape language. The shock is the contrast.
  intended_platform: reels
  aspect_ratio: "9:16"
  target_fps: 30
  total_duration_s: 8.0

visual_vocabulary:
  palette:
    primary: ["#2a2a2e", "#3f3f44", "#e8d098"]
    secondary: ["#f5e9c4"]
    neutrals: ["#000000", "#fafafa"]
    forbidden: []
  palette_progression: custom_described
  palette_progression_note: >
    Scene 1 is monochrome cool grey. Scene 2 is warm, saturated, golden.
    The mask reveal IS the palette transition.
  shape_language: angular
  shape_language_note: "Hard architectural geometry on the corridor side; organic on the field side."
  density_curve: falling
  lighting_mood: low_key
  time_of_day: abstract
  weather: none_specified
  look_references:
    - name: "Liminal architectural photography"
      note: "Corridor side; flat overhead light, hard shadows."

arc:
  structure: portal_reveal
  beats:
    - id: approach
      label: "approach"
      function: hook
      narrative_note: "Figure walks toward doorway; we see only their back."
      target_time_s: 0.0
      target_scenes: [1]
    - id: cross
      label: "the threshold"
      function: turning
      narrative_note: "Figure enters the doorway; mask reveals second world."
      target_time_s: 4.0
      target_scenes: [1, 2]
    - id: arrival
      label: "arrival"
      function: release
      narrative_note: "Figure stands in golden field; corridor world is gone."
      target_time_s: 5.0
      target_scenes: [2]

casting:
  - id: figure
    kind: character
    canonical_description: >
      A standing human silhouette, full-body, seen from behind. Solid black
      fill, no internal detail. Proportions: head ~1/8 of total height, slight
      shoulder width, neutral standing posture. Walks with subtle vertical bob
      when animated. Used in both scenes; identical silhouette, identical fill.
    role_in_story: "The traveller; our point of identification."
    palette_locked:
      body: "#000000"
      forbidden: ["#ffffff"]
    forbidden_changes:
      - "must remain pure black silhouette in both scenes; no detail emerges"
    canonical_svg: null
    first_appearance_scene: 1

scenes:
  - index: 1
    duration_s: 4.0
    logline: "Figure walks down corridor toward a tall, glowing doorway."
    emotional_function: establishing
    camera_intent: cinematic_truck_in
    transition_in:
      type: fade_from_black
      duration_s: 0.5
    transition_out:
      type: portal_reveal
      duration_s: 0.5
      emotional_intent: "rupture, revelation"
      paired_scene: 2
    must_feature: ["figure"]
    pacing: steady
    motif_features: ["doorway", "overhead light cone"]

  - index: 2
    duration_s: 4.0
    logline: "Figure stands in a golden field; everything is warm and open."
    emotional_function: release
    camera_intent: drone_fpv_pullout
    transition_in:
      type: portal_reveal
      duration_s: 0.5
      paired_scene: 1
    must_feature: ["figure"]
    pacing: held
    motif_features: ["wheat", "long horizon", "sun low on left"]
    callback_to_scene: 1
    director_notes: "Figure's silhouette must occupy the same screen position it occupied at the moment of mask reveal in scene 1."

continuity:
  hard_rules:
    - "FIGURE silhouette must be byte-identical SVG in both scenes."
    - "NO color from scene 1's primary palette may appear in scene 2 (and vice versa)."
    - "THE doorway's geometry and the portal mask must be visually congruent."

audio_intent:
  music_arc: silent
  sfx_notes:
    - "footsteps in scene 1; wind in scene 2; the moment of crossing is silent"
  vo_intent: none
```

These three examples are the canonical regression cases; they live in `tests/storyboards/` and validate against the schema as part of CI.

---

## 11.5 The Director Agent

### 11.5.1 One agent or several?

**Default: one Opus call.** For pieces under 60 seconds and structures other than `save_the_cat`, a single Opus call writes the whole storyboard in one shot. This is cheaper, simpler, and avoids inter‑sub‑agent inconsistency.

**Decomposed mode for longer pieces (≥ 60 s, or `save_the_cat`):** the director becomes a four‑step internal chain:

1. **Brief‑decomposer** (Sonnet): turns the free‑text brief + references into a structured *treatment* — title, logline, theme, summary, intended platform, audience note. Output: a partial `project` block.
2. **Arc‑architect** (Opus): given the treatment, produces `arc` + `scenes[*].emotional_function` + `scenes[*].duration_s` + per‑scene logline. Output: skeletal scenes list.
3. **Scene‑architect** (Opus): given the arc, fleshes out per‑scene `camera_intent`, `transition_in/out`, `pacing`, `motif_features`, `must_feature`, `palette_override`. Output: complete scenes list. Also produces `casting` and `visual_vocabulary` after seeing all scenes.
4. **Continuity‑checker** (Sonnet): given the assembled storyboard, validates pairing constraints (transition types, paired_scene indices, casting first_appearance_scene values, callback indices), proposes `continuity.hard_rules`. May raise structural errors back to scene‑architect for one revision pass.

The decomposed mode is enabled by `director.mode = "decomposed"` in `config.yaml`; the default is `mode = "single"`. The project manager picks the mode by a deterministic Python rule (§11.9.1), not the director.

### 11.5.2 Model selection

| Configuration | Brief‑decomposer | Arc‑architect | Scene‑architect | Continuity‑checker | Notes |
|---|---|---|---|---|---|
| Default short‑form (single mode) | — | — | Opus (one call) | — | One Opus call writes everything |
| Long‑form (decomposed) | Sonnet | Opus | Opus | Sonnet | |
| Budget mode (`config.budget = "thrift"`) | Haiku | Sonnet | Sonnet | Haiku | Acceptable for trivial briefs |
| Premium mode (`config.budget = "premium"`) | Sonnet | Opus | Opus | Sonnet | Full quality, default for ≥ 60 s |

Pricing context (April 2026 Anthropic rates): Haiku 4.5 at $1/$5 per MTok, Sonnet 4.6 at $3/$15, Opus 4.7 at $5/$25 — a clean 5× output‑to‑input ratio across tiers and a 1.67× Opus‑over‑Sonnet ratio. Prompt caching delivers up to 90% discount on cached input tokens, which we exploit aggressively for the storyboard schema and few‑shot examples (§11.12).

### 11.5.3 The director's full prompt (canonical)

This is the prompt for the **single‑mode Opus director**. The decomposed sub‑prompts are derived from this by elision; they live in `prompts/director/` in the repo. Length here is illustrative; the production prompt is approximately 900 words plus appended schema and examples.

> **System prompt — Director (single mode)**
>
> You are the Director of `parallax-engine`, an automated 2.5D parallax animation pipeline. Your sole responsibility is to convert a user brief — sometimes accompanied by mood‑board reference URIs — into a single `storyboard.yaml` artifact that downstream agents will consume to produce a short‑form animated MP4.
>
> You are not a renderer. You will never see pixels. You will never see SVG markup. You will never produce camera path coordinates, layer Z values, or asset filenames. You produce *creative direction*, expressed as structured YAML, and nothing else. The implementation of your direction is the job of subordinate agents whose work you will not observe.
>
> Treat yourself as a director in pre‑production. Your closest analogues are the directors who develop a film's color script, casting bible, and storyboard before a single frame is animated. You have all the constraints of that role: every decision you make must make sense across the whole piece, not in isolation; every recurring element must be specified once, canonically, so that scene‑level interpretations cannot drift; and the dramatic arc must do the work — there is no audio, no dialogue, no voiceover, only image, time and motion.
>
> Your output is a single YAML document conforming exactly to the storyboard schema attached below. The schema is non‑negotiable. You must:
>
> 1. **Read the brief carefully**, including any reference URIs. References are URIs only — you cannot see image content. Treat them as named visual hints (the user telling you "look at this kind of thing") rather than as data. Refer to them by name in `look_references` and your prose.
> 2. **Decide a structure** before writing scenes. For pieces ≤ 12 s use `establish_disrupt_resolve` or a `biome_tour` or a `portal_reveal`. For 12–30 s any of the above plus `three_act`. For ≥ 30 s, `three_act`, `save_the_cat`, or a justified `custom`. Write the `arc` block first internally before writing scenes.
> 3. **Decide visual vocabulary globally.** Pick the palette, shape language, density curve, and lighting mood for the whole piece. Every scene inherits these unless it has an explicit, justified `palette_override`. Resist scene‑local invention — the strength of the piece comes from constraint.
> 4. **Cast before you write scenes.** If the same motif, character, or prop is going to appear in more than one scene, add it to `casting` *before* writing the scenes that use it. Each casting entry's `canonical_description` must be self‑sufficient — an asset‑generator must be able to produce a faithful SVG from that description alone, with no other context.
> 5. **Write scenes with intent.** Each scene must have an `emotional_function` that contributes to the arc; a `camera_intent` chosen for that emotion (close camera moves for intimacy and rising action, pulled‑back camera for release and scale, static held for breath); transitions paired correctly with neighbours; and concrete `motif_features` so the implementation tier knows what to put on screen.
> 6. **Pair transitions correctly.** A `biome_wipe` out of scene N requires a `biome_wipe` into scene N+1 with `paired_scene: N`. A `portal_reveal` is one transition spanning two scenes' stacks; both scenes must declare it with each other as `paired_scene`. A `match_cut` requires the donor and receiver to share a compositional anchor; describe that anchor in the donor's `director_notes`.
> 7. **Specify continuity rules** as `hard_rules`. Phrase them so a downstream QA critic can answer yes/no per scene. *"NO character appears"* is checkable; *"the mood should be melancholy"* is not.
> 8. **Capture audio intent** even though no audio will be generated. The pacing of music is information about the pacing of image; capturing it forces you to think about pacing.
> 9. **Don't over‑specify.** You are not the scene‑designer. Do not write SVG. Do not write Z values. Do not write camera paths. Do not write Pillow filter parameters. If you find yourself reaching for those, you are doing the wrong job.
> 10. **Don't ask the user back.** If the brief is ambiguous, pick the most defensible interpretation and proceed. State your assumption in `director_notes`. Asking back is reserved for cases where the brief is internally contradictory.
>
> When in doubt, reach for restraint. Short‑form video that tries to do too many things does none of them. Pick a single emotion, give it space, and resolve it.
>
> Your output must be a single YAML document. Wrap it in a single fenced block tagged `yaml`. No prose outside the block.
>
> Below: the formal schema. Below that: three worked examples. Below that: the user's brief and any references.

The schema is appended verbatim from `parallax_engine/director/schema.yaml`. The three examples are the three from §11.4 — they live in the prompt as canonical demonstrations and are aggressively prompt‑cached (the schema and examples are stable across all invocations; only the brief varies).

### 11.5.4 Reference image handling

The director never receives image bytes. References are surfaced as URIs and human‑readable filenames. This is a deliberate choice for three reasons:

- **Context discipline.** A 1MB image consumes ~1300 tokens worst‑case after vision encoding; mood‑board sets can blow up context fast and Opus prompt caching does not extend cleanly to image content that varies per project.
- **Cost.** Vision input tokens are billed at the same rate as text, but the encoded representation of an image is large and usually unhelpful for *direction* (vs. for asset generation, where it might be).
- **Determinism.** Re‑running the director on the same brief should produce broadly similar storyboards; vision input introduces additional variance.

A v2 might add a separate vision‑capable casting agent that *does* see references and produces enriched canonical descriptions. That is out of scope for v1 and not specified here.

### 11.5.5 Ambiguous briefs

The director defaults to **assume and document**, never **ask back**. This is non‑negotiable: the system is a one‑shot pipeline; interactive clarification breaks the contract. If the brief is genuinely contradictory (e.g. "a 30‑second video with no characters about a man riding a horse"), the director writes a storyboard for the most defensible reading, records the contradiction in `director_notes`, and proceeds. The QA critic may, at the storyboard tier, flag the assumption for the user to inspect.

---

## 11.6 The Scene Designer (Tier 2)

### 11.6.1 Role change

The pre‑director scene‑designer was a one‑shot that produced a complete `scene.yaml` from a brief. The director‑era scene‑designer is a **translator**: it consumes `storyboard.yaml`, the index of the scene to build, prior scenes' fragments, and the casting bible, and produces a `scene.yaml` *fragment* for one scene plus an asset manifest.

### 11.6.2 Input contract

- `workspace/storyboard.yaml` (read‑only)
- `workspace/casting.yaml` (read; append‑only on canonical SVG paths)
- `workspace/scenes/scene_*.yaml` for all indices < this scene (read‑only)
- The scene index to produce, passed in the prompt
- The renderer's `scene.yaml` schema (§2.2), passed in the prompt as canonical reference

### 11.6.3 Output contract

The scene‑designer writes two things atomically:

- `workspace/scenes/scene_NN.yaml` — a fragment conforming to a strict subset of the scene.yaml schema (one scene only, no top‑level project metadata, just the `stack`, `layers`, `masks`, `camera`, and `transitions` for this scene).
- A line appended to `workspace/scenes/scene_NN.manifest.json` — the asset manifest, listing every SVG this scene needs by `id`, with `purpose`, `kind` (`canonical` if it references a casting entry whose SVG already exists, `produce_canonical` if it's the first reference to a casting entry, or `local` for non‑cast assets).

If the scene references a casting entry whose `canonical_svg` is null in `casting.yaml`, the scene‑designer:

- Emits the asset in its manifest with `kind: produce_canonical`.
- Writes the path it expects (`assets/canon/<casting_id>.svg`) into `casting.yaml` and saves it.
- Asset‑generator (Tier 3) sees `produce_canonical`, reads the casting entry's `canonical_description` (not the scene's local description), produces the SVG, and writes it to the predicted path.
- All subsequent scenes that reference the same casting entry get `kind: canonical` and reuse the path.

This guarantees that the **first scene to use a cast member triggers production; every subsequent scene reuses the byte‑identical SVG.** The QA critic at scene level verifies this hash equality.

### 11.6.4 Going off‑style is structurally prevented

Scene drift is the single most common failure mode in multi‑shot AI video. We prevent it three ways:

1. **The visual vocabulary block is in the prompt** — palette hexes, shape language, density curve, lighting mood are all literal strings the scene‑designer is instructed not to deviate from.
2. **Casting descriptions are read, not invented.** The scene‑designer is forbidden by prompt from re‑describing a cast member; it must reference by id.
3. **Prior scenes are visible.** The scene‑designer is given previous scenes' fragments. This catches second‑order drift (scene 3 designed without seeing scene 2 will inevitably contradict it).

### 11.6.5 Scene merger

After all N scene‑designers complete, a deterministic Python step (`parallax_engine.scene.merger.merge`) concatenates the fragments into the single `scene.yaml` the renderer consumes. The merger is **not an LLM**. Its job is purely mechanical:

- Stitches scene fragments into a `scenes` list.
- Resolves cross‑scene transition pairs (validates that `paired_scene` references match on both sides; aborts if not).
- Promotes shared `palette` from `storyboard.visual_vocabulary` to scene.yaml's project block.
- Writes timing offsets (each scene's `start_t` derived from cumulative durations).
- Writes the final `scene.yaml` and a sidecar `scene.merge.log.json` documenting which fragments produced which sections.

If the merger raises an error, the project manager treats it as a scene‑level failure and rebuilds the offending scene.

### 11.6.6 Scene‑designer prompt (canonical)

> **System prompt — Scene Designer**
>
> You are a Scene Designer for `parallax-engine`. Your job is to translate one scene of a director's storyboard into one fragment of `scene.yaml` that the renderer can execute. You are a translator and a craftsperson, not a director and not a renderer.
>
> You will be given:
> - The full `storyboard.yaml` (read it carefully — every field affects you).
> - The casting bible `casting.yaml` (treat every entry as canonical; never re‑describe).
> - All prior scenes' fragments, in order. Read them. Your scene must visually flow from scene N−1 and into scene N+1.
> - The `scene.yaml` schema as canonical reference for your output.
> - The integer index of the scene you are to build.
>
> Your output is a YAML fragment for one scene plus an asset manifest. You do not write the project block, the music track, the global palette, or anything that is not specifically your scene. The merger will assemble fragments after you.
>
> Your translation responsibilities:
>
> 1. **Camera.** Convert `camera_intent` into a concrete `camera` block per the schema. The translation table (§11.10) is authoritative; consult it. Pick `drone.path`, `parallax.pan_axis`, `parallax.dolly`, or `portal.mask_path` parameters that realise the storyboard's intent. You have latitude in *parameters* but not in *behaviour*: a `cinematic_pan_right` always becomes `camera.mode: parallax` with rightward pan.
> 2. **Layers.** Decide how many planes (Z values) the scene needs. Use the storyboard's `density_curve` and the per‑scene `pacing` to size: `sparse` and `held` mean ≤ 4 planes; `steady` means 5–7; `dense` and `rising` mean 8–12. Each plane has a Z value, a parallax factor (closer planes move faster), and a list of layer assets.
> 3. **Assets.** For each layer asset, decide whether it is canonical (cast member: reference by id) or local (one‑off: invent a description). For canonical assets that have not yet been produced, write `kind: produce_canonical` in the manifest. Never write inline SVG. Never specify colors that contradict the storyboard's palette unless the scene has an explicit `palette_override`.
> 4. **Masks.** If the scene's `transition_out.type` is `portal_reveal` or `biome_wipe_donor`, define the mask geometry here. The mask is world‑anchored, not screen‑anchored — it lives in the scene's coordinate space and is consistent across both the donor and receiver. Match the mask's anchor to the receiver's anchor (which you can see in their fragment if it has been written; if not, write yours and the merger will validate).
> 5. **Timing.** Your scene's `duration_s` is given by the storyboard. Distribute it: typically 60% in the steady camera state, 20% ramp‑in, 20% ramp‑out. Pacing modifies this — `held` is 100% steady, `rising` ends with the camera still accelerating.
> 6. **Hard rules.** Read `continuity.hard_rules`. Each is a constraint your scene must satisfy. State in your scene's `qa_self_check` block which rules apply and how you've satisfied them.
>
> What you do NOT do:
> - You do not change the storyboard. If you think the storyboard is wrong, write a `scene_designer_protest` field at the bottom of your fragment with your reasoning. The QA critic will see it. The next director re‑run (if any) will see it. You do not edit `storyboard.yaml`.
> - You do not invent recurring cast. If a motif appears in your scene that is not in casting and is not in `must_feature`, treat it as local and per‑scene, not casting.
> - You do not produce SVG. You produce paths and descriptions; asset‑generator does the SVG.
> - You do not edit prior scenes' fragments.
> - You do not call any other agent. The Claude Agent SDK in this configuration disallows it; even if it didn't, you wouldn't.
>
> Your output is a single YAML document for the scene fragment, followed by a single JSON document for the manifest. Both wrapped in fenced blocks. No prose outside.

---

## 11.7 The Casting / Continuity System

The casting bible is the single mechanism that prevents the "red bird is now a sparrow" failure mode. Every observation from the AI video field — Sora 2's reference images, Runway Gen‑4's identity encoding, Veo 3.1's Scenebuilder, Pika's Scene Ingredients, Kling's Identity‑Lock — converges on the same insight: **casting must be a file every agent reads, not a prompt every agent has to remember.**

### 11.7.1 Lifecycle of a cast member

1. **Director writes the entry.** `casting.yaml` gets a new entry with `id`, `kind`, `canonical_description`, `palette_locked`, `forbidden_changes`, `first_appearance_scene`, `canonical_svg: null`.
2. **First scene‑designer to need it** writes `kind: produce_canonical` in its manifest and reserves the path `assets/canon/<id>.svg`.
3. **Asset‑generator produces the SVG.** It reads only the casting entry's `canonical_description`, the project's `shape_language`, and the entry's `palette_locked`. It does **not** read the scene's local context. This is intentional: the canonical asset must be context‑free.
4. **Scene‑designer (the same one) writes the path** into `casting.yaml`, atomically updating `canonical_svg` from null to the path.
5. **Every subsequent scene** that references the cast member emits `kind: canonical` and the path; asset‑generator does nothing for those references.
6. **Per‑scene variants** (e.g. "red_bird, scene 3, smaller, in shadow") are produced as **transformations** of the canonical SVG, not regenerations. The asset‑generator gets `variant_of: red_bird` plus a transformation spec (scale, lighting tint within `palette_locked` constraints) and applies it in‑memory to the canonical SVG to produce a per‑scene SVG. The canonical asset is never modified.

### 11.7.2 What the asset‑generator gets when producing a variant

```json
{
  "kind": "variant",
  "variant_of": "red_bird",
  "scene_index": 3,
  "canonical_svg_path": "assets/canon/red_bird.svg",
  "canonical_description": "<copied from casting.yaml>",
  "palette_locked": {"body": "#c84032", "accent": "#000000"},
  "forbidden_changes": ["color must always be #c84032..."],
  "transformation": {
    "scale": 0.3,
    "lighting_tint": "cool_shadow",
    "position_hint": "lower_third_left"
  }
}
```

Asset‑generator's contract: produce an SVG that is a faithful transformation of the canonical, obeying every constraint in `palette_locked` and `forbidden_changes`. The QA critic runs a hash‑based check: variant SVGs must share their canonical's structural fingerprint (path topology, fill colors as a multiset).

### 11.7.3 Casting‑aware QA

The QA critic, at scene level, verifies:

- Every `must_feature` cast id is present in the scene fragment's manifest.
- Every `must_avoid` cast id is *not* present.
- The canonical SVG hash of each canonical reference matches `assets/canon/<id>.svg`.
- Variant SVGs obey their `palette_locked` constraint (color histogram check).
- Hard rules naming cast ids are individually satisfied.

If any check fails, QA raises a scene‑level failure; the scene is rebuilt (up to its retry cap, §11.9).

---

## 11.8 The QA tiering system

QA failures cluster at three levels with very different remediation costs.

| Level | Failure example | Detection method | Remediation | Cost |
|---|---|---|---|---|
| **Asset/mask/render** | A specific SVG fails to load; a mask's anchor doesn't align; a frame is fully black | Renderer error; pixel‑level checks; SVG validation | Re‑run asset‑generator or mask‑author for that one asset | Cheap: ~1 Sonnet call |
| **Scene** | Scene 3 reads as celebratory when its `emotional_function` is `denouement`; a cast member is missing; a hard rule is violated; scene fragment fails merger validation | LLM critic reads the rendered frames + the storyboard's intent for that scene + the scene fragment | Re‑run scene‑designer for that scene only | Moderate: 1 Opus/Sonnet call + downstream Tier 3 re‑runs |
| **Storyboard** | The whole arc doesn't deliver the brief's theme; structural inconsistency the merger can't fix; the user's brief is fundamentally not realised | LLM critic reads the brief + storyboard + a representative subset of rendered frames | Re‑run director from scratch; use prior storyboard as a "what didn't work" anti‑example | Expensive: 1 Opus call + full Tier 2 + Tier 3 re‑run |

### 11.8.1 Retry caps (enforced in Python, not prompts)

```python
MAX_ASSET_RETRIES_PER_ASSET = 3
MAX_SCENE_REDESIGNS_PER_SCENE = 2
MAX_STORYBOARD_REGENERATIONS = 1
MAX_TOTAL_BUDGET_USD = 8.00     # default; user-overridable in config
```

If a cap is hit, the project manager either escalates to the next tier (asset failure that exhausts retries → escalate to scene level) or terminates with a partial result and a `qa/fatal.json` describing the failure mode. **Counters live in Python, not in any prompt.** The QA critic must not be told "this is your second try"; it must judge each attempt fresh.

### 11.8.2 The QA critic prompt distinguishes levels

The QA critic is invoked in three modes, with three different system prompts:

- **`qa-critic --level=asset`**: Sonnet. Input: one rendered frame, the asset's manifest entry, palette constraints. Output: pass/fail per asset with reason.
- **`qa-critic --level=scene`**: Sonnet (Opus if `budget=premium`). Input: storyboard's scene entry, scene fragment, rendered frames covering the scene, casting entries referenced. Output: pass/fail with classification (`function_mismatch`, `casting_drift`, `palette_violation`, `hard_rule_violation`, `pacing_off`, `transition_paired_wrong`).
- **`qa-critic --level=storyboard`**: Opus. Input: brief, full storyboard, full casting bible, rendered frames at 1 frame per scene, prior pass's QA report. Output: pass/fail with classification (`theme_unmet`, `arc_doesnt_land`, `structural_contradiction`).

Each level's prompt is explicit that the critic must classify, not just judge. The classification drives the project manager's routing.

### 11.8.3 Partial re‑renders

A scene‑level redesign does not re‑render scenes that didn't change. The project manager:

- Re‑invokes scene‑designer for the failed scene.
- Diffs the new fragment against the old.
- Re‑runs only the asset‑generator / mask‑author calls whose inputs changed.
- Re‑runs the renderer for the full piece (the renderer is cheap; full re‑renders are not the cost driver).

A storyboard‑level regeneration discards `scenes/`, retains `assets/canon/` if the new storyboard's casting is a superset of the old (deterministic check), and proceeds.

---

## 11.9 The revised harness topology and the project manager's prompt

### 11.9.1 Sequence of operations

```
brief.md  →  director  →  storyboard.yaml + casting.yaml
             │
             ▼
          (validate schema; abort on parse failure)
             │
             ▼
          qa-critic --level=storyboard --pre  (cheap sanity check, optional)
             │
             ▼
          for i in 1..N:
              scene-designer(i)  →  scenes/scene_i.yaml + manifest
                                    (sequential, scene i sees 1..i-1)
             │
             ▼
          merger  →  scene.yaml
             │
             ▼
          parallel wave: asset-generator(*)   (one per manifest entry)
          parallel wave: mask-author(*)       (one per mask)
             │
             ▼
          renderer  →  out.mp4
             │
             ▼
          qa-critic --level=asset (per asset, parallel)
          qa-critic --level=scene (per scene, parallel)
          qa-critic --level=storyboard (one)
             │
             ▼
          on FAIL: route to lowest tier that explains the failure
                   - asset failure  → re-run asset-generator (cap 3/asset)
                   - scene failure  → re-run scene-designer i (cap 2/scene)
                   - story failure  → re-run director (cap 1/run)
                   on cap exhaustion: escalate up
             │
             ▼
          PASS → write final out.mp4 + manifest.json
          FAIL with caps exhausted → write partial + qa/fatal.json
```

The single‑mode vs decomposed‑mode decision is taken by the project manager via a deterministic rule:

```python
def director_mode(brief: Brief) -> str:
    if brief.target_duration_s >= 60.0: return "decomposed"
    if brief.requested_structure == "save_the_cat": return "decomposed"
    if brief.config_budget == "thrift": return "single"
    return "single"
```

### 11.9.2 Project manager's prompt (canonical)

> **System prompt — Project Manager**
>
> You are the Project Manager for `parallax-engine`. You are not creative. You do not invent. You coordinate. Your role is the producer who hires the director, schedules the crew, runs dailies, and decides when a take is good enough to ship. The director directs; you produce.
>
> Your responsibilities:
>
> 1. **Sequence the tiers.** Invoke the director once. Then for each scene index 1..N, invoke a scene‑designer in order, never in parallel. Then dispatch implementation agents (asset‑generator, mask‑author) in parallel waves. Then call the renderer. Then call QA at all three levels.
> 2. **Route QA failures.** When QA fails, read the failure level and re‑invoke the matching tier. Asset failures go to asset‑generator. Scene failures go to scene‑designer. Storyboard failures go to director. Never escalate prematurely; always try the lowest tier first.
> 3. **Honour budget caps.** Counters for retries and dollar spend are maintained in Python; you will be told when a cap is hit. When a cap is hit, escalate to the next tier; if the highest tier's cap is hit, terminate with the best output produced so far.
> 4. **Never make creative decisions.** If two valid choices present themselves and you cannot route deterministically, write a routing diagnostic and pick the one that minimises remaining cost.
> 5. **Never spawn unnamed agents.** You may invoke only: director, scene‑designer, asset‑generator, mask‑author, camera‑pather, renderer (Python tool), qa‑critic. The Claude Agent SDK enforces this; the prompt reaffirms it.
> 6. **Write a single line to logs/manager.log per decision** so the run is replayable.
>
> What you do NOT do: write storyboards, edit storyboards, redesign scenes, paint SVGs, change masks, or critique the work. Each of those has an agent. You hire that agent.

This prompt is shorter than the director's because the project manager has no creative latitude. Most of its behaviour is enforced in Python (counters, file I/O, retry routing, parallel dispatch). The prompt's role is to make it stable when the deterministic rules don't fully cover a case.

---

## 11.10 The translation table — `storyboard.yaml` → `scene.yaml`

This table is the canonical contract between Tier 1 and Tier 2. Every storyboard field that maps to a scene.yaml field is listed; everything else is metadata for QA.

| Storyboard field | Scene.yaml field(s) | Translation | Scene‑designer latitude |
|---|---|---|---|
| `project.aspect_ratio` | `project.canvas.width`, `.height` | "9:16" → 1080×1920; "16:9" → 1920×1080; etc. | None (table lookup) |
| `project.target_fps` | `project.fps` | Direct copy | None |
| `visual_vocabulary.palette.primary[*]` | `project.palette.background_*` per scene | Distributed: scene N's background uses primary[N mod len] unless `palette_progression: warming` etc. | Picks which primary tone per scene from the palette; cannot introduce new colors |
| `visual_vocabulary.palette.forbidden` | constraint passed to asset‑generator | Negative constraint | None |
| `visual_vocabulary.palette_progression` | scene‑level palette interpolation | `warming` → progressive shift toward warm hex; `cooling` → cool; `static` → identical | None (algorithm is fixed) |
| `visual_vocabulary.shape_language` | constraint passed to asset‑generator | Hard prompt constraint | None |
| `visual_vocabulary.density_curve` | `scene.layers.count` budget | `sparse` → 3–4; `medium` → 5–7; `dense` → 8–12; rising/falling apply per scene index | Picks within band |
| `visual_vocabulary.lighting_mood` | `scene.atmosphere.fog_density`, `.vignette`, `.gradient` | Lookup table per mood | None for fog/vignette parameters; latitude in gradient direction |
| `visual_vocabulary.time_of_day` | `scene.atmosphere.sky_palette` | Direct lookup | None |
| `arc.beats[*].target_time_s` | `scene.start_t` (cumulative) | Computed by merger from durations | None |
| `casting[*].id` | referenced as `assets[*].id` in scene fragments | Direct id reference | None on id; latitude on placement |
| `casting[*].canonical_description` | passed to asset‑generator on first reference | Direct copy | None |
| `casting[*].palette_locked` | hard constraint on asset‑generator | Direct copy | None |
| `scenes[i].duration_s` | `scene.duration_s` | Direct copy | None |
| `scenes[i].camera_intent` | `scene.camera.mode` + parameters | See §11.3.5.1 lookup; camera‑pather then fills coordinates | Latitude in coordinates within mode |
| `scenes[i].transition_in.type` | `scene.transition_in.kind` | Direct enum mapping | None on kind; latitude on duration interpolation curve |
| `scenes[i].transition_in.duration_s` | `scene.transition_in.duration` | Direct copy | None |
| `scenes[i].pacing` | `scene.timing.curve` | `slow_burn` → ease‑in‑slow; `held` → linear; `rising` → ease‑out‑accel; etc. | None |
| `scenes[i].must_feature[*]` | `scene.layers[*].assets[*]` must include matching id | Set membership constraint | Latitude in placement, scale, layer assignment |
| `scenes[i].must_avoid[*]` | constraint: id must not appear | Negative set membership | None |
| `scenes[i].motif_features[*]` | passed to asset‑generator as ad‑hoc local asset descriptions | Free‑text passthrough | Latitude in interpretation; constrained by visual vocabulary |
| `scenes[i].palette_override` | `scene.palette` overrides project.palette | Direct override | Latitude in distribution |
| `scenes[i].callback_to_scene` | metadata only, used by QA | Not directly emitted; informs scene‑designer's choices | High — designer chooses the visual rhyme |
| `continuity.hard_rules[*]` | passed to QA as boolean checks | Listed as `qa.required_checks` | None |
| `audio_intent.*` | ignored by renderer in v1 | No mapping | None |

**Where the scene‑designer is purely a transcoder:** ids, types, durations, paired_scene values, hard constraints. **Where it has creative latitude:** layer count within a band, layer Z values, asset placement within the frame, camera path coordinates within the named mode, gradient direction within the mood, transition timing curves. This is the right division of labor: the director makes the choices that depend on the whole piece; the scene‑designer makes the choices that depend on one scene.

---

## 11.11 Determinism, persistence, and the user re‑run workflow

The renderer is byte‑deterministic given `scene.yaml + seed` (see §7). The director and scene‑designers are LLMs and therefore are not. But because both produce serialised artifacts (`storyboard.yaml`, `scenes/scene_NN.yaml`), **a re‑run from a saved storyboard is far more deterministic than a re‑run from a brief.**

This unlocks an "edit the script, re‑render" workflow that is far more useful than the previous "edit the brief, hope":

```
$ parallax-engine render --brief brief.md
# ... produces workspace/storyboard.yaml, scenes/, out.mp4 ...

$ vim workspace/storyboard.yaml      # user changes scene 3's emotional_function
                                     # from "rising" to "turning"

$ parallax-engine render --resume     # re-runs only from scene-designer for scene 3,
                                     # leaves storyboard, casting, scenes 1/2/4 untouched
```

The CLI flag `--resume` triggers a deterministic dependency walk:

1. Validate `storyboard.yaml` schema. Abort on failure.
2. Diff against `workspace/.cache/storyboard.yaml.last`. Compute changed scene indices.
3. For each changed scene index, invalidate `scenes/scene_NN.yaml` and dependent assets.
4. Run scene‑designer only for changed scenes; skip the director entirely.
5. Run asset‑generator only for changed scenes' new manifest entries.
6. Re‑render (cheap).
7. Re‑run QA at scene level for changed scenes; storyboard‑level QA only if structurally significant fields changed (arc, casting, visual_vocabulary).

This makes the storyboard a first‑class user‑editable artifact. The casting bible is similarly editable: a user can hand‑edit `casting.yaml` to pin a hex color, change a description, or swap a canonical SVG path; `--resume` will re‑run downstream as needed.

---

## 11.12 Cost analysis

### 11.12.1 The pre‑director baseline

The previous one‑shot scene‑designer harness, on the canonical 24 s 4‑biome brief, was approximately:

- Scene‑designer (Opus 4.6, single call): ~6 K input tokens, ~3 K output tokens → ~$0.11
- Asset‑generator × 8: Sonnet, ~2 K in / ~1 K out each → ~$0.20
- Mask‑author × 3: Sonnet, ~1 K / ~0.5 K each → ~$0.03
- Camera‑pather × 4: Sonnet, ~1 K / ~0.5 K each → ~$0.04
- QA‑critic × 2 passes: Sonnet, ~3 K / ~1 K each → ~$0.05
- **Total: ~$0.43 happy path; ~$1.20 with retries; ~$2.50 budget cap**

### 11.12.2 The director‑era cost

For the same 24 s 4‑biome brief in single‑mode (default, < 60 s):

- Director (Opus 4.7, single call): ~12 K input tokens (schema + 3 examples + brief + references list) of which ~10 K are cacheable, ~5 K output tokens → cold call ~$0.19; subsequent calls with prompt cache ~$0.06
- Scene‑designer × 4 (Sonnet 4.6, sequential): each gets ~8 K input (storyboard + casting + prior scenes + scene.yaml schema) of which ~5 K are cacheable across scenes, ~2.5 K output → ~$0.10 each, ~$0.40 total; with cache ~$0.20
- Asset‑generator × 8: unchanged → ~$0.20 (canonical SVGs are produced once and reused; this is now lower for repeat assets)
- Mask‑author, camera‑pather, QA: unchanged in unit cost; QA scene level adds a fourth call at scene level (~$0.05 added)
- **Total happy path: ~$0.85 first run; ~$0.50 cached re‑run**
- **With one scene redesign: +$0.10 → ~$0.95**
- **With one storyboard regeneration (rare): +$0.20 → ~$1.15**

### 11.12.3 New realistic floor

The previous $2.50/render budget cap holds. The new realistic *floor* (no retries, single scene, simple brief) is approximately **$0.60**, up from approximately $0.30. The new realistic *expected* per render (with one scene retry, typical brief) is approximately **$1.20**, up from approximately $0.80. The cap is not breached; the increase is in expected cost, not worst case.

For long‑form (≥ 60 s, decomposed director mode), expected cost roughly doubles (four director sub‑calls instead of one), pushing the realistic expected per render to ~$2.40 with cap $5.00. We recommend that long‑form be a separate config (`config.budget = "longform"`) with its own cap, to avoid surprising users.

### 11.12.4 Where to cut cost

- **Sonnet director for short pieces** (< 15 s, simple brief): Sonnet 4.6 produces acceptable storyboards for trivial briefs. Saves ~$0.13 on first run.
- **Haiku scene‑designers for trivial briefs**: Haiku 4.5 can transcode a scene fragment when the storyboard is already detailed. Saves ~$0.30 on first run; only acceptable if storyboard is high‑quality.
- **Aggressive prompt caching**: the schema (~3 K tokens) and three canonical examples (~7 K tokens) are stable across all invocations of the director. The 90% cache discount on input means the director's *cached* cost is roughly $0.06 per call vs. $0.19 cold. The same applies to scene‑designer's schema reference (~2 K) and to all QA prompts. Combined cache savings on a typical run: ~$0.20.
- **Caching the director output for re‑runs**: `--resume` skips the director entirely; saves the entire director cost on iteration.
- **Batch processing** (50% discount): not applicable to interactive runs but useful for nightly regeneration / regression testing of the example storyboards.

---

## 11.13 Anti‑patterns specific to this expansion

These are non‑negotiable. They are listed here so they have a single citable reference for code review.

1. **Don't have the director also do asset generation.** Mixing tiers is the death of clean topologies. The director never produces SVG, never specifies hex colors at the layer level, never picks Z values. If a code path lets the director write to `assets/`, that code path is wrong.
2. **Don't let scene‑designers spawn their own subagents.** The Claude Agent SDK enforces this — agents in a fork cannot themselves spawn agents. The prompt reaffirms it. If a scene needs work that another agent does, the project manager dispatches that agent; the scene‑designer requests it via its manifest.
3. **Don't let the director see every asset's pixels.** Context blowup, cost explosion, and worse quality (the director starts second‑guessing the implementation tier instead of doing its own job). The director reasons on the storyboard, not on the rendered output. The QA critic sees pixels; the director sees text.
4. **Don't bake casting into prompts; bake it into a file every agent reads.** A casting entry in the prompt of the director is fine; a casting entry in the prompt of the scene‑designer is wrong. Scene‑designer reads `casting.yaml`. This is the fundamental insight from every modern multi‑shot AI video system.
5. **Don't skip the storyboard for "simple" briefs.** Even for a 6‑second one‑shot, the storyboard tier produces a one‑scene storyboard with a casting bible (possibly empty) and a visual vocabulary block. This is not waste; it is consistency. The trivial case emerges naturally: a one‑scene `establish_disrupt_resolve` storyboard with no casting and minimal vocabulary is fast to write and adds ~$0.03 cost in single‑Sonnet mode. The benefit is that *every* render flows through the same pipeline; there is no special path that diverges in subtle ways.
6. **Don't try to generate music or audio in v1.** The `audio_intent` block exists to capture intent for v2. The renderer ignores it. Adding audio synthesis to v1 invalidates the byte‑deterministic renderer guarantee and triples the cost.
7. **Don't let the QA critic edit artifacts.** QA produces verdicts and classifications; it never writes to `storyboard.yaml`, `casting.yaml`, or `scenes/`. Edits flow through the agent that owns the artifact. This keeps tier ownership clean.
8. **Don't re‑describe cast members in scene‑designer output.** A scene‑designer that re‑describes the red bird has failed. The fragment must reference by id. The QA critic at scene level grep‑checks for re‑description (a regex match on the casting entry's first sentence appearing inside a non‑canonical asset's description).
9. **Don't put retry counters in any prompt.** Counters are state; state belongs in Python. Telling an LLM "this is your second attempt" biases its output; telling it nothing keeps each attempt independent.
10. **Don't conflate `must_feature` with placement.** `must_feature: ["red_bird"]` means the bird is in the scene. It does not mean the bird is in the centre, in the foreground, or at a particular Z. Those are the scene‑designer's calls. If the director wants the bird in a specific position, that goes in `director_notes`, not into a new schema field.
11. **Don't add new transitions without renderer support.** Every value in the `transition_in.type` enum maps to a renderer behaviour. Adding `match_dissolve_with_color_smear` to the enum without adding the renderer code that implements it is a contract violation; the merger will not catch it because the merger validates pairing, not implementability. The schema's enum values are defined by the renderer's capabilities; expanding the schema requires expanding the renderer first.
12. **Don't let the merger be an LLM.** The merger is mechanical; making it an LLM introduces non‑determinism in a place that has no business being non‑deterministic. If the merger needs to make a judgement call, that's a sign the scene fragments are underspecified — fix the scene‑designer prompt, not the merger.

---

## 11.14 Implementation checklist (for Claude Code, when building this tier)

This is the order of operations. Do not skip steps; each depends on the previous. **Fits between Phase 4 and Phase 5 of the build phase plan in §8.** Treat it as Phase 4.5: the director tier is built on top of a working v0 implementation harness, then the v0 implementation tier is downgraded to "implementation-only" agents that consume scene fragments rather than briefs.

1. Implement `parallax_engine/director/schema.py` (Pydantic v2 models for `Storyboard`, `Casting`, `Scene`, `VisualVocabulary`, `Arc`, `Beat`, `TransitionSpec`, etc.). Generate JSON Schema. Round‑trip‑test against the three example storyboards in §11.4.
2. Implement `parallax_engine/director/prompt.py` — the canonical director prompt builder. Use the prompt in §11.5.3 verbatim; parameterise only the brief and references list. Wire prompt caching for the schema + examples block.
3. Implement `parallax_engine/director/agent.py` — the single‑mode and decomposed‑mode director invocations. Single mode is one Opus call returning a YAML block; parse and validate. Decomposed mode is the four‑step chain in §11.5.1.
4. Implement `parallax_engine/casting/bible.py` — read/append‑only API for `casting.yaml`. Atomic write of `canonical_svg` paths. Hash verification utilities.
5. Implement `parallax_engine/scene/designer.py` — scene‑designer invocation with the prompt in §11.6.6. Input contract from §11.6.2; output contract from §11.6.3.
6. Implement `parallax_engine/scene/merger.py` — pure Python; no LLM. Handles transition pairing validation, palette propagation, timing offsets. Emits `scene.merge.log.json`.
7. Update `parallax_engine/asset/generator.py` to be casting‑aware: branches on `kind` in (`canonical`, `produce_canonical`, `local`, `variant`). On `produce_canonical`, reads only the casting entry. On `variant`, applies a transformation to the canonical SVG. On `canonical`, no‑op. On `local`, current behaviour.
8. Update `parallax_engine/qa/critic.py` to support `--level` in (`asset`, `scene`, `storyboard`). Add classification taxonomies (§11.8.2). Wire the project manager to route on classification.
9. Update `parallax_engine/manager.py` (the renamed orchestrator):
   - Keep the prompt minimal (§11.9.2).
   - Move retry counters and budget tracking to Python.
   - Implement the sequence in §11.9.1.
   - Implement `--resume` (§11.11) by diffing `storyboard.yaml` against `.cache/storyboard.yaml.last`.
10. Update `tests/storyboards/` with the three canonical examples. Add CI step: schema validation + dry‑run merge.
11. Update `tests/integration/` with one end‑to‑end run per example; budget cap enforced; outputs compared against golden MP4 hashes (frames hash; audio is not generated in v1).
12. Update `SKILL.md` (the Claude Skill shim) to document the new flags: `--resume`, `--budget thrift|standard|premium|longform`, `--director-mode single|decomposed|auto`. Keep the user‑facing surface minimal: most users invoke `parallax-engine render --brief brief.md` and never see the new tier.
13. Update §3 of this SPEC to point to this section as the canonical reference for the director tier; the existing §3 supersession header already does this.

---

## 11.15 Open questions and v2 hooks

These are explicitly out of scope for v1 but are worth flagging now so the schema doesn't paint us into corners.

- **Vision‑capable casting agent.** A v2 step that consumes user reference images and writes enriched `canonical_description` fields. The current schema accommodates this: `reference_image_uris` exists, and `canonical_description` is free text.
- **Audio synthesis.** The `audio_intent` block is captured. A v2 audio agent reads it and produces a stereo MP3; the renderer's `ffmpeg` invocation already supports an audio track input (currently unused).
- **Interactive director.** A v2 mode where the director can ask one question of the user before committing. This violates the current "never ask back" rule and would need a different orchestration shape; the schema doesn't change, but the project manager does.
- **Multi‑pass color script generation.** A separate Pixar‑style color script agent that produces per‑scene reference thumbnails (low‑res renders) before scene‑designer runs. This would catch palette drift earlier. Cost‑prohibitive in v1; potentially valuable for premium mode.
- **Storyboard versioning and branching.** `storyboard.yaml.v2`, `storyboard.yaml.experimental`. Useful for A/B comparison. The CLI doesn't currently support it; the workspace layout does.

---

*End of Section 11.*

<!-- END SECTION 11 -->

---

## End of Spec

Read this document first. When in doubt, the spec wins. When the spec is wrong, fix the spec, commit it, then proceed.
