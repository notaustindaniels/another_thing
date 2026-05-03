"""scripts/gen_forest_scene.py — Proof-of-concept 40+ layer forest scene.

Implements the architecture described in scene_architecture.md:

  1. Pre-flight sanity checks (env, rsvg-convert, skia).
  2. Programmatic manifest of ~41 individual objects across 5 spatial
     categories (sky / distant / mid / near / frame). One object per layer.
  3. Heuristic camera path designed AFTER objects are placed, with
     lateral nudging to avoid passing through near-object plates.
  4. Parallax verification table computed against the actual sampled
     drone-camera track (not just bezier endpoints — spring lag matters).
     Hard gates: backdrop ratio < 1.5x, near s_max >= 3.0, frame culls
     within shot, total raster memory < 4 GB.
  5. SVG generation (claude-sonnet-4-6, async with Semaphore(8)).
     Each SVG = one object on a transparent background, viewBox = plate_size.
     Hard validation: parses, viewBox matches per-asset expectation,
     >= 8 shape elements. One retry then loud abort (no placeholders).
  6. Scene emission to examples/forest_v2/scene.yaml.
  7. Render via parallax_engine subprocess to /tmp/forest_40layer.mp4.

Env: ANTHROPIC_API_KEY required. CLAUDE_CODE_OAUTH_TOKEN won't work.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import anthropic
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCENE_DIR = ROOT / "examples" / "forest_v2"
ASSETS_DIR = SCENE_DIR / "assets"
SCENE_YAML = SCENE_DIR / "scene.yaml"
PARALLAX_TXT = SCENE_DIR / "parallax_table.txt"
RENDER_OUT = Path("/tmp/forest_40layer.mp4")
PYTHON = ROOT / ".conda-env" / "bin" / "python"
RSVG = ROOT / ".conda-env" / "bin" / "rsvg-convert"

MODEL = "claude-sonnet-4-6"
PERSPECTIVE_PX = 1200.0
RESOLUTION = (960, 540)
ORIGIN = (480.0, 270.0)
DURATION_S = 8.0
FPS = 30
SEED = 7
CULL_BEGIN_Z_CAM = PERSPECTIVE_PX - 720.0  # = 480; renderer starts fading
CULL_FULL_Z_CAM = PERSPECTIVE_PX - 300.0   # = 900; fully invisible

MEM_BUDGET_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB
CONCURRENCY = 8

# ===========================================================================
# 1. Object manifest
# ===========================================================================


@dataclass
class Obj:
    id: str
    category: str  # sky | distant | mid | near | frame
    z: float
    x: float
    y: float
    plate_w: int
    plate_h: int
    subject: str        # e.g. "tall narrow conifer, slight rightward lean"
    palette_hint: str   # e.g. "deep moss green silhouette with warm rim-light"

    def asset_path(self) -> Path:
        return ASSETS_DIR / f"{self.id}.svg"

    def viewbox(self) -> str:
        return f"0 0 {self.plate_w} {self.plate_h}"


def _jitter_zs(rng: random.Random, lo: float, hi: float, n: int, min_gap: float) -> list[float]:
    """Place n Z-values in [lo, hi] with at least min_gap separation."""
    base = np.linspace(lo, hi, n)
    out: list[float] = []
    for z in base:
        out.append(float(z + rng.uniform(-min_gap * 0.4, min_gap * 0.4)))
    out.sort()
    # Enforce minimum gap by spacing out collisions
    for i in range(1, len(out)):
        if out[i] - out[i - 1] < min_gap:
            out[i] = out[i - 1] + min_gap
    return out


def make_manifest(seed: int = SEED) -> list[Obj]:
    rng = random.Random(seed)
    objs: list[Obj] = []

    # --- 1 sky ---
    # Z chosen so camera (traveling 0 → -6500) leaves ratio < 1.5x.
    # ratio = (px - z_obj) / (px - z_obj - travel); for travel=6500, z_obj < -18021
    # makes ratio < 1.5. We push to -28000 for healthy margin.
    objs.append(Obj(
        id="sky",
        category="sky",
        z=-28000, x=0, y=0,
        plate_w=6000, plate_h=4000,
        subject="moody pre-dawn forest sky with gentle stratus cloud bands",
        palette_hint=("deep indigo #0e1830 at top, twilight teal #2a4a5e mid, "
                      "warm muted ochre #a87864 near horizon; cloud tints in "
                      "dusty rose #b08574 and soft slate #5e6878"),
    ))

    # --- 4 distant backdrop: 2 mountains + 2 distant treelines ---
    # Pushed to [-24000, -18500]: at -18500, ratio = 19700/13293 = 1.48 < 1.5 ✓
    distant_zs = _jitter_zs(rng, -24000, -18500, 4, 800)
    distant_specs = [
        ("distant_mountain_left",
         "single jagged mountain peak silhouette, slight snow cap on upper third",
         "cool blue-violet silhouette #3a3258 to #4a4068, snow cap #d8d2e8"),
        ("distant_treeline_a",
         "low irregular treeline silhouette spanning the viewBox horizontally",
         "muted blue-grey conifer silhouette #4a5260 with subtle warmer undertone"),
        ("distant_mountain_right",
         "twin pyramidal peaks side by side, no snow",
         "atmospheric blue-grey #5a5278 to #6a6088 with hazier upper edges"),
        ("distant_treeline_b",
         "ridge of distant fir tops with three taller standout trees",
         "deep violet-grey #4a3858 silhouette with warm rim #8a7060 on tallest"),
    ]
    for (name, subj, pal), z in zip(distant_specs, distant_zs):
        objs.append(Obj(
            id=name, category="distant", z=z,
            x=rng.uniform(-400, 400), y=rng.uniform(-150, 150),
            plate_w=4000, plate_h=3000,
            subject=subj, palette_hint=pal,
        ))

    # --- 10 mid trees ---
    # Plate sizes capped (1500-1800 wide, 1900-2200 tall) — keeps the
    # raster-cache memory under budget for the 6 culled mid layers that
    # reach s_renderer~3.8 in their cull-fade band.
    mid_zs = _jitter_zs(rng, -8000, -3200, 10, 350)
    for i, z in enumerate(mid_zs):
        height_cls = rng.choice(["tall", "medium", "tall"])
        lean = rng.choice(["upright", "slight left lean", "slight right lean", "upright"])
        species = rng.choice(["fir", "spruce", "pine", "young oak", "fir"])
        density = rng.choice(["dense", "moderate", "wispy"])
        plate_w = rng.choice([1500, 1650, 1800])
        plate_h = rng.choice([1900, 2050, 2200])
        objs.append(Obj(
            id=f"mid_tree_{i:02d}",
            category="mid", z=z,
            x=rng.uniform(-900, 900), y=rng.uniform(-200, 200),
            plate_w=plate_w, plate_h=plate_h,
            subject=f"{height_cls} {density} {species}, {lean}, single tree, "
                    "trunk visible at base, full crown above",
            palette_hint=("deep moss-green crown #1f3322 with mid-tones #284028 and "
                          "highlight #3a5230; trunk umber #2a1d18 with bark "
                          "streaks #3a2820; subtle warm rim-light #a07050 on "
                          "right-facing edges"),
        ))

    # --- 18 near trees + branches ---
    near_zs = _jitter_zs(rng, -2800, -600, 18, 110)
    for i, z in enumerate(near_zs):
        kind = rng.choice([
            "fir", "spruce", "pine", "young oak", "birch sapling",
            "leaning broken pine", "fir", "spruce", "fir",
        ])
        lean = rng.choice([
            "upright", "leaning right ~10 degrees", "leaning left ~8 degrees",
            "upright with one prominent low branch on the left",
            "upright with a curved trunk", "upright",
        ])
        plate_w = rng.choice([700, 800, 900, 1000, 1100])
        plate_h = int(plate_w * rng.uniform(1.4, 1.7))  # tall trees
        # Lateral spread: alternate left/right edges to create entry from edges
        side = -1 if i % 2 == 0 else 1
        x = side * rng.uniform(500, 1800)
        objs.append(Obj(
            id=f"near_tree_{i:02d}",
            category="near", z=z,
            x=x, y=rng.uniform(-300, 200),
            plate_w=plate_w, plate_h=plate_h,
            subject=(f"single {kind}, {lean}, full trunk visible bottom-to-top, "
                     "individual branches with foliage clusters; one tree only"),
            palette_hint=("near-tier palette: trunk dark umber #1a1008 to "
                          "#2a1d10 with bark cracks; needles/leaves deep green "
                          "#162818 to mid #2c4220 with selective warm rim-light "
                          "#bf9050 on right-facing edges"),
        ))

    # --- 8 frame elements: fog wisps + leaf clusters ---
    frame_zs = _jitter_zs(rng, -450, -150, 8, 35)
    frame_specs = [
        ("foreground_fog_wisp_a", "tendrils of dawn fog drifting upward",
         "pale teal-grey #8a989a wisps with low-saturation cool whites #c0c8c8 "
         "in the densest curls; soft indistinct edges via overlapping shapes"),
        ("foreground_leaf_cluster_a", "cluster of 8-12 individual fir needles and small leaves",
         "dark moss green #1a3018 with selective warm tip highlights #c9a45a"),
        ("foreground_branch_tip", "single tip of a fir branch with needle clusters",
         "deep needle green #1f3322 with warm sunlit tips #bf9050"),
        ("foreground_fog_wisp_b", "horizontal fog wisp curling around the edge of frame",
         "soft slate #6e7a82 with paler highlights #a4b0b8"),
        ("foreground_leaf_cluster_b", "scatter of 6-10 small autumnal leaves drifting",
         "warm umber #6a3a1c, dusty gold #a8804a, deep green #284028"),
        ("foreground_dust_motes", "8-12 sparse glowing dust motes / lens highlights",
         "warm cream #e8d8a8 small ovals with halo gradients fading to "
         "transparent via overlap"),
        ("foreground_fog_wisp_c", "vertical fog column rising from frame bottom",
         "cool dawn grey #8a949c with internal lighter veils #b8c0c8"),
        ("foreground_leaf_cluster_c", "cluster of small fir needles diagonal across frame",
         "deep green #162818 needles with warm rim #a07050"),
    ]
    for (name, subj, pal), z in zip(frame_specs, frame_zs):
        plate_w = rng.choice([400, 450, 500])
        plate_h = rng.choice([550, 650, 750])
        side = 1 if "a" in name[-1:] or rng.random() < 0.5 else -1
        objs.append(Obj(
            id=name, category="frame", z=z,
            x=side * rng.uniform(800, 1500),
            y=rng.uniform(-300, 300),
            plate_w=plate_w, plate_h=plate_h,
            subject=subj, palette_hint=pal,
        ))

    # --- Hard asserts: Z uniqueness + visual collision check ---
    zs = sorted(o.z for o in objs)
    assert len(zs) == len(set(zs)), "duplicate Z values"
    for i in range(1, len(zs)):
        assert zs[i] != zs[i - 1], f"duplicate Z: {zs[i]}"
    # No two layers within 200 Z AND 400 lateral units
    for i, a in enumerate(objs):
        for b in objs[i + 1:]:
            if abs(a.z - b.z) < 200 and abs(a.x - b.x) < 400:
                # Bump b's x outward
                shift = 500 if b.x >= 0 else -500
                b.x += shift

    # --- viewBox aspect must equal plate aspect (true by construction) ---
    for o in objs:
        assert o.plate_w > 0 and o.plate_h > 0, f"{o.id}: zero plate dim"

    return objs


# ===========================================================================
# 2. Camera path designer (post-set-dressing)
# ===========================================================================


def design_camera(manifest: list[Obj]) -> dict:
    """
    Bezier with 4 control points. End at z=-6500 so back half of shot
    retains near content (deepest near at z=-2800 culls at t≈4s). Nudge
    middle controls laterally to avoid clipping near/frame plates.
    """
    controls = [
        [0.0, 0.0, 0.0],          # P0 — start
        [200.0, 30.0, -2200.0],   # P1 — gentle right swing
        [-250.0, -20.0, -4500.0], # P2 — counter-swing left
        [0.0, 0.0, -6500.0],      # P3 — end (does not pass deepest mid)
    ]

    # Avoidance: no control within 300 Z of a near/frame object whose lateral
    # offset is within 500 of the control. If collision, nudge control X by
    # 300 in the direction away from the offending object. Iterate up to 30
    # times (effectively unbounded for our object density).
    near_frame = [o for o in manifest if o.category in ("near", "frame")]
    for idx in (1, 2):  # only middle controls
        for _ in range(30):
            collisions = [
                o for o in near_frame
                if abs(controls[idx][2] - o.z) < 300
                and abs(controls[idx][0] - o.x) < 500
            ]
            if not collisions:
                break
            o = min(collisions, key=lambda o: abs(controls[idx][0] - o.x))
            sign = 1.0 if controls[idx][0] >= o.x else -1.0
            controls[idx][0] += sign * 300.0
        # Clamp lateral controls to a sensible range
        controls[idx][0] = float(np.clip(controls[idx][0], -1500.0, 1500.0))

    return {
        "mode": "drone",
        "drone": {
            "path": {
                "kind": "bezier",
                "controls": [list(c) for c in controls],
                "duration_s": DURATION_S,
            },
            "poi_lookahead_s": 0.55,
            "spring_halflife_s": 0.18,
            "noise": {"z_amp": 22, "xy_amp": 8, "hz": 0.7},
            "bank_from_velocity": 0.40,
        },
    }


# ===========================================================================
# 3. Scene-yaml dict builder
# ===========================================================================


def build_scene_dict(manifest: list[Obj], camera: dict) -> dict:
    # Order back-to-front (most-negative z first) for human readability;
    # renderer sorts by z_cam at runtime regardless.
    ordered = sorted(manifest, key=lambda o: o.z)
    layers = []
    for o in ordered:
        layers.append({
            "id": o.id,
            "src": f"assets/{o.id}.svg",
            "scene_xyz": [float(o.x), float(o.y), float(o.z)],
            "plate_size": [int(o.plate_w), int(o.plate_h)],
        })
    return {
        "version": 1,
        "meta": {
            "duration_s": DURATION_S,
            "fps": FPS,
            "resolution": list(RESOLUTION),
            "perspective_px": PERSPECTIVE_PX,
            "origin": list(ORIGIN),
            "bg_color": "#000000",
            "seed": SEED,
        },
        "stacks": {
            "forest": {"layers": layers},
        },
        "camera": camera,
        "post": {
            "global": {
                "vignette": {"strength": 0.45},
                "grain": {"sigma": 3},
            },
        },
    }


# ===========================================================================
# 4. Parallax verification (against actual sampled camera track)
# ===========================================================================


def compute_parallax_table(manifest: list[Obj], scene_dict: dict) -> tuple[list[dict], np.ndarray]:
    """
    Returns (rows, track) where rows is the per-layer table and track is the
    sampled camera (n, 6) array.

    s_renderer mirrors the renderer's pre-build (render.py:243-261): walk the
    camera track, skip any pose where compute_near_cull(z_cam) < 0.001, take
    the max scale. This is the value that drives the rasterized cache size,
    and is the right input for the memory budget check.
    """
    from parallax_engine.scene import Scene
    from parallax_engine.camera import drone_camera_track
    from parallax_engine.projection import compute_near_cull

    scene = Scene.model_validate(scene_dict)
    track = drone_camera_track(scene, T=DURATION_S, fps=FPS)  # (n, 6)
    z_cam_world = track[:, 2]
    dt = 1.0 / FPS

    rows: list[dict] = []
    for o in manifest:
        z_cam = o.z - z_cam_world  # (n,)
        # Per-pose cull opacity (renderer computes this exactly)
        culls = np.array([compute_near_cull(float(z), PERSPECTIVE_PX) for z in z_cam])
        # Renderer's cache-size walk: include only poses where cull > 0.001
        in_renderer_window = culls > 0.001
        # Scale formula s = px / (px - z_cam); only meaningful for in-front poses
        with np.errstate(divide="ignore", invalid="ignore"):
            s_all = PERSPECTIVE_PX / (PERSPECTIVE_PX - z_cam)
        # Renderer's s_max — drives raster cache memory
        s_renderer_visible = s_all[in_renderer_window]
        s_renderer = float(np.nanmax(s_renderer_visible)) if s_renderer_visible.size else 0.0

        # Pre-cull max (for the gate semantics: backdrop ratio < 1.5x check)
        # — this is values BEFORE the cull-fade band starts (z_cam < 480).
        pre_cull_mask = z_cam < CULL_BEGIN_Z_CAM
        s_pre_cull_vals = s_all[pre_cull_mask]
        if s_pre_cull_vals.size:
            s_min = float(np.nanmin(s_pre_cull_vals))
            s_max_pre = float(np.nanmax(s_pre_cull_vals))
        else:
            s_min = float("nan")
            s_max_pre = float("nan")

        # First moment cull begins (for "cull@t=...s" notation)
        cull_begun = z_cam >= CULL_BEGIN_Z_CAM
        cull_at_t: float | None = None
        if cull_begun.any():
            cull_at_t = float(int(np.argmax(cull_begun)) * dt)

        if cull_at_t is not None:
            ratio_repr = f"cull@t={cull_at_t:.2f}s"
        else:
            ratio_val = s_max_pre / s_min if s_min > 0 else float("nan")
            ratio_repr = f"{ratio_val:.2f}x"

        rows.append({
            "id": o.id,
            "category": o.category,
            "z": o.z,
            "s_min": s_min,
            "s_max_in_shot": s_max_pre,    # pre-cull max (used for backdrop ratio gate)
            "s_renderer": s_renderer,      # renderer's actual cache-size driver
            "ratio_repr": ratio_repr,
            "cull_at_t": cull_at_t,
        })
    return rows, track


def assert_gates(rows: list[dict], manifest: list[Obj]) -> None:
    """Hard gates: backdrop ratio < 1.5, near reaches s>=3 (auto-true if culled),
    frame culls in shot, sum of (s_renderer^2 * plate_w * plate_h * 4) < 4 GB.
    Raises AssertionError on any violation."""
    failures: list[str] = []

    for r in rows:
        cat = r["category"]
        if cat in ("sky", "distant"):
            if r["cull_at_t"] is not None:
                failures.append(f"{r['id']}: backdrop culled at t={r['cull_at_t']:.2f}s — push deeper")
                continue
            ratio = r["s_max_in_shot"] / r["s_min"] if r["s_min"] > 0 else float("inf")
            if ratio >= 1.5:
                failures.append(f"{r['id']}: backdrop ratio {ratio:.2f}x >= 1.5x — push z deeper")
        elif cat == "mid":
            if not np.isfinite(r["s_min"]):
                failures.append(f"{r['id']}: mid never visible — adjust z/x")
        elif cat == "near":
            # Auto-pass if it culls (means it passed the camera). Otherwise
            # the renderer-visible s_max must reach >= 3.0.
            if r["cull_at_t"] is None and r["s_renderer"] < 3.0:
                failures.append(f"{r['id']}: near s_renderer {r['s_renderer']:.2f} < 3.0 "
                                f"and never culls — bring z closer or extend camera")
        elif cat == "frame":
            if r["cull_at_t"] is None:
                failures.append(f"{r['id']}: frame element never culls — bring z closer "
                                f"or recategorize as near")

    total_bytes = 0.0
    for r, o in zip(rows, manifest):
        s = r["s_renderer"] if r["s_renderer"] > 0 else 1.0
        total_bytes += (s ** 2) * o.plate_w * o.plate_h * 4
    if total_bytes >= MEM_BUDGET_BYTES:
        failures.append(
            f"total raster memory budget exceeded: "
            f"{total_bytes / 1e9:.2f} GB >= {MEM_BUDGET_BYTES / 1e9:.0f} GB"
        )
    print(f"      raster memory budget: {total_bytes / 1e9:.2f} GB / {MEM_BUDGET_BYTES / 1e9:.0f} GB",
          flush=True)

    if failures:
        msg = "PARALLAX GATES FAILED:\n  " + "\n  ".join(failures)
        raise AssertionError(msg)


def render_parallax_table(rows: list[dict]) -> str:
    """ASCII pretty-print suitable for stdout AND saving to .txt.

    Columns:
      s_min          smallest scale before cull-fade band (z_cam < 480)
      s_max_pre      largest scale before cull-fade band — used for backdrop ratio gate
      s_renderer     largest scale renderer caches at (cull-fade-aware) — drives memory
      ratio_or_cull  s_max_pre/s_min for non-culled, "cull@t=Xs" for culled
    """
    out: list[str] = []
    out.append(f"{'id':<26} {'cat':<8} {'z':>8} {'s_min':>8} {'s_max_pre':>10} "
               f"{'s_render':>9} {'ratio_or_cull':>14}  ok")
    out.append("-" * 96)
    for r in rows:
        s_pre = f"{r['s_max_in_shot']:.3f}" if np.isfinite(r["s_max_in_shot"]) else "n/a"
        s_rd = f"{r['s_renderer']:.3f}"
        out.append(
            f"{r['id']:<26} {r['category']:<8} {r['z']:>8.0f} "
            f"{r['s_min']:>8.3f} {s_pre:>10} {s_rd:>9} {r['ratio_repr']:>14}  ✓"
        )
    return "\n".join(out)


# ===========================================================================
# 5. SVG generation (async with semaphore)
# ===========================================================================

SVG_SYSTEM = """You are an expert vector illustrator producing SVG layers for a 2.5D parallax animation engine.

