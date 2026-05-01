"""Tests for parallax_engine.director.schema — Pydantic v2 storyboard models.

Validates:
  - All three §11.4 canonical storyboard examples parse without error
  - Schema validation catches known-bad storyboards (pairing violations, missing fields)
  - JSON Schema is generated and contains expected structure
  - Round-trip: yaml → Storyboard → JSON → Storyboard produces identical result

Milestone: P4_5.M01
Spec anchors: §11.3, §11.4, §11.14
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from parallax_engine.director.schema import (
    Arc,
    AudioIntent,
    Beat,
    CastingEntry,
    Continuity,
    PaletteOverride,
    SceneEntry,
    Storyboard,
    TransitionSpec,
    VisualVocabulary,
    load_storyboard,
    load_storyboard_yaml,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

STORYBOARDS_DIR = Path(__file__).parent / "storyboards"
SCHEMA_JSON_PATH = Path(__file__).parent.parent / "parallax_engine" / "director" / "schema.json"


def _load_yaml(fname: str) -> dict[str, Any]:
    with open(STORYBOARDS_DIR / fname, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# §11.4.1 — Example A: 10-second drone forest flythrough
# ---------------------------------------------------------------------------


class TestExampleA:
    """Parse and structural checks on Example A (forest flythrough)."""

    def test_parses_without_error(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        assert sb is not None

    def test_project_title(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        assert sb.project.title == "Through the Pines"

    def test_total_duration(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        assert sb.project.total_duration_s == 10.0

    def test_scene_count(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        assert len(sb.scenes) == 1

    def test_scene_index_dense(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        assert sb.scenes[0].index == 1

    def test_casting_empty(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        assert sb.casting == []

    def test_arc_structure(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        assert sb.arc.structure == "establish_disrupt_resolve"

    def test_arc_beat_count(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        assert len(sb.arc.beats) == 3

    def test_visual_vocabulary_palette(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        assert len(sb.visual_vocabulary.palette.primary) >= 2

    def test_palette_progression_warming(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        assert sb.visual_vocabulary.palette_progression == "warming"

    def test_continuity_hard_rules(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        assert sb.continuity is not None
        assert sb.continuity.hard_rules is not None
        assert len(sb.continuity.hard_rules) >= 1

    def test_audio_intent_present(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        assert sb.audio_intent is not None
        assert sb.audio_intent.music_arc == "building"

    def test_camera_intent(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        assert sb.scenes[0].camera_intent == "drone_fpv_forward"

    def test_transition_in_type(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        assert sb.scenes[0].transition_in.type == "fade_from_black"

    def test_last_scene_no_transition_out(self):
        """Single-scene storyboard: last scene may omit transition_out."""
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_a.yaml")
        # Last scene has no transition_out (it's None)
        assert sb.scenes[0].transition_out is None


# ---------------------------------------------------------------------------
# §11.4.2 — Example B: 24-second 4-biome explainer
# ---------------------------------------------------------------------------


class TestExampleB:
    """Parse and structural checks on Example B (4-biome explainer)."""

    def test_parses_without_error(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_b.yaml")
        assert sb is not None

    def test_project_title(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_b.yaml")
        assert sb.project.title == "Four Climates"

    def test_total_duration(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_b.yaml")
        assert sb.project.total_duration_s == 24.0

    def test_scene_count(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_b.yaml")
        assert len(sb.scenes) == 4

    def test_scene_indices_dense(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_b.yaml")
        indices = sorted(s.index for s in sb.scenes)
        assert indices == [1, 2, 3, 4]

    def test_casting_red_bird(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_b.yaml")
        assert len(sb.casting) == 1
        assert sb.casting[0].id == "red_bird"
        assert sb.casting[0].kind == "character"

    def test_red_bird_canonical_svg_null(self):
        """canonical_svg must be null at director time (§11.3.4)."""
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_b.yaml")
        assert sb.casting[0].canonical_svg is None

    def test_biome_wipe_pairing(self):
        """Biome wipe transitions must be paired across scene boundaries."""
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_b.yaml")
        scene2 = next(s for s in sb.scenes if s.index == 2)
        assert scene2.transition_in.type == "biome_wipe"
        assert scene2.transition_in.paired_scene == 1

    def test_arc_structure_biome_tour(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_b.yaml")
        assert sb.arc.structure == "biome_tour"

    def test_palette_progression_custom_described(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_b.yaml")
        assert sb.visual_vocabulary.palette_progression == "custom_described"
        assert sb.visual_vocabulary.palette_progression_note is not None

    def test_must_feature_casting_refs_valid(self):
        """must_feature lists must reference valid casting ids."""
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_b.yaml")
        cast_ids = {c.id for c in sb.casting}
        for scene in sb.scenes:
            for cid in scene.must_feature or []:
                assert cid in cast_ids, f"Unknown casting id '{cid}' in scene {scene.index}"

    def test_motif_evolution_present(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_b.yaml")
        assert sb.continuity is not None
        assert sb.continuity.motif_evolution is not None
        assert len(sb.continuity.motif_evolution) >= 1

    def test_last_scene_no_biome_wipe_out(self):
        """Last scene (index=4) must not have a biome_wipe transition_out."""
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_b.yaml")
        last = max(sb.scenes, key=lambda s: s.index)
        if last.transition_out is not None:
            assert last.transition_out.type not in {"biome_wipe", "portal_reveal"}


# ---------------------------------------------------------------------------
# §11.4.3 — Example C: 8-second portal transition
# ---------------------------------------------------------------------------


class TestExampleC:
    """Parse and structural checks on Example C (portal transition)."""

    def test_parses_without_error(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_c.yaml")
        assert sb is not None

    def test_project_title(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_c.yaml")
        assert sb.project.title == "The Door"

    def test_total_duration(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_c.yaml")
        assert sb.project.total_duration_s == 8.0

    def test_scene_count(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_c.yaml")
        assert len(sb.scenes) == 2

    def test_casting_figure(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_c.yaml")
        assert len(sb.casting) == 1
        assert sb.casting[0].id == "figure"

    def test_portal_reveal_pairing(self):
        """portal_reveal must be paired across scene 1 → scene 2."""
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_c.yaml")
        scene1 = next(s for s in sb.scenes if s.index == 1)
        scene2 = next(s for s in sb.scenes if s.index == 2)
        assert scene1.transition_out is not None
        assert scene1.transition_out.type == "portal_reveal"
        assert scene2.transition_in.type == "portal_reveal"
        assert scene2.transition_in.paired_scene == 1

    def test_arc_structure_portal_reveal(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_c.yaml")
        assert sb.arc.structure == "portal_reveal"

    def test_shape_language_angular(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_c.yaml")
        assert sb.visual_vocabulary.shape_language == "angular"

    def test_callback_to_scene(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_c.yaml")
        scene2 = next(s for s in sb.scenes if s.index == 2)
        assert scene2.callback_to_scene == 1

    def test_music_arc_silent(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_c.yaml")
        assert sb.audio_intent is not None
        assert sb.audio_intent.music_arc == "silent"

    def test_sfx_notes_present(self):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_c.yaml")
        assert sb.audio_intent is not None
        assert sb.audio_intent.sfx_notes is not None


# ---------------------------------------------------------------------------
# Known-bad storyboard detection (acceptance criterion 2)
# ---------------------------------------------------------------------------


class TestKnownBadStoryboards:
    """Schema must reject malformed storyboards."""

    def _minimal_valid(self) -> dict[str, Any]:
        """Return a minimal valid storyboard dict for mutation."""
        return {
            "storyboard_version": "1.0",
            "project": {
                "title": "Test Project",
                "logline": "A minimal test project for validation.",
                "theme": "test",
                "summary": "This is a summary of the test project. It has enough words to meet the requirement.",
                "intended_platform": "tiktok",
                "aspect_ratio": "9:16",
                "target_fps": 30,
                "total_duration_s": 10.0,
            },
            "visual_vocabulary": {
                "palette": {
                    "primary": ["#000000", "#ffffff"],
                    "secondary": ["#888888"],
                    "neutrals": ["#cccccc"],
                },
                "palette_progression": "static",
                "shape_language": "angular",
                "density_curve": "medium",
                "lighting_mood": "flat",
                "time_of_day": "noon",
            },
            "arc": {
                "structure": "three_act",
                "beats": [
                    {
                        "id": "b1", "label": "setup", "function": "hook",
                        "narrative_note": "Sets the scene.", "target_time_s": 0.0, "target_scenes": [1],
                    },
                    {
                        "id": "b2", "label": "resolve", "function": "release",
                        "narrative_note": "Resolves.", "target_time_s": 8.0, "target_scenes": [1],
                    },
                ],
            },
            "casting": [],
            "scenes": [
                {
                    "index": 1, "duration_s": 10.0, "logline": "Scene 1",
                    "emotional_function": "establishing",
                    "camera_intent": "drone_fpv_forward",
                    "transition_in": {"type": "hard_cut"},
                    "pacing": "steady",
                },
            ],
        }

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            Storyboard.model_validate({})

    def test_missing_project_block_raises(self):
        bad = self._minimal_valid()
        del bad["project"]
        with pytest.raises(ValidationError):
            Storyboard.model_validate(bad)

    def test_missing_arc_raises(self):
        bad = self._minimal_valid()
        del bad["arc"]
        with pytest.raises(ValidationError):
            Storyboard.model_validate(bad)

    def test_missing_scenes_raises(self):
        bad = self._minimal_valid()
        del bad["scenes"]
        with pytest.raises(ValidationError):
            Storyboard.model_validate(bad)

    def test_empty_scenes_list_raises(self):
        bad = self._minimal_valid()
        bad["scenes"] = []
        with pytest.raises(ValidationError):
            Storyboard.model_validate(bad)

    def test_biome_wipe_pairing_mismatch_raises(self):
        """biome_wipe out must pair with biome_wipe in on the next scene."""
        bad = self._minimal_valid()
        bad["scenes"] = [
            {
                "index": 1, "duration_s": 5.0, "logline": "Scene 1",
                "emotional_function": "establishing", "camera_intent": "drone_fpv_forward",
                "transition_in": {"type": "hard_cut"},
                "transition_out": {"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 2},
                "pacing": "steady",
            },
            {
                "index": 2, "duration_s": 5.0, "logline": "Scene 2",
                "emotional_function": "release", "camera_intent": "drone_fpv_forward",
                # WRONG: should be biome_wipe, not dissolve
                "transition_in": {"type": "dissolve", "duration_s": 1.0},
                "pacing": "steady",
            },
        ]
        with pytest.raises(ValidationError, match="Transition pairing violations"):
            Storyboard.model_validate(bad)

    def test_portal_reveal_pairing_mismatch_raises(self):
        """portal_reveal out must pair with portal_reveal in."""
        bad = self._minimal_valid()
        bad["scenes"] = [
            {
                "index": 1, "duration_s": 4.0, "logline": "Scene 1",
                "emotional_function": "establishing", "camera_intent": "cinematic_truck_in",
                "transition_in": {"type": "hard_cut"},
                "transition_out": {"type": "portal_reveal", "duration_s": 0.5, "paired_scene": 2},
                "pacing": "steady",
            },
            {
                "index": 2, "duration_s": 4.0, "logline": "Scene 2",
                "emotional_function": "release", "camera_intent": "drone_fpv_pullout",
                # WRONG: should be portal_reveal
                "transition_in": {"type": "fade_from_black", "duration_s": 0.5},
                "pacing": "held",
            },
        ]
        with pytest.raises(ValidationError, match="Transition pairing violations"):
            Storyboard.model_validate(bad)

    def test_non_dense_scene_indices_raises(self):
        """Scene indices must be 1-based and have no gaps."""
        bad = self._minimal_valid()
        bad["scenes"] = [
            {
                "index": 1, "duration_s": 5.0, "logline": "Scene 1",
                "emotional_function": "establishing", "camera_intent": "drone_fpv_forward",
                "transition_in": {"type": "hard_cut"},
                "pacing": "steady",
            },
            {
                "index": 3,  # GAP: missing index 2
                "duration_s": 5.0, "logline": "Scene 3",
                "emotional_function": "release", "camera_intent": "drone_fpv_forward",
                "transition_in": {"type": "hard_cut"},
                "pacing": "steady",
            },
        ]
        with pytest.raises(ValidationError, match="dense"):
            Storyboard.model_validate(bad)

    def test_invalid_camera_intent_raises(self):
        bad = self._minimal_valid()
        bad["scenes"][0]["camera_intent"] = "not_a_valid_intent"
        with pytest.raises(ValidationError):
            Storyboard.model_validate(bad)

    def test_invalid_transition_type_raises(self):
        bad = self._minimal_valid()
        bad["scenes"][0]["transition_in"]["type"] = "teleport"
        with pytest.raises(ValidationError):
            Storyboard.model_validate(bad)

    def test_custom_structure_without_note_raises(self):
        """arc.structure='custom' requires structure_note."""
        bad = self._minimal_valid()
        bad["arc"]["structure"] = "custom"
        # structure_note is missing
        with pytest.raises(ValidationError, match="structure_note"):
            Storyboard.model_validate(bad)

    def test_custom_palette_progression_without_note_raises(self):
        """palette_progression='custom_described' requires palette_progression_note."""
        bad = self._minimal_valid()
        bad["visual_vocabulary"]["palette_progression"] = "custom_described"
        # palette_progression_note is missing
        with pytest.raises(ValidationError, match="palette_progression_note"):
            Storyboard.model_validate(bad)

    def test_callback_to_scene_forward_reference_raises(self):
        """callback_to_scene must be < scene.index (no forward refs)."""
        bad = self._minimal_valid()
        bad["scenes"].append({
            "index": 2, "duration_s": 5.0, "logline": "Scene 2",
            "emotional_function": "release", "camera_intent": "drone_fpv_forward",
            "transition_in": {"type": "hard_cut"},
            "pacing": "steady",
            "callback_to_scene": 3,  # forward reference — scene 3 doesn't exist
        })
        with pytest.raises(ValidationError, match="callback_to_scene"):
            Storyboard.model_validate(bad)

    def test_must_feature_unknown_casting_id_raises(self):
        """must_feature references must be valid casting ids."""
        bad = self._minimal_valid()
        bad["scenes"][0]["must_feature"] = ["ghost_character"]
        with pytest.raises(ValidationError, match="Casting reference violations"):
            Storyboard.model_validate(bad)

    def test_non_cut_transition_without_duration_raises(self):
        """Non-cut transitions require duration_s."""
        bad = self._minimal_valid()
        bad["scenes"][0]["transition_in"] = {"type": "fade_from_black"}  # no duration_s
        with pytest.raises(ValidationError, match="duration_s"):
            Storyboard.model_validate(bad)

    def test_duration_out_of_range_raises(self):
        """transition duration_s must be between 0.1 and 2.0."""
        bad = self._minimal_valid()
        bad["scenes"][0]["transition_in"] = {"type": "fade_from_black", "duration_s": 5.0}
        with pytest.raises(ValidationError):
            Storyboard.model_validate(bad)

    def test_too_few_arc_beats_raises(self):
        """Arc requires at least 2 beats."""
        bad = self._minimal_valid()
        bad["arc"]["beats"] = [
            {"id": "b1", "label": "only", "function": "hook",
             "narrative_note": "Only beat.", "target_time_s": 0.0, "target_scenes": [1]},
        ]
        with pytest.raises(ValidationError):
            Storyboard.model_validate(bad)

    def test_too_many_arc_beats_raises(self):
        """Arc allows at most 8 beats."""
        bad = self._minimal_valid()
        bad["arc"]["beats"] = [
            {"id": f"b{i}", "label": f"beat {i}", "function": "hook",
             "narrative_note": f"Beat {i}.", "target_time_s": float(i), "target_scenes": [1]}
            for i in range(9)
        ]
        with pytest.raises(ValidationError):
            Storyboard.model_validate(bad)


# ---------------------------------------------------------------------------
# JSON Schema generation (acceptance criterion 3)
# ---------------------------------------------------------------------------


class TestJsonSchemaGeneration:
    """JSON Schema must be generated and parseable."""

    def test_schema_json_file_exists(self):
        assert SCHEMA_JSON_PATH.exists(), f"schema.json not found at {SCHEMA_JSON_PATH}"

    def test_schema_json_is_valid_json(self):
        with open(SCHEMA_JSON_PATH, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        assert isinstance(schema, dict)

    def test_schema_json_has_properties(self):
        with open(SCHEMA_JSON_PATH, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        assert "properties" in schema

    def test_schema_json_has_required_fields(self):
        with open(SCHEMA_JSON_PATH, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        required = schema.get("required", [])
        assert "storyboard_version" in required
        assert "project" in required
        assert "scenes" in required

    def test_model_json_schema_generates(self):
        schema = Storyboard.model_json_schema()
        assert isinstance(schema, dict)
        assert "properties" in schema

    def test_save_json_schema_roundtrip(self, tmp_path):
        """save_json_schema writes valid JSON that loads back cleanly."""
        out = tmp_path / "schema.json"
        Storyboard(
            storyboard_version="1.0",
            project={
                "title": "X", "logline": "Y", "theme": "z",
                "summary": "A short summary of the test project used for roundtrip schema testing.",
                "intended_platform": "tiktok", "aspect_ratio": "9:16",
                "target_fps": 30, "total_duration_s": 5.0,
            },
            visual_vocabulary={
                "palette": {"primary": ["#000", "#fff"], "secondary": ["#aaa"], "neutrals": ["#ccc"]},
                "palette_progression": "static", "shape_language": "angular",
                "density_curve": "medium", "lighting_mood": "flat", "time_of_day": "noon",
            },
            arc={
                "structure": "three_act",
                "beats": [
                    {"id": "b1", "label": "open", "function": "hook",
                     "narrative_note": "Opens.", "target_time_s": 0.0, "target_scenes": [1]},
                    {"id": "b2", "label": "close", "function": "release",
                     "narrative_note": "Closes.", "target_time_s": 4.0, "target_scenes": [1]},
                ],
            },
            casting=[],
            scenes=[{
                "index": 1, "duration_s": 5.0, "logline": "Only scene",
                "emotional_function": "establishing", "camera_intent": "static_held",
                "transition_in": {"type": "hard_cut"}, "pacing": "steady",
            }],
        ).save_json_schema(out)
        loaded = json.loads(out.read_text())
        assert "properties" in loaded


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """YAML → Storyboard → JSON → Storyboard round-trip."""

    @pytest.mark.parametrize("fname", ["example_a.yaml", "example_b.yaml", "example_c.yaml"])
    def test_yaml_to_model_roundtrip(self, fname):
        """Parse YAML, serialise to dict, re-parse; must not raise."""
        sb = load_storyboard_yaml(STORYBOARDS_DIR / fname)
        serialised = sb.model_dump(mode="json")
        sb2 = Storyboard.model_validate(serialised)
        assert sb2.project.title == sb.project.title
        assert len(sb2.scenes) == len(sb.scenes)

    @pytest.mark.parametrize("fname", ["example_a.yaml", "example_b.yaml", "example_c.yaml"])
    def test_all_scenes_have_valid_indices(self, fname):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / fname)
        indices = sorted(s.index for s in sb.scenes)
        assert indices == list(range(1, len(sb.scenes) + 1))

    def test_example_b_biome_wipes_all_paired(self):
        """Every biome_wipe in Example B must have a valid paired_scene."""
        sb = load_storyboard_yaml(STORYBOARDS_DIR / "example_b.yaml")
        scene_indices = {s.index for s in sb.scenes}
        for scene in sb.scenes:
            for spec in [scene.transition_in, scene.transition_out]:
                if spec is not None and spec.type == "biome_wipe":
                    assert spec.paired_scene is not None
                    assert spec.paired_scene in scene_indices

    def test_load_storyboard_dict_function(self):
        """load_storyboard(dict) is equivalent to load_storyboard_yaml."""
        raw = _load_yaml("example_c.yaml")
        sb = load_storyboard(raw)
        assert sb.project.title == "The Door"


# ---------------------------------------------------------------------------
# Storyboard YAML files: existence and schema conformance (CI gate)
# ---------------------------------------------------------------------------


class TestStoryboardYamlFiles:
    """tests/storyboards/*.yaml must exist and conform to the schema."""

    def test_example_a_yaml_exists(self):
        assert (STORYBOARDS_DIR / "example_a.yaml").exists()

    def test_example_b_yaml_exists(self):
        assert (STORYBOARDS_DIR / "example_b.yaml").exists()

    def test_example_c_yaml_exists(self):
        assert (STORYBOARDS_DIR / "example_c.yaml").exists()

    @pytest.mark.parametrize("fname", ["example_a.yaml", "example_b.yaml", "example_c.yaml"])
    def test_storyboard_yaml_validates_against_schema(self, fname):
        """Every YAML in tests/storyboards/ must validate without exception."""
        sb = load_storyboard_yaml(STORYBOARDS_DIR / fname)
        assert isinstance(sb, Storyboard)

    @pytest.mark.parametrize("fname", ["example_a.yaml", "example_b.yaml", "example_c.yaml"])
    def test_storyboard_version_is_1_0(self, fname):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / fname)
        assert sb.storyboard_version == "1.0"

    @pytest.mark.parametrize("fname", ["example_a.yaml", "example_b.yaml", "example_c.yaml"])
    def test_storyboard_has_at_least_one_scene(self, fname):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / fname)
        assert len(sb.scenes) >= 1

    @pytest.mark.parametrize("fname", ["example_a.yaml", "example_b.yaml", "example_c.yaml"])
    def test_storyboard_target_fps_valid(self, fname):
        sb = load_storyboard_yaml(STORYBOARDS_DIR / fname)
        assert sb.project.target_fps in {24, 30, 60}
