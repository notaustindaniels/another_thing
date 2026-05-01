"""parallax_engine.director.schema — Pydantic v2 storyboard models.

Implements the full Storyboard schema per SPEC.md §11.3.  The schema covers all
sub-schemas: VisualVocabulary, Arc, Beat, CastingEntry, SceneEntry, TransitionSpec,
Continuity, AudioIntent.

Usage::

    from parallax_engine.director.schema import Storyboard, load_storyboard_yaml

    sb = load_storyboard_yaml(Path("workspace/storyboard.yaml"))
    json_schema = Storyboard.model_json_schema()

Determinism: all dict traversals are sorted; no random state is introduced here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Shared type aliases
# ---------------------------------------------------------------------------

HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{3,8}$")]


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class LookReference(BaseModel):
    """A named visual style reference (§11.3.2)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Named visual style, e.g. 'Studio Ghibli pastoral'")
    note: str = Field(description="One sentence describing relevance")


class Palette(BaseModel):
    """Color palette block inside VisualVocabulary (§11.3.2)."""

    model_config = ConfigDict(extra="forbid")

    primary: list[str] = Field(
        min_length=2,
        max_length=4,
        description="2–4 dominant hex color tones",
    )
    secondary: list[str] = Field(
        min_length=1,
        max_length=3,
        description="1–3 accent hex colors",
    )
    neutrals: list[str] = Field(
        min_length=1,
        max_length=4,
        description="1–4 hex colors used for text/whitespace",
    )
    forbidden: list[str] | None = Field(
        default=None,
        description="Colors that must NEVER appear",
    )


class VisualVocabulary(BaseModel):
    """Visual grammar of the piece — the 'color script in YAML' (§11.3.2)."""

    model_config = ConfigDict(extra="forbid")

    palette: Palette
    palette_progression: Literal[
        "static", "warming", "cooling", "desaturating", "saturating", "custom_described"
    ]
    palette_progression_note: str | None = Field(
        default=None,
        description="Required when palette_progression == 'custom_described'",
    )
    shape_language: Literal["angular", "rounded", "organic", "geometric", "mixed"]
    shape_language_note: str | None = Field(
        default=None,
        max_length=200,
        description="Optional clarification of shape language, ≤ 200 chars",
    )
    density_curve: Literal[
        "sparse",
        "medium",
        "steady",           # used in §11.4.2 Example B; treated as alias for medium
        "dense",
        "rising",
        "falling",
        "rising_then_falling",
    ]
    lighting_mood: Literal[
        "high_key", "low_key", "natural", "neon", "golden_hour", "overcast",
        "chiaroscuro", "flat",
    ]
    time_of_day: Literal[
        "dawn", "morning", "noon", "afternoon", "dusk", "night", "abstract"
    ]
    weather: Literal[
        "clear", "overcast", "rain", "snow", "fog", "wind", "none_specified"
    ] | None = Field(default=None)
    look_references: list[LookReference] | None = Field(default=None)
    reference_image_uris: list[str] | None = Field(
        default=None,
        description="Paths under workspace/references/; URIs only, never image bytes",
    )

    @model_validator(mode="after")
    def _require_progression_note(self) -> "VisualVocabulary":
        if (
            self.palette_progression == "custom_described"
            and not self.palette_progression_note
        ):
            raise ValueError(
                "palette_progression_note is required when "
                "palette_progression == 'custom_described'"
            )
        return self


# ---------------------------------------------------------------------------
# Beat function enum
# Note: SPEC §11.3.3 lists 'turning_point'; Example B uses 'turning'.
# Both are accepted per the canonical examples (SPEC §11.4).  See SPEC AMBIGUITY
# documented in claude-progress.txt.
# ---------------------------------------------------------------------------

BeatFunction = Literal[
    "hook",
    "establish",
    "rising",
    "turning_point",
    "turning",          # used in §11.4.2 Example B; treated as alias for turning_point
    "midpoint",
    "escalation",
    "climax",
    "release",
    "coda",
]


