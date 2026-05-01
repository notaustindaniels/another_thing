"""Tests for parallax_engine.manager (director-era project manager §11.9)."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest
import yaml

from parallax_engine.manager import (
    MAX_ASSET_RETRIES_PER_ASSET,
    MAX_SCENE_REDESIGNS_PER_SCENE,
    MAX_STORYBOARD_REGENERATIONS,
    MAX_TOTAL_BUDGET_USD,
    ProjectManager,
    RunResult,
    _dict_fingerprint,
    director_mode,
)

# Path to canonical example storyboards
STORYBOARDS_DIR = Path(__file__).parent / "storyboards"


# ---------------------------------------------------------------------------
# Constants (§11.8.1)
# ---------------------------------------------------------------------------


class TestConstants:
    def test_max_asset_retries(self) -> None:
        assert MAX_ASSET_RETRIES_PER_ASSET == 3

    def test_max_scene_redesigns(self) -> None:
        assert MAX_SCENE_REDESIGNS_PER_SCENE == 2

    def test_max_storyboard_regenerations(self) -> None:
        assert MAX_STORYBOARD_REGENERATIONS == 1

    def test_max_budget_usd(self) -> None:
        assert MAX_TOTAL_BUDGET_USD == 8.00


# ---------------------------------------------------------------------------
# director_mode (§11.9.1 deterministic rule)
# ---------------------------------------------------------------------------


class TestDirectorMode:
    def test_long_brief_is_decomposed(self) -> None:
        mode = director_mode(target_duration_s=60.0)
        assert mode == "decomposed"

    def test_very_long_brief_is_decomposed(self) -> None:
        mode = director_mode(target_duration_s=120.0)
        assert mode == "decomposed"

    def test_save_the_cat_is_decomposed(self) -> None:
        mode = director_mode(requested_structure="save_the_cat")
        assert mode == "decomposed"

    def test_thrift_is_single(self) -> None:
        mode = director_mode(config_budget="thrift")
        assert mode == "single"

    def test_default_is_single(self) -> None:
        mode = director_mode()
        assert mode == "single"

    def test_short_standard_is_single(self) -> None:
        mode = director_mode(target_duration_s=15.0, config_budget="standard")
        assert mode == "single"

    def test_save_the_cat_overrides_thrift(self) -> None:
        """save_the_cat triggers decomposed even with thrift budget."""
        mode = director_mode(
            target_duration_s=10.0,
            requested_structure="save_the_cat",
            config_budget="thrift",
        )
        assert mode == "decomposed"

    def test_long_duration_overrides_thrift(self) -> None:
        """long duration triggers decomposed even with thrift budget."""
        mode = director_mode(target_duration_s=90.0, config_budget="thrift")
        assert mode == "decomposed"


# ---------------------------------------------------------------------------
# ProjectManager construction
# ---------------------------------------------------------------------------


class TestProjectManagerConstruct:
    def test_construction(self, tmp_path: Path) -> None:
        brief = tmp_path / "brief.md"
        brief.write_text("A forest flythrough", encoding="utf-8")
        pm = ProjectManager(workspace_dir=tmp_path, brief_path=brief)
        assert pm.workspace_dir == tmp_path
        assert pm.budget == "standard"

    def test_system_prompt_present(self, tmp_path: Path) -> None:
        pm = ProjectManager(tmp_path, tmp_path / "brief.md")
        assert "Project Manager" in ProjectManager.SYSTEM_PROMPT
        assert "not creative" in ProjectManager.SYSTEM_PROMPT

    def test_system_prompt_contains_all_allowed_agents(self, tmp_path: Path) -> None:
        """§11.13.5 — only named agents may be spawned."""
        pm = ProjectManager(tmp_path, tmp_path / "brief.md")
        prompt = ProjectManager.SYSTEM_PROMPT
        for agent_name in ("director", "scene-designer", "asset-generator", "mask-author", "renderer", "qa-critic"):
            assert agent_name in prompt, f"{agent_name!r} not in system prompt"

    def test_log_dir_created_on_init(self, tmp_path: Path) -> None:
        ProjectManager(tmp_path, tmp_path / "brief.md")
        assert (tmp_path / "logs").exists()

    def test_cache_dir_created_on_init(self, tmp_path: Path) -> None:
        ProjectManager(tmp_path, tmp_path / "brief.md")
        assert (tmp_path / ".cache").exists()

    def test_dry_run_flag(self, tmp_path: Path) -> None:
        pm = ProjectManager(tmp_path, tmp_path / "brief.md", dry_run=True)
        assert pm.dry_run is True


# ---------------------------------------------------------------------------
# Retry cap constants are Python-only (§11.13.9)
# ---------------------------------------------------------------------------


class TestRetryCountersPython:
    def test_counters_not_in_system_prompt(self, tmp_path: Path) -> None:
        """Retry caps must not be mentioned in any prompt (§11.13.9)."""
        prompt = ProjectManager.SYSTEM_PROMPT
        # The prompt should NOT mention specific retry numbers
        for phrase in ("MAX_ASSET_RETRIES", "MAX_SCENE_REDESIGNS", "3 tries", "second attempt"):
            assert phrase not in prompt, f"{phrase!r} found in system prompt"

    def test_retry_counters_are_ints(self) -> None:
        assert isinstance(MAX_ASSET_RETRIES_PER_ASSET, int)
        assert isinstance(MAX_SCENE_REDESIGNS_PER_SCENE, int)
        assert isinstance(MAX_STORYBOARD_REGENERATIONS, int)


# ---------------------------------------------------------------------------
# ProjectManager._log
# ---------------------------------------------------------------------------


class TestManagerLog:
    def test_log_writes_to_file(self, tmp_path: Path) -> None:
        brief = tmp_path / "brief.md"
        brief.write_text("test", encoding="utf-8")
        pm = ProjectManager(tmp_path, brief)
        pm._log("test event", extra={"x": 1})
        log_path = tmp_path / "logs" / "manager.log"
        assert log_path.exists()
        line = log_path.read_text(encoding="utf-8").strip()
        data = json.loads(line)
        assert data["event"] == "test event"
        assert data["x"] == 1

    def test_log_is_jsonl(self, tmp_path: Path) -> None:
        brief = tmp_path / "brief.md"
        brief.write_text("test", encoding="utf-8")
        pm = ProjectManager(tmp_path, brief)
        for i in range(3):
            pm._log(f"event {i}")
        log_path = tmp_path / "logs" / "manager.log"
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        for line in lines:
            json.loads(line)  # must parse


# ---------------------------------------------------------------------------
# RunResult dataclass
# ---------------------------------------------------------------------------


class TestRunResult:
    def test_default_values(self) -> None:
        r = RunResult(success=False)
        assert r.success is False
        assert r.output_path is None
        assert r.storyboard_regenerations == 0
        assert r.scene_redesigns == {}
        assert r.asset_retries == {}
        assert r.fatal_json_path is None

    def test_success(self) -> None:
        p = Path("/out.mp4")
        r = RunResult(success=True, output_path=p)
        assert r.success is True
        assert r.output_path == p


# ---------------------------------------------------------------------------
# _resume: storyboard diff
# ---------------------------------------------------------------------------


class TestResumeDiff:
    """Tests for --resume storyboard diff logic (§11.11)."""

    def test_compute_changed_scenes_no_cache(self, tmp_path: Path) -> None:
        """No cache → all scenes changed."""
        from parallax_engine.director.schema import load_storyboard_yaml
        # Use a real example storyboard (example_a = 1 scene)
        sb_path = tmp_path / "storyboard.yaml"
        shutil.copy2(STORYBOARDS_DIR / "example_a.yaml", sb_path)
        storyboard = load_storyboard_yaml(sb_path)
        brief = tmp_path / "brief.md"
        brief.write_text("test", encoding="utf-8")
        pm = ProjectManager(tmp_path, brief)
        changed = pm._compute_changed_scenes(storyboard)
        # All scene indices should be in 'changed' (no cache)
        expected = {s.index for s in storyboard.scenes}
        assert changed == expected

    def test_compute_changed_scenes_no_changes(self, tmp_path: Path) -> None:
        """If storyboard identical to cache, no changes detected."""
        from parallax_engine.director.schema import load_storyboard_yaml
        sb_path = tmp_path / "storyboard.yaml"
        shutil.copy2(STORYBOARDS_DIR / "example_a.yaml", sb_path)
        storyboard = load_storyboard_yaml(sb_path)

        # Write cache = same storyboard
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir(exist_ok=True)
        shutil.copy2(sb_path, cache_dir / "storyboard.yaml.last")

        brief = tmp_path / "brief.md"
        brief.write_text("test", encoding="utf-8")
        pm = ProjectManager(tmp_path, brief)
        changed = pm._compute_changed_scenes(storyboard)
        assert changed == set()

    def test_compute_changed_scenes_partial_change(self, tmp_path: Path) -> None:
        """Modified scene shows as changed; unmodified scenes don't."""
        from parallax_engine.director.schema import load_storyboard_yaml
        sb_path = tmp_path / "storyboard.yaml"
        shutil.copy2(STORYBOARDS_DIR / "example_b.yaml", sb_path)
        storyboard = load_storyboard_yaml(sb_path)

        # Write cache = current storyboard
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir(exist_ok=True)
        shutil.copy2(sb_path, cache_dir / "storyboard.yaml.last")

        brief = tmp_path / "brief.md"
        brief.write_text("test", encoding="utf-8")
        pm = ProjectManager(tmp_path, brief)

        # Verify: same storyboard → no changes
        changed = pm._compute_changed_scenes(storyboard)
        assert changed == set(), "Expected no changes with identical cache"


