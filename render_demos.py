"""render_demos.py — Render the three demo reel MP4s for P6.M04.

Renders demo_a.yaml (forest drone), demo_b.yaml (biome pan), demo_c.yaml
(portal transition) into evidence/demo/.

Each demo is at least 6 seconds, demonstrating one of the three core engine
behavior families per §1.3 and §11.4.
"""
from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

REPO_ROOT  = Path(__file__).parent
SCENES_DIR = REPO_ROOT / "tests" / "scenes"
DEMO_DIR   = REPO_ROOT / "evidence" / "demo"

DEMOS = [
    {
        "id": "demo_a",
        "yaml": SCENES_DIR / "demo_a.yaml",
        "family": "drone-FPV",
        "description": "§11.4.1 — 8s drone-FPV forest flythrough, bezier path, bank, fisheye, grain",
    },
    {
        "id": "demo_b",
        "yaml": SCENES_DIR / "demo_b.yaml",
        "family": "biome-wipe",
        "description": "§11.4.2 — 8s keyframed biome pan, rightward parallax depth reveal",
    },
    {
        "id": "demo_c",
        "yaml": SCENES_DIR / "demo_c.yaml",
        "family": "portal",
        "description": "§11.4.3 — 8s portal transition, perspective mask, stack compositing",
    },
]


def main() -> None:
    from parallax_engine.scene import load_scene_yaml
    from parallax_engine.render import render_scene

    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    force = "--force" in sys.argv

    results = []
    for demo in DEMOS:
        out_path = DEMO_DIR / f"{demo['id']}.mp4"
        if out_path.exists() and not force:
            size = out_path.stat().st_size
            print(f"  SKIP  {demo['id']}.mp4 already exists ({size} bytes)")
            results.append({"id": demo["id"], "skipped": True, "path": str(out_path)})
            continue

        print(f"  RENDER  {demo['yaml'].name} → {out_path.name} …", flush=True)
        t0 = time.monotonic()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            scene = load_scene_yaml(demo["yaml"])
            render_scene(scene, SCENES_DIR, out_path)

        elapsed = time.monotonic() - t0
        size = out_path.stat().st_size
        print(f"         done in {elapsed:.1f}s  size={size}  duration={scene.meta.duration_s}s")
        results.append({
            "id": demo["id"],
            "family": demo["family"],
            "description": demo["description"],
            "path": str(out_path),
            "duration_s": scene.meta.duration_s,
            "resolution": list(scene.meta.resolution),
            "fps": scene.meta.fps,
            "mp4_size_bytes": size,
            "render_time_s": round(elapsed, 2),
            "skipped": False,
        })

    print("\n[render_demos] Done. Demo files:")
    all_ok = True
    for r in results:
        status = "SKIP" if r.get("skipped") else "DONE"
        path = Path(r["path"])
        dur = r.get("duration_s", "?")
        print(f"  {status}  {path.name}  duration={dur}s  size={path.stat().st_size}")
        if path.stat().st_size == 0:
            print(f"  ERROR: {path.name} is empty!")
            all_ok = False
        if isinstance(dur, float) and dur < 6.0:
            print(f"  ERROR: {path.name} is under 6 seconds!")
            all_ok = False

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
