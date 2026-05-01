"""parallax_engine.scene — scene schema models + scene-designer agent.

This package consolidates what was previously ``parallax_engine/scene.py``
(the renderer's Pydantic schema) with the new director-era scene-designer
module (P4_5.M04).

Renderer schema re-exports (§2.2 — backward-compatible with all existing code)
---------------------------------------------------------------------------
Scene, SceneMeta, StackSpec, LayerSpec, CameraSpec, MaskSpec, MaskGrowth,
PostSpec, SceneVersionError, load_scene_yaml, load_scene_dict, dump_scene_yaml

Scene-designer re-exports (§11.6)
---------------------------------------------------------------------------
SceneDesigner, SceneDesignerStub, SceneFragment, ManifestEntry,
SceneDesignerOutput
"""
from __future__ import annotations

# ── Renderer schema (previously parallax_engine/scene.py) ──────────────────
from parallax_engine.scene._renderer import (
    BezierPath,
    CameraSpec,
    ColorGradeSpec,
    DroneCamera,
    DroneNoise,
    EasingName,
    FisheyeSpec,
    GlobalPost,
    GrainSpec,
    GrowthKind,
    KeyframedEntry,
    LayerPost,
    LayerSpec,
    LightLeaksSpec,
    MaskAnchor,
    MaskGrowth,
    MaskSpec,
    MatteMode,
    PostSpec,
    Scene,
    SceneMeta,
    SceneVersionError,
    StackSpec,
    VignetteSpec,
    _validate_raw,
    dump_scene_yaml,
    load_scene_dict,
    load_scene_yaml,
)

# ── Scene-designer (§11.6, P4_5.M04) ───────────────────────────────────────
from parallax_engine.scene.designer import (
    ManifestEntry,
    SceneDesigner,
    SceneDesignerOutput,
    SceneDesignerStub,
    SceneFragment,
)

# ── Scene-merger (§11.6.5, P4_5.M05) ────────────────────────────────────────
from parallax_engine.scene.merger import (
    MergeError,
    merge,
)

__all__ = [
    # Renderer schema
    "BezierPath",
    "CameraSpec",
    "ColorGradeSpec",
    "DroneCamera",
    "DroneNoise",
    "EasingName",
    "FisheyeSpec",
    "GlobalPost",
    "GrainSpec",
    "GrowthKind",
    "KeyframedEntry",
    "LayerPost",
    "LayerSpec",
    "LightLeaksSpec",
    "MaskAnchor",
    "MaskGrowth",
    "MaskSpec",
    "MatteMode",
    "PostSpec",
    "Scene",
    "SceneMeta",
    "SceneVersionError",
    "StackSpec",
    "VignetteSpec",
    "dump_scene_yaml",
    "load_scene_dict",
    "load_scene_yaml",
    # Scene-designer
    "ManifestEntry",
    "SceneDesigner",
    "SceneDesignerOutput",
    "SceneDesignerStub",
    "SceneFragment",
    # Scene-merger
    "MergeError",
    "merge",
]
