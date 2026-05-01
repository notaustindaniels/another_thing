"""Tests for parallax_engine.scene.designer (P4_5.M04).

Covers:
- SceneFragment: validates required fields, rejects project-level keys
- ManifestEntry: validates kind constraints, canonical_description for produce_canonical
- parse_fragment / parse_manifest: YAML and JSON parsing
- SceneDesigner.run(): produces fragment + manifest; scene_index matches; duration matches
- produce_canonical entries get canonical_description from casting, not LLM
- canonical example B: scene 1 → red_bird manifest entry (acceptance criterion)
- extract_yaml_block / extract_json_block: block extraction
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from parallax_engine.casting.bible import CastingBible
from parallax_engine.director.schema import CastingEntry, Storyboard, load_storyboard_yaml
from parallax_engine.scene.designer import (
    ManifestEntry,
    SceneDesigner,
    SceneDesignerStub,
    SceneFragment,
    extract_json_block,
    extract_yaml_block,
    parse_fragment,
    parse_manifest,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EXAMPLE_B_PATH = Path(__file__).parent / "storyboards" / "example_b.yaml"

RED_BIRD_DESC = (
    "A small songbird, simplified to two overlapping rounded shapes for body\n"
    "and head. Solid #c84032 fill across the entire silhouette. Black dot eye.\n"
    "Wing rendered as two short curved strokes on the side. No beak detail,\n"
    "no feet. Designed for clean visibility against any biome background.\n"
)


@pytest.fixture
def example_b_storyboard() -> Storyboard:
    return load_storyboard_yaml(EXAMPLE_B_PATH)


@pytest.fixture
def casting_bible_with_red_bird(tmp_path: Path) -> CastingBible:
    """A CastingBible with red_bird (canonical_svg=null) written to a temp dir."""
    casting_path = tmp_path / "casting.yaml"
    entries = [
        {
            "id": "red_bird",
            "kind": "character",
            "canonical_description": RED_BIRD_DESC,
            "role_in_story": "The thread that connects all four biomes.",
            "palette_locked": {
                "body": "#c84032",
                "accent": "#000000",
                "forbidden": ["#0000ff", "#00ff00"],
            },
            "allowed_variations": [
                "may be larger or smaller in frame",
                "may be reflected in water (scene 4)",
            ],
            "forbidden_changes": [
                "color must always be exactly #c84032; no shading, no gradients",
                "must always be a solid silhouette; never an outline",
            ],
            "canonical_svg": None,
            "first_appearance_scene": 1,
            "appearance_evolution": (
                "starts mid-frame in scene 1; grows progressively smaller in scenes 2-3; "
                "grows large again in scene 4 ascent"
            ),
        }
    ]
    casting_path.write_text(
        yaml.dump({"casting": entries}, default_flow_style=False, sort_keys=True),
        encoding="utf-8",
    )
    return CastingBible(casting_path)


# ---------------------------------------------------------------------------
# Helper: build a minimal valid stub response
# ---------------------------------------------------------------------------

def _build_stub_response(
    scene_index: int,
    duration_s: float,
    manifest_entries: list[dict],
) -> str:
    """Return a string containing both ```yaml and ```json blocks."""
    fragment_yaml = yaml.dump(
        {
            "scene_index": scene_index,
            "duration_s": duration_s,
            "stacks": {
                "main": {
                    "layers": [
                        {
                            "id": "bg",
                            "src": "assets/local/bg.svg",
                            "scene_xyz": [0, 0, -8000],
                            "plate_size": [1080, 1920],
                        }
                    ]
                }
            },
            "camera": {"mode": "parallax", "parallax": {"pan_axis": "x", "speed": 100, "dolly": 0}},
            "transitions": {
                "transition_in": {"kind": "fade_from_white", "duration": 0.5},
            },
        },
        default_flow_style=False,
        sort_keys=True,
    )
    manifest_json = json.dumps(manifest_entries, indent=2)
    return f"```yaml\n{fragment_yaml}\n```\n```json\n{manifest_json}\n```"


# ---------------------------------------------------------------------------
# SceneFragment tests
# ---------------------------------------------------------------------------


class TestSceneFragment:
    def test_minimal_valid(self):
        """scene_index and duration_s are sufficient."""
        frag = SceneFragment(scene_index=1, duration_s=5.0)
        assert frag.scene_index == 1
        assert frag.duration_s == 5.0

    def test_extra_fields_allowed(self):
        """extra='allow' — stacks, camera, etc. pass through."""
        frag = SceneFragment(
            scene_index=2,
            duration_s=3.0,
            stacks={"main": {"layers": []}},
            camera={"mode": "parallax"},
        )
        assert frag.scene_index == 2
        assert frag.model_extra["stacks"] == {"main": {"layers": []}}

    def test_scene_index_must_be_positive(self):
        with pytest.raises(Exception):
            SceneFragment(scene_index=0, duration_s=5.0)

    def test_duration_must_be_positive(self):
        with pytest.raises(Exception):
            SceneFragment(scene_index=1, duration_s=0.0)

    def test_rejects_fps(self):
        """fps is project-level — must be rejected."""
        with pytest.raises(Exception, match="fps"):
            SceneFragment(scene_index=1, duration_s=5.0, fps=30)

    def test_rejects_resolution(self):
        with pytest.raises(Exception, match="resolution"):
            SceneFragment(scene_index=1, duration_s=5.0, resolution=[1080, 1920])

    def test_rejects_seed(self):
        with pytest.raises(Exception, match="seed"):
            SceneFragment(scene_index=1, duration_s=5.0, seed=42)

    def test_rejects_meta(self):
        with pytest.raises(Exception, match="meta"):
            SceneFragment(scene_index=1, duration_s=5.0, meta={"fps": 30})

    def test_rejects_bg_color(self):
        with pytest.raises(Exception, match="bg_color"):
            SceneFragment(scene_index=1, duration_s=5.0, bg_color="#000000")

    def test_allows_qa_self_check(self):
        """qa_self_check is scene-level; allowed."""
        frag = SceneFragment(
            scene_index=1,
            duration_s=5.0,
            qa_self_check={"hard_rules_checked": ["RED_BIRD appears"]},
        )
        assert frag.model_extra["qa_self_check"]["hard_rules_checked"] == [
            "RED_BIRD appears"
        ]

    def test_allows_scene_designer_protest(self):
        """scene_designer_protest is scene-level; allowed."""
        frag = SceneFragment(
            scene_index=3,
            duration_s=5.0,
            scene_designer_protest="The storyboard asks for a biome wipe but no receiver",
        )
        assert "scene_designer_protest" in frag.model_extra


# ---------------------------------------------------------------------------
# ManifestEntry tests
# ---------------------------------------------------------------------------


class TestManifestEntry:
    def test_local_minimal(self):
        e = ManifestEntry(id="bg_sky", kind="local", purpose="Sky background")
        assert e.kind == "local"
        assert e.canonical_description is None

    def test_canonical_no_description_needed(self):
        e = ManifestEntry(
            id="red_bird",
            kind="canonical",
            purpose="Main character",
            path="assets/canon/red_bird.svg",
        )
        assert e.kind == "canonical"

    def test_produce_canonical_requires_description(self):
        """produce_canonical without canonical_description must fail."""
        with pytest.raises(Exception, match="canonical_description"):
            ManifestEntry(id="red_bird", kind="produce_canonical", purpose="First use")

    def test_produce_canonical_with_description(self):
        e = ManifestEntry(
            id="red_bird",
            kind="produce_canonical",
            purpose="First use of the bird",
            canonical_description="A small red bird ...",
            path="assets/canon/red_bird.svg",
        )
        assert e.kind == "produce_canonical"
        assert e.canonical_description.startswith("A small red bird")

    def test_extra_fields_forbidden(self):
        with pytest.raises(Exception):
            ManifestEntry(
                id="x",
                kind="local",
                purpose="test",
                unknown_field="oops",
            )


# ---------------------------------------------------------------------------
# Block extraction tests
# ---------------------------------------------------------------------------


class TestBlockExtraction:
    def test_extract_yaml_block_basic(self):
        text = "some prose\n```yaml\nfoo: bar\n```\nmore prose"
        assert extract_yaml_block(text) == "foo: bar"

    def test_extract_yaml_block_missing(self):
        with pytest.raises(ValueError, match="yaml"):
            extract_yaml_block("no blocks here")

    def test_extract_json_block_basic(self):
        text = "prose\n```json\n[1, 2, 3]\n```\nend"
        assert extract_json_block(text) == "[1, 2, 3]"

    def test_extract_json_block_missing(self):
        with pytest.raises(ValueError, match="json"):
            extract_json_block("no json block")

    def test_extract_yaml_block_multiline(self):
        text = "```yaml\nscene_index: 1\nduration_s: 5.0\n```"
        result = extract_yaml_block(text)
        assert "scene_index: 1" in result


# ---------------------------------------------------------------------------
# parse_fragment tests
# ---------------------------------------------------------------------------


class TestParseFragment:
    def test_valid_minimal(self):
        raw = "scene_index: 1\nduration_s: 5.0\n"
        frag = parse_fragment(raw)
        assert frag.scene_index == 1
        assert frag.duration_s == 5.0

    def test_invalid_project_key(self):
        raw = "scene_index: 1\nduration_s: 5.0\nfps: 30\n"
        with pytest.raises(Exception, match="fps"):
            parse_fragment(raw)

    def test_non_mapping_fails(self):
        with pytest.raises(Exception):
            parse_fragment("- item1\n- item2\n")


# ---------------------------------------------------------------------------
# parse_manifest tests
# ---------------------------------------------------------------------------


class TestParseManifest:
    def test_local_entry(self, casting_bible_with_red_bird: CastingBible):
        raw = json.dumps([{"id": "bg", "kind": "local", "purpose": "Background"}])
        entries = parse_manifest(raw, casting_bible_with_red_bird)
        assert len(entries) == 1
        assert entries[0].kind == "local"
        assert entries[0].path == "assets/local/bg.svg"

    def test_produce_canonical_uses_casting_description(
        self, casting_bible_with_red_bird: CastingBible
    ):
        """produce_canonical description must come from casting, not LLM."""
        raw = json.dumps(
            [
                {
                    "id": "red_bird",
                    "kind": "produce_canonical",
                    "purpose": "The main character",
                    "canonical_description": "INVENTED by LLM — wrong!",
                }
            ]
        )
        entries = parse_manifest(raw, casting_bible_with_red_bird)
        assert entries[0].kind == "produce_canonical"
        # The LLM's invented description is REPLACED with the casting bible's
        assert "INVENTED by LLM" not in entries[0].canonical_description
        assert "simplified to two overlapping rounded shapes" in entries[0].canonical_description

    def test_produce_canonical_path_set(self, casting_bible_with_red_bird: CastingBible):
        """produce_canonical path is assets/canon/<id>.svg."""
        raw = json.dumps(
            [{"id": "red_bird", "kind": "produce_canonical", "purpose": "Bird"}]
        )
        entries = parse_manifest(raw, casting_bible_with_red_bird)
        assert entries[0].path == "assets/canon/red_bird.svg"

    def test_non_list_json_fails(self, casting_bible_with_red_bird: CastingBible):
        with pytest.raises(Exception, match="list"):
            parse_manifest('{"id": "x"}', casting_bible_with_red_bird)


# ---------------------------------------------------------------------------
# SceneDesigner.run() — stub mode
# ---------------------------------------------------------------------------


class TestSceneDesignerRun:
    """All tests use SceneDesignerStub — no real LLM calls."""

    def test_basic_run_returns_output(
        self,
        example_b_storyboard: Storyboard,
        casting_bible_with_red_bird: CastingBible,
    ):
        manifest_entries = [
            {"id": "red_bird", "kind": "produce_canonical", "purpose": "Main character"},
            {"id": "desert_bg", "kind": "local", "purpose": "Desert background"},
        ]
        stub_resp = _build_stub_response(1, 5.0, manifest_entries)
        stub = SceneDesignerStub({1: stub_resp})
        designer = SceneDesigner(client=stub)

        out = designer.run(
            storyboard=example_b_storyboard,
            casting=casting_bible_with_red_bird,
            prior_fragments=[],
            scene_index=1,
        )

        assert out.fragment.scene_index == 1
        assert out.fragment.duration_s == 5.0
        assert len(out.manifest) == 2
        assert stub.call_count == 1

    def test_scene_index_mismatch_raises(
        self,
        example_b_storyboard: Storyboard,
        casting_bible_with_red_bird: CastingBible,
    ):
        """Fragment with wrong scene_index must raise."""
        stub_resp = _build_stub_response(99, 5.0, [])  # scene_index=99 wrong
        stub = SceneDesignerStub({1: stub_resp})
        designer = SceneDesigner(client=stub)

        with pytest.raises(ValueError, match="scene_index"):
            designer.run(
                storyboard=example_b_storyboard,
                casting=casting_bible_with_red_bird,
                prior_fragments=[],
                scene_index=1,
            )

    def test_duration_mismatch_raises(
        self,
        example_b_storyboard: Storyboard,
        casting_bible_with_red_bird: CastingBible,
    ):
        """Fragment with wrong duration_s must raise."""
        stub_resp = _build_stub_response(1, 99.0, [])  # duration wrong
        stub = SceneDesignerStub({1: stub_resp})
        designer = SceneDesigner(client=stub)

        with pytest.raises(ValueError, match="duration"):
            designer.run(
                storyboard=example_b_storyboard,
                casting=casting_bible_with_red_bird,
                prior_fragments=[],
                scene_index=1,
            )

    def test_no_response_for_scene_raises(
        self,
        example_b_storyboard: Storyboard,
        casting_bible_with_red_bird: CastingBible,
    ):
        """Stub with empty map must raise ValueError."""
        stub = SceneDesignerStub({})
        designer = SceneDesigner(client=stub)

        with pytest.raises(ValueError):
            designer.run(
                storyboard=example_b_storyboard,
                casting=casting_bible_with_red_bird,
                prior_fragments=[],
                scene_index=1,
            )

    def test_fragment_no_project_metadata(
        self,
        example_b_storyboard: Storyboard,
        casting_bible_with_red_bird: CastingBible,
    ):
        """Fragment returned by run() must not expose fps/resolution/seed."""
        manifest_entries = [
            {"id": "red_bird", "kind": "produce_canonical", "purpose": "Main character"},
        ]
        stub_resp = _build_stub_response(1, 5.0, manifest_entries)
        stub = SceneDesignerStub({1: stub_resp})
        designer = SceneDesigner(client=stub)

        out = designer.run(
            storyboard=example_b_storyboard,
            casting=casting_bible_with_red_bird,
            prior_fragments=[],
            scene_index=1,
        )

        frag_dict = out.fragment.model_dump(mode="json")
        for forbidden_key in ("fps", "resolution", "seed", "meta", "version"):
            assert forbidden_key not in frag_dict, f"Fragment has forbidden key: {forbidden_key}"

    def test_prior_fragments_accepted(
        self,
        example_b_storyboard: Storyboard,
        casting_bible_with_red_bird: CastingBible,
    ):
        """run() with prior_fragments (scene 1 already done) works for scene 2."""
        prior = [{"scene_index": 1, "duration_s": 5.0, "stacks": {}}]
        manifest_entries = [
            {"id": "red_bird", "kind": "produce_canonical", "purpose": "Main character"},
        ]
        stub_resp = _build_stub_response(2, 5.0, manifest_entries)
        stub = SceneDesignerStub({2: stub_resp})
        designer = SceneDesigner(client=stub)

        out = designer.run(
            storyboard=example_b_storyboard,
            casting=casting_bible_with_red_bird,
            prior_fragments=prior,
            scene_index=2,
        )

        assert out.fragment.scene_index == 2


# ---------------------------------------------------------------------------
# ACCEPTANCE CRITERION: example B, scene 1 → red_bird manifest entry
# ---------------------------------------------------------------------------


class TestExampleBAcceptanceCriterion:
    """Validates the key acceptance criterion from phase_milestones.json:
    'test validates that scene-designer for canonical example B produces a
    fragment with red_bird manifest entry'
    """

    def test_example_b_scene1_has_red_bird_manifest_entry(
        self,
        example_b_storyboard: Storyboard,
        casting_bible_with_red_bird: CastingBible,
    ):
        """Example B scene 1 must produce a manifest entry for red_bird.

        Since red_bird.canonical_svg is null, the entry must be produce_canonical.
        The canonical_description must match the casting bible entry.
        """
        manifest_entries = [
            {
                "id": "red_bird",
                "kind": "produce_canonical",
                "purpose": "The recurring red bird character — first appearance",
                # LLM would write something here; we verify it gets overwritten
                "canonical_description": "some LLM invented description",
            },
            {"id": "desert_sky", "kind": "local", "purpose": "Desert sky background"},
            {"id": "desert_ground", "kind": "local", "purpose": "Sand and dunes"},
        ]
        stub_resp = _build_stub_response(1, 5.0, manifest_entries)
        stub = SceneDesignerStub({1: stub_resp})
        designer = SceneDesigner(client=stub)

        out = designer.run(
            storyboard=example_b_storyboard,
            casting=casting_bible_with_red_bird,
            prior_fragments=[],
            scene_index=1,
        )

        # Must have a red_bird entry
        red_bird_entries = [e for e in out.manifest if e.id == "red_bird"]
        assert len(red_bird_entries) == 1, "Expected exactly one red_bird entry in manifest"

        red_bird_entry = red_bird_entries[0]
        # Must be produce_canonical (svg is null)
        assert red_bird_entry.kind == "produce_canonical", (
            f"Expected produce_canonical, got {red_bird_entry.kind}"
        )

        # canonical_description must be from casting bible, not LLM-invented
        assert "simplified to two overlapping rounded shapes" in red_bird_entry.canonical_description, (
            "canonical_description must come from the casting bible"
        )
        assert "LLM invented" not in red_bird_entry.canonical_description

        # Path must be reserved correctly
        assert red_bird_entry.path == "assets/canon/red_bird.svg"

        # Fragment must be scene 1 with correct duration
        assert out.fragment.scene_index == 1
        assert out.fragment.duration_s == 5.0

    def test_example_b_scene2_red_bird_canonical(
        self,
        example_b_storyboard: Storyboard,
        tmp_path: Path,
    ):
        """Example B scene 2: red_bird.canonical_svg is set → 'canonical' entry."""
        # Set up casting with canonical_svg already set (as scene 1 produced it)
        casting_path = tmp_path / "casting.yaml"
        entries = [
            {
                "id": "red_bird",
                "kind": "character",
                "canonical_description": RED_BIRD_DESC,
                "role_in_story": "The thread that connects all four biomes.",
                "palette_locked": {"body": "#c84032", "accent": "#000000"},
                "canonical_svg": "assets/canon/red_bird.svg",
                "first_appearance_scene": 1,
            }
        ]
        casting_path.write_text(
            yaml.dump({"casting": entries}, default_flow_style=False, sort_keys=True),
            encoding="utf-8",
        )
        casting = CastingBible(casting_path)

        prior = [{"scene_index": 1, "duration_s": 5.0, "stacks": {}}]
        manifest_entries = [
            {
                "id": "red_bird",
                "kind": "canonical",
                "purpose": "Red bird in forest",
                "path": "assets/canon/red_bird.svg",
            },
            {"id": "forest_bg", "kind": "local", "purpose": "Forest background"},
        ]
        stub_resp = _build_stub_response(2, 5.0, manifest_entries)
        stub = SceneDesignerStub({2: stub_resp})
        designer = SceneDesigner(client=stub)

        out = designer.run(
            storyboard=example_b_storyboard,
            casting=casting,
            prior_fragments=prior,
            scene_index=2,
        )

        red_bird_entries = [e for e in out.manifest if e.id == "red_bird"]
        assert len(red_bird_entries) == 1
        assert red_bird_entries[0].kind == "canonical"
        assert red_bird_entries[0].path == "assets/canon/red_bird.svg"
        # No canonical_description needed for canonical entries
        assert red_bird_entries[0].canonical_description is None

    def test_example_b_fragment_has_no_project_metadata(
        self,
        example_b_storyboard: Storyboard,
        casting_bible_with_red_bird: CastingBible,
    ):
        """Fragment for example B scene 1 must not contain project metadata."""
        manifest_entries = [
            {"id": "red_bird", "kind": "produce_canonical", "purpose": "Main character"},
        ]
        stub_resp = _build_stub_response(1, 5.0, manifest_entries)
        stub = SceneDesignerStub({1: stub_resp})
        designer = SceneDesigner(client=stub)

        out = designer.run(
            storyboard=example_b_storyboard,
            casting=casting_bible_with_red_bird,
            prior_fragments=[],
            scene_index=1,
        )

        frag_keys = set(SceneFragment.model_fields.keys()) | set(out.fragment.model_extra.keys())
        forbidden = {"fps", "resolution", "seed", "meta", "version", "perspective_px", "origin", "bg_color"}
        overlap = frag_keys & forbidden
        assert not overlap, f"Fragment has forbidden project-level keys: {overlap}"
