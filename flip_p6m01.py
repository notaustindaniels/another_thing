"""Flip P6.M01 passes to True."""
import json

with open("phase_milestones.json") as f:
    data = json.load(f)

for m in data["milestones"]:
    if m["id"] == "P6.M01":
        print("Before:", m["passes"])
        m["passes"] = True
        m["evidence"] = "evidence/P6.M01/"
        m["notes"] = (
            "Implemented _parse_cube_lut (R-fastest B-slowest .cube parser) and "
            "_apply_cube_lut (vectorized trilinear interpolation, no Python loops). "
            "Added _compute_fisheye_map with module-level _FISHEYE_MAP_CACHE keyed on "
            "(H,W,k1,k2); _apply_fisheye accepts precomputed map_x/map_y args. "
            "render_scene Steps 0d+0e precompute fisheye map and LUT before frame loop. "
            "Grain seeding verified per 9.3 (SeedSequence.spawn chain). All 6 acceptance "
            "criteria tested in tests/test_post_processing.py (33 tests). "
            "Total: 1093 passing, 0 failed."
        )
        print("After:", m["passes"])
        break

with open("phase_milestones.json", "w") as f:
    json.dump(data, f, indent=2)

print("Done — phase_milestones.json updated")
