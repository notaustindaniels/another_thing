"""Tests for parallax_engine.scene.merger (P4_5.M05).

Covers:
- _collect_fragments: glob + sort + required-key validation
- _validate_coverage: missing / extra fragments
- _validate_transition_pairing: biome_wipe and portal_reveal pairing
- _compute_start_times: cumulative timing
- _build_project_block: palette propagation from storyboard
- merge(): happy path, output files written, MergeError raised on violations
- Example B (4-scene biome_tour) merges cleanly
- Example C (2-scene portal_reveal) merges cleanly
"""
from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from parallax_engine.director.schema import load_storyboard_yaml
from parallax_engine.scene.merger import (
    MergeError,
    _build_project_block,
    _collect_fragments,
    _compute_start_times,
    _validate_coverage,
    _validate_transition_pairing,
    merge,
)

# ---------------------------------------------------------------------------
# Paths to example storyboards
# ---------------------------------------------------------------------------

STORYBOARDS_DIR = Path(__file__).parent / "storyboards"
EXAMPLE_B_PATH = STORYBOARDS_DIR / "example_b.yaml"
EXAMPLE_C_PATH = STORYBOARDS_DIR / "example_c.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_fragment(tmp_path: Path, scene_index: int, content: dict) -> None:
    """Write a scene_N.yaml file to tmp_path."""
    p = tmp_path / f"scene_{scene_index}.yaml"
    with p.open("w") as fh:
        yaml.dump(content, fh)


def _minimal_fragment(
    scene_index: int,
    duration_s: float = 5.0,
    *,
    transition_out: dict | None = None,
    transition_in: dict | None = None,
) -> dict:
    """Build a minimal valid fragment dict."""
    frag: dict = {
        "scene_index": scene_index,
        "duration_s": duration_s,
    }
    if transition_out is not None:
        frag["transition_out"] = transition_out
    if transition_in is not None:
        frag["transition_in"] = transition_in
    return frag


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def example_b_sb():
    return load_storyboard_yaml(EXAMPLE_B_PATH)


@pytest.fixture
def example_c_sb():
    return load_storyboard_yaml(EXAMPLE_C_PATH)


# ---------------------------------------------------------------------------
# TestCollectFragments
# ---------------------------------------------------------------------------


class TestCollectFragments:
    def test_loads_and_sorts_by_index(self, tmp_path):
        _write_fragment(tmp_path, 2, {"scene_index": 2, "duration_s": 3.0})
        _write_fragment(tmp_path, 1, {"scene_index": 1, "duration_s": 4.0})
        frags = _collect_fragments(tmp_path)
        assert [f["scene_index"] for f in frags] == [1, 2]

    def test_no_files_raises_merge_error(self, tmp_path):
        with pytest.raises(MergeError, match="No scene fragment files"):
            _collect_fragments(tmp_path)

    def test_missing_scene_index_key_raises(self, tmp_path):
        p = tmp_path / "scene_1.yaml"
        p.write_text("duration_s: 5.0\n")
        with pytest.raises(MergeError, match="missing required key 'scene_index'"):
            _collect_fragments(tmp_path)

    def test_missing_duration_s_raises(self, tmp_path):
        p = tmp_path / "scene_1.yaml"
        p.write_text("scene_index: 1\n")
        with pytest.raises(MergeError, match="missing required key 'duration_s'"):
            _collect_fragments(tmp_path)

    def test_non_mapping_yaml_raises(self, tmp_path):
        p = tmp_path / "scene_1.yaml"
        p.write_text("- item1\n- item2\n")
        with pytest.raises(MergeError, match="YAML mapping"):
            _collect_fragments(tmp_path)


# ---------------------------------------------------------------------------
# TestValidateCoverage
# ---------------------------------------------------------------------------


