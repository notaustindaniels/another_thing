"""
generate_golden_hashes.py — Render all three canonical regression scenes
and store golden SHA-256 hashes in evidence/golden/.

Run once to establish golden hashes; commit the results.
Called from tests/test_regression.py only for re-verification (not re-generation).

Usage:
    python generate_golden_hashes.py [--force]
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import warnings
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent
SCENES_DIR = REPO_ROOT / "tests" / "scenes"
EVIDENCE_DIR = REPO_ROOT / "evidence" / "golden"

CANONICAL_SCENES = [
    {
        "id": "example_a",
        "yaml": SCENES_DIR / "canonical_a.yaml",
        "description": "§11.4.1 — drone-FPV forest flythrough",
        "storyboard": "tests/storyboards/example_a.yaml",
    },
    {
        "id": "example_b",
        "yaml": SCENES_DIR / "canonical_b.yaml",
        "description": "§11.4.2 — keyframed biome pan",
        "storyboard": "tests/storyboards/example_b.yaml",
    },
    {
        "id": "example_c",
        "yaml": SCENES_DIR / "canonical_c.yaml",
        "description": "§11.4.3 — portal transition",
        "storyboard": "tests/storyboards/example_c.yaml",
    },
]


def render_and_hash(scene_info: dict, tmp_dir: Path, force: bool) -> dict:
    """Render a canonical scene; return hash record."""
    from parallax_engine.scene import load_scene_yaml
    from parallax_engine.render import render_scene

    sha256_path = EVIDENCE_DIR / f"{scene_info['id']}.sha256"
    meta_path   = EVIDENCE_DIR / f"{scene_info['id']}_meta.json"

    if sha256_path.exists() and not force:
        existing = sha256_path.read_text().strip()
        print(f"  SKIP  {scene_info['id']}: hash already exists ({existing[:16]}…)")
        return {"id": scene_info["id"], "sha256": existing, "skipped": True}

    yaml_path = scene_info["yaml"]
    print(f"  RENDER  {yaml_path.name} …", flush=True)
    t0 = time.monotonic()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        scene = load_scene_yaml(yaml_path)
        out_mp4 = tmp_dir / f"{scene_info['id']}.mp4"
        render_scene(scene, yaml_path.parent, out_mp4)

    elapsed = time.monotonic() - t0
    mp4_bytes = out_mp4.read_bytes()
    sha256 = hashlib.sha256(mp4_bytes).hexdigest()

    print(f"         done in {elapsed:.1f}s  size={len(mp4_bytes)}  sha256={sha256[:16]}…")

    # Write hash file (plain hex, one line — compatible with `sha256sum -c`)
    sha256_path.write_text(f"{sha256}  {scene_info['id']}.mp4\n")

    # Write metadata JSON
    meta = {
        "id": scene_info["id"],
        "description": scene_info["description"],
        "storyboard": scene_info["storyboard"],
        "scene_yaml": str(yaml_path.relative_to(REPO_ROOT)),
        "resolution": list(scene.meta.resolution),
        "fps": scene.meta.fps,
        "duration_s": scene.meta.duration_s,
        "seed": scene.meta.seed,
        "render_time_s": round(elapsed, 3),
        "mp4_size_bytes": len(mp4_bytes),
        "sha256": sha256,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "spec_anchor": "§7",
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    return {"id": scene_info["id"], "sha256": sha256, "skipped": False,
            "elapsed_s": elapsed, "size_bytes": len(mp4_bytes)}


def main() -> None:
    import tempfile
    force = "--force" in sys.argv

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[generate_golden_hashes] evidence dir: {EVIDENCE_DIR}")
    print(f"[generate_golden_hashes] force={force}")
    print()

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for scene_info in CANONICAL_SCENES:
            rec = render_and_hash(scene_info, tmp_dir, force=force)
            results.append(rec)

    print()
    print("[generate_golden_hashes] Summary:")
    all_ok = True
    for r in results:
        status = "SKIP" if r.get("skipped") else "DONE"
        print(f"  {status}  {r['id']}  sha256={r['sha256'][:16]}…")
        if not r["sha256"]:
            all_ok = False

    # Write manifest
    manifest_path = EVIDENCE_DIR / "manifest.json"
    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scenes": results,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\n[generate_golden_hashes] manifest: {manifest_path}")

    if not all_ok:
        sys.exit(1)
    print("[generate_golden_hashes] All golden hashes stored successfully.")


if __name__ == "__main__":
    main()