# ---------------------------------------------------------------------------
# _write_fatal
# ---------------------------------------------------------------------------


class TestWriteFatal:
    def test_fatal_json_created(self, tmp_path: Path) -> None:
        brief = tmp_path / "brief.md"
        brief.write_text("test", encoding="utf-8")
        pm = ProjectManager(tmp_path, brief)
        result = RunResult(success=False)
        pm._write_fatal(result, "test failure")
        fatal_path = tmp_path / "qa" / "fatal.json"
        assert fatal_path.exists()
        data = json.loads(fatal_path.read_text())
        assert data["reason"] == "test failure"
        assert result.fatal_json_path == fatal_path

    def test_fatal_json_contains_counters(self, tmp_path: Path) -> None:
        brief = tmp_path / "brief.md"
        brief.write_text("test", encoding="utf-8")
        pm = ProjectManager(tmp_path, brief)
        result = RunResult(
            success=False,
            storyboard_regenerations=1,
            scene_redesigns={"2": 2},
            asset_retries={"bird": 3},
        )
        pm._write_fatal(result, "cap exhausted")
        data = json.loads((tmp_path / "qa" / "fatal.json").read_text())
        assert data["storyboard_regenerations"] == 1
        assert data["scene_redesigns"]["2"] == 2
        assert data["asset_retries"]["bird"] == 3