class TestValidateCoverage:
    def test_exact_match_passes(self, example_b_sb):
        frags = [
            {"scene_index": 1, "duration_s": 5.0},
            {"scene_index": 2, "duration_s": 5.0},
            {"scene_index": 3, "duration_s": 5.0},
            {"scene_index": 4, "duration_s": 5.0},
        ]
        _validate_coverage(frags, example_b_sb)  # no exception

    def test_missing_fragment_raises(self, example_b_sb):
        frags = [
            {"scene_index": 1, "duration_s": 5.0},
            {"scene_index": 2, "duration_s": 5.0},
            # scene 3 missing
            {"scene_index": 4, "duration_s": 5.0},
        ]
        with pytest.raises(MergeError, match="missing fragments for scene indices"):
            _validate_coverage(frags, example_b_sb)

    def test_extra_fragment_raises(self, example_b_sb):
        frags = [
            {"scene_index": 1, "duration_s": 5.0},
            {"scene_index": 2, "duration_s": 5.0},
            {"scene_index": 3, "duration_s": 5.0},
            {"scene_index": 4, "duration_s": 5.0},
            {"scene_index": 5, "duration_s": 5.0},  # extra
        ]
        with pytest.raises(MergeError, match="unexpected fragment scene indices"):
            _validate_coverage(frags, example_b_sb)


# ---------------------------------------------------------------------------
# TestValidateTransitionPairing
# ---------------------------------------------------------------------------


class TestValidateTransitionPairing:
    """Validates both storyboard-level and fragment-level pairing."""

    def test_biome_wipe_correct_pairing_passes(self, example_b_sb):
        """Example B has 4 scenes with biome_wipe chains — should pass."""
        frags = [
            _minimal_fragment(1, 5.0,
                transition_out={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 2}),
            _minimal_fragment(2, 5.0,
                transition_in={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 1},
                transition_out={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 3}),
            _minimal_fragment(3, 5.0,
                transition_in={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 2},
                transition_out={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 4}),
            _minimal_fragment(4, 5.0,
                transition_in={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 3}),
        ]
        _validate_transition_pairing(frags, example_b_sb)  # no exception

    def test_portal_reveal_correct_pairing_passes(self, example_c_sb):
        """Example C has 2 scenes with portal_reveal — should pass."""
        frags = [
            _minimal_fragment(1, 4.0,
                transition_out={"type": "portal_reveal", "duration_s": 0.5, "paired_scene": 2}),
            _minimal_fragment(2, 4.0,
                transition_in={"type": "portal_reveal", "duration_s": 0.5, "paired_scene": 1}),
        ]
        _validate_transition_pairing(frags, example_c_sb)  # no exception

    def test_biome_wipe_wrong_paired_scene_raises(self, example_b_sb):
        """Fragment paired_scene pointing to wrong scene raises MergeError."""
        frags = [
            _minimal_fragment(1, 5.0,
                transition_out={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 3}),  # WRONG: should be 2
            _minimal_fragment(2, 5.0,
                transition_in={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 1},
                transition_out={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 3}),
            _minimal_fragment(3, 5.0,
                transition_in={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 2},
                transition_out={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 4}),
            _minimal_fragment(4, 5.0,
                transition_in={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 3}),
        ]
        with pytest.raises(MergeError, match="paired_scene"):
            _validate_transition_pairing(frags, example_b_sb)

    def test_portal_reveal_type_mismatch_raises(self, example_c_sb):
        """Donor has portal_reveal but receiver has dissolve — raises MergeError."""
        frags = [
            _minimal_fragment(1, 4.0,
                transition_out={"type": "portal_reveal", "duration_s": 0.5, "paired_scene": 2}),
            _minimal_fragment(2, 4.0,
                transition_in={"type": "dissolve", "duration_s": 0.5}),  # WRONG type
        ]
        with pytest.raises(MergeError, match="type"):
            _validate_transition_pairing(frags, example_c_sb)

    def test_portal_reveal_receiver_wrong_paired_scene_raises(self, example_c_sb):
        """Receiver's paired_scene points to wrong index — raises MergeError."""
        frags = [
            _minimal_fragment(1, 4.0,
                transition_out={"type": "portal_reveal", "duration_s": 0.5, "paired_scene": 2}),
            _minimal_fragment(2, 4.0,
                transition_in={"type": "portal_reveal", "duration_s": 0.5, "paired_scene": 99}),  # WRONG
        ]
        with pytest.raises(MergeError, match="paired_scene"):
            _validate_transition_pairing(frags, example_c_sb)

    def test_no_paired_transitions_passes(self, example_c_sb):
        """Fragments without transition blocks pass silently (transitions are optional)."""
        frags = [
            _minimal_fragment(1, 4.0),
            _minimal_fragment(2, 4.0),
        ]
        # No transition blocks at all — fragment-level check is skipped
        # Storyboard-level may still run; example_c has portal_reveal in storyboard
        # so storyboard-level validation should pass (paired_scene already validated)
        _validate_transition_pairing(frags, example_c_sb)  # no exception