class Beat(BaseModel):
    """A dramatic beat within the arc (§11.3.3)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="snake_case unique id")
    label: str = Field(max_length=40)
    function: BeatFunction
    narrative_note: str = Field(description="One sentence: what this beat does")
    target_time_s: float = Field(ge=0.0, description="When this beat lands (seconds from start)")
    target_scenes: list[int] = Field(
        min_length=1,
        description="1-based scene indices this beat covers",
    )


class Arc(BaseModel):
    """Dramatic structure (§11.3.3)."""

    model_config = ConfigDict(extra="forbid")

    structure: Literal[
        "three_act",
        "save_the_cat",
        "establish_disrupt_resolve",
        "biome_tour",
        "portal_reveal",
        "custom",
    ]
    structure_note: str | None = Field(
        default=None,
        max_length=200,
        description="Required when structure == 'custom'",
    )
    beats: list[Beat] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def _require_structure_note(self) -> "Arc":
        if self.structure == "custom" and not self.structure_note:
            raise ValueError("structure_note is required when structure == 'custom'")
        return self


# ---------------------------------------------------------------------------
# PaletteLocked — flexible mapping of named colors + forbidden list (§11.3.4)
# Uses extra='allow' because color key names (body, accent, wing, …) are
# open-ended per the spec.
# ---------------------------------------------------------------------------


class PaletteLocked(BaseModel):
    """Locked color constraints for a cast member (§11.3.4)."""

    model_config = ConfigDict(extra="allow")

    forbidden: list[str] | None = Field(
        default=None,
        description="Hex colors that may NOT appear on this cast member",
    )


class CastingEntry(BaseModel):
    """A recurring visual element in the casting bible (§11.3.4)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="snake_case unique id within project")
    kind: Literal["character", "prop", "motif", "environment_element"]
    canonical_description: str = Field(
        description="2–4 self-contained sentences; fully describes the asset",
    )
    role_in_story: str = Field(description="One sentence role")
    palette_locked: PaletteLocked | None = Field(default=None)
    allowed_variations: list[str] | None = Field(default=None)
    forbidden_changes: list[str] | None = Field(default=None)
    canonical_svg: str | None = Field(
        default=None,
        description="null at director time; filled by asset-generator on first production",
    )
    first_appearance_scene: int = Field(ge=1, description="1-based scene index")
    appearance_evolution: str | None = Field(
        default=None,
        description="One sentence describing how the cast member evolves",
    )


# ---------------------------------------------------------------------------
# TransitionSpec (§11.3.5.2)
# ---------------------------------------------------------------------------


TransitionType = Literal[
    "hard_cut",
    "fade_from_black",
    "fade_from_white",
    "dissolve",
    "biome_wipe",
    "portal_reveal",
    "iris_in",
    "iris_out",
    "match_cut",
    "j_cut",
    "l_cut",
]


class TransitionSpec(BaseModel):
    """A transition_in or transition_out specification (§11.3.5.2)."""

    model_config = ConfigDict(extra="forbid")

    type: TransitionType
    duration_s: float | None = Field(
        default=None,
        ge=0.1,
        le=2.0,
        description="Required for non-cut transitions; 0.1 ≤ x ≤ 2.0",
    )
    emotional_intent: str | None = Field(
        default=None,
        max_length=80,
        description="One phrase describing the emotional quality of the transition",
    )
    paired_scene: int | None = Field(
        default=None,
        ge=1,
        description="1-based scene index this transition is paired with",
    )

    @model_validator(mode="after")
    def _require_duration_for_non_cuts(self) -> "TransitionSpec":
        no_duration_types: set[str] = {"hard_cut"}
        if self.type not in no_duration_types and self.duration_s is None:
            raise ValueError(
                f"duration_s is required for transition type '{self.type}'"
            )
        return self


# ---------------------------------------------------------------------------
# PaletteOverride (§11.3.5)
# ---------------------------------------------------------------------------


class PaletteOverride(BaseModel):
    """Per-scene palette override (§11.3.5)."""

    model_config = ConfigDict(extra="forbid")

    primary: list[str] | None = Field(default=None)
    secondary: list[str] | None = Field(default=None)
    reason: str = Field(description="Required: why this scene overrides the palette")