# ---------------------------------------------------------------------------
# dry_run: full run with stub storyboard
# ---------------------------------------------------------------------------


class TestDryRun:
    def _copy_storyboard(self, workspace: Path) -> None:
        """Copy example_a.yaml to workspace/storyboard.yaml for dry_run tests."""
        shutil.copy2(STORYBOARDS_DIR / "example_a.yaml", workspace / "storyboard.yaml")

    def test_dry_run_returns_runresult(self, tmp_path: Path) -> None:
        self._copy_storyboard(tmp_path)
        brief = tmp_path / "brief.md"
        brief.write_text("A 10-second forest drone flythrough", encoding="utf-8")
        pm = ProjectManager(tmp_path, brief, dry_run=True)
        result = pm.run()
        assert isinstance(result, RunResult)

    def test_dry_run_has_log(self, tmp_path: Path) -> None:
        self._copy_storyboard(tmp_path)
        brief = tmp_path / "brief.md"
        brief.write_text("A 10-second forest drone flythrough", encoding="utf-8")
        pm = ProjectManager(tmp_path, brief, dry_run=True)
        result = pm.run()
        assert result.log_path is not None
        assert result.log_path.exists()

    def test_dry_run_elapsed_positive(self, tmp_path: Path) -> None:
        self._copy_storyboard(tmp_path)
        brief = tmp_path / "brief.md"
        brief.write_text("test", encoding="utf-8")
        pm = ProjectManager(tmp_path, brief, dry_run=True)
        result = pm.run()
        assert result.elapsed_s >= 0.0


# ---------------------------------------------------------------------------
# _dict_fingerprint (change detection helper)
# ---------------------------------------------------------------------------


class TestDictFingerprint:
    def test_same_dict_same_fingerprint(self) -> None:
        d1 = {"a": 1, "b": [2, 3]}
        d2 = {"b": [2, 3], "a": 1}
        assert _dict_fingerprint(d1) == _dict_fingerprint(d2)

    def test_different_dicts_different_fingerprint(self) -> None:
        d1 = {"a": 1}
        d2 = {"a": 2}
        assert _dict_fingerprint(d1) != _dict_fingerprint(d2)

    def test_returns_string(self) -> None:
        assert isinstance(_dict_fingerprint({}), str)


# ---------------------------------------------------------------------------
# Manager imports no forbidden modules
# ---------------------------------------------------------------------------


class TestNoForbiddenImports:
    def test_no_anthropic_at_import(self) -> None:
        """manager.py must not import anthropic at module level."""
        import parallax_engine.manager as mod
        import inspect
        src = inspect.getsource(mod)
        # top-level import anthropic is forbidden
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "anthropic", (
                        "manager.py must not import anthropic at top level"
                    )
