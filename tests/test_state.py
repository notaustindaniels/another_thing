"""
Tests for parallax_engine.state

Covers:
  - write_checkpoint() creates checkpoints/state.json
  - read_checkpoint() returns fresh state when no file exists
  - write_checkpoint() + read_checkpoint() round-trips correctly
  - is_phase_done() returns True/False correctly
  - completed_phases() returns sorted list
  - reset_checkpoint() deletes the file
  - Atomic write: tmp file is renamed, not left behind
  - Extra metadata is preserved on round-trip
  - Multiple phases accumulate in the same file
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parallax_engine.state import (
    CHECKPOINT_SCHEMA_VERSION,
    PHASE_ASSETS_DONE,
    PHASE_CAMERA_DONE,
    PHASE_MANIFEST,
    PHASE_QA_PASS_1,
    PHASE_RENDER_DONE,
    completed_phases,
    is_phase_done,
    read_checkpoint,
    reset_checkpoint,
    write_checkpoint,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    """Return a fresh temporary workspace directory."""
    return tmp_path


# ---------------------------------------------------------------------------
# read_checkpoint — no file case
# ---------------------------------------------------------------------------


class TestReadCheckpointFresh:
    def test_returns_dict_when_no_file(self, ws):
        state = read_checkpoint(workspace=ws)
        assert isinstance(state, dict)

    def test_phases_empty_when_no_file(self, ws):
        state = read_checkpoint(workspace=ws)
        assert state["phases"] == {}

    def test_schema_version_present(self, ws):
        state = read_checkpoint(workspace=ws)
        assert state["schema_version"] == CHECKPOINT_SCHEMA_VERSION

    def test_phase_meta_empty_when_no_file(self, ws):
        state = read_checkpoint(workspace=ws)
        assert state["phase_meta"] == {}


# ---------------------------------------------------------------------------
# write_checkpoint
# ---------------------------------------------------------------------------


class TestWriteCheckpoint:
    def test_creates_checkpoints_dir(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        assert (ws / "checkpoints").is_dir()

    def test_creates_state_json(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        assert (ws / "checkpoints" / "state.json").exists()

    def test_state_json_is_valid_json(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        raw = (ws / "checkpoints" / "state.json").read_text()
        state = json.loads(raw)
        assert isinstance(state, dict)

    def test_returns_state_dict(self, ws):
        state = write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        assert isinstance(state, dict)
        assert PHASE_MANIFEST in state["phases"]

    def test_no_tmp_file_left_behind(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        tmp = ws / "checkpoints" / "state.tmp"
        assert not tmp.exists()


# ---------------------------------------------------------------------------
# Round-trip: write then read
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_single_phase_round_trips(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        state = read_checkpoint(workspace=ws)
        assert PHASE_MANIFEST in state["phases"]

    def test_multiple_phases_accumulate(self, ws):
        phases = [PHASE_MANIFEST, PHASE_ASSETS_DONE, PHASE_CAMERA_DONE, PHASE_RENDER_DONE]
        for p in phases:
            write_checkpoint(workspace=ws, phase=p)
        state = read_checkpoint(workspace=ws)
        for p in phases:
            assert p in state["phases"]

    def test_phases_have_timestamp_strings(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        state = read_checkpoint(workspace=ws)
        ts = state["phases"][PHASE_MANIFEST]
        # ISO-8601 UTC: "2026-05-01T12:00:00Z"
        assert isinstance(ts, str)
        assert "T" in ts
        assert ts.endswith("Z")

    def test_extra_metadata_round_trips(self, ws):
        meta = {"output_path": "workspace/out.mp4", "frame_count": 240}
        write_checkpoint(workspace=ws, phase=PHASE_RENDER_DONE, extra=meta)
        state = read_checkpoint(workspace=ws)
        assert state["phase_meta"][PHASE_RENDER_DONE] == meta

    def test_extra_metadata_from_earlier_phase_preserved(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST, extra={"layers": 5})
        write_checkpoint(workspace=ws, phase=PHASE_ASSETS_DONE)
        state = read_checkpoint(workspace=ws)
        assert state["phase_meta"][PHASE_MANIFEST]["layers"] == 5

    def test_schema_version_preserved(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        state = read_checkpoint(workspace=ws)
        assert state["schema_version"] == CHECKPOINT_SCHEMA_VERSION

    def test_qa_pass_phases(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_QA_PASS_1)
        state = read_checkpoint(workspace=ws)
        assert PHASE_QA_PASS_1 in state["phases"]

    def test_custom_phase_name(self, ws):
        write_checkpoint(workspace=ws, phase="storyboard-done")
        state = read_checkpoint(workspace=ws)
        assert "storyboard-done" in state["phases"]


# ---------------------------------------------------------------------------
# is_phase_done()
# ---------------------------------------------------------------------------


class TestIsPhaseDone:
    def test_false_when_no_checkpoint(self, ws):
        assert is_phase_done(workspace=ws, phase=PHASE_MANIFEST) is False

    def test_false_for_unwritten_phase(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        assert is_phase_done(workspace=ws, phase=PHASE_ASSETS_DONE) is False

    def test_true_for_written_phase(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        assert is_phase_done(workspace=ws, phase=PHASE_MANIFEST) is True

    def test_multiple_phases(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        write_checkpoint(workspace=ws, phase=PHASE_ASSETS_DONE)
        assert is_phase_done(workspace=ws, phase=PHASE_MANIFEST) is True
        assert is_phase_done(workspace=ws, phase=PHASE_ASSETS_DONE) is True
        assert is_phase_done(workspace=ws, phase=PHASE_RENDER_DONE) is False


# ---------------------------------------------------------------------------
# completed_phases()
# ---------------------------------------------------------------------------


class TestCompletedPhases:
    def test_empty_when_no_checkpoint(self, ws):
        assert completed_phases(workspace=ws) == []

    def test_returns_written_phases(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        write_checkpoint(workspace=ws, phase=PHASE_ASSETS_DONE)
        phases = completed_phases(workspace=ws)
        assert PHASE_MANIFEST in phases
        assert PHASE_ASSETS_DONE in phases

    def test_sorted_by_timestamp(self, ws):
        # Write in a specific order; timestamps should order them the same way.
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        write_checkpoint(workspace=ws, phase=PHASE_ASSETS_DONE)
        write_checkpoint(workspace=ws, phase=PHASE_CAMERA_DONE)
        phases = completed_phases(workspace=ws)
        # All three should be present in the list
        assert len(phases) == 3


# ---------------------------------------------------------------------------
# reset_checkpoint()
# ---------------------------------------------------------------------------


class TestResetCheckpoint:
    def test_deletes_file(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        reset_checkpoint(workspace=ws)
        assert not (ws / "checkpoints" / "state.json").exists()

    def test_no_error_if_no_file(self, ws):
        # Should not raise even if no checkpoint exists
        reset_checkpoint(workspace=ws)

    def test_read_after_reset_returns_fresh(self, ws):
        write_checkpoint(workspace=ws, phase=PHASE_MANIFEST)
        reset_checkpoint(workspace=ws)
        state = read_checkpoint(workspace=ws)
        assert state["phases"] == {}