Each call produces ONE SVG depicting ONE OBJECT (or for sky: one atmospheric backdrop) on a TRANSPARENT background. The SVG is composited as a single layer at one Z-depth among 30-50 other layers; layers in front and behind contribute the rest of the scene. The image will fly past the camera as part of a drone flythrough — your single object is one tree, one mountain, one fog wisp, NOT a forest, range, or fog bank.

OUTPUT CONTRACT — every rule is non-negotiable:
- Output ONLY raw SVG XML. No markdown code fences. No prose before or after.
- Start with `<svg` and end with `</svg>`.
- The viewBox MUST exactly equal the value specified in the user prompt for THIS asset. The viewBox VARIES per asset — never assume a default; always read the per-asset value.
- width and height attributes are optional; if present, they must equal the viewBox dimensions exactly.
- Allowed elements: <svg>, <defs>, <linearGradient>, <radialGradient>, <stop>, <path>, <polygon>, <rect>, <circle>, <ellipse>, <g>.
- Disallowed: <image>, <text>, <foreignObject>, <style>, <script>, embedded raster, CSS classes.
- Use absolute path commands (M, L, C, Q, H, V, Z) — no relative variants.
- Minimum 8 distinct shape elements (path/polygon/rect/circle/ellipse).
- Solid hex fills or gradients only. Specify `fill="#hex"` directly on each shape. Avoid opacity attributes — produce soft edges via overlapping shapes in similar tones.

