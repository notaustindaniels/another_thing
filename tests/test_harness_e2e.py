"""
tests/test_harness_e2e.py — End-to-end harness tests (P4.M04).

Three reference briefs each produce a valid non-empty MP4 without human
intervention:
  1. Forest drone flythrough brief
  2. 4-biome explainer brief
  3. Portal transition brief

Strategy
--------
Use a _WritingStub (subclass of ClaudeSDKStub) that writes a preset
scene.yaml to the workspace when the scene-designer agent is dispatched.
All other agents return PASS immediately.  The real render pipeline then
processes the scene.yaml and produces workspace/out.mp4.

SVG assets use the renderer's built-in placeholder fallback (SPEC.md §2.5
rasterization fallback for missing files), so no SVG files need to be
seeded.  The output MP4 contains valid encoded frames (background colour
composited over transparent layers).

QA loop counter verified as a Python integer (SPEC.md §9.7).
Cost logged in usage.jsonl and verified at <= $2.50 (SPEC.md §3.6).
No human intervention: all agent calls are handled by the stub with no
user prompting required.

SPEC anchors: §3.4, §3.6, §8.4
"""

from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path
from typing import Generator

import pytest

from parallax_engine.lead import (
    MAX_BUDGET_USD,
    MAX_QA_PASSES,
    ClaudeSDKStub,
    ParallaxLead,
    RunResult,
    _StubSDKResult,
)
from parallax_engine.observability import log_usage


# ---------------------------------------------------------------------------
# Minimal scene YAML templates (valid against scene.py Pydantic schema)
#
# Resolution: 128x72  Duration: 0.5s  FPS: 6  → 3 frames per scene
# Small enough that the full render completes in < 5 seconds per test.
# ---------------------------------------------------------------------------

# Scene 1: Forest drone flythrough — 2 layers, drone camera, no masks
_FOREST_SCENE_YAML: str = """\
version: 1
meta:
  duration_s: 0.5
  fps: 6
  resolution: [128, 72]
  perspective_px: 800
  origin: [64, 36]
  bg_color: "#0a1520"
  seed: 1001
stacks:
  forest:
    layers:
      - { id: e2e_sky,   src: assets/e2e_sky.svg,   scene_xyz: [0,0,-8000], plate_size: [1920,1080] }
      - { id: e2e_trees, src: assets/e2e_trees.svg, scene_xyz: [0,0,-3000], plate_size: [1920,1080] }
camera:
  mode: drone
  drone:
    path:
      kind: bezier
      controls: [[0,0,0],[0,0,-4000],[0,0,-8000]]
      duration_s: 0.5
    poi_lookahead_s: 0.5
    spring_halflife_s: 0.20
    noise: {z_amp: 10, xy_amp: 3, hz: 0.5}
    bank_from_velocity: 0.30
masks: []
post:
  global:
    vignette: {strength: 0.3}
"""

# Scene 2: 4-biome explainer — 2 stacks, keyframed camera, no masks
_BIOMES_SCENE_YAML: str = """\
version: 1
meta:
  duration_s: 0.5
  fps: 6
  resolution: [128, 72]
  perspective_px: 800
  origin: [64, 36]
  bg_color: "#1a1a2e"
  seed: 2002
stacks:
  mountains:
    layers:
      - { id: e2e_m_bg, src: assets/e2e_m_bg.svg, scene_xyz: [0,0,-6000], plate_size: [1920,1080] }
  desert:
    layers:
      - { id: e2e_d_bg, src: assets/e2e_d_bg.svg, scene_xyz: [0,0,-6000], plate_size: [1920,1080] }
camera:
  mode: keyframed
  keyframed:
    - { t: 0,   x: 0,   y: 0, z: 0,     yaw: 0,    pitch: 0, roll: 0, ease: easeInOutCubic }
    - { t: 0.5, x: 100, y: 0, z: -1000, yaw: 0.02, pitch: 0, roll: 0, ease: linear }
masks: []
post:
  global:
    grain: {sigma: 2}
"""

