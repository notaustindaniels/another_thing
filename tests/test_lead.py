"""Tests for parallax_engine/lead.py — ParallaxLead orchestrator.

Tests cover:
  - Module import without anthropic installed
  - ParallaxLead.__init__ defaults per §3.6
  - QA pass counter is a Python integer (§9.7)
  - Budget exhaustion → salvage (not crash)
  - Turns exhaustion → salvage (not crash)
  - ClaudeSDKStub basic functionality
  - Full run with stub (happy path)
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

import parallax_engine.lead as lead
from parallax_engine.lead import (
    MAX_BUDGET_USD,
    MAX_QA_PASSES,
    MAX_TURNS,
    PERMISSION_MODE,
    ClaudeSDKStub,
    ParallaxLead,
    RunResult,
    _StubSDKResult,
    _BUDGET_ERROR,
    _TURNS_ERROR,
)


# ---------------------------------------------------------------------------
# Helper factory — injects a stub into ParallaxLead without real SDK
# ---------------------------------------------------------------------------

def make_lead(
    ws: pathlib.Path,
    response_map: dict | None = None,
    **kwargs,
) -> ParallaxLead:
    stub = ClaudeSDKStub(response_map=response_map or {})
    return ParallaxLead(ws, sdk_client_factory=lambda *a: stub, **kwargs)


# ---------------------------------------------------------------------------
# 1. Module import
# ---------------------------------------------------------------------------

class TestModuleImport:
    def test_module_importable(self):
        """lead.py must import without anthropic installed."""
        assert hasattr(lead, "ParallaxLead")
        assert hasattr(lead, "ClaudeSDKStub")
        assert hasattr(lead, "RunResult")

    def test_constants_present(self):
        assert MAX_TURNS == 80
        assert MAX_BUDGET_USD == 2.50
        assert PERMISSION_MODE == "acceptEdits"
        assert MAX_QA_PASSES == 3

    def test_budget_error_string(self):
        assert _BUDGET_ERROR == "error_max_budget_usd"

    def test_turns_error_string(self):
        assert _TURNS_ERROR == "error_max_turns"


# ---------------------------------------------------------------------------
# 2. ParallaxLead.__init__ defaults (§3.6 acceptance criteria)
# ---------------------------------------------------------------------------

class TestParallaxLeadInit:
    def test_defaults(self, tmp_path):
        pl = make_lead(tmp_path)
        assert pl.max_turns == 80
        assert pl.max_budget_usd == 2.50
        assert pl.permission_mode == "acceptEdits"
        assert pl.max_qa_passes == 3

    def test_workspace_is_path(self, tmp_path):
        pl = make_lead(tmp_path)
        assert isinstance(pl.workspace, pathlib.Path)

    def test_workspace_str_coerced_to_path(self, tmp_path):
        pl = make_lead(tmp_path)
        pl2 = ParallaxLead(
            str(tmp_path),
            sdk_client_factory=lambda *a: ClaudeSDKStub(),
        )
        assert isinstance(pl2.workspace, pathlib.Path)

    def test_custom_max_turns(self, tmp_path):
        pl = make_lead(tmp_path, max_turns=10)
        assert pl.max_turns == 10

    def test_custom_budget(self, tmp_path):
        pl = make_lead(tmp_path, max_budget_usd=1.0)
        assert pl.max_budget_usd == 1.0

    def test_custom_permission_mode(self, tmp_path):
        pl = make_lead(tmp_path, permission_mode="acceptEdits")
        assert pl.permission_mode == "acceptEdits"

    def test_custom_max_qa_passes(self, tmp_path):
        pl = make_lead(tmp_path, max_qa_passes=1)
        assert pl.max_qa_passes == 1

    def test_sdk_client_receives_defaults(self, tmp_path):
        calls = []
        def factory(max_turns, max_budget_usd, permission_mode):
            calls.append((max_turns, max_budget_usd, permission_mode))
            return ClaudeSDKStub()
        ParallaxLead(tmp_path, sdk_client_factory=factory)
        assert calls == [(80, 2.50, "acceptEdits")]


# ---------------------------------------------------------------------------
# 3. _StubSDKResult
# ---------------------------------------------------------------------------

class TestStubSDKResult:
    def test_pass_response(self):
        r = _StubSDKResult("PASS")
        assert r.last_content == "PASS"
        assert not r.is_budget_error()
        assert not r.is_turns_error()

    def test_budget_error_detected(self):
        r = _StubSDKResult("error_max_budget_usd exceeded")
        assert r.is_budget_error()
        assert not r.is_turns_error()

    def test_turns_error_detected(self):
        r = _StubSDKResult("error_max_turns exceeded")
        assert r.is_turns_error()
        assert not r.is_budget_error()

    def test_both_errors(self):
        r = _StubSDKResult("error_max_budget_usd and error_max_turns")
        assert r.is_budget_error()
        assert r.is_turns_error()

    def test_default_content(self):
        r = _StubSDKResult()
        assert r.last_content == "PASS"


# ---------------------------------------------------------------------------
# 4. ClaudeSDKStub
# ---------------------------------------------------------------------------

class TestClaudeSDKStub:
    def test_default_response_is_pass(self):
        stub = ClaudeSDKStub()
        r = stub.run(prompt="hello", agent_name="qa-critic")
        assert r.last_content == "PASS"

    def test_response_map(self):
        stub = ClaudeSDKStub(response_map={"scene-designer": "scene written: 3 layers, 1 masks, duration 10s"})
        r = stub.run(prompt="go", agent_name="scene-designer")
        assert "scene written" in r.last_content

    def test_unknown_agent_defaults_pass(self):
        stub = ClaudeSDKStub(response_map={"scene-designer": "custom"})
        r = stub.run(prompt="go", agent_name="qa-critic")
        assert r.last_content == "PASS"

    def test_call_count(self):
        stub = ClaudeSDKStub()
        stub.run(prompt="a", agent_name="x")
        stub.run(prompt="b", agent_name="y")
        assert stub.call_count == 2

    def test_defaults_match_spec(self):
        stub = ClaudeSDKStub()
        assert stub.max_turns == MAX_TURNS
        assert stub.max_budget_usd == MAX_BUDGET_USD
        assert stub.permission_mode == PERMISSION_MODE

    def test_budget_error_response(self):
        stub = ClaudeSDKStub(response_map={"scene-designer": "error_max_budget_usd"})
        r = stub.run(prompt="go", agent_name="scene-designer")
        assert r.is_budget_error()

    def test_turns_error_response(self):
        stub = ClaudeSDKStub(response_map={"camera-pather": "error_max_turns"})
        r = stub.run(prompt="go", agent_name="camera-pather")
        assert r.is_turns_error()


# ---------------------------------------------------------------------------
# 5. QA pass counter is a Python integer (§9.7)
# ---------------------------------------------------------------------------

class TestQAPassCounter:
    def test_qa_passes_returned_as_int(self, tmp_path):
        """The QA loop must return an integer count — never a string or None."""
        pl = make_lead(tmp_path, response_map={"qa-critic": "PASS"})
        qa_passed, qa_passes = pl._run_qa_loop(None)
        assert isinstance(qa_passes, int), (
            f"qa_passes is {type(qa_passes)}, must be int (§9.7)"
        )

    def test_qa_passes_zero_on_pass_first_try(self, tmp_path):
        pl = make_lead(tmp_path, response_map={"qa-critic": "PASS"})
        qa_passed, qa_passes = pl._run_qa_loop(None)
        assert qa_passed is True
        assert qa_passes == 1

    def test_qa_capped_at_max_qa_passes(self, tmp_path):
        """If QA always returns FAIL, the loop stops at max_qa_passes."""
        pl = make_lead(
            tmp_path,
            response_map={"qa-critic": "FAIL: bad colors"},
            max_qa_passes=3,
        )
        qa_passed, qa_passes = pl._run_qa_loop(None)
        assert qa_passed is False
        assert qa_passes == 3

    def test_qa_respects_custom_max(self, tmp_path):
        """max_qa_passes=1 means at most 1 QA call."""
        pl = make_lead(
            tmp_path,
            response_map={"qa-critic": "FAIL: something"},
            max_qa_passes=1,
        )
        qa_passed, qa_passes = pl._run_qa_loop(None)
        assert qa_passes == 1

    def test_qa_prompt_does_not_say_stop(self, tmp_path):
        """The qa-critic prompt must NOT contain 'stop after N passes'."""
        from parallax_engine.subagents import QA_CRITIC
        prompt_lower = QA_CRITIC.system_prompt.lower()
        assert "stop after" not in prompt_lower, (
            "QA stop instruction found in prompt — must be Python counter only (§9.7)"
        )


# ---------------------------------------------------------------------------
# 6. Budget exhaustion → salvage (not crash)
# ---------------------------------------------------------------------------

class TestBudgetSalvage:
    def test_budget_exhaustion_on_scene_designer(self, tmp_path):
        pl = make_lead(tmp_path, response_map={
            "scene-designer": "error_max_budget_usd",
        })
        result = pl.run("a brief")
        assert isinstance(result, RunResult)
        assert not result.ok
        assert result.salvage is True
        assert result.error is not None

    def test_budget_exhaustion_on_camera_pather(self, tmp_path):
        pl = make_lead(tmp_path, response_map={
            "scene-designer": "scene written: 0 layers, 0 masks, duration 0s",
            "camera-pather": "error_max_budget_usd",
        })
        result = pl.run("a brief")
        assert not result.ok
        assert result.salvage is True

    def test_turns_exhaustion_on_qa_critic(self, tmp_path):
        pl = make_lead(tmp_path, response_map={
            "scene-designer": "scene written: 0 layers, 0 masks, duration 0s",
            "camera-pather": "camera path written: 0 keyframes",
            "qa-critic": "error_max_turns",
        })
        result = pl.run("a brief")
        # render will fail (no real scene), so salvage path
        assert isinstance(result, RunResult)

    def test_salvage_with_existing_mp4(self, tmp_path):
        # Create a fake out.mp4
        fake_mp4 = tmp_path / "out.mp4"
        fake_mp4.write_bytes(b"fake")
        pl = make_lead(tmp_path, response_map={
            "scene-designer": "error_max_budget_usd",
        })
        result = pl.run("a brief")
        assert result.salvage
        # salvage picks up the existing mp4
        assert result.out_mp4 == fake_mp4

    def test_no_exception_on_any_error(self, tmp_path):
        """The orchestrator must NEVER raise; always return RunResult."""
        for agent_name in ["scene-designer", "asset-generator", "mask-author", "camera-pather"]:
            pl = make_lead(tmp_path, response_map={
                agent_name: "error_max_budget_usd",
            })
            result = pl.run("brief")
            assert isinstance(result, RunResult)


# ---------------------------------------------------------------------------
# 7. RunResult dataclass
# ---------------------------------------------------------------------------

class TestRunResult:
    def test_default_fields(self):
        r = RunResult(ok=True)
        assert r.ok is True
        assert r.out_mp4 is None
        assert r.salvage is False
        assert r.phases_completed == []
        assert r.error is None

    def test_salvage_result(self):
        r = RunResult(ok=False, salvage=True, error="budget exceeded")
        assert not r.ok
        assert r.salvage
        assert "budget" in r.error


# ---------------------------------------------------------------------------
# 8. Checkpoint resumption
# ---------------------------------------------------------------------------

class TestCheckpointResumption:
    def test_skips_completed_manifest_phase(self, tmp_path):
        """If manifest checkpoint exists, scene-designer should not be called."""
        from parallax_engine.state import write_checkpoint, PHASE_MANIFEST
        write_checkpoint(workspace=tmp_path, phase=PHASE_MANIFEST)

        call_tracker = []
        class TrackingStub(ClaudeSDKStub):
            def run(self, *, agent_name, **kw):
                call_tracker.append(agent_name)
                return super().run(agent_name=agent_name, **kw)

        stub = TrackingStub(response_map={"camera-pather": "camera path written: 2 keyframes"})
        pl = ParallaxLead(tmp_path, sdk_client_factory=lambda *a: stub)
        pl._run_scene_designer("brief")
        # scene-designer should be skipped
        assert "scene-designer" not in call_tracker