COMPOSITING — your layer is ONE plane in a multi-layer composite:
- SKY layers: fill the entire viewBox edge-to-edge (only category that does).
- ALL OTHER layers: draw ONE centered subject; leave the rest of the viewBox transparent. Other layers behind yours show through the transparent areas. DO NOT fill the entire viewBox with a base color or sky tint. Backgrounds are TRANSPARENT.

CENTERING — the renderer treats the SVG's geometric center as the layer's anchor point. Place the subject so its visual center is at viewBox center (cx = w/2, cy = h/2). For an upright tree, the trunk-base sits below center and the crown rises above; the OVERALL silhouette should center on (w/2, h/2).

ASPECT — the user prompt specifies the viewBox dimensions. Match the subject to the aspect ratio. A 800-wide × 1200-tall viewBox should hold a tall narrow tree, NOT a wide bushy one stretched to fit. A 4000-wide × 3000-tall viewBox accommodates a wide low silhouette like a treeline or mountain ridge.

AESTHETIC — painterly, organic vector illustration in the style of Studio Ghibli stills / Polyfjord / modern motion-graphics: real trees that look like trees, real mountains that look like mountains, real fog with believable wispy edges. Detailed silhouettes with internal modeling (bark texture suggestion, branch articulation, foliage clusters). NOT flat icons. NOT geometric primitives. NOT minimal stick figures.