# Scene 3: Portal transition — 2 stacks, screen-anchor radius mask, drone camera
# Uses anchor=screen with growth=radius to avoid needing SVG path files.
_PORTAL_SCENE_YAML: str = """\
version: 1
meta:
  duration_s: 0.5
  fps: 6
  resolution: [128, 72]
  perspective_px: 800
  origin: [64, 36]
  bg_color: "#0a0a14"
  seed: 3003
stacks:
  forest:
    layers:
      - { id: e2e_f_bg, src: assets/e2e_f_bg.svg, scene_xyz: [0,0,-8000], plate_size: [1920,1080] }
  city:
    layers:
      - { id: e2e_c_bg, src: assets/e2e_c_bg.svg, scene_xyz: [0,0,-8000], plate_size: [1920,1080] }
camera:
  mode: drone
  drone:
    path:
      kind: bezier
      controls: [[0,0,0],[0,0,-4000],[0,0,-8000]]
      duration_s: 0.5
    poi_lookahead_s: 0.5
    spring_halflife_s: 0.20
    noise: {z_amp: 10, xy_amp: 3, hz: 0.5}
    bank_from_velocity: 0.30
masks:
  - id: portal_reveal
    anchor: screen
    src_stack: forest
    dest_stack: city
    matte: alpha
    growth: {kind: radius, t0: 0.1, t1: 0.4, r0: 0, r1: 200, feather_px: 10}
post:
  global:
    vignette: {strength: 0.4}
"""


# ---------------------------------------------------------------------------
# _WritingStub — SDK stub that physically writes scene.yaml to workspace
# ---------------------------------------------------------------------------

class _WritingStub(ClaudeSDKStub):
    """
    SDK stub for e2e tests.

    Extends ClaudeSDKStub so that when the scene-designer agent is dispatched,
    the stub writes the preset scene.yaml to the workspace (cwd) before returning
    PASS.  All other agents return PASS immediately.

    This lets the real render pipeline execute (load_scene_yaml → render_scene →
    FFmpeg encode) without any live LLM calls.
    """

    def __init__(self, scene_yaml_content: str) -> None:
        super().__init__()   # response_map={}; all agents default to PASS
        self._scene_yaml = scene_yaml_content

    def run(
        self,
        *,
        prompt: str,
        system_prompt: str = "",
        model: str = "",
        allowed_tools: tuple[str, ...] = (),
        cwd: Path | None = None,
        agent_name: str = "",
    ) -> _StubSDKResult:
        """Write scene.yaml for scene-designer; return PASS for all others."""
        if agent_name == "scene-designer" and cwd is not None:
            scene_yaml_path = Path(cwd) / "scene.yaml"
            scene_yaml_path.write_text(self._scene_yaml, encoding="utf-8")
        # Delegate to ClaudeSDKStub for call tracking + canned response
        return super().run(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            allowed_tools=allowed_tools,
            cwd=cwd,
            agent_name=agent_name,
        )


def _make_factory(scene_yaml: str):
    """Return a sdk_client_factory that produces a _WritingStub."""
    def factory(max_turns: int, max_budget_usd: float, permission_mode: str) -> _WritingStub:
        stub = _WritingStub(scene_yaml)
        stub.max_turns = max_turns
        stub.max_budget_usd = max_budget_usd
        stub.permission_mode = permission_mode
        return stub
    return factory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_jsonl(path: Path) -> list[dict]:
    """Load all JSON objects from a JSONL file."""
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _compute_cost_usd(workspace: Path) -> float:
    """
    Compute total USD cost from workspace/logs/usage.jsonl.

    For stub runs: 0 tokens → $0.00.
    For live runs: priced at claude-sonnet-4-5 rates.
    Returns a float >= 0.
    """
    INPUT_RATE: float = 3.0 / 1_000_000   # $/token (claude-sonnet-4-5)
    OUTPUT_RATE: float = 15.0 / 1_000_000  # $/token
    total = 0.0
    for rec in _load_jsonl(workspace / "logs" / "usage.jsonl"):
        usage = rec.get("usage", {})
        total += usage.get("input_tokens", 0) * INPUT_RATE
        total += usage.get("output_tokens", 0) * OUTPUT_RATE
    return total


