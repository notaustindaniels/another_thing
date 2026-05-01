"""parallax_engine.director — Phase 4.5 Director tier.

Exports the Storyboard schema, prompt builder, and agent runner.
"""
from __future__ import annotations

from parallax_engine.director.schema import (
    Arc,
    AudioIntent,
    AudioPerScene,
    Beat,
    CastingEntry,
    Continuity,
    LookReference,
    MotifEvolution,
    MotifStage,
    Palette,
    PaletteOverride,
    PaletteProgEntry,
    PaletteLocked,
    ProjectMeta,
    SceneEntry,
    Storyboard,
    TransitionSpec,
    VisualVocabulary,
    load_storyboard,
    load_storyboard_yaml,
)
from parallax_engine.director.prompt import (
    DirectorBrief,
    director_mode,
    build_single_mode_system_blocks,
    build_single_mode_messages,
    build_decomposed_system_prompts,
    build_decomposed_user_messages,
    extract_yaml_block,
)
from parallax_engine.director.agent import (
    DirectorAgent,
    DirectorResult,
    DirectorStub,
)

__all__ = [
    # schema
    "Arc",
    "AudioIntent",
    "AudioPerScene",
    "Beat",
    "CastingEntry",
    "Continuity",
    "LookReference",
    "MotifEvolution",
    "MotifStage",
    "Palette",
    "PaletteOverride",
    "PaletteProgEntry",
    "PaletteLocked",
    "ProjectMeta",
    "SceneEntry",
    "Storyboard",
    "TransitionSpec",
    "VisualVocabulary",
    "load_storyboard",
    "load_storyboard_yaml",
    # prompt
    "DirectorBrief",
    "director_mode",
    "build_single_mode_system_blocks",
    "build_single_mode_messages",
    "build_decomposed_system_prompts",
    "build_decomposed_user_messages",
    "extract_yaml_block",
    # agent
    "DirectorAgent",
    "DirectorResult",
    "DirectorStub",
]
