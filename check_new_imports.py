"""Check all new module imports."""
from parallax_engine.asset.generator import generate, MAX_ASSET_RETRIES_PER_ASSET
from parallax_engine.qa.critic import (
    critique, CritiqueResult,
    SCENE_CLASSIFICATIONS, STORYBOARD_CLASSIFICATIONS,
    MODEL_SONNET, MODEL_OPUS,
)
from parallax_engine.manager import (
    ProjectManager,
    MAX_ASSET_RETRIES_PER_ASSET as MGR_ASSET,
    MAX_SCENE_REDESIGNS_PER_SCENE,
    MAX_STORYBOARD_REGENERATIONS,
    director_mode,
)
print("All imports OK")
print("MAX_ASSET_RETRIES_PER_ASSET =", MAX_ASSET_RETRIES_PER_ASSET)
print("MAX_SCENE_REDESIGNS_PER_SCENE =", MAX_SCENE_REDESIGNS_PER_SCENE)
print("MAX_STORYBOARD_REGENERATIONS =", MAX_STORYBOARD_REGENERATIONS)
print("SCENE_CLASSIFICATIONS =", sorted(SCENE_CLASSIFICATIONS))
print("STORYBOARD_CLASSIFICATIONS =", sorted(STORYBOARD_CLASSIFICATIONS))
print("MODEL_OPUS =", MODEL_OPUS)
print("MODEL_SONNET =", MODEL_SONNET)