# ---------------------------------------------------------------------------
# CameraIntent enum (§11.3.5.1)
# ---------------------------------------------------------------------------

CameraIntent = Literal[
    "drone_fpv_forward",
    "drone_fpv_orbit",
    "drone_fpv_pullout",
    "cinematic_pan_left",
    "cinematic_pan_right",
    "cinematic_truck_in",
    "cinematic_pullback",
    "static_held",
    "iris_in",
    "iris_out",
    "match_cut_setup",
    "portal_reveal",
    "biome_wipe_donor",
    "biome_wipe_receiver",
]

EmotionalFunction = Literal[
    "establishing",
    "rising",
    "escalation",
    "reveal",
    "turning",
    "climax",
    "release",
    "denouement",
    "coda",
    "bridge",
]

Pacing = Literal["slow_burn", "steady", "rising", "rapid", "held", "breath"]


class SceneEntry(BaseModel):
    """One scene in the storyboard's scene list (§11.3.5)."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1, description="1-based, dense, no gaps")
    duration_s: float = Field(gt=0.0)
    logline: str = Field(max_length=160)
    emotional_function: EmotionalFunction
    camera_intent: CameraIntent
    transition_in: TransitionSpec
    transition_out: TransitionSpec | None = Field(
        default=None,
        description="Required on all scenes except the last",
    )
    must_feature: list[str] | None = Field(
        default=None,
        description="Casting ids that must appear in this scene",
    )
    must_avoid: list[str] | None = Field(
        default=None,
        description="Casting ids that must NOT appear in this scene",
    )
    motif_features: list[str] | None = Field(
        default=None,
        description="Non-cast motifs: 'rain on glass', 'long shadow', etc.",
    )
    pacing: Pacing
    palette_override: PaletteOverride | None = Field(default=None)
    callback_to_scene: int | None = Field(
        default=None,
        ge=1,
        description="1-based scene index this scene visually echoes",
    )
    director_notes: str | None = Field(
        default=None,
        max_length=600,
    )


# ---------------------------------------------------------------------------
# Continuity (§11.3.6)
# ---------------------------------------------------------------------------


class PaletteProgEntry(BaseModel):
    """Explicit per-scene palette progression entry (§11.3.6)."""

    model_config = ConfigDict(extra="forbid")

    scene_index: int = Field(ge=1)
    palette_shift: str


class MotifStage(BaseModel):
    """One stage of a motif evolution arc (§11.3.6)."""

    model_config = ConfigDict(extra="forbid")

    scenes: list[int] = Field(min_length=1)
    state: str


class MotifEvolution(BaseModel):
    """Evolution of a single cast member across scenes (§11.3.6)."""

    model_config = ConfigDict(extra="forbid")

    cast_id: str
    stages: list[MotifStage] = Field(min_length=1)


class Continuity(BaseModel):
    """Global continuity rules (§11.3.6)."""

    model_config = ConfigDict(extra="forbid")

    palette_progression_explicit: list[PaletteProgEntry] | None = Field(default=None)
    motif_evolution: list[MotifEvolution] | None = Field(default=None)
    callbacks: list[int] | None = Field(default=None)
    hard_rules: list[str] | None = Field(default=None)


# ---------------------------------------------------------------------------
# AudioIntent (§11.3.7)
# ---------------------------------------------------------------------------


class AudioPerScene(BaseModel):
    """Per-scene audio mood note (§11.3.7)."""

    model_config = ConfigDict(extra="forbid")

    scene_index: int = Field(ge=1)
    mood: str


class AudioIntent(BaseModel):
    """Audio intent — captured but not generated in v1 (§11.3.7)."""

    model_config = ConfigDict(extra="forbid")

    music_arc: Literal[
        "ambient", "building", "sparse", "dense", "silent", "call_response"
    ] | None = Field(default=None)
    per_scene: list[AudioPerScene] | None = Field(default=None)
    sfx_notes: list[str] | None = Field(default=None)
    vo_intent: Literal[
        "none", "sparse_narration", "dialogue", "textless"
    ] | None = Field(default=None)


# ---------------------------------------------------------------------------
# ProjectMeta (§11.3.1)
# ---------------------------------------------------------------------------


class ProjectMeta(BaseModel):
    """Top-level project metadata block (§11.3.1)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=80)
    logline: str = Field(max_length=200)
    theme: str = Field(description="1–3 words, e.g. 'wonder', 'loneliness'")
    summary: str = Field(
        description="1 paragraph, 60–400 words",
    )
    intended_platform: Literal[
        "tiktok", "reels", "youtube_short", "square_social", "wide_landscape", "custom"
    ]
    aspect_ratio: Literal["9:16", "1:1", "16:9", "4:5", "2.39:1"]
    target_fps: Literal[24, 30, 60]
    total_duration_s: float = Field(gt=0.0, le=300.0)
    audience_note: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# Top-level Storyboard (§11.3.1)