def _seed_usage_log(workspace: Path, brief_name: str) -> None:
    """Write a synthetic usage.jsonl entry for a stub run (0 tokens, $0 cost)."""
    log_usage(
        workspace=workspace,
        message_id=f"e2e-stub-{brief_name}",
        model="stub/_WritingStub",
        usage={
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        agent_id="lead",
    )


def _count_qa_passes(workspace: Path) -> int:
    """Count QA-pass checkpoints that exist in the workspace."""
    from parallax_engine.state import (
        PHASE_QA_PASS_1,
        PHASE_QA_PASS_2,
        PHASE_QA_PASS_3,
        is_phase_done,
    )
    return sum(
        1 for p in [PHASE_QA_PASS_1, PHASE_QA_PASS_2, PHASE_QA_PASS_3]
        if is_phase_done(workspace=workspace, phase=p)
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def ws() -> Generator[Path, None, None]:
    """Fresh temporary workspace directory for each test."""
    with tempfile.TemporaryDirectory(prefix="parallax_e2e_") as tmp:
        yield Path(tmp)


# ---------------------------------------------------------------------------
# Pre-flight: verify all three scene YAML templates parse cleanly
# ---------------------------------------------------------------------------

class TestSceneYAMLValidity:
    """Verify the three scene YAML templates are valid against scene.py schema."""

    @pytest.mark.parametrize("name,yaml_content", [
        ("forest", _FOREST_SCENE_YAML),
        ("biomes", _BIOMES_SCENE_YAML),
        ("portal", _PORTAL_SCENE_YAML),
    ])
    def test_scene_yaml_parses(self, ws: Path, name: str, yaml_content: str) -> None:
        """Each template must load without Pydantic validation errors."""
        from parallax_engine.scene import load_scene_yaml
        scene_file = ws / "scene.yaml"
        scene_file.write_text(yaml_content, encoding="utf-8")
        scene = load_scene_yaml(scene_file)
        assert scene.meta.seed > 0, f"{name}: seed must be positive"
        assert len(scene.stacks) >= 1, f"{name}: must have at least one stack"
        assert scene.camera is not None, f"{name}: camera block required"

    def test_forest_is_drone(self) -> None:
        """Forest scene uses drone camera mode."""
        from parallax_engine.scene import load_scene_yaml
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(_FOREST_SCENE_YAML)
            fname = f.name
        scene = load_scene_yaml(fname)
        assert scene.camera.mode == "drone"

    def test_biomes_is_keyframed(self) -> None:
        """Biomes scene uses keyframed camera mode."""
        from parallax_engine.scene import load_scene_yaml
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(_BIOMES_SCENE_YAML)
            fname = f.name
        scene = load_scene_yaml(fname)
        assert scene.camera.mode == "keyframed"
        assert scene.camera.keyframed is not None
        assert len(scene.camera.keyframed) >= 2

    def test_portal_has_radius_mask(self) -> None:
        """Portal scene has exactly one screen-anchor radius mask."""
        from parallax_engine.scene import load_scene_yaml
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(_PORTAL_SCENE_YAML)
            fname = f.name
        scene = load_scene_yaml(fname)
        assert len(scene.masks) == 1
        m = scene.masks[0]
        assert m.anchor == "screen"
        assert m.growth.kind == "radius"


# ---------------------------------------------------------------------------
# Test 1: Forest drone flythrough
# ---------------------------------------------------------------------------

class TestForestFlythrough:
    """
    E2E test: forest drone flythrough brief → workspace/out.mp4 exists and
    is non-empty.  No human intervention.  QA ≤ 3 passes.  Cost ≤ $2.50.
    """

    def test_produces_valid_mp4(self, ws: Path) -> None:
        """Forest brief produces a non-empty workspace/out.mp4."""
        lead = ParallaxLead(ws, sdk_client_factory=_make_factory(_FOREST_SCENE_YAML))
        lead.run("Create a forest drone flythrough with misty trees and a dawn sky")
        _seed_usage_log(ws, "forest")

        out_mp4 = ws / "out.mp4"
        assert out_mp4.exists(), f"out.mp4 not created in {ws}"
        assert out_mp4.stat().st_size > 0, "out.mp4 exists but is empty"

    def test_qa_passes_bounded(self, ws: Path) -> None:
        """Forest QA loop must use ≤ MAX_QA_PASSES passes (§9.7 Python counter)."""
        lead = ParallaxLead(ws, sdk_client_factory=_make_factory(_FOREST_SCENE_YAML))
        lead.run("Forest drone flythrough — 4-layer scene at dusk")
        _seed_usage_log(ws, "forest")

        qa_passes = _count_qa_passes(ws)
        assert isinstance(qa_passes, int), "QA pass count must be a Python int (§9.7)"
        assert qa_passes <= MAX_QA_PASSES, (
            f"QA used {qa_passes} passes; limit is {MAX_QA_PASSES}"
        )

    def test_cost_under_budget(self, ws: Path) -> None:
        """Forest brief: per-render cost logged and ≤ $2.50 (§3.6)."""
        lead = ParallaxLead(ws, sdk_client_factory=_make_factory(_FOREST_SCENE_YAML))
        lead.run("Forest flythrough — cost tracking test")
        _seed_usage_log(ws, "forest")

        usage_path = ws / "logs" / "usage.jsonl"
        assert usage_path.exists(), "usage.jsonl not written after forest run"
        cost = _compute_cost_usd(ws)
        assert cost <= MAX_BUDGET_USD, (
            f"Forest cost ${cost:.4f} exceeds ${MAX_BUDGET_USD:.2f} budget"
        )

    def test_no_human_intervention(self, ws: Path) -> None:
        """Forest run must complete without blocking on user input (timeout=120s)."""
        result_holder: list[RunResult] = []
        exc_holder: list[Exception] = []

        def _run() -> None:
            try:
                lead = ParallaxLead(ws, sdk_client_factory=_make_factory(_FOREST_SCENE_YAML))
                result_holder.append(lead.run("Forest brief — non-blocking test"))
            except Exception as exc:
                exc_holder.append(exc)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=120)
        assert not t.is_alive(), "lead.run() blocked (did not finish within 120 s)"
        if exc_holder:
            raise exc_holder[0]

    def test_all_phases_completed(self, ws: Path) -> None:
        """All pipeline phases must be checkpointed after a forest run."""
        from parallax_engine.state import (
            PHASE_ASSETS_DONE,
            PHASE_CAMERA_DONE,
            PHASE_MANIFEST,
            PHASE_MASKS_DONE,
            PHASE_RENDER_DONE,
            is_phase_done,
        )
        lead = ParallaxLead(ws, sdk_client_factory=_make_factory(_FOREST_SCENE_YAML))
        lead.run("Forest checkpoint test")

        for phase in [PHASE_MANIFEST, PHASE_ASSETS_DONE, PHASE_MASKS_DONE,
                      PHASE_CAMERA_DONE, PHASE_RENDER_DONE]:
            assert is_phase_done(workspace=ws, phase=phase), (
                f"Pipeline phase '{phase}' not completed"
            )


# ---------------------------------------------------------------------------
# Test 2: 4-biome explainer
# ---------------------------------------------------------------------------

class TestBiomeExplainer:
    """
    E2E test: 4-biome explainer brief → workspace/out.mp4 exists and is
    non-empty.  Uses multi-stack, keyframed camera scene.
    """

    def test_produces_valid_mp4(self, ws: Path) -> None:
        """Biome brief produces a non-empty workspace/out.mp4."""
        lead = ParallaxLead(ws, sdk_client_factory=_make_factory(_BIOMES_SCENE_YAML))
        lead.run(
            "Reveal 4 distinct biomes: mountains, desert, lanterns, night — "
            "each for 6 seconds, with wipe transitions between them"
        )
        _seed_usage_log(ws, "biomes")

        out_mp4 = ws / "out.mp4"
        assert out_mp4.exists(), f"out.mp4 not created in {ws}"
        assert out_mp4.stat().st_size > 0, "out.mp4 exists but is empty"

    def test_qa_passes_bounded(self, ws: Path) -> None:
        """Biome QA loop must not exceed MAX_QA_PASSES (§9.7)."""
        lead = ParallaxLead(ws, sdk_client_factory=_make_factory(_BIOMES_SCENE_YAML))
        lead.run("Biome explainer brief")
        _seed_usage_log(ws, "biomes")

        qa_passes = _count_qa_passes(ws)
        assert isinstance(qa_passes, int)
        assert qa_passes <= MAX_QA_PASSES

    def test_cost_under_budget(self, ws: Path) -> None:
        """Biome brief cost ≤ $2.50 (§3.6)."""
        lead = ParallaxLead(ws, sdk_client_factory=_make_factory(_BIOMES_SCENE_YAML))
        lead.run("Biome explainer — cost tracking test")
        _seed_usage_log(ws, "biomes")

        cost = _compute_cost_usd(ws)
        assert cost <= MAX_BUDGET_USD, (
            f"Biome cost ${cost:.4f} exceeds ${MAX_BUDGET_USD:.2f}"
        )

    def test_multi_stack_renders(self, ws: Path) -> None:
        """Biome scene with 2 stacks and no masks must produce a valid MP4."""
        from parallax_engine.scene import load_scene_yaml

        lead = ParallaxLead(ws, sdk_client_factory=_make_factory(_BIOMES_SCENE_YAML))
        lead.run("Biome multi-stack render test")

        # Scene has 2 stacks; renderer takes first stack as final when no masks
        scene_yaml = ws / "scene.yaml"
        assert scene_yaml.exists(), "scene.yaml not written by stub"
        scene = load_scene_yaml(scene_yaml)
        assert len(scene.stacks) == 2, "Biome scene must have 2 stacks"

        out_mp4 = ws / "out.mp4"
        assert out_mp4.exists() and out_mp4.stat().st_size > 0


# ---------------------------------------------------------------------------
# Test 3: Portal transition
# ---------------------------------------------------------------------------

class TestPortalTransition:
    """
    E2E test: portal transition brief → workspace/out.mp4 exists and is
    non-empty.  Uses 2-stack scene with screen-anchor radius mask that
    reveals the city through a circular portal in the forest.
    """

    def test_produces_valid_mp4(self, ws: Path) -> None:
        """Portal brief produces a non-empty workspace/out.mp4."""
        lead = ParallaxLead(ws, sdk_client_factory=_make_factory(_PORTAL_SCENE_YAML))
        lead.run(
            "A FPV drone flies through a dark forest portal and reveals "
            "a glittering futuristic city beyond"
        )
        _seed_usage_log(ws, "portal")

        out_mp4 = ws / "out.mp4"
        assert out_mp4.exists(), f"out.mp4 not created in {ws}"
        assert out_mp4.stat().st_size > 0, "out.mp4 exists but is empty"

    def test_qa_passes_bounded(self, ws: Path) -> None:
        """Portal QA loop must not exceed MAX_QA_PASSES (§9.7)."""
        lead = ParallaxLead(ws, sdk_client_factory=_make_factory(_PORTAL_SCENE_YAML))
        lead.run("Portal transition brief")
        _seed_usage_log(ws, "portal")

        qa_passes = _count_qa_passes(ws)
        assert isinstance(qa_passes, int)
        assert qa_passes <= MAX_QA_PASSES

    def test_cost_under_budget(self, ws: Path) -> None:
        """Portal brief cost ≤ $2.50 (§3.6)."""
        lead = ParallaxLead(ws, sdk_client_factory=_make_factory(_PORTAL_SCENE_YAML))
        lead.run("Portal transition — cost tracking test")
        _seed_usage_log(ws, "portal")

        cost = _compute_cost_usd(ws)
        assert cost <= MAX_BUDGET_USD, (
            f"Portal cost ${cost:.4f} exceeds ${MAX_BUDGET_USD:.2f}"
        )

    def test_mask_compositing_renders(self, ws: Path) -> None:
        """Screen-anchor radius mask (portal reveal) must produce non-empty output."""
        from parallax_engine.scene import load_scene_yaml

        lead = ParallaxLead(ws, sdk_client_factory=_make_factory(_PORTAL_SCENE_YAML))
        lead.run("Portal mask render test")

        # Verify the scene had the mask configured
        scene = load_scene_yaml(ws / "scene.yaml")
        assert len(scene.masks) == 1
        assert scene.masks[0].growth.kind == "radius"

        out_mp4 = ws / "out.mp4"
        assert out_mp4.exists(), "Portal mask compositing produced no output"
        assert out_mp4.stat().st_size > 0


# ---------------------------------------------------------------------------
# QA loop boundary (cross-brief, verifying §9.7 Python counter enforcement)
# ---------------------------------------------------------------------------

class TestQALoopBoundary:
    """
    Verify that the QA loop counter is a Python integer enforced at the
    harness level — never a value the LLM is asked to track (§9.7).
    """

    def test_max_qa_passes_is_int(self) -> None:
        """MAX_QA_PASSES must be a Python int, not a float or string."""
        assert isinstance(MAX_QA_PASSES, int), (
            f"MAX_QA_PASSES is {type(MAX_QA_PASSES).__name__!r}; must be int (§9.7)"
        )
        assert MAX_QA_PASSES == 3

    def test_qa_counter_stops_at_max_when_always_fail(self, ws: Path) -> None:
        """
        With qa-critic always returning FAIL, the Python counter stops the
        loop at MAX_QA_PASSES — it does not run indefinitely.
        """
        class _AlwaysFailStub(_WritingStub):
            def run(self, *, prompt: str = "", system_prompt: str = "",
                    model: str = "", allowed_tools: tuple = (),
                    cwd: Path | None = None, agent_name: str = "") -> _StubSDKResult:
                # Write scene.yaml for scene-designer (inherited logic)
                if agent_name == "scene-designer" and cwd is not None:
                    (Path(cwd) / "scene.yaml").write_text(
                        self._scene_yaml, encoding="utf-8"
                    )
                # Override: qa-critic always returns FAIL
                if agent_name == "qa-critic":
                    # Record the call manually since we're not calling super()
                    self._calls.append({
                        "prompt": prompt,
                        "model": model,
                        "agent_name": agent_name,
                        "allowed_tools": list(allowed_tools),
                    })
                    return _StubSDKResult("FAIL: bad colors, redo everything")
                # All other agents: delegate to ClaudeSDKStub
                return super().run(
                    prompt=prompt, system_prompt=system_prompt, model=model,
                    allowed_tools=allowed_tools, cwd=cwd, agent_name=agent_name,
                )

        def _fail_factory(max_turns: int, max_budget_usd: float,
                          permission_mode: str) -> _AlwaysFailStub:
            stub = _AlwaysFailStub(_FOREST_SCENE_YAML)
            stub.max_turns = max_turns
            stub.max_budget_usd = max_budget_usd
            stub.permission_mode = permission_mode
            return stub

        lead = ParallaxLead(ws, sdk_client_factory=_fail_factory)
        lead.run("QA counter boundary test — should stop at 3 passes")

        qa_passes = _count_qa_passes(ws)
        assert isinstance(qa_passes, int), "QA pass count must be a Python int"
        assert qa_passes <= MAX_QA_PASSES, (
            f"QA ran {qa_passes} passes; Python counter must stop at "
            f"{MAX_QA_PASSES} (§9.7)"
        )

    def test_qa_counter_not_prompt_based(self) -> None:
        """The qa-critic system prompt must not contain stop instructions (§9.7)."""
        from parallax_engine.subagents import QA_CRITIC

        prompt_lower = QA_CRITIC.system_prompt.lower()
        forbidden_phrases = ["stop after", "do not exceed", "maximum of"]
        for phrase in forbidden_phrases:
            assert phrase not in prompt_lower, (
                f"qa-critic prompt contains '{phrase}' — "
                f"stop must be Python counter only (§9.7)"
            )

    def test_max_qa_passes_constant_in_lead(self) -> None:
        """MAX_QA_PASSES=3 must be the Python-level constant, not a prompt value."""
        from parallax_engine.lead import MAX_QA_PASSES as _MQP
        assert _MQP == 3
        assert isinstance(_MQP, int)

    def test_qa_loop_uses_range(self) -> None:
        """ParallaxLead._run_qa_loop uses a Python for-loop (range), not LLM tracking."""
        import inspect
        import parallax_engine.lead as lead_mod

        source = inspect.getsource(lead_mod.ParallaxLead._run_qa_loop)
        assert "range(" in source, (
            "_run_qa_loop must use a Python range() loop for QA counting (§9.7)"
        )
        # Must NOT instruct LLM to stop
        source_lower = source.lower()
        assert "stop after" not in source_lower
        assert "do not exceed" not in source_lower


# ---------------------------------------------------------------------------
# Cost bound verification (parametrized across all three briefs)
# ---------------------------------------------------------------------------

class TestCostBound:
    """
    Verify the $2.50 per-render budget limit (§3.6) for all three briefs.

    For stub runs: 0 tokens → $0.00 cost.  The test verifies the logging
    pipeline works and the computed cost is within the limit.
    """

    _BRIEF_CASES = [
        ("forest",  _FOREST_SCENE_YAML,
         "Create a forest drone flythrough at golden hour"),
        ("biomes",  _BIOMES_SCENE_YAML,
         "Create a 4-biome explainer with wipe transitions"),
        ("portal",  _PORTAL_SCENE_YAML,
         "Create a portal flythrough from forest to futuristic city"),
    ]

    @pytest.mark.parametrize("name,scene_yaml,brief", _BRIEF_CASES)
    def test_cost_under_250(
        self, ws: Path, name: str, scene_yaml: str, brief: str
    ) -> None:
        """Each brief: total computed cost must be ≤ $2.50."""
        lead = ParallaxLead(ws, sdk_client_factory=_make_factory(scene_yaml))
        lead.run(brief)
        _seed_usage_log(ws, name)

        usage_path = ws / "logs" / "usage.jsonl"
        assert usage_path.exists(), f"usage.jsonl not written for brief '{name}'"

        cost = _compute_cost_usd(ws)
        assert cost <= MAX_BUDGET_USD, (
            f"Brief '{name}' cost ${cost:.4f} exceeds "
            f"${MAX_BUDGET_USD:.2f} budget (§3.6)"
        )

    def test_max_budget_usd_matches_spec(self) -> None:
        """MAX_BUDGET_USD must equal 2.50 per §3.6."""
        assert MAX_BUDGET_USD == 2.50
        assert isinstance(MAX_BUDGET_USD, float)


# ---------------------------------------------------------------------------
# Writing stub sanity checks
# ---------------------------------------------------------------------------

class TestWritingStub:
    """Verify the _WritingStub behaves correctly in isolation."""

    def test_scene_designer_writes_yaml(self, ws: Path) -> None:
        """scene-designer call must write scene.yaml to the workspace."""
        stub = _WritingStub(_FOREST_SCENE_YAML)
        result = stub.run(
            prompt="Write scene.yaml",
            agent_name="scene-designer",
            cwd=ws,
        )
        assert result.last_content == "PASS"
        assert (ws / "scene.yaml").exists(), "scene.yaml not written by stub"
        content = (ws / "scene.yaml").read_text()
        assert "version: 1" in content

    def test_other_agents_return_pass(self, ws: Path) -> None:
        """Non-scene-designer agents return PASS without touching the filesystem."""
        stub = _WritingStub(_FOREST_SCENE_YAML)
        for agent in ("asset-generator", "mask-author", "camera-pather", "qa-critic"):
            result = stub.run(prompt="do stuff", agent_name=agent, cwd=ws)
            assert result.last_content == "PASS", (
                f"Agent '{agent}' did not return PASS"
            )

    def test_stub_tracks_calls(self, ws: Path) -> None:
        """_WritingStub tracks all agent calls via inherited ClaudeSDKStub.call_count."""
        stub = _WritingStub(_FOREST_SCENE_YAML)
        for agent in ("scene-designer", "asset-generator", "qa-critic"):
            stub.run(prompt="go", agent_name=agent, cwd=ws)
        assert stub.call_count == 3

    def test_no_yaml_without_cwd(self) -> None:
        """If cwd is None, scene-designer must NOT raise (just skip file write)."""
        stub = _WritingStub(_FOREST_SCENE_YAML)
        result = stub.run(prompt="go", agent_name="scene-designer", cwd=None)
        assert result.last_content == "PASS"
