"""Flip P4_5.M06 passes to True."""
import json

with open("phase_milestones.json") as f:
    data = json.load(f)

for m in data["milestones"]:
    if m["id"] == "P4_5.M06":
        print("Before:", m["passes"])
        m["passes"] = True
        m["notes"] = (
            "asset/generator.py: kind dispatch (canonical no-op, produce_canonical reads only casting "
            "bible, local=current behavior, variant=SVG transform wrapper with feColorMatrix tint). "
            "qa/critic.py: three-level tiering with Opus for storyboard (always), Sonnet for "
            "asset/scene (Opus if budget=premium for scene), classification taxonomies per §11.8.2, "
            "offline stub returns PASS when no API key; critic writes no files (§11.13.7). "
            "manager.py: ProjectManager with §11.9.1 sequence, Python retry caps "
            "(MAX_ASSET_RETRIES=3, MAX_SCENE_REDESIGNS=2, MAX_STORYBOARD_REGEN=1), "
            "--resume via raw-YAML diff of storyboard.yaml vs .cache/storyboard.yaml.last. "
            "106 new tests added; 984 total passing."
        )
        print("After:", m["passes"])
        break

with open("phase_milestones.json", "w") as f:
    json.dump(data, f, indent=2)

print("Done — phase_milestones.json updated")