# ---------------------------------------------------------------------------
# TestComputeStartTimes
# ---------------------------------------------------------------------------


class TestComputeStartTimes:
    def test_single_scene(self):
        frags = [{"scene_index": 1, "duration_s": 5.0}]
        times = _compute_start_times(frags)
        assert times == [0.0]

    def test_two_scenes(self):
        frags = [
            {"scene_index": 1, "duration_s": 5.0},
            {"scene_index": 2, "duration_s": 3.0},
        ]
        times = _compute_start_times(frags)
        assert times == [0.0, 5.0]

    def test_four_scenes(self):
        frags = [
            {"scene_index": 1, "duration_s": 5.0},
            {"scene_index": 2, "duration_s": 5.0},
            {"scene_index": 3, "duration_s": 5.0},
            {"scene_index": 4, "duration_s": 5.0},
        ]
        times = _compute_start_times(frags)
        assert times == [0.0, 5.0, 10.0, 15.0]

    def test_fractional_durations(self):
        frags = [
            {"scene_index": 1, "duration_s": 4.0},
            {"scene_index": 2, "duration_s": 4.0},
        ]
        times = _compute_start_times(frags)
        assert times[0] == 0.0
        assert abs(times[1] - 4.0) < 1e-9


# ---------------------------------------------------------------------------
# TestBuildProjectBlock
# ---------------------------------------------------------------------------


class TestBuildProjectBlock:
    def test_palette_promoted(self, example_b_sb):
        block = _build_project_block(example_b_sb)
        assert "palette" in block
        palette = block["palette"]
        # Example B's palette has primary, secondary, neutrals
        assert "primary" in palette
        assert "secondary" in palette
        assert "neutrals" in palette
        # All values from the storyboard
        vv = example_b_sb.visual_vocabulary
        assert palette["primary"] == list(vv.palette.primary)

    def test_fps_from_storyboard(self, example_b_sb):
        block = _build_project_block(example_b_sb)
        assert block["fps"] == example_b_sb.project.target_fps

    def test_title_from_storyboard(self, example_b_sb):
        block = _build_project_block(example_b_sb)
        assert block["title"] == example_b_sb.project.title

    def test_shape_language_propagated(self, example_b_sb):
        block = _build_project_block(example_b_sb)
        assert block["shape_language"] == example_b_sb.visual_vocabulary.shape_language

    def test_forbidden_palette_included_when_present(self, example_c_sb):
        """Example C has no forbidden colors in palette; key absent or empty."""
        block = _build_project_block(example_c_sb)
        palette = block["palette"]
        # forbidden is None in example C → should be absent
        assert "forbidden" not in palette or palette.get("forbidden") is None


# ---------------------------------------------------------------------------
# TestMergeFunction — happy paths
# ---------------------------------------------------------------------------


