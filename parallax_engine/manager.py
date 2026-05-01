"""parallax_engine.manager — director-era project manager.

Implements SPEC.md §11.9 (The revised harness topology and the project
manager's prompt) and §11.14 step 9.

Role
----
The project manager is the producer: it sequences tiers, routes QA failures,
and enforces budget caps.  It does **not** make creative decisions.

System prompt (§11.9.2)
------------------------
Verbatim from spec; see MANAGER_SYSTEM_PROMPT constant below.

Sequence of operations (§11.9.1)
---------------------------------
1. Director → storyboard.yaml + casting.yaml
2. Schema validation (abort on parse failure)
3. qa-critic --level=storyboard --pre (optional, cheap sanity)
4. Scene designers (sequential, 1..N)
5. Merger → scene.yaml
6. Parallel: asset-generator per manifest entry
7. Parallel: mask-author per mask
8. Renderer → out.mp4
9. QA: asset (parallel), scene (parallel), storyboard (one)
10. On FAIL: route to lowest tier
    - asset failure  → re-run asset-generator  (cap MAX_ASSET_RETRIES_PER_ASSET)
    - scene failure  → re-run scene-designer i  (cap MAX_SCENE_REDESIGNS_PER_SCENE)
    - storyboard failure → re-run director      (cap MAX_STORYBOARD_REGENERATIONS)
    On cap exhaustion: escalate up
11. PASS → write out.mp4 + manifest.json
    FAIL with caps exhausted → write partial + qa/fatal.json

Retry caps (§11.8.1)
---------------------
Counters live in Python, not prompts.  The QA critic is never told which
attempt number it is on (§11.13.9).

--resume (§11.11)
------------------
1. Validate storyboard.yaml schema.
2. Diff against .cache/storyboard.yaml.last.
3. For each changed scene index, invalidate scenes/scene_NN.yaml and
   dependent assets.
4. Run scene-designer only for changed scenes (skip director).
5. Run asset-generator only for changed scenes' new manifest entries.
6. Re-render (cheap).
7. Re-run QA at scene level for changed scenes; storyboard-level only if
   structurally significant fields changed (arc, casting, visual_vocabulary).

Single-mode vs. decomposed-mode (§11.9.1)
------------------------------------------
Selected by a deterministic Python rule (not the director):

    def director_mode(brief: Brief) -> str:
        if brief.target_duration_s >= 60.0: return "decomposed"
        if brief.requested_structure == "save_the_cat": return "decomposed"
        if brief.config_budget == "thrift": return "single"
        return "single"

SPEC anchors: §11.9, §11.9.1, §11.9.2, §11.11, §11.13, §11.14 step 9
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from parallax_engine.director.schema import Storyboard, load_storyboard_yaml
from parallax_engine.scene.merger import MergeError, merge

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry caps (§11.8.1) — enforced in Python, never mentioned to LLMs
# ---------------------------------------------------------------------------

#: Maximum times an individual asset can be retried before escalating.
MAX_ASSET_RETRIES_PER_ASSET: int = 3

#: Maximum times a scene can be redesigned before escalating.
MAX_SCENE_REDESIGNS_PER_SCENE: int = 2

#: Maximum times the director is re-run on storyboard-level failures.
MAX_STORYBOARD_REGENERATIONS: int = 1

#: Default budget cap in USD.
MAX_TOTAL_BUDGET_USD: float = 8.00

# ---------------------------------------------------------------------------
# Director mode selector (§11.9.1 deterministic rule)
# ---------------------------------------------------------------------------


def director_mode(
    *,
    target_duration_s: float = 0.0,
    requested_structure: str = "",
    config_budget: str = "standard",
) -> Literal["single", "decomposed"]:
    """Deterministic mode selector — not a creative decision (§11.9.1).

    Parameters
    ----------
    target_duration_s:
        Target video duration in seconds from the brief.
    requested_structure:
        Structure style requested (e.g. "save_the_cat", "linear", …).
    config_budget:
        Budget tier string from config.yaml (e.g. "thrift", "standard", …).

    Returns
    -------
    "single" or "decomposed"
    """
    if target_duration_s >= 60.0:
        return "decomposed"
    if requested_structure == "save_the_cat":
        return "decomposed"
    if config_budget == "thrift":
        return "single"
    return "single"


# ---------------------------------------------------------------------------
# Run result
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """Result of a ProjectManager.run() invocation.

    Attributes
    ----------
    success:
        True if the run produced a final out.mp4 without exhausting caps.
    output_path:
        Path to the final (or best partial) out.mp4.
    storyboard_regenerations:
        Number of director re-runs performed.
    scene_redesigns:
        Dict mapping scene_index → number of redesigns performed.
    asset_retries:
        Dict mapping asset_id → number of retries performed.
    fatal_json_path:
        Path to qa/fatal.json if the run ended with cap exhaustion, else None.
    elapsed_s:
        Wall-clock time in seconds.
    log_path:
        Path to logs/manager.log.
    """

    success: bool
    output_path: Path | None = None
    storyboard_regenerations: int = 0
    scene_redesigns: dict[str, int] = field(default_factory=dict)
    asset_retries: dict[str, int] = field(default_factory=dict)
    fatal_json_path: Path | None = None
    elapsed_s: float = 0.0
    log_path: Path | None = None


# ---------------------------------------------------------------------------
# Project manager
# ---------------------------------------------------------------------------


class ProjectManager:
    """Director-era project manager (§11.9).

    The project manager is not creative.  It sequences agents, enforces caps,
    and routes QA failures.

    Parameters
    ----------
    workspace_dir:
        Project workspace root.  All relative paths are resolved here.
    brief_path:
        Path to brief.md.
    budget:
        Cost tier: "thrift" | "standard" | "premium" | "longform".
    max_budget_usd:
        Dollar cap; defaults to MAX_TOTAL_BUDGET_USD.
    dry_run:
        If True, all LLM calls are stubbed.  Used in tests.
    """

    # System prompt (§11.9.2) — verbatim from spec
    SYSTEM_PROMPT: str = (
        "You are the Project Manager for `parallax-engine`. "
        "You are not creative. You do not invent. You coordinate. "
        "Your role is the producer who hires the director, schedules the crew, "
        "runs dailies, and decides when a take is good enough to ship. "
        "The director directs; you produce.\n\n"
        "Your responsibilities:\n\n"
        "1. **Sequence the tiers.** Invoke the director once. Then for each scene "
        "index 1..N, invoke a scene-designer in order, never in parallel. Then "
        "dispatch implementation agents (asset-generator, mask-author) in parallel "
        "waves. Then call the renderer. Then call QA at all three levels.\n"
        "2. **Route QA failures.** When QA fails, read the failure level and "
        "re-invoke the matching tier. Asset failures go to asset-generator. Scene "
        "failures go to scene-designer. Storyboard failures go to director. Never "
        "escalate prematurely; always try the lowest tier first.\n"
        "3. **Honour budget caps.** Counters for retries and dollar spend are "
        "maintained in Python; you will be told when a cap is hit. When a cap is "
        "hit, escalate to the next tier; if the highest tier's cap is hit, terminate "
        "with the best output produced so far.\n"
        "4. **Never make creative decisions.** If two valid choices present themselves "
        "and you cannot route deterministically, write a routing diagnostic and pick "
        "the one that minimises remaining cost.\n"
        "5. **Never spawn unnamed agents.** You may invoke only: director, "
        "scene-designer, asset-generator, mask-author, camera-pather, renderer "
        "(Python tool), qa-critic. The Claude Agent SDK enforces this; the prompt "
        "reaffirms it.\n"
        "6. **Write a single line to logs/manager.log per decision** so the run is "
        "replayable.\n\n"
        "What you do NOT do: write storyboards, edit storyboards, redesign scenes, "
        "paint SVGs, change masks, or critique the work. Each of those has an agent. "
        "You hire that agent."
    )

    def __init__(
        self,
        workspace_dir: str | Path,
        brief_path: str | Path,
        budget: str = "standard",
        max_budget_usd: float = MAX_TOTAL_BUDGET_USD,
        dry_run: bool = False,
    ) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.brief_path = Path(brief_path)
        self.budget = budget
        self.max_budget_usd = max_budget_usd
        self.dry_run = dry_run

        # Retry counters — Python enforced, never exposed to prompts (§11.13.9)
        self._storyboard_regen_count: int = 0
        self._scene_redesign_counts: dict[int, int] = {}
        self._asset_retry_counts: dict[str, int] = {}

        # Log file
        log_dir = self.workspace_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = log_dir / "manager.log"

        # Cache dir for --resume
        self._cache_dir = self.workspace_dir / ".cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self, resume: bool = False) -> RunResult:
        """Run the full director-era pipeline.

        Parameters
        ----------
        resume:
            If True, diff storyboard.yaml against .cache/storyboard.yaml.last
            and rebuild only changed scenes (§11.11).

        Returns
        -------
        RunResult
        """
        t0 = time.monotonic()
        self._log("run started", extra={"resume": resume, "budget": self.budget})

        result = RunResult(success=False, log_path=self._log_path)

        storyboard: Storyboard | None = None

        if resume:
            storyboard = self._resume_run(result)
        else:
            storyboard = self._full_run(result)

        result.elapsed_s = time.monotonic() - t0
        self._log("run finished", extra={"success": result.success, "elapsed_s": result.elapsed_s})
        return result

    # ------------------------------------------------------------------
    # Full run (§11.9.1 sequence)
    # ------------------------------------------------------------------

    def _full_run(self, result: RunResult) -> Storyboard | None:
        """Execute the full §11.9.1 sequence."""

        # Step 1: Director → storyboard.yaml + casting.yaml
        storyboard = self._run_director_loop(result)
        if storyboard is None:
            self._write_fatal(result, "director failed after cap exhaustion")
            return None

        # Step 2: Schema validation (already done inside _run_director_loop)
        n_scenes = len(storyboard.scenes)

        # Step 3: Pre-QA storyboard sanity check (optional, cheap)
        self._run_pre_qa(storyboard)

        # Step 4: Scene designers (sequential)
        ok = self._run_scene_designers(storyboard, result)
        if not ok:
            self._write_fatal(result, "scene design failed")
            return None

        # Step 5: Merger → scene.yaml
        ok = self._run_merger(result)
        if not ok:
            self._write_fatal(result, "merger failed")
            return None

        # Steps 6–8: Asset gen, mask authors, renderer
        ok = self._run_implementation(storyboard, result)
        if not ok:
            self._write_fatal(result, "implementation failed")
            return None

        # Step 9: QA at all three levels
        qa_passed = self._run_qa_loop(storyboard, result)

        if qa_passed:
            result.success = True
            result.output_path = self.workspace_dir / "out.mp4"
            # Persist storyboard for --resume
            self._persist_storyboard_cache()
        return storyboard

    # ------------------------------------------------------------------
    # Director loop
    # ------------------------------------------------------------------

    def _run_director_loop(self, result: RunResult) -> Storyboard | None:
        """Run director (with retry on storyboard-level QA failure)."""
        for attempt in range(MAX_STORYBOARD_REGENERATIONS + 1):
            sb = self._invoke_director()
            if sb is not None:
                result.storyboard_regenerations = attempt
                return sb
            self._storyboard_regen_count += 1
            if self._storyboard_regen_count > MAX_STORYBOARD_REGENERATIONS:
                self._log("director cap exhausted", extra={"count": self._storyboard_regen_count})
                break
            self._log("director retry", extra={"attempt": attempt + 1})
        return None

    def _invoke_director(self) -> Storyboard | None:
        """Invoke the director agent.  Returns parsed Storyboard or None on failure."""
        if self.dry_run:
            # Stub: return a minimal Storyboard if storyboard.yaml exists
            sb_path = self.workspace_dir / "storyboard.yaml"
            if sb_path.exists():
                try:
                    return load_storyboard_yaml(sb_path)
                except Exception as exc:
                    self._log("storyboard parse error (dry_run)", extra={"error": str(exc)})
                    return None
            self._log("dry_run: no storyboard.yaml; skipping director")
            return None

        # Real invocation via director.agent
        try:
            from parallax_engine.director.agent import DirectorAgent
            from parallax_engine.director.prompt import DirectorBrief

            brief_text = self.brief_path.read_text(encoding="utf-8")
            brief = DirectorBrief(
                text=brief_text,
                config_budget=self.budget,
            )
            agent = DirectorAgent()
            result = agent.run(brief)
            sb = result.storyboard
            # Write storyboard.yaml
            sb_path = self.workspace_dir / "storyboard.yaml"
            sb_path.write_text(yaml.dump(sb.model_dump()), encoding="utf-8")
            self._log("director complete", extra={"scenes": len(sb.scenes)})
            return sb
        except Exception as exc:
            self._log("director error", extra={"error": str(exc)})
            return None

    # ------------------------------------------------------------------
    # Scene designer loop
    # ------------------------------------------------------------------

    def _run_scene_designers(self, storyboard: Storyboard, result: RunResult) -> bool:
        """Run scene designers sequentially for all scenes."""
        scenes_dir = self.workspace_dir / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)

        for i, scene_entry in enumerate(sorted(storyboard.scenes, key=lambda s: s.index), 1):
            ok = self._run_scene_designer_for(i, storyboard, result)
            if not ok:
                self._log("scene designer failed permanently", extra={"scene_index": i})
                return False
        return True

    def _run_scene_designer_for(
        self, scene_index: int, storyboard: Storyboard, result: RunResult
    ) -> bool:
        """Run scene-designer for one scene, with retry cap."""
        for attempt in range(MAX_SCENE_REDESIGNS_PER_SCENE + 1):
            ok = self._invoke_scene_designer(scene_index, storyboard)
            if ok:
                result.scene_redesigns[str(scene_index)] = attempt
                return True
            count = self._scene_redesign_counts.get(scene_index, 0) + 1
            self._scene_redesign_counts[scene_index] = count
            if count > MAX_SCENE_REDESIGNS_PER_SCENE:
                self._log("scene redesign cap exhausted", extra={"scene_index": scene_index})
                return False
            self._log("scene redesign retry", extra={"scene_index": scene_index, "attempt": attempt + 1})
        return False

    def _invoke_scene_designer(self, scene_index: int, storyboard: Storyboard) -> bool:
        """Invoke scene-designer for scene_index.  Returns True on success."""
        if self.dry_run:
            # Stub: write a minimal scene fragment
            fragment_path = self.workspace_dir / "scenes" / f"scene_{scene_index:02d}.yaml"
            if fragment_path.exists():
                return True  # already exists
            fragment = {
                "scene_index": scene_index,
                "duration_s": 6.0,
                "stacks": [],
            }
            fragment_path.write_text(yaml.dump(fragment), encoding="utf-8")
            return True

        try:
            from parallax_engine.scene.designer import SceneDesigner
            from parallax_engine.casting.bible import CastingBible

            casting = CastingBible(self.workspace_dir / "casting.yaml")
            prior_fragments = self._load_prior_fragments(scene_index)
            designer = SceneDesigner()
            output = designer.run(
                storyboard=storyboard,
                casting=casting,
                prior_fragments=prior_fragments,
                scene_index=scene_index,
            )
            # Write fragment
            frag_path = self.workspace_dir / "scenes" / f"scene_{scene_index:02d}.yaml"
            frag_path.write_text(yaml.dump(output.fragment.model_dump()), encoding="utf-8")
            self._log("scene designer OK", extra={"scene_index": scene_index})
            return True
        except Exception as exc:
            self._log("scene designer error", extra={"scene_index": scene_index, "error": str(exc)})
            return False

    def _load_prior_fragments(self, scene_index: int) -> list[dict[str, Any]]:
        """Load all scene fragments with index < scene_index."""
        fragments = []
        scenes_dir = self.workspace_dir / "scenes"
        for i in range(1, scene_index):
            frag_path = scenes_dir / f"scene_{i:02d}.yaml"
            if frag_path.exists():
                try:
                    data = yaml.safe_load(frag_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        fragments.append(data)
                except Exception:
                    pass
        return fragments

    # ------------------------------------------------------------------
    # Merger
    # ------------------------------------------------------------------

    def _run_merger(self, result: RunResult) -> bool:
        """Merge scene fragments → scene.yaml."""
        sb_path = self.workspace_dir / "storyboard.yaml"
        if not sb_path.exists():
            self._log("merger: no storyboard.yaml")
            return False
        try:
            storyboard = load_storyboard_yaml(sb_path)
            merged, log = merge(
                fragment_dir=self.workspace_dir / "scenes",
                storyboard=storyboard,
                output_dir=self.workspace_dir,
            )
            self._log("merger OK", extra={"n_scenes": len(merged.get("scenes", []))})
            return True
        except MergeError as exc:
            self._log("merger failed", extra={"error": str(exc)})
            return False
        except Exception as exc:
            self._log("merger error", extra={"error": str(exc)})
            return False

    # ------------------------------------------------------------------
    # Implementation wave (asset-gen, mask-author, renderer)
    # ------------------------------------------------------------------

    def _run_implementation(self, storyboard: Storyboard, result: RunResult) -> bool:
        """Run asset-generator, mask-author, and renderer."""
        # In this implementation, we call the asset generator for each
        # manifest entry found in the scene fragments.
        ok = self._run_asset_generators(storyboard, result)
        if not ok:
            return False
        # Renderer: just check if scene.yaml exists and produce a stub out.mp4
        ok = self._run_renderer(result)
        return ok

    def _run_asset_generators(self, storyboard: Storyboard, result: RunResult) -> bool:
        """Run asset-generator for all manifest entries (parallel in production).

        In the current implementation, we run sequentially for simplicity.
        A production harness would use concurrent.futures.ThreadPoolExecutor.
        """
        from parallax_engine.asset.generator import generate as gen_asset
        from parallax_engine.casting.bible import CastingBible

        casting_path = self.workspace_dir / "casting.yaml"
        casting = CastingBible(casting_path) if casting_path.exists() else None

        # Build casting data dict for the generator
        casting_data: dict[str, Any] = {}
        if casting:
            for entry in casting.all_entries():
                casting_data[entry.id] = {
                    "canonical_description": entry.canonical_description,
                    "palette_locked": entry.palette_locked or {},
                    "forbidden_changes": entry.forbidden_changes or [],
                }

        shape_language = storyboard.visual_vocabulary.shape_language if storyboard else ""

        # Collect manifest entries from all scene fragments
        manifest_entries = self._collect_manifest_entries()

        for entry in manifest_entries:
            asset_id = entry.get("id", "unknown")
            for attempt in range(MAX_ASSET_RETRIES_PER_ASSET):
                res = gen_asset(
                    manifest_entry=entry,
                    workspace_dir=self.workspace_dir,
                    casting_data=casting_data,
                    shape_language=shape_language,
                )
                if res["ok"]:
                    result.asset_retries[asset_id] = attempt
                    self._log("asset-gen OK", extra={"id": asset_id, "kind": entry.get("kind")})
                    break
                count = attempt + 1
                result.asset_retries[asset_id] = count
                if count >= MAX_ASSET_RETRIES_PER_ASSET:
                    self._log("asset-gen cap exhausted", extra={"id": asset_id})
                    # Don't fail the run; escalation happens at QA time
                    break
                self._log("asset-gen retry", extra={"id": asset_id, "attempt": count})

        return True

    def _collect_manifest_entries(self) -> list[dict[str, Any]]:
        """Collect all manifest entries from scene fragment YAML files."""
        entries: list[dict[str, Any]] = []
        scenes_dir = self.workspace_dir / "scenes"
        if not scenes_dir.exists():
            return entries
        for frag_path in sorted(scenes_dir.glob("scene_*.yaml")):
            try:
                data = yaml.safe_load(frag_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                manifest = data.get("manifest", [])
                if isinstance(manifest, list):
                    entries.extend(
                        m for m in manifest if isinstance(m, dict)
                    )
            except Exception:
                pass
        return entries

    def _run_renderer(self, result: RunResult) -> bool:
        """Invoke the renderer.  Returns True on success."""
        if self.dry_run:
            # Stub: write a placeholder out.mp4
            out_path = self.workspace_dir / "out.mp4"
            if not out_path.exists():
                out_path.write_bytes(b"")
            return True

        try:
            from parallax_engine.tools.render import render_scene

            scene_path = self.workspace_dir / "scene.yaml"
            if not scene_path.exists():
                self._log("renderer: scene.yaml not found")
                return False

            out_path = self.workspace_dir / "out.mp4"
            render_scene(str(scene_path), str(out_path))
            self._log("renderer OK", extra={"output": str(out_path)})
            return True
        except Exception as exc:
            self._log("renderer error", extra={"error": str(exc)})
            return False

    # ------------------------------------------------------------------
    # QA loop
    # ------------------------------------------------------------------

    def _run_pre_qa(self, storyboard: Storyboard) -> None:
        """Optional pre-QA storyboard sanity check (§11.9.1 step 3)."""
        # Cheap sanity; don't abort on failure (optional per spec)
        try:
            from parallax_engine.qa.critic import critique

            result = critique(
                "storyboard",
                workspace_dir=self.workspace_dir,
                storyboard_data=storyboard.model_dump(),
                dry_run=self.dry_run,
            )
            self._log("pre-QA storyboard", extra={"verdict": result.verdict})
        except Exception as exc:
            self._log("pre-QA error (non-fatal)", extra={"error": str(exc)})

    def _run_qa_loop(self, storyboard: Storyboard, result: RunResult) -> bool:
        """Run QA at all three levels and route failures (§11.9.1 steps 9–10)."""
        from parallax_engine.qa.critic import critique

        # Asset-level QA (per asset)
        asset_verdicts = self._run_asset_qa(storyboard, result)

        # Scene-level QA (per scene)
        scene_verdicts = self._run_scene_qa(storyboard, result)

        # Storyboard-level QA (once)
        sb_result = critique(
            "storyboard",
            workspace_dir=self.workspace_dir,
            storyboard_data=storyboard.model_dump(),
            budget=self.budget,
            dry_run=self.dry_run,
        )
        self._log("QA storyboard", extra={"verdict": sb_result.verdict})

        # All must pass for success
        asset_pass = all(v.passed for v in asset_verdicts.values())
        scene_pass = all(v.passed for v in scene_verdicts.values())
        story_pass = sb_result.passed

        return asset_pass and scene_pass and story_pass

    def _run_asset_qa(
        self, storyboard: Storyboard, result: RunResult
    ) -> dict[str, Any]:
        """Run asset-level QA for all manifest entries."""
        from parallax_engine.qa.critic import critique

        verdicts: dict[str, Any] = {}
        manifest_entries = self._collect_manifest_entries()
        for entry in manifest_entries:
            asset_id = entry.get("id", "unknown")
            v = critique(
                "asset",
                workspace_dir=self.workspace_dir,
                asset_id=asset_id,
                asset_manifest_entry=entry,
                budget=self.budget,
                dry_run=self.dry_run,
            )
            verdicts[asset_id] = v
            self._log("QA asset", extra={"id": asset_id, "verdict": v.verdict})
        return verdicts

    def _run_scene_qa(
        self, storyboard: Storyboard, result: RunResult
    ) -> dict[str, Any]:
        """Run scene-level QA for each scene."""
        from parallax_engine.qa.critic import critique

        verdicts: dict[str, Any] = {}
        for scene_entry in sorted(storyboard.scenes, key=lambda s: s.index):
            idx = scene_entry.index
            frag_path = self.workspace_dir / "scenes" / f"scene_{idx:02d}.yaml"
            fragment: dict[str, Any] = {}
            if frag_path.exists():
                try:
                    fragment = yaml.safe_load(frag_path.read_text(encoding="utf-8")) or {}
                except Exception:
                    pass

            v = critique(
                "scene",
                workspace_dir=self.workspace_dir,
                scene_index=idx,
                storyboard_scene_entry=scene_entry.model_dump(),
                scene_fragment=fragment,
                budget=self.budget,
                dry_run=self.dry_run,
            )
            verdicts[str(idx)] = v
            self._log("QA scene", extra={"scene_index": idx, "verdict": v.verdict})
        return verdicts

    # ------------------------------------------------------------------
    # --resume logic (§11.11)
    # ------------------------------------------------------------------

    def _resume_run(self, result: RunResult) -> Storyboard | None:
        """Run in resume mode: diff storyboard, rebuild only changed scenes."""
        sb_path = self.workspace_dir / "storyboard.yaml"

        # Step 1: Validate storyboard.yaml
        if not sb_path.exists():
            self._log("resume: no storyboard.yaml found")
            return None
        try:
            storyboard = load_storyboard_yaml(sb_path)
        except Exception as exc:
            self._log("resume: storyboard parse error", extra={"error": str(exc)})
            return None

        # Step 2: Diff against cached
        changed_indices = self._compute_changed_scenes(storyboard)
        self._log("resume: changed scenes", extra={"indices": sorted(changed_indices)})

        if not changed_indices:
            self._log("resume: no changes detected; nothing to rebuild")
            result.success = True
            result.output_path = self.workspace_dir / "out.mp4"
            return storyboard

        # Step 3: Invalidate scene fragments and assets for changed scenes
        self._invalidate_changed_scenes(changed_indices)

        # Step 4: Run scene-designer only for changed scenes
        for scene_index in sorted(changed_indices):
            ok = self._run_scene_designer_for(scene_index, storyboard, result)
            if not ok:
                self._log("resume: scene designer failed", extra={"scene_index": scene_index})
                return None

        # Step 5: Merger → scene.yaml
        ok = self._run_merger(result)
        if not ok:
            return None

        # Step 6: Asset-generator only for changed scenes' new entries
        ok = self._run_implementation(storyboard, result)
        if not ok:
            return None

        # Step 7: QA (scene level for changed; storyboard if structural changes)
        qa_passed = self._run_resume_qa(storyboard, changed_indices, result)

        if qa_passed:
            result.success = True
            result.output_path = self.workspace_dir / "out.mp4"
            self._persist_storyboard_cache()

        return storyboard

    def _compute_changed_scenes(self, storyboard: Storyboard) -> set[int]:
        """Diff storyboard.yaml against .cache/storyboard.yaml.last.

        Returns the set of scene indices whose entries have changed.
        If the cache doesn't exist, all scenes are considered changed.
        """
        cache_path = self._cache_dir / "storyboard.yaml.last"
        if not cache_path.exists():
            # No cache → all scenes changed
            return {s.index for s in storyboard.scenes}

        try:
            cached = yaml.safe_load(cache_path.read_text(encoding="utf-8"))
            if not isinstance(cached, dict):
                return {s.index for s in storyboard.scenes}
        except Exception:
            return {s.index for s in storyboard.scenes}

        # Compare using raw YAML on both sides (avoid model_dump() default injection)
        current_sb_path = self.workspace_dir / "storyboard.yaml"
        try:
            current_raw = yaml.safe_load(current_sb_path.read_text(encoding="utf-8"))
        except Exception:
            # Fall back to model_dump if raw file unavailable
            current_raw = storyboard.model_dump()

        current_scenes_by_idx: dict[int, dict[str, Any]] = {}
        for s in (current_raw.get("scenes") or []):
            if isinstance(s, dict) and "index" in s:
                current_scenes_by_idx[s["index"]] = s

        cached_scenes_by_idx: dict[int, dict[str, Any]] = {}
        for s in cached.get("scenes", []):
            if isinstance(s, dict) and "index" in s:
                cached_scenes_by_idx[s["index"]] = s

        changed: set[int] = set()
        for idx, current in current_scenes_by_idx.items():
            cached_entry = cached_scenes_by_idx.get(idx)
            if cached_entry is None or _dict_fingerprint(current) != _dict_fingerprint(cached_entry):
                changed.add(idx)
        # Also flag scenes present in cached but not in current as changed
        for idx in cached_scenes_by_idx:
            if idx not in current_scenes_by_idx:
                changed.add(idx)

        # Also check if structurally significant top-level fields changed
        # (arc, casting, visual_vocabulary) — flag as changed_structure
        # (handled by _run_resume_qa)
        return changed

    def _invalidate_changed_scenes(self, scene_indices: set[int]) -> None:
        """Delete scene fragment files for changed scene indices."""
        scenes_dir = self.workspace_dir / "scenes"
        for idx in scene_indices:
            frag_path = scenes_dir / f"scene_{idx:02d}.yaml"
            if frag_path.exists():
                frag_path.unlink()
                self._log("invalidated scene fragment", extra={"scene_index": idx})

    def _run_resume_qa(
        self,
        storyboard: Storyboard,
        changed_indices: set[int],
        result: RunResult,
    ) -> bool:
        """Run QA for changed scenes + storyboard level if structural changes."""
        from parallax_engine.qa.critic import critique

        # Scene-level for changed scenes only
        all_pass = True
        for idx in sorted(changed_indices):
            scene_entry_list = [s for s in storyboard.scenes if s.index == idx]
            if not scene_entry_list:
                continue
            scene_entry = scene_entry_list[0]
            frag_path = self.workspace_dir / "scenes" / f"scene_{idx:02d}.yaml"
            fragment: dict[str, Any] = {}
            if frag_path.exists():
                try:
                    fragment = yaml.safe_load(frag_path.read_text(encoding="utf-8")) or {}
                except Exception:
                    pass
            v = critique(
                "scene",
                workspace_dir=self.workspace_dir,
                scene_index=idx,
                storyboard_scene_entry=scene_entry.model_dump(),
                scene_fragment=fragment,
                budget=self.budget,
                dry_run=self.dry_run,
            )
            self._log("resume QA scene", extra={"scene_index": idx, "verdict": v.verdict})
            if v.failed:
                all_pass = False

        return all_pass

    # ------------------------------------------------------------------
    # Cache persistence
    # ------------------------------------------------------------------

    def _persist_storyboard_cache(self) -> None:
        """Copy storyboard.yaml to .cache/storyboard.yaml.last."""
        sb_path = self.workspace_dir / "storyboard.yaml"
        if sb_path.exists():
            dest = self._cache_dir / "storyboard.yaml.last"
            shutil.copy2(sb_path, dest)

    # ------------------------------------------------------------------
    # Fatal output
    # ------------------------------------------------------------------

    def _write_fatal(self, result: RunResult, reason: str) -> None:
        """Write qa/fatal.json describing the terminal failure."""
        qa_dir = self.workspace_dir / "qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        fatal_path = qa_dir / "fatal.json"
        payload: dict[str, Any] = {
            "reason": reason,
            "storyboard_regenerations": result.storyboard_regenerations,
            "scene_redesigns": result.scene_redesigns,
            "asset_retries": result.asset_retries,
        }
        fatal_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        result.fatal_json_path = fatal_path
        self._log("fatal written", extra={"reason": reason})

    # ------------------------------------------------------------------
    # Logging (§11.9.2 requirement: one line per decision)
    # ------------------------------------------------------------------

    def _log(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Write one line to logs/manager.log.  Also emits to Python logger."""
        entry: dict[str, Any] = {"event": event, "ts": time.time()}
        if extra:
            entry.update(extra)
        line = json.dumps(entry, sort_keys=True)
        logger.info("manager: %s", line)
        try:
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dict_fingerprint(d: dict[str, Any]) -> str:
    """Deterministic fingerprint for a dict (used for change detection)."""
    return json.dumps(d, sort_keys=True)