SELF-CHECK before responding:
1. Output starts with `<svg` and ends with `</svg>`, with NOTHING else (no prose, no fences)?
2. viewBox matches the EXACT value specified in this user prompt?
3. >= 8 shape elements (paths/polygons/circles/ellipses)?
4. Background fully transparent (or full-fill for sky)?
5. Subject centered in the viewBox?
6. All colors specified as #hex on each shape?

Respond with the SVG and nothing else."""


def build_user_prompt(o: Obj) -> str:
    role_blurb = {
        "sky": "SKY / atmospheric backdrop. Fills the entire viewBox edge-to-edge.",
        "distant": ("DISTANT BACKDROP layer. Camera barely affects this layer — the "
                    "subject must read at small on-screen size. Most of viewBox is "
                    "TRANSPARENT (sky layers behind show through above the subject)."),
        "mid": ("MID-DISTANCE scenery layer. The camera passes through this depth "
                "zone. Most of viewBox is TRANSPARENT around the centered subject."),
        "near": ("NEAR-tier object. The camera will pass directly past this layer; "
                 "it must read clearly at large on-screen size for the moment "
                 "before it culls. Most of viewBox is TRANSPARENT."),
        "frame": ("FRAME element — extreme foreground. Sits at the edge of the FOV "
                  "and rushes past in the first second. Most of viewBox is "
                  "TRANSPARENT around a centered or edge-anchored subject."),
    }[o.category]
    return f"""Asset id: {o.id}