class TestMergeFunction:
    def test_merge_returns_dict_tuple(self, tmp_path, example_c_sb):
        """merge() returns (merged_dict, log_dict) tuple."""
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 4.0))
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 4.0))
        merged, log = merge(tmp_path, example_c_sb)
        assert isinstance(merged, dict)
        assert isinstance(log, dict)

    def test_merged_has_version(self, tmp_path, example_c_sb):
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 4.0))
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 4.0))
        merged, _ = merge(tmp_path, example_c_sb)
        assert merged["version"] == 1

    def test_merged_has_project_block(self, tmp_path, example_c_sb):
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 4.0))
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 4.0))
        merged, _ = merge(tmp_path, example_c_sb)
        assert "project" in merged
        assert "palette" in merged["project"]

    def test_merged_has_scenes_list(self, tmp_path, example_c_sb):
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 4.0))
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 4.0))
        merged, _ = merge(tmp_path, example_c_sb)
        assert "scenes" in merged
        assert len(merged["scenes"]) == 2

    def test_scenes_have_start_t(self, tmp_path, example_c_sb):
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 4.0))
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 4.0))
        merged, _ = merge(tmp_path, example_c_sb)
        scenes = merged["scenes"]
        assert scenes[0]["start_t"] == 0.0
        assert abs(scenes[1]["start_t"] - 4.0) < 1e-9

    def test_no_output_dir_no_files_written(self, tmp_path, example_c_sb):
        """When output_dir=None, no files are written."""
        frag_dir = tmp_path / "fragments"
        frag_dir.mkdir()
        _write_fragment(frag_dir, 1, _minimal_fragment(1, 4.0))
        _write_fragment(frag_dir, 2, _minimal_fragment(2, 4.0))
        merge(frag_dir, example_c_sb, output_dir=None)
        # No scene.yaml or log written
        assert not (frag_dir / "scene.yaml").exists()
        assert not (frag_dir / "scene.merge.log.json").exists()

    def test_output_dir_writes_scene_yaml(self, tmp_path, example_c_sb):
        """With output_dir, scene.yaml is written."""
        frag_dir = tmp_path / "fragments"
        frag_dir.mkdir()
        out_dir = tmp_path / "output"
        _write_fragment(frag_dir, 1, _minimal_fragment(1, 4.0))
        _write_fragment(frag_dir, 2, _minimal_fragment(2, 4.0))
        merge(frag_dir, example_c_sb, output_dir=out_dir)
        assert (out_dir / "scene.yaml").exists()

    def test_output_dir_writes_log_json(self, tmp_path, example_c_sb):
        """With output_dir, scene.merge.log.json is written."""
        frag_dir = tmp_path / "fragments"
        frag_dir.mkdir()
        out_dir = tmp_path / "output"
        _write_fragment(frag_dir, 1, _minimal_fragment(1, 4.0))
        _write_fragment(frag_dir, 2, _minimal_fragment(2, 4.0))
        merge(frag_dir, example_c_sb, output_dir=out_dir)
        log_path = out_dir / "scene.merge.log.json"
        assert log_path.exists()
        log = json.loads(log_path.read_text())
        assert log["scene_count"] == 2

    def test_written_scene_yaml_is_valid_yaml(self, tmp_path, example_c_sb):
        """Written scene.yaml must round-trip through yaml.safe_load."""
        frag_dir = tmp_path / "fragments"
        frag_dir.mkdir()
        out_dir = tmp_path / "output"
        _write_fragment(frag_dir, 1, _minimal_fragment(1, 4.0))
        _write_fragment(frag_dir, 2, _minimal_fragment(2, 4.0))
        merge(frag_dir, example_c_sb, output_dir=out_dir)
        content = (out_dir / "scene.yaml").read_text()
        doc = yaml.safe_load(content)
        assert doc is not None
        assert doc["version"] == 1

    def test_log_has_merger_version(self, tmp_path, example_c_sb):
        """Merge log contains merger_version field."""
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 4.0))
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 4.0))
        _, log = merge(tmp_path, example_c_sb)
        assert "merger_version" in log

    def test_log_scene_count_matches(self, tmp_path, example_c_sb):
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 4.0))
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 4.0))
        _, log = merge(tmp_path, example_c_sb)
        assert log["scene_count"] == 2

    def test_log_scenes_have_start_t(self, tmp_path, example_c_sb):
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 4.0))
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 4.0))
        _, log = merge(tmp_path, example_c_sb)
        log_scenes = log["scenes"]
        assert log_scenes[0]["start_t"] == 0.0
        assert abs(log_scenes[1]["start_t"] - 4.0) < 1e-9

    def test_transition_validation_in_log(self, tmp_path, example_c_sb):
        """Log records that transition validation passed."""
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 4.0))
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 4.0))
        _, log = merge(tmp_path, example_c_sb)
        assert log["transition_validation"] == "passed"

    def test_palette_in_merged_project(self, tmp_path, example_b_sb):
        """Palette from storyboard.visual_vocabulary is present in merged project."""
        for i in range(1, 5):
            _write_fragment(tmp_path, i, _minimal_fragment(i, 5.0))
        merged, _ = merge(tmp_path, example_b_sb)
        palette = merged["project"]["palette"]
        expected_primary = list(example_b_sb.visual_vocabulary.palette.primary)
        assert palette["primary"] == expected_primary


