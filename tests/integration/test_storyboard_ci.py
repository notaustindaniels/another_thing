"""tests/integration/test_storyboard_ci.py — CI schema validation gate (P4_5.M07).

Validates:
- All three canonical storyboard YAML files in tests/storyboards/ parse
  without error against the Pydantic v2 schema (§11.3).
- Known-bad storyboards (transition pairing violations) are rejected.
- The merger accepts all three storyboards with minimal stub fragments.
- Dry-run merge log is written and machine-parseable.

This test file is the ``tests/storyboards/`` CI step from §11.14 step 10.
It runs in CI without network access (no LLM calls, no asset generation).

Spec anchors: §11.3, §11.4, §11.6.5, §11.14 step 10
Milestone: P4_5.M07
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from parallax_engine.director.schema import (
    Storyboard,
    load_storyboard_yaml,
)
from parallax_engine.scene.merger import MergeError, merge

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

STORYBOARDS_DIR = Path(__file__).parent.parent / "storyboards"

EXAMPLE_A = STORYBOARDS_DIR / "example_a.yaml"  # 10 s forest (1 scene)
EXAMPLE_B = STORYBOARDS_DIR / "example_b.yaml"  # 24 s biome tour (4 scenes)
EXAMPLE_C = STORYBOARDS_DIR / "example_c.yaml"  # 8 s portal (2 scenes)


# ---------------------------------------------------------------------------
# Helper: write minimal stub scene fragments for a storyboard
# ---------------------------------------------------------------------------


def _write_stub_fragments(fragment_dir: Path, storyboard: Storyboard) -> None:
    """Write one minimal stub fragment per scene in the storyboard.

    Stubs contain only the required keys (scene_index, duration_s, stacks).
    They intentionally omit transition blocks so the fragment-level transition
    validation is exercised only by the storyboard-level check.
    """
    fragment_dir.mkdir(parents=True, exist_ok=True)
    for scene in storyboard.scenes:
        frag: dict[str, Any] = {
            "scene_index": scene.index,
            "duration_s": scene.duration_s,
            "stacks": [],
        }
        path = fragment_dir / f"scene_{scene.index:02d}.yaml"
        path.write_text(yaml.dump(frag), encoding="utf-8")


# ---------------------------------------------------------------------------
# TestSchemaValidation — tests/storyboards/ CI step
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    """All three canonical storyboards parse cleanly against the Pydantic schema."""

    def test_example_a_exists(self) -> None:
        assert EXAMPLE_A.exists(), f"Missing storyboard file: {EXAMPLE_A}"

    def test_example_b_exists(self) -> None:
        assert EXAMPLE_B.exists(), f"Missing storyboard file: {EXAMPLE_B}"

    def test_example_c_exists(self) -> None:
        assert EXAMPLE_C.exists(), f"Missing storyboard file: {EXAMPLE_C}"

    def test_example_a_parses(self) -> None:
        sb = load_storyboard_yaml(EXAMPLE_A)
        assert isinstance(sb, Storyboard)

    def test_example_b_parses(self) -> None:
        sb = load_storyboard_yaml(EXAMPLE_B)
        assert isinstance(sb, Storyboard)

    def test_example_c_parses(self) -> None:
        sb = load_storyboard_yaml(EXAMPLE_C)
        assert isinstance(sb, Storyboard)

    def test_example_a_project_meta(self) -> None:
        sb = load_storyboard_yaml(EXAMPLE_A)
        assert sb.project.title == "Through the Pines"
        assert sb.project.total_duration_s == pytest.approx(10.0)
        assert len(sb.scenes) == 1

    def test_example_b_project_meta(self) -> None:
        sb = load_storyboard_yaml(EXAMPLE_B)
        assert sb.project.title == "Four Climates"
        assert sb.project.total_duration_s == pytest.approx(24.0)
        assert len(sb.scenes) == 4
        assert len(sb.casting) == 1
        assert sb.casting[0].id == "red_bird"

    def test_example_c_project_meta(self) -> None:
        sb = load_storyboard_yaml(EXAMPLE_C)
        assert sb.project.title == "The Door"
        assert sb.project.total_duration_s == pytest.approx(8.0)
        assert len(sb.scenes) == 2
        assert len(sb.casting) == 1
        assert sb.casting[0].id == "figure"

    def test_example_a_arc_structure(self) -> None:
        sb = load_storyboard_yaml(EXAMPLE_A)
        assert sb.arc.structure == "establish_disrupt_resolve"
        assert len(sb.arc.beats) == 3

    def test_example_b_arc_structure(self) -> None:
        sb = load_storyboard_yaml(EXAMPLE_B)
        assert sb.arc.structure == "biome_tour"
        assert len(sb.arc.beats) == 4

    def test_example_c_arc_structure(self) -> None:
        sb = load_storyboard_yaml(EXAMPLE_C)
        assert sb.arc.structure == "portal_reveal"
        assert len(sb.arc.beats) == 3

    def test_example_b_transition_pairings_valid(self) -> None:
        """Example B has biome_wipe transitions; all pairs must be correct."""
        sb = load_storyboard_yaml(EXAMPLE_B)
        scene_by_idx = {s.index: s for s in sb.scenes}
        # Scenes 1-4 with biome_wipe; scene 1→2, 2→3, 3→4 must be paired
        for idx in (1, 2, 3):
            scene = scene_by_idx[idx]
            assert scene.transition_out is not None, f"Scene {idx} has no transition_out"
            assert scene.transition_out.type == "biome_wipe"
            assert scene.transition_out.paired_scene == idx + 1
            next_scene = scene_by_idx[idx + 1]
            assert next_scene.transition_in.type == "biome_wipe"
            assert next_scene.transition_in.paired_scene == idx

    def test_example_c_transition_pairings_valid(self) -> None:
        """Example C has portal_reveal transitions; the pair must be symmetric."""
        sb = load_storyboard_yaml(EXAMPLE_C)
        scene1 = next(s for s in sb.scenes if s.index == 1)
        scene2 = next(s for s in sb.scenes if s.index == 2)
        assert scene1.transition_out is not None
        assert scene1.transition_out.type == "portal_reveal"
        assert scene1.transition_out.paired_scene == 2
        assert scene2.transition_in.type == "portal_reveal"
        assert scene2.transition_in.paired_scene == 1

    def test_json_schema_exportable(self) -> None:
        """The Storyboard JSON Schema can be generated (no exception)."""
        schema = Storyboard.model_json_schema()
        assert isinstance(schema, dict)
        assert "title" in schema or "$defs" in schema

    def test_round_trip_example_a(self) -> None:
        """YAML → Storyboard → JSON → dict → Storyboard is lossless."""
        sb_original = load_storyboard_yaml(EXAMPLE_A)
        dumped_json = sb_original.model_dump_json()
        data = json.loads(dumped_json)
        sb_reload = Storyboard.model_validate(data)
        assert sb_reload.project.title == sb_original.project.title
        assert sb_reload.project.total_duration_s == sb_original.project.total_duration_s
        assert len(sb_reload.scenes) == len(sb_original.scenes)


# ---------------------------------------------------------------------------
# TestMergerCI — merger accepts all three storyboards with stub fragments
# ---------------------------------------------------------------------------


class TestMergerCI:
    """The merger runs cleanly on all three storyboards with minimal stub fragments."""

    def test_example_a_merge_succeeds(self, tmp_path: Path) -> None:
        sb = load_storyboard_yaml(EXAMPLE_A)
        frag_dir = tmp_path / "fragments_a"
        _write_stub_fragments(frag_dir, sb)
        merged, log = merge(frag_dir, sb)
        assert isinstance(merged, dict)
        assert "version" in merged
        assert merged.get("version") == 1

    def test_example_b_merge_succeeds(self, tmp_path: Path) -> None:
        sb = load_storyboard_yaml(EXAMPLE_B)
        frag_dir = tmp_path / "fragments_b"
        _write_stub_fragments(frag_dir, sb)
        merged, log = merge(frag_dir, sb)
        assert isinstance(merged, dict)
        assert len(merged.get("scenes", [])) == 4

    def test_example_c_merge_succeeds(self, tmp_path: Path) -> None:
        sb = load_storyboard_yaml(EXAMPLE_C)
        frag_dir = tmp_path / "fragments_c"
        _write_stub_fragments(frag_dir, sb)
        merged, log = merge(frag_dir, sb)
        assert isinstance(merged, dict)
        assert len(merged.get("scenes", [])) == 2

    def test_example_a_merge_log_json_parseable(self, tmp_path: Path) -> None:
        sb = load_storyboard_yaml(EXAMPLE_A)
        frag_dir = tmp_path / "fragments_a_log"
        _write_stub_fragments(frag_dir, sb)
        output_dir = tmp_path / "output_a"
        merged, log = merge(frag_dir, sb, output_dir=output_dir)
        log_path = output_dir / "scene.merge.log.json"
        assert log_path.exists(), "merge log was not written"
        with log_path.open() as fh:
            log_data = json.load(fh)
        assert log_data["scene_count"] == 1
        assert log_data["storyboard_title"] == "Through the Pines"

    def test_example_b_merge_log_scene_count(self, tmp_path: Path) -> None:
        sb = load_storyboard_yaml(EXAMPLE_B)
        frag_dir = tmp_path / "fragments_b_log"
        _write_stub_fragments(frag_dir, sb)
        output_dir = tmp_path / "output_b"
        merged, log = merge(frag_dir, sb, output_dir=output_dir)
        assert log["scene_count"] == 4

    def test_example_b_merge_start_times_monotonic(self, tmp_path: Path) -> None:
        """Cumulative start_t must be monotonically increasing."""
        sb = load_storyboard_yaml(EXAMPLE_B)
        frag_dir = tmp_path / "fragments_b_timing"
        _write_stub_fragments(frag_dir, sb)
        merged, _ = merge(frag_dir, sb)
        scenes = merged.get("scenes", [])
        start_times = [s["start_t"] for s in scenes]
        assert start_times == sorted(start_times), "start_t values not monotonically increasing"
        assert start_times[0] == pytest.approx(0.0), "First scene must start at 0.0"

    def test_example_c_merge_writes_scene_yaml(self, tmp_path: Path) -> None:
        """merge() writes scene.yaml to output_dir when given."""
        sb = load_storyboard_yaml(EXAMPLE_C)
        frag_dir = tmp_path / "fragments_c_write"
        _write_stub_fragments(frag_dir, sb)
        output_dir = tmp_path / "output_c"
        merge(frag_dir, sb, output_dir=output_dir)
        scene_yaml = output_dir / "scene.yaml"
        assert scene_yaml.exists(), "merger did not write scene.yaml"
        with scene_yaml.open() as fh:
            data = yaml.safe_load(fh)
        assert data.get("version") == 1

    def test_merge_rejects_missing_fragment(self, tmp_path: Path) -> None:
        """Merger raises MergeError when a required fragment is missing."""
        sb = load_storyboard_yaml(EXAMPLE_B)  # 4 scenes
        frag_dir = tmp_path / "fragments_b_missing"
        _write_stub_fragments(frag_dir, sb)
        # Remove scene 2 fragment to create a gap
        (frag_dir / "scene_02.yaml").unlink()
        with pytest.raises(MergeError, match="missing fragments"):
            merge(frag_dir, sb)

    def test_merge_rejects_extra_fragment(self, tmp_path: Path) -> None:
        """Merger raises MergeError when an unexpected fragment is present."""
        sb = load_storyboard_yaml(EXAMPLE_A)  # 1 scene
        frag_dir = tmp_path / "fragments_a_extra"
        _write_stub_fragments(frag_dir, sb)
        # Add a spurious second fragment
        extra = {"scene_index": 99, "duration_s": 5.0, "stacks": []}
        (frag_dir / "scene_99.yaml").write_text(yaml.dump(extra), encoding="utf-8")
        with pytest.raises(MergeError, match="unexpected"):
            merge(frag_dir, sb)

    def test_bad_transition_pairing_is_rejected(self, tmp_path: Path) -> None:
        """Broken transition pairings are caught at schema validation time.

        The Pydantic schema validates transition pair symmetry in a model
        validator (§11.3); a storyboard with an inconsistent paired_scene
        cross-reference raises ValidationError before it can reach the merger.
        This test verifies that the system does reject bad pairings.
        """
        from pydantic import ValidationError

        with EXAMPLE_B.open() as fh:
            data = yaml.safe_load(fh)

        # Corrupt the paired_scene on scene 2's transition_in (should be 1, set to 99)
        data["scenes"][1]["transition_in"]["paired_scene"] = 99

        with pytest.raises(ValidationError, match="paired_scene|pairing"):
            Storyboard.model_validate(data)

    def test_merger_rejects_bad_fragment_pairing(self, tmp_path: Path) -> None:
        """Merger catches transition type mismatches in fragment-level blocks.

        When a fragment explicitly declares a transition type that does not
        match the storyboard's expected pairing, the merger raises MergeError.
        """
        sb = load_storyboard_yaml(EXAMPLE_B)
        frag_dir = tmp_path / "fragments_b_frag_bad"
        _write_stub_fragments(frag_dir, sb)

        # Overwrite scene_01.yaml with an explicit mismatched transition_out
        frag_data = {
            "scene_index": 1,
            "duration_s": 5.0,
            "stacks": [],
            # Claims portal_reveal but storyboard says biome_wipe → mismatch
            "transition_out": {"type": "portal_reveal", "paired_scene": 2},
        }
        (frag_dir / "scene_01.yaml").write_text(yaml.dump(frag_data), encoding="utf-8")

        with pytest.raises(MergeError, match="Transition|pairing"):
            merge(frag_dir, sb)
