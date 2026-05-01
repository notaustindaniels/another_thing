"""Debug the storyboard diff logic."""
import yaml
from pathlib import Path
from parallax_engine.director.schema import load_storyboard_yaml
from parallax_engine.manager import _dict_fingerprint

sb_path = Path("tests/storyboards/example_a.yaml")
storyboard = load_storyboard_yaml(sb_path)

# What the current storyboard scene looks like via model_dump
scene = storyboard.scenes[0]
current = scene.model_dump()
print("Current (model_dump) index key:", list(current.keys())[:5])
print("Current scene index:", current.get("index"), current.get("scene_index"))

# What the cached storyboard scene looks like via yaml.safe_load
raw = yaml.safe_load(sb_path.read_text())
cached_scene = raw["scenes"][0]
print("Cached (yaml) keys:", list(cached_scene.keys())[:5])
print("Cached scene index:", cached_scene.get("index"), cached_scene.get("scene_index"))

# Compare fingerprints
fp_current = _dict_fingerprint(current)
fp_cached = _dict_fingerprint(cached_scene)
print("Fingerprints equal:", fp_current == fp_cached)
print("Current fingerprint[:200]:", fp_current[:200])
print("Cached fingerprint[:200]:", fp_cached[:200])