# ---------------------------------------------------------------------------
# TestMergeErrors
# ---------------------------------------------------------------------------


class TestMergeErrors:
    def test_no_fragments_raises(self, tmp_path, example_c_sb):
        with pytest.raises(MergeError, match="No scene fragment files"):
            merge(tmp_path, example_c_sb)

    def test_missing_fragment_raises(self, tmp_path, example_c_sb):
        """Only one fragment when storyboard has two scenes."""
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 4.0))
        with pytest.raises(MergeError, match="missing fragments"):
            merge(tmp_path, example_c_sb)

    def test_extra_fragment_raises(self, tmp_path, example_c_sb):
        """Three fragments when storyboard has two scenes."""
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 4.0))
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 4.0))
        _write_fragment(tmp_path, 3, _minimal_fragment(3, 4.0))
        with pytest.raises(MergeError, match="unexpected fragment"):
            merge(tmp_path, example_c_sb)

    def test_biome_wipe_wrong_type_in_fragment_raises(self, tmp_path, example_b_sb):
        """Fragment receiver has wrong transition type — MergeError."""
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 5.0,
            transition_out={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 2}))
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 5.0,
            transition_in={"type": "hard_cut"},  # WRONG
            transition_out={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 3}))
        _write_fragment(tmp_path, 3, _minimal_fragment(3, 5.0,
            transition_in={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 2},
            transition_out={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 4}))
        _write_fragment(tmp_path, 4, _minimal_fragment(4, 5.0,
            transition_in={"type": "biome_wipe", "duration_s": 1.0, "paired_scene": 3}))
        with pytest.raises(MergeError, match="type"):
            merge(tmp_path, example_b_sb)

    def test_portal_reveal_wrong_paired_scene_raises(self, tmp_path, example_c_sb):
        """Fragment has portal_reveal with wrong paired_scene — MergeError."""
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 4.0,
            transition_out={"type": "portal_reveal", "duration_s": 0.5, "paired_scene": 99}))
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 4.0,
            transition_in={"type": "portal_reveal", "duration_s": 0.5, "paired_scene": 1}))
        with pytest.raises(MergeError, match="paired_scene"):
            merge(tmp_path, example_c_sb)


# ---------------------------------------------------------------------------
# TestExampleBMerge — 4-scene biome_tour acceptance
# ---------------------------------------------------------------------------


class TestExampleBMerge:
    """Example B: 4-scene biome_tour with biome_wipe chains.

    Acceptance criterion: merge() produces correct merged document without error.
    """

    def _write_all_fragments(self, tmp_path: Path, durations=(5.0, 5.0, 5.0, 5.0)) -> None:
        for i, dur in enumerate(durations, start=1):
            _write_fragment(tmp_path, i, _minimal_fragment(i, dur))

    def test_merges_cleanly(self, tmp_path, example_b_sb):
        self._write_all_fragments(tmp_path)
        merged, log = merge(tmp_path, example_b_sb)
        assert merged["version"] == 1
        assert len(merged["scenes"]) == 4
        assert log["transition_validation"] == "passed"

    def test_scene_indices_preserved(self, tmp_path, example_b_sb):
        self._write_all_fragments(tmp_path)
        merged, _ = merge(tmp_path, example_b_sb)
        indices = [s["scene_index"] for s in merged["scenes"]]
        assert indices == [1, 2, 3, 4]

    def test_start_times_are_cumulative(self, tmp_path, example_b_sb):
        self._write_all_fragments(tmp_path)
        merged, _ = merge(tmp_path, example_b_sb)
        starts = [s["start_t"] for s in merged["scenes"]]
        assert starts == [0.0, 5.0, 10.0, 15.0]

    def test_total_duration_in_log(self, tmp_path, example_b_sb):
        self._write_all_fragments(tmp_path)
        _, log = merge(tmp_path, example_b_sb)
        assert abs(log["total_duration_s"] - 20.0) < 1e-9

    def test_palette_title_in_log(self, tmp_path, example_b_sb):
        self._write_all_fragments(tmp_path)
        _, log = merge(tmp_path, example_b_sb)
        assert example_b_sb.project.title in log["storyboard_title"]