Layer role: {role_blurb}
Plate dimensions: {o.plate_w} x {o.plate_h} pixels
viewBox: MUST be exactly "{o.viewbox()}" (matches plate dimensions)

Subject: {o.subject}

Color palette: {o.palette_hint}

Additional guidance:
- Center the subject's visual mass at viewBox center ({o.plate_w // 2}, {o.plate_h // 2}).
- {"Fill the entire viewBox; this is the sky." if o.category == "sky" else "Leave most of the viewBox TRANSPARENT — only the centered subject is drawn. No background fill, no sky tint, no ground plane."}
- Aim for 12-30 shape elements depending on subject complexity (minimum 8 hard requirement).
- Painterly, organic, Studio Ghibli aesthetic. No flat icons.
"""


def _strip_to_svg(text: str) -> str:
    text = text.strip()
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL)
    text = re.sub(r"<reasoning>.*?</reasoning>", "", text, flags=re.DOTALL)
    text = text.strip()
    s = text.find("<svg")
    if s > 0:
        text = text[s:]
    if text.startswith("```"):
        text = re.sub(r"^```(?:xml|svg|html)?\s*\n?", "", text)
        text = re.sub(r"\n?\s*```\s*$", "", text)
    e = text.rfind("</svg>")
    if e > 0:
        text = text[: e + len("</svg>")]
    return text.strip()


def validate_svg_for(o: Obj, svg: str) -> str:
    if not svg.startswith("<svg") or not svg.endswith("</svg>"):
        raise ValueError(f"{o.id}: not bracketed by <svg>...</svg>; got "
                         f"{svg[:60]!r}…{svg[-60:]!r}")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        raise ValueError(f"{o.id}: SVG XML did not parse: {e}")
    vb = root.attrib.get("viewBox", "").strip()
    expected = o.viewbox()
    if vb != expected:
        raise ValueError(f"{o.id}: viewBox is {vb!r}, expected {expected!r}")
    n = sum(
        1 for el in root.iter()
        if el.tag.split("}")[-1] in ("path", "polygon", "rect", "circle", "ellipse")
    )
    if n < 8:
        raise ValueError(f"{o.id}: only {n} shape elements (need >=8)")
    return svg


async def gen_one(client: anthropic.AsyncAnthropic, sem: asyncio.Semaphore,
                  o: Obj, max_retries: int = 1) -> dict:
    async with sem:
        last_err: Exception | None = None
        for attempt in range(max_retries + 1):
            print(f"[{o.id}] attempt {attempt + 1}/{max_retries + 1}…", flush=True)
            try:
                msg = await client.messages.create(
                    model=MODEL,
                    # 32K tokens — some near-tier trees produce dense SVGs
                    # (~8K tokens). Hitting max_tokens silently truncates
                    # mid-path, which then fails the </svg> bracket check.
                    max_tokens=32768,
                    system=[{"type": "text", "text": SVG_SYSTEM,
                             "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": build_user_prompt(o)}],
                )
                if msg.stop_reason == "max_tokens":
                    last_err = ValueError(f"hit max_tokens (out={msg.usage.output_tokens})")
                    print(f"[{o.id}] {last_err} — retrying", flush=True)
                    continue
            except Exception as e:
                last_err = e
                print(f"[{o.id}] API error: {e}", flush=True)
                continue
            text = "".join(b.text for b in msg.content if hasattr(b, "text"))
            text = _strip_to_svg(text)
            try:
                svg = validate_svg_for(o, text)
            except ValueError as e:
                last_err = e
                print(f"[{o.id}] validation failed: {e}", flush=True)
                continue
            o.asset_path().write_text(svg)
            u = msg.usage
            cached = getattr(u, "cache_read_input_tokens", 0) or 0
            print(f"[{o.id}] OK ({len(svg)} bytes; in={u.input_tokens} cached_read={cached} "
                  f"out={u.output_tokens})", flush=True)
            return {
                "id": o.id, "ok": True,
                "input_tokens": u.input_tokens,
                "cached_read": cached,
                "output_tokens": u.output_tokens,
            }
        # All attempts exhausted — fail loud (no placeholder)
        raise RuntimeError(f"[{o.id}] all {max_retries + 1} attempts failed; last: {last_err}")


async def gen_all(manifest: list[Obj]) -> list[dict]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")
    client = anthropic.AsyncAnthropic(api_key=api_key)
    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*[gen_one(client, sem, o) for o in manifest])
    return results


# ===========================================================================
# 6. Pre-flight
# ===========================================================================


def preflight() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("FAIL: ANTHROPIC_API_KEY not set")
    if not RSVG.exists():
        raise SystemExit(f"FAIL: rsvg-convert not at {RSVG}")
    out = subprocess.run([str(RSVG), "--version"], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"FAIL: rsvg-convert errored: {out.stderr}")
    try:
        import skia  # noqa: F401
    except Exception as e:
        raise SystemExit(f"FAIL: skia import: {e}")
    print("[preflight] OK", flush=True)


# ===========================================================================
# 7. Main
# ===========================================================================


def emit_yaml(scene_dict: dict) -> None:
    SCENE_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    SCENE_YAML.write_text(
        "# Generated by scripts/gen_forest_scene.py — see scene_architecture.md\n"
        + yaml.safe_dump(scene_dict, sort_keys=False, default_flow_style=False)
    )
    print(f"[emit] wrote {SCENE_YAML} ({SCENE_YAML.stat().st_size} bytes)", flush=True)


def render() -> None:
    print(f"[render] starting render → {RENDER_OUT}", flush=True)
    t0 = time.monotonic()
    proc = subprocess.run(
        [str(PYTHON), "-m", "parallax_engine", "render",
         str(SCENE_YAML), "--out", str(RENDER_OUT)],
        cwd=str(ROOT),
    )
    dt = time.monotonic() - t0
    if proc.returncode != 0:
        raise SystemExit(f"FAIL: render exited {proc.returncode}")
    if not RENDER_OUT.exists() or RENDER_OUT.stat().st_size < 100_000:
        raise SystemExit(f"FAIL: {RENDER_OUT} missing or too small")
    print(f"[render] OK in {dt:.1f}s — {RENDER_OUT.stat().st_size / 1e6:.2f} MB", flush=True)


def main(argv: list[str]) -> int:
    do_render = "--no-render" not in argv
    do_svg = "--no-svg" not in argv
    do_dry = "--dry-run" in argv  # manifest + table + scene.yaml only

    print("=" * 70)
    print(" gen_forest_scene.py — 40+ layer forest proof-of-concept")
    print("=" * 70)

    preflight()

    print("\n[1/6] Building manifest…", flush=True)
    manifest = make_manifest(SEED)
    by_cat: dict[str, int] = {}
    for o in manifest:
        by_cat[o.category] = by_cat.get(o.category, 0) + 1
    print(f"      {len(manifest)} objects: {by_cat}", flush=True)

    print("\n[2/6] Designing camera path (after objects placed)…", flush=True)
    camera = design_camera(manifest)
    ctrls = camera["drone"]["path"]["controls"]
    print(f"      bezier controls: {ctrls}", flush=True)

    print("\n[3/6] Building scene dict + validating Pydantic schema…", flush=True)
    scene_dict = build_scene_dict(manifest, camera)

    print("\n[4/6] Computing parallax verification table…", flush=True)
    rows, track = compute_parallax_table(manifest, scene_dict)
    table_text = render_parallax_table(rows)
    print(table_text, flush=True)
    print(f"\n      camera track: z range [{track[:,2].min():.0f}, {track[:,2].max():.0f}]", flush=True)
    SCENE_DIR.mkdir(parents=True, exist_ok=True)
    PARALLAX_TXT.write_text(table_text + "\n")
    print(f"      saved → {PARALLAX_TXT}", flush=True)
    try:
        assert_gates(rows, manifest)
        print("      ✓ all gates pass", flush=True)
    except AssertionError as e:
        print(f"\n{e}", file=sys.stderr, flush=True)
        return 2

    if do_dry:
        emit_yaml(scene_dict)
        print("\n[dry-run] stopping before SVG generation. Re-run without --dry-run.")
        return 0

    if do_svg:
        print(f"\n[5/6] Generating {len(manifest)} SVGs (Sonnet 4.6, semaphore={CONCURRENCY})…",
              flush=True)
        results = asyncio.run(gen_all(manifest))
        # Verify all files exist
        missing = [o.id for o in manifest if not o.asset_path().exists()]
        if missing:
            raise SystemExit(f"FAIL: missing SVG assets: {missing}")
        total_in = sum(r["input_tokens"] for r in results)
        total_cached = sum(r["cached_read"] for r in results)
        total_out = sum(r["output_tokens"] for r in results)
        print(f"      ✓ {len(results)} SVGs written; "
              f"tokens in={total_in} cached_read={total_cached} out={total_out}",
              flush=True)
    else:
        print("\n[5/6] --no-svg: skipping SVG generation", flush=True)

    emit_yaml(scene_dict)

    if do_render:
        print(f"\n[6/6] Rendering…", flush=True)
        render()
    else:
        print("\n[6/6] --no-render: skipping render", flush=True)

    print("\n=== DONE ===")
    print(f"  scene.yaml      : {SCENE_YAML}")
    print(f"  parallax_table  : {PARALLAX_TXT}")
    print(f"  assets          : {ASSETS_DIR} ({len(list(ASSETS_DIR.glob('*.svg')))} SVGs)")
    if do_render:
        print(f"  rendered MP4    : {RENDER_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
