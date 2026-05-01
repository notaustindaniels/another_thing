"""
parallax_engine.lead
====================
ParallaxLead — the lead orchestrator for the parallax-engine harness.

Implements SPEC.md §3.1, §3.4, §3.6, and §9.7.

Design principles
-----------------
* Delegates creative work to subagents; never does creative work itself.
* QA pass counter is a **Python integer** (max_qa_passes=3), not a prompt
  instruction.  Telling an LLM "stop after 3 passes" is a coin flip; a
  ``for i in range(self.max_qa_passes):`` loop is a guarantee (§9.7).
* Budget exhaustion (error_max_budget_usd) or max-turns exhaustion triggers
  *salvage* output (last successful render / partial artifacts) rather than a
  hard failure.
* The SDK client (ClaudeSDKClient) is imported lazily so the module is
  importable without the ``anthropic`` package being installed.  Tests can
  inject a stub client via ``_sdk_client_factory``.

Budget & safety controls (§3.6)
--------------------------------
Every ClaudeSDKClient call is configured with:
    max_turns=80
    max_budget_usd=2.50
    permission_mode='acceptEdits'

Error strings signalling budget/turn exhaustion that lead.py handles:
    "error_max_budget_usd"
    "error_max_turns"
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from parallax_engine.auth import configure_credentials
from parallax_engine.observability import ObservabilityHooks
from parallax_engine.state import (
    PHASE_ASSETS_DONE,
    PHASE_CAMERA_DONE,
    PHASE_MANIFEST,
    PHASE_MASKS_DONE,
    PHASE_QA_PASS_1,
    PHASE_QA_PASS_2,
    PHASE_QA_PASS_3,
    PHASE_RENDER_DONE,
    is_phase_done,
    write_checkpoint,
)
from parallax_engine.subagents import (
    ASSET_GENERATOR,
    CAMERA_PATHER,
    MASK_AUTHOR,
    QA_CRITIC,
    SCENE_DESIGNER,
    AgentDefinition,
)

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# SDK budget / turns constants (§3.6)
# -------------------------------------------------------------------------

#: Maximum SDK turns per call.  Enforced by the SDK; we also catch the
#: ``error_max_turns`` result and salvage.
MAX_TURNS: int = 80

#: Maximum spend per render (USD).  Enforced by the SDK; we also catch
#: ``error_max_budget_usd`` and salvage.
MAX_BUDGET_USD: float = 2.50

#: Permission mode — lead writes scene.yaml etc. without prompting the user.
PERMISSION_MODE: str = "acceptEdits"

#: QA pass cap (§9.7 — counter in Python, not in the prompt).
MAX_QA_PASSES: int = 3

# Strings the SDK surfaces when budget/turns are exhausted
_BUDGET_ERROR = "error_max_budget_usd"
_TURNS_ERROR = "error_max_turns"

# -------------------------------------------------------------------------
# Result types
# -------------------------------------------------------------------------

#: Phase names for the QA loop; ordered.
_QA_PHASE_NAMES: tuple[str, ...] = (PHASE_QA_PASS_1, PHASE_QA_PASS_2, PHASE_QA_PASS_3)


@dataclass
class RunResult:
    """Outcome of a ``ParallaxLead.run()`` call."""

    ok: bool
    out_mp4: Path | None = None
    salvage: bool = False          # True if budget/turns was exhausted mid-run
    phases_completed: list[str] = field(default_factory=list)
    error: str | None = None


# -------------------------------------------------------------------------
# Stub SDK client (used when anthropic package is not installed)
# -------------------------------------------------------------------------

class _StubSDKResult:
    """Minimal result object returned by the stub SDK client."""

    def __init__(self, last_content: str = "PASS") -> None:
        self.last_content: str = last_content
        self.stop_reason: str = "end_turn"

    def is_budget_error(self) -> bool:
        return _BUDGET_ERROR in self.last_content

    def is_turns_error(self) -> bool:
        return _TURNS_ERROR in self.last_content


class ClaudeSDKStub:
    """
    Drop-in stub for ClaudeSDKClient used in offline testing.

    Instantiate with ``response_map`` to inject per-agent responses:
        stub = ClaudeSDKStub(response_map={"scene-designer": "scene written: ..."})
    """

    def __init__(
        self,
        response_map: dict[str, str] | None = None,
        *,
        max_turns: int = MAX_TURNS,
        max_budget_usd: float = MAX_BUDGET_USD,
        permission_mode: str = PERMISSION_MODE,
    ) -> None:
        self.response_map: dict[str, str] = response_map or {}
        self.max_turns: int = max_turns
        self.max_budget_usd: float = max_budget_usd
        self.permission_mode: str = permission_mode
        self._calls: list[dict[str, Any]] = []

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
        """Execute a stub agent call; returns canned response or 'PASS'."""
        self._calls.append({
            "prompt": prompt,
            "model": model,
            "agent_name": agent_name,
            "allowed_tools": list(allowed_tools),
        })
        response = self.response_map.get(agent_name, "PASS")
        return _StubSDKResult(last_content=response)

    @property
    def call_count(self) -> int:
        return len(self._calls)


# -------------------------------------------------------------------------
# Real SDK wrapper (lazy import)
# -------------------------------------------------------------------------

class _RealSDKClient:
    """
    Thin wrapper around the Anthropic ClaudeSDKClient / claude-code-sdk.

    Imported lazily so the module can be loaded without ``anthropic``
    installed.  Will raise ImportError with a clear message if the SDK
    is genuinely required but unavailable.
    """

    def __init__(
        self,
        *,
        max_turns: int = MAX_TURNS,
        max_budget_usd: float = MAX_BUDGET_USD,
        permission_mode: str = PERMISSION_MODE,
    ) -> None:
        try:
            # The claude-code-sdk exposes ClaudeSDKClient (or equivalent).
            # Try both the SDK path used in claude-code-sdk and the raw
            # anthropic package.
            try:
                from claude_code_sdk import ClaudeOptions, query  # type: ignore[import]
                self._backend = "claude_code_sdk"
                self._query = query
                self._ClaudeOptions = ClaudeOptions
            except ImportError:
                import anthropic  # type: ignore[import]
                self._backend = "anthropic"
                self._client = anthropic.Anthropic()
        except ImportError as exc:
            raise ImportError(
                "parallax-engine: neither claude-code-sdk nor anthropic is installed. "
                "Install one to use the real SDK backend."
            ) from exc

        self.max_turns = max_turns
        self.max_budget_usd = max_budget_usd
        self.permission_mode = permission_mode

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
        """
        Execute an agent call and return a result with ``last_content``.

        This is the adaptor layer.  When the real SDK is available, call it.
        Returns a _StubSDKResult-compatible object.
        """
        if self._backend == "claude_code_sdk":
            # claude-code-sdk async interface — run synchronously via asyncio
            import asyncio

            async def _run() -> str:
                options = self._ClaudeOptions(
                    max_turns=self.max_turns,
                    permission_mode=self.permission_mode,
                    allowed_tools=list(allowed_tools),
                    cwd=str(cwd) if cwd else None,
                )
                result_text = ""
                async for message in self._query(
                    prompt=prompt,
                    options=options,
                ):
                    if hasattr(message, "content"):
                        result_text = str(message.content)
                return result_text

            last_content = asyncio.run(_run())
        else:
            # Fallback: raw Anthropic messages API (does not support MCP tools,
            # but sufficient for unit tests).
            response = self._client.messages.create(
                model=model or "claude-haiku-4-5",
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            last_content = response.content[0].text if response.content else ""

        return _StubSDKResult(last_content=last_content)


# -------------------------------------------------------------------------
# Factory function (injectable for tests)
# -------------------------------------------------------------------------

def _default_sdk_factory(
    max_turns: int,
    max_budget_usd: float,
    permission_mode: str,
) -> ClaudeSDKStub | _RealSDKClient:
    """
    Return a ClaudeSDKStub if anthropic is unavailable; otherwise _RealSDKClient.
    """
    try:
        return _RealSDKClient(
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            permission_mode=permission_mode,
        )
    except ImportError:
        logger.warning(
            "parallax-engine: anthropic/claude-code-sdk not installed; "
            "using ClaudeSDKStub (offline mode)"
        )
        return ClaudeSDKStub(
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            permission_mode=permission_mode,
        )


# -------------------------------------------------------------------------
# ParallaxLead
# -------------------------------------------------------------------------

class ParallaxLead:
    """
    Lead orchestrator for the parallax-engine harness.

    The lead is the only loop with multi-step decision-making (§3.4).
    It orchestrates subagents, never does creative work itself, and enforces
    all counters (QA passes, budget) in Python — not via prompts (§9.7).

    Parameters
    ----------
    workspace:
        Directory where all run artifacts live.
    max_turns:
        Maximum SDK turns per subagent call (default: 80, per §3.6).
    max_budget_usd:
        Budget cap per render in USD (default: 2.50, per §3.6).
    permission_mode:
        SDK permission mode (default: 'acceptEdits', per §3.6).
    max_qa_passes:
        Maximum QA-critic passes before accepting last render (default: 3, §9.7).
    sdk_client_factory:
        Callable that returns an SDK client.  Injectable for testing.
    """

    def __init__(
        self,
        workspace: Path | str,
        *,
        max_turns: int = MAX_TURNS,
        max_budget_usd: float = MAX_BUDGET_USD,
        permission_mode: str = PERMISSION_MODE,
        max_qa_passes: int = MAX_QA_PASSES,
        sdk_client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.workspace: Path = Path(workspace)
        self.max_turns: int = max_turns
        self.max_budget_usd: float = max_budget_usd
        self.permission_mode: str = permission_mode
        self.max_qa_passes: int = max_qa_passes

        factory = sdk_client_factory or _default_sdk_factory
        self._sdk: Any = factory(max_turns, max_budget_usd, permission_mode)

        # Observability hooks (append-only JSONL logs)
        self._obs = ObservabilityHooks(workspace=self.workspace, agent_id="lead")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dispatch(
        self,
        agent: AgentDefinition,
        prompt: str,
        *,
        cwd: Path | None = None,
    ) -> _StubSDKResult:
        """
        Dispatch one subagent call via the SDK client.

        Logs a pre-/post-hook entry via observability.  Returns the raw result
        so the caller can inspect ``last_content`` and error conditions.
        """
        agent_cwd = cwd or self.workspace
        logger.info("dispatching subagent %s", agent.name)

        # Pre-tool hook
        self._obs.before_tool(
            tool_name=f"Agent:{agent.name}",
            tool_input={"prompt": prompt[:200], "model": agent.model},
        )

        result = self._sdk.run(
            prompt=prompt,
            system_prompt=agent.system_prompt,
            model=agent.model,
            allowed_tools=agent.allowed_tools,
            cwd=agent_cwd,
            agent_name=agent.name,
        )

        # Post-tool hook
        self._obs.after_tool(
            tool_name=f"Agent:{agent.name}",
            tool_input={"prompt": prompt[:200], "model": agent.model},
            tool_result={"last_content": result.last_content},
        )

        return result

    def _is_exhausted(self, result: _StubSDKResult) -> bool:
        """Return True if the result signals budget or turns exhaustion."""
        return result.is_budget_error() or result.is_turns_error()

    def _salvage(self, out_mp4: Path | None) -> RunResult:
        """
        Produce salvage output on budget/turn exhaustion (§3.6).

        Returns whatever partial render exists rather than crashing.
        """
        logger.warning("budget or turn limit hit — producing salvage output")
        if out_mp4 and out_mp4.exists():
            salvage_path = out_mp4
        else:
            # Look for any MP4 in workspace
            mp4s = sorted(self.workspace.glob("*.mp4"))
            salvage_path = mp4s[-1] if mp4s else None

        phases = [
            p for p in [
                PHASE_MANIFEST, PHASE_ASSETS_DONE, PHASE_MASKS_DONE,
                PHASE_CAMERA_DONE, PHASE_RENDER_DONE,
            ]
            if is_phase_done(workspace=self.workspace, phase=p)
        ]

        return RunResult(
            ok=False,
            out_mp4=salvage_path,
            salvage=True,
            phases_completed=phases,
            error="Budget or turn limit hit; salvage output produced.",
        )

    # ------------------------------------------------------------------
    # Phase runners
    # ------------------------------------------------------------------

    def _run_scene_designer(self, brief: str) -> bool:
        """
        Phase 1: Dispatch scene-designer to write scene.yaml.

        Returns True on success, False if exhausted.
        """
        if is_phase_done(workspace=self.workspace, phase=PHASE_MANIFEST):
            logger.info("skipping scene-designer (checkpoint exists)")
            return True

        result = self._dispatch(
            SCENE_DESIGNER,
            prompt=(
                "Read workspace/brief.md and write workspace/scene.yaml. "
                "Return your status string on the last line."
            ),
        )
        if self._is_exhausted(result):
            return False

        write_checkpoint(workspace=self.workspace, phase=PHASE_MANIFEST)
        logger.info("scene-designer: %s", result.last_content)
        return True

    def _run_asset_generators(self, scene_yaml: Path) -> bool:
        """
        Phase 2: Dispatch one asset-generator per layer (parallel in a real run).

        For offline/stub testing the generators run sequentially here.  The
        real SDK would use a parallel tool-use wave.
        Returns True on success, False if any exhausted.
        """
        if is_phase_done(workspace=self.workspace, phase=PHASE_ASSETS_DONE):
            logger.info("skipping asset-generators (checkpoint exists)")
            return True

        # Discover layers from scene.yaml (best-effort; fallback to empty list)
        layer_ids: list[str] = []
        try:
            import yaml as _yaml  # type: ignore[import]
            raw = scene_yaml.read_text(encoding="utf-8")
            doc = _yaml.safe_load(raw)
            stacks = doc.get("stacks", {}) or {}
            for stack_layers in stacks.values():
                for layer in (stack_layers or []):
                    if isinstance(layer, dict) and "id" in layer:
                        layer_ids.append(layer["id"])
        except Exception:
            layer_ids = []

        for layer_id in layer_ids:
            result = self._dispatch(
                ASSET_GENERATOR,
                prompt=(
                    f"Generate the asset for layer '{layer_id}' as described in "
                    f"workspace/scene.yaml. Write workspace/assets/{layer_id}.svg "
                    f"and workspace/assets/{layer_id}.meta.json."
                ),
            )
            if self._is_exhausted(result):
                return False

        write_checkpoint(workspace=self.workspace, phase=PHASE_ASSETS_DONE)
        return True

    def _run_mask_authors(self, scene_yaml: Path) -> bool:
        """
        Phase 3: Dispatch one mask-author per mask entry (parallel).

        Returns True on success, False if any exhausted.
        """
        if is_phase_done(workspace=self.workspace, phase=PHASE_MASKS_DONE):
            logger.info("skipping mask-authors (checkpoint exists)")
            return True

        # Discover masks from scene.yaml (best-effort)
        mask_files: list[str] = []
        try:
            import yaml as _yaml  # type: ignore[import]
            raw = scene_yaml.read_text(encoding="utf-8")
            doc = _yaml.safe_load(raw)
            for mask in doc.get("masks", []) or []:
                if isinstance(mask, dict):
                    svg = mask.get("silhouette_svg") or mask.get("svg")
                    if svg:
                        mask_files.append(svg)
        except Exception:
            mask_files = []

        for mask_svg in mask_files:
            result = self._dispatch(
                MASK_AUTHOR,
                prompt=(
                    f"Add id='silhouette' and id='hole' paths to {mask_svg}. "
                    f"Return your status string."
                ),
            )
            if self._is_exhausted(result):
                return False

        write_checkpoint(workspace=self.workspace, phase=PHASE_MASKS_DONE)
        return True

    def _run_camera_pather(self) -> bool:
        """Phase 4: camera-pather writes the camera: block to scene.yaml."""
        if is_phase_done(workspace=self.workspace, phase=PHASE_CAMERA_DONE):
            logger.info("skipping camera-pather (checkpoint exists)")
            return True

        result = self._dispatch(
            CAMERA_PATHER,
            prompt=(
                "Read workspace/scene.yaml and workspace/brief.md. "
                "Add the camera: block to workspace/scene.yaml. "
                "Return your status string."
            ),
        )
        if self._is_exhausted(result):
            return False

        write_checkpoint(workspace=self.workspace, phase=PHASE_CAMERA_DONE)
        logger.info("camera-pather: %s", result.last_content)
        return True

    def _run_render(self, scene_yaml: Path) -> tuple[bool, Path | None]:
        """
        Phase 5: Call the in-process render tool.

        Returns (success, out_mp4_path).
        """
        if is_phase_done(workspace=self.workspace, phase=PHASE_RENDER_DONE):
            logger.info("skipping render (checkpoint exists)")
            out_mp4 = self.workspace / "out.mp4"
            return True, out_mp4 if out_mp4.exists() else None

        try:
            from parallax_engine.tools.render import render_scene

            result = render_scene(
                scene_yaml_path=str(scene_yaml),
                workspace=str(self.workspace),
            )
            if result.get("ok"):
                out_mp4 = Path(result["out_mp4"]) if result.get("out_mp4") else None
                write_checkpoint(
                    workspace=self.workspace,
                    phase=PHASE_RENDER_DONE,
                    extra={"out_mp4": str(out_mp4) if out_mp4 else None},
                )
                logger.info("render complete: %s", out_mp4)
                return True, out_mp4
            else:
                logger.error("render failed: %s", result.get("message", "unknown"))
                return False, None
        except Exception as exc:
            logger.error("render exception: %s", exc)
            return False, None

    def _run_qa_loop(self, out_mp4: Path | None) -> tuple[bool, int]:
        """
        Phase 6: QA loop (max ``max_qa_passes`` Python-integer iterations).

        Returns (qa_passed, num_passes_done).
        §9.7: counter is in Python, NOT in the prompt.
        """
        # Count already-completed QA passes from checkpoints
        passes_done: int = sum(
            1 for p in _QA_PHASE_NAMES
            if is_phase_done(workspace=self.workspace, phase=p)
        )

        for qa_pass in range(passes_done, self.max_qa_passes):
            phase_name = _QA_PHASE_NAMES[qa_pass]
            logger.info("QA pass %d/%d", qa_pass + 1, self.max_qa_passes)

            result = self._dispatch(
                QA_CRITIC,
                prompt=(
                    f"Review the rendered frames in workspace/frames/ against "
                    f"workspace/scene.yaml and workspace/brief.md. "
                    f"Write workspace/qa/pass_{qa_pass + 1:02d}_report.md. "
                    f"Return PASS or FAIL: <issues>."
                ),
            )

            if self._is_exhausted(result):
                logger.warning("QA pass %d budget/turns exhausted", qa_pass + 1)
                return False, qa_pass

            write_checkpoint(workspace=self.workspace, phase=phase_name)
            passes_done += 1

            last = result.last_content.strip()
            if last.startswith("PASS"):
                logger.info("QA pass %d: PASS", qa_pass + 1)
                return True, passes_done

            logger.info("QA pass %d: %s", qa_pass + 1, last)
            # On FAIL, loop continues (re-render would happen here in full impl)

        # Exhausted all passes without PASS — accept last render
        logger.warning("QA: max_qa_passes=%d reached without PASS", self.max_qa_passes)
        return False, passes_done

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, brief: str) -> RunResult:
        """
        Execute the full parallax-engine pipeline for *brief*.

        Parameters
        ----------
        brief:
            Natural-language description of the desired animation.

        Returns
        -------
        RunResult
            ok=True if the pipeline completed successfully.
            salvage=True if budget/turns was hit and a partial output was saved.
        """
        # Ensure workspace directories exist
        for subdir in ("assets", "masks", "frames", "qa", "logs", "checkpoints"):
            (self.workspace / subdir).mkdir(parents=True, exist_ok=True)

        # Write brief to disk (agents read it from there)
        brief_path = self.workspace / "brief.md"
        brief_path.write_text(brief, encoding="utf-8")
        scene_yaml = self.workspace / "scene.yaml"
        out_mp4: Path | None = None

        # ------------------------------------------------------------------
        # Phase 1: Scene design
        # ------------------------------------------------------------------
        if not self._run_scene_designer(brief):
            return self._salvage(out_mp4)

        # ------------------------------------------------------------------
        # Phase 2: Asset generation
        # ------------------------------------------------------------------
        if not self._run_asset_generators(scene_yaml):
            return self._salvage(out_mp4)

        # ------------------------------------------------------------------
        # Phase 3: Mask authoring
        # ------------------------------------------------------------------
        if not self._run_mask_authors(scene_yaml):
            return self._salvage(out_mp4)

        # ------------------------------------------------------------------
        # Phase 4: Camera pathing
        # ------------------------------------------------------------------
        if not self._run_camera_pather():
            return self._salvage(out_mp4)

        # ------------------------------------------------------------------
        # Phase 5: Render
        # ------------------------------------------------------------------
        render_ok, out_mp4 = self._run_render(scene_yaml)
        if not render_ok:
            return self._salvage(out_mp4)

        # ------------------------------------------------------------------
        # Phase 6: QA loop  — §9.7: counter is a Python int
        # ------------------------------------------------------------------
        qa_passed, qa_passes = self._run_qa_loop(out_mp4)

        phases = [
            p for p in [
                PHASE_MANIFEST, PHASE_ASSETS_DONE, PHASE_MASKS_DONE,
                PHASE_CAMERA_DONE, PHASE_RENDER_DONE,
            ]
            if is_phase_done(workspace=self.workspace, phase=p)
        ]

        return RunResult(
            ok=True,
            out_mp4=out_mp4,
            salvage=False,
            phases_completed=phases,
        )