# ---------------------------------------------------------------------------

_BIOME_TRANSITION_TYPES: frozenset[str] = frozenset({"biome_wipe"})
_PORTAL_TRANSITION_TYPES: frozenset[str] = frozenset({"portal_reveal"})
_PAIRED_TRANSITION_TYPES: frozenset[str] = _BIOME_TRANSITION_TYPES | _PORTAL_TRANSITION_TYPES


class Storyboard(BaseModel):
    """Full storyboard artifact (§11.3.1).

    Written by the Director agent; consumed read-only by Scene Designers.
    Validated by Pydantic on every read.
    """

    model_config = ConfigDict(extra="forbid")

    storyboard_version: str = Field(
        description="Semver of this schema, e.g. '1.0'",
    )
    project: ProjectMeta
    visual_vocabulary: VisualVocabulary
    arc: Arc
    casting: list[CastingEntry] = Field(default_factory=list)
    scenes: list[SceneEntry] = Field(min_length=1)
    continuity: Continuity | None = Field(default=None)
    audio_intent: AudioIntent | None = Field(default=None)
    director_notes: str | None = Field(
        default=None,
        max_length=2000,
    )

    # ------------------------------------------------------------------
    # Cross-field validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_scene_indices_dense(self) -> "Storyboard":
        """Scene indices must be 1-based and contiguous (no gaps)."""
        indices = [s.index for s in self.scenes]
        expected = list(range(1, len(self.scenes) + 1))
        if sorted(indices) != expected:
            raise ValueError(
                f"Scene indices must be 1-based and dense (no gaps). "
                f"Got {sorted(indices)}, expected {expected}"
            )
        return self

    @model_validator(mode="after")
    def _validate_transition_pairing(self) -> "Storyboard":
        """Enforce transition pairing rules (§11.3.5.2, §11.14 anti-pattern 11).

        Rules:
        - biome_wipe out of scene N ↔ biome_wipe into scene N+1 with paired_scene=N
        - portal_reveal out of scene N ↔ portal_reveal into scene N+1 with paired_scene=N
        """
        by_index: dict[int, SceneEntry] = {s.index: s for s in self.scenes}
        errors: list[str] = []

        for scene in self.scenes:
            tout = scene.transition_out
            if tout is None:
                continue  # last scene; no outgoing transition
            if tout.type not in _PAIRED_TRANSITION_TYPES:
                continue  # no pairing requirement for this type

            # Expect scene index+1 to exist and have a matching transition_in
            next_idx = scene.index + 1
            if next_idx not in by_index:
                errors.append(
                    f"Scene {scene.index} has transition_out.type='{tout.type}' "
                    f"but scene {next_idx} does not exist"
                )
                continue

            next_scene = by_index[next_idx]
            tin = next_scene.transition_in

            if tin.type != tout.type:
                errors.append(
                    f"Scene {scene.index} transition_out.type='{tout.type}' but "
                    f"scene {next_idx} transition_in.type='{tin.type}' — must match"
                )

            # paired_scene on scene N's transition_out should point to N+1
            if tout.paired_scene is not None and tout.paired_scene != next_idx:
                errors.append(
                    f"Scene {scene.index} transition_out.paired_scene={tout.paired_scene} "
                    f"but should point to the next scene ({next_idx})"
                )

            # paired_scene on scene N+1's transition_in should point back to N
            if tin.paired_scene is not None and tin.paired_scene != scene.index:
                errors.append(
                    f"Scene {next_idx} transition_in.paired_scene={tin.paired_scene} "
                    f"but should point back to scene {scene.index}"
                )

        if errors:
            raise ValueError("Transition pairing violations:\n" + "\n".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_callback_scene_refs(self) -> "Storyboard":
        """callback_to_scene must refer to a prior scene index (§11.3.5)."""
        scene_indices = {s.index for s in self.scenes}
        errors: list[str] = []
        for scene in self.scenes:
            cb = scene.callback_to_scene
            if cb is None:
                continue
            if cb >= scene.index:
                errors.append(
                    f"Scene {scene.index} callback_to_scene={cb} must be < {scene.index}"
                )
            if cb not in scene_indices:
                errors.append(
                    f"Scene {scene.index} callback_to_scene={cb} references "
                    f"non-existent scene"
                )
        if errors:
            raise ValueError("callback_to_scene violations:\n" + "\n".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_casting_refs_exist(self) -> "Storyboard":
        """must_feature / must_avoid must reference valid casting ids."""
        cast_ids = {c.id for c in self.casting}
        errors: list[str] = []
        for scene in self.scenes:
            for cid in scene.must_feature or []:
                if cid not in cast_ids:
                    errors.append(
                        f"Scene {scene.index} must_feature references unknown "
                        f"casting id '{cid}'"
                    )
            for cid in scene.must_avoid or []:
                if cid not in cast_ids:
                    errors.append(
                        f"Scene {scene.index} must_avoid references unknown "
                        f"casting id '{cid}'"
                    )
        if errors:
            raise ValueError("Casting reference violations:\n" + "\n".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_last_scene_no_transition_out(self) -> "Storyboard":
        """The last scene should not have a transition_out in most cases.

        This is a warning-level check rather than hard error, because the spec
        says 'omitted on last scene' (§11.3.5).  We emit a ValueError only when
        the last scene explicitly sets transition_out to a non-None value, which
        is almost certainly a mistake.
        """
        if not self.scenes:
            return self
        # Sort by index to find the true last scene
        last = max(self.scenes, key=lambda s: s.index)
        if last.transition_out is not None:
            # Only raise for paired transitions — hard_cut or dissolve might be
            # intentional for looping videos.  biome_wipe/portal_reveal need a
            # partner that doesn't exist.
            if last.transition_out.type in _PAIRED_TRANSITION_TYPES:
                raise ValueError(
                    f"Last scene ({last.index}) has transition_out.type="
                    f"'{last.transition_out.type}' which requires a paired next scene, "
                    f"but there is no scene {last.index + 1}"
                )
        return self

    # ------------------------------------------------------------------
    # Convenience API
    # ------------------------------------------------------------------

    def to_json_schema(self) -> dict[str, Any]:
        """Return the JSON Schema dict for this model."""
        return self.model_json_schema()

    def save_json_schema(self, path: Path) -> None:
        """Write JSON Schema to *path*."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.model_json_schema(), fh, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def load_storyboard_yaml(path: Path | str) -> Storyboard:
    """Parse a ``storyboard.yaml`` file and validate against the schema.

    Args:
        path: Path to the YAML file.

    Returns:
        A validated :class:`Storyboard` instance.

    Raises:
        FileNotFoundError: if the file does not exist.
        pydantic.ValidationError: if the YAML is schema-invalid.
        yaml.YAMLError: if the file is not valid YAML.
    """
    p = Path(path)
    with open(p, "r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)
    return Storyboard.model_validate(raw)


def load_storyboard(data: dict[str, Any]) -> Storyboard:
    """Validate a pre-parsed dict against the schema.

    Args:
        data: Dictionary parsed from YAML (e.g. via ``yaml.safe_load``).

    Returns:
        A validated :class:`Storyboard` instance.
    """
    return Storyboard.model_validate(data)