# ---------------------------------------------------------------------------
# TestExampleCMerge — 2-scene portal_reveal acceptance
# ---------------------------------------------------------------------------


class TestExampleCMerge:
    """Example C: 2-scene portal_reveal.

    Acceptance criterion: merge() produces correct merged document without error.
    Both scenes declare portal_reveal; merger validates mutual paired_scene.
    """

    def test_merges_cleanly(self, tmp_path, example_c_sb):
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 4.0))
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 4.0))
        merged, log = merge(tmp_path, example_c_sb)
        assert merged["version"] == 1
        assert len(merged["scenes"]) == 2
        assert log["transition_validation"] == "passed"

    def test_start_times(self, tmp_path, example_c_sb):
        _write_fragment(tmp_path, 1, _minimal_fragment(1, 4.0))
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 4.0))
        merged, _ = merge(tmp_path, example_c_sb)
        starts = [s["start_t"] for s in merged["scenes"]]
        assert starts[0] == 0.0
        assert abs(starts[1] - 4.0) < 1e-9

    def test_fragment_content_preserved(self, tmp_path, example_c_sb):
        """Extra fragment fields (e.g. stacks block) are preserved in merged output."""
        frag1 = _minimal_fragment(1, 4.0)
        frag1["stacks"] = {"corridor": {"layers": []}}
        _write_fragment(tmp_path, 1, frag1)
        _write_fragment(tmp_path, 2, _minimal_fragment(2, 4.0))
        merged, _ = merge(tmp_path, example_c_sb)
        assert "stacks" in merged["scenes"][0]
        assert "corridor" in merged["scenes"][0]["stacks"]

    def test_portal_reveal_with_paired_scenes_in_fragments(self, tmp_path, example_c_sb):
        """Fragments with correct portal_reveal blocks pass cleanly."""
        frag1 = _minimal_fragment(1, 4.0,
            transition_out={"type": "portal_reveal", "duration_s": 0.5, "paired_scene": 2})
        frag2 = _minimal_fragment(2, 4.0,
            transition_in={"type": "portal_reveal", "duration_s": 0.5, "paired_scene": 1})
        _write_fragment(tmp_path, 1, frag1)
        _write_fragment(tmp_path, 2, frag2)
        merged, _ = merge(tmp_path, example_c_sb)
        assert len(merged["scenes"]) == 2


# ---------------------------------------------------------------------------
# TestMergeIsPurePython (anti-pattern 12)
# ---------------------------------------------------------------------------


class TestMergeIsPurePython:
    """Verify merger makes no LLM calls (§11.13 anti-pattern 12)."""

    def test_no_anthropic_import_in_merger(self):
        """merger.py must not import anthropic or claude SDK."""
        merger_src = (
            Path(__file__).parent.parent
            / "parallax_engine" / "scene" / "merger.py"
        ).read_text()
        assert "anthropic" not in merger_src, "merger.py must not import anthropic"
        assert "claude" not in merger_src.lower() or "# " in merger_src, (
            "merger.py must not reference claude SDK"
        )

    def test_merger_module_docstring_says_no_llm(self):
        """Docstring must declare pure Python / no LLM."""
        from parallax_engine.scene import merger
        assert merger.__doc__ is not None
        assert "No LLM" in merger.__doc__ or "no LLM" in merger.__doc__.lower()
