"""
tests/test_skill_packaging.py — Skill packaging verification (P5.M03).

Validates that the Skill infrastructure (SKILL.md, run.sh, CLI default path)
satisfies the §4.2–§4.4 acceptance criteria that can be tested automatically:

  1. skill/SKILL.md exists and matches the §4.2 template (name, description,
     usage, notes, examples sections).
  2. skill/scripts/run.sh exists, is executable, and matches §4.3 verbatim.
  3. The CLI handles ``--workspace DIR`` without a subcommand (the run.sh
     invocation form): with a brief.md present it runs the pipeline; without
     brief.md it exits 1 gracefully.
  4. Running the pipeline in dry-run mode with a pre-seeded storyboard.yaml
     produces workspace/out.mp4 (acceptance criterion 4).

Acceptance criteria that require a live Claude Code session with the Skill
registered are noted as MANUAL below and are not automated here.  The sole
validation_command for this milestone is ``python tools/validate_licensing.py``
which passes independently.

MANUAL criteria (require ``claude skill add ./skill`` + Anthropic API):
  - "Skill triggers on 'Make a 10-second drone flythrough of a redwood
    forest at golden hour'"
  - "Skill triggers on 'Show a portal in a tree opening into a neon city'"
    (both trigger the SKILL.md description → SKILL.md §4.2 matching text)

SPEC anchors: §4.2, §4.3, §4.4, §8.5
"""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest

# Project root — two levels up from this file (tests/ → project root)
_PROJECT_ROOT = Path(__file__).parent.parent

# Expected verbatim content per §4.3
_EXPECTED_RUN_SH = """\
#!/usr/bin/env bash
set -euo pipefail
WORKSPACE="${1:-./workspace}"
shift || true
python -m parallax_engine.cli --workspace "$WORKSPACE" "$@"
"""


# ---------------------------------------------------------------------------
# 1. SKILL.md content tests
# ---------------------------------------------------------------------------


class TestSkillMD:
    """SKILL.md must match §4.2 template exactly."""

    @pytest.fixture(autouse=True)
    def skill_md_path(self) -> Path:
        p = _PROJECT_ROOT / "skill" / "SKILL.md"
        assert p.exists(), "skill/SKILL.md not found"
        return p

    def test_skill_md_exists(self, skill_md_path: Path) -> None:
        assert skill_md_path.is_file()

    def test_skill_md_has_yaml_frontmatter(self, skill_md_path: Path) -> None:
        """SKILL.md must open with YAML frontmatter block (---...---)."""
        content = skill_md_path.read_text(encoding="utf-8")
        assert content.startswith("---"), "SKILL.md must start with YAML frontmatter ---"
        assert "---\n" in content[3:], "SKILL.md frontmatter must be closed with ---"

    def test_skill_md_name_is_parallax_video(self, skill_md_path: Path) -> None:
        """name: must be 'parallax-video' per §4.2."""
        content = skill_md_path.read_text(encoding="utf-8")
        assert "name: parallax-video" in content, (
            "SKILL.md frontmatter must contain 'name: parallax-video'"
        )

    def test_skill_md_description_covers_trigger_phrases(self, skill_md_path: Path) -> None:
        """Description must cover the trigger phrases from §4.2."""
        content = skill_md_path.read_text(encoding="utf-8")
        # Key discriminating phrases from the §4.2 template
        required_phrases = [
            "parallax animation",
            "drone-FPV",
            "portal transition",
            "stacked illustrated layers",
        ]
        for phrase in required_phrases:
            assert phrase in content, (
                f"SKILL.md description missing required phrase: {phrase!r}"
            )

    def test_skill_md_usage_section_present(self, skill_md_path: Path) -> None:
        """## Usage section must be present."""
        content = skill_md_path.read_text(encoding="utf-8")
        assert "## Usage" in content, "SKILL.md missing '## Usage' section"

    def test_skill_md_usage_references_run_sh(self, skill_md_path: Path) -> None:
        """Usage section must invoke bash scripts/run.sh per §4.2."""
        content = skill_md_path.read_text(encoding="utf-8")
        assert "bash scripts/run.sh" in content, (
            "SKILL.md Usage section must invoke 'bash scripts/run.sh'"
        )

    def test_skill_md_notes_section_present(self, skill_md_path: Path) -> None:
        """## Notes section must be present."""
        content = skill_md_path.read_text(encoding="utf-8")
        assert "## Notes" in content, "SKILL.md missing '## Notes' section"

    def test_skill_md_notes_no_multi_agent_delegation(self, skill_md_path: Path) -> None:
        """Notes must say the harness manages multi-agent work (§4.4 boundary)."""
        content = skill_md_path.read_text(encoding="utf-8")
        # §4.4: Skill does NOT carry multi-agent prompts
        assert "multi-agent" in content.lower() or "harness" in content.lower(), (
            "SKILL.md Notes must reference the harness handling multi-agent work"
        )

    def test_skill_md_examples_section_present(self, skill_md_path: Path) -> None:
        """## Examples section must be present."""
        content = skill_md_path.read_text(encoding="utf-8")
        assert "## Examples" in content, "SKILL.md missing '## Examples' section"

    def test_skill_md_example_prompts_match_spec(self, skill_md_path: Path) -> None:
        """§4.2 example prompts must appear in Examples section."""
        content = skill_md_path.read_text(encoding="utf-8")
        # Exact strings from §4.2
        assert "Make a 10-second drone flythrough of a redwood forest at golden hour" in content, (
            "SKILL.md missing example prompt 1 from §4.2"
        )
        assert "Show a portal in a tree opening into a neon city" in content, (
            "SKILL.md missing example prompt 3 from §4.2"
        )

    def test_skill_md_no_renderer_code(self, skill_md_path: Path) -> None:
        """SKILL.md must not embed renderer code or multi-agent prompts (§4.4)."""
        content = skill_md_path.read_text(encoding="utf-8")
        forbidden = [
            "render_scene",          # renderer function
            "project_points",        # projection math
            "numpy",                 # library code
            "import parallax",       # Python imports
        ]
        for token in forbidden:
            assert token not in content, (
                f"SKILL.md must not embed renderer code; found: {token!r}"
            )

    def test_skill_md_budget_override_documented(self, skill_md_path: Path) -> None:
        """§4.2 Notes: budget override syntax must be documented."""
        content = skill_md_path.read_text(encoding="utf-8")
        # §4.2 Notes: "Override with bash scripts/run.sh ./workspace --budget 5.00"
        assert "--budget" in content, (
            "SKILL.md Notes must document --budget override flag (§4.2)"
        )


# ---------------------------------------------------------------------------
# 2. run.sh content and permissions tests
# ---------------------------------------------------------------------------


class TestRunSh:
    """skill/scripts/run.sh must match §4.3 verbatim and be executable."""

    @pytest.fixture(autouse=True)
    def run_sh_path(self) -> Path:
        p = _PROJECT_ROOT / "skill" / "scripts" / "run.sh"
        assert p.exists(), "skill/scripts/run.sh not found"
        return p

    def test_run_sh_exists(self, run_sh_path: Path) -> None:
        assert run_sh_path.is_file()

    def test_run_sh_is_executable(self, run_sh_path: Path) -> None:
        """run.sh must have execute permission set."""
        mode = run_sh_path.stat().st_mode
        assert mode & stat.S_IXUSR, "skill/scripts/run.sh is not executable (chmod +x needed)"

    def test_run_sh_content_verbatim(self, run_sh_path: Path) -> None:
        """Content must match §4.3 verbatim (no extra lines or mutations)."""
        content = run_sh_path.read_text(encoding="utf-8")
        assert content == _EXPECTED_RUN_SH, (
            f"skill/scripts/run.sh content does not match §4.3 verbatim.\n"
            f"Expected:\n{_EXPECTED_RUN_SH!r}\n\nGot:\n{content!r}"
        )

    def test_run_sh_shebang(self, run_sh_path: Path) -> None:
        """run.sh must start with #!/usr/bin/env bash per §4.3."""
        first_line = run_sh_path.read_text(encoding="utf-8").splitlines()[0]
        assert first_line == "#!/usr/bin/env bash", (
            f"run.sh shebang incorrect: {first_line!r}"
        )

    def test_run_sh_set_euo_pipefail(self, run_sh_path: Path) -> None:
        """run.sh must set -euo pipefail per §4.3."""
        content = run_sh_path.read_text(encoding="utf-8")
        assert "set -euo pipefail" in content

    def test_run_sh_workspace_default(self, run_sh_path: Path) -> None:
        """run.sh must default workspace to ./workspace per §4.3."""
        content = run_sh_path.read_text(encoding="utf-8")
        assert 'WORKSPACE="${1:-./workspace}"' in content

    def test_run_sh_invokes_cli_module(self, run_sh_path: Path) -> None:
        """run.sh must invoke parallax_engine.cli as a module per §4.3."""
        content = run_sh_path.read_text(encoding="utf-8")
        assert "python -m parallax_engine.cli" in content

    def test_run_sh_passes_workspace_flag(self, run_sh_path: Path) -> None:
        """run.sh must pass --workspace to the CLI per §4.3."""
        content = run_sh_path.read_text(encoding="utf-8")
        assert '--workspace "$WORKSPACE"' in content

    def test_run_sh_passes_remaining_args(self, run_sh_path: Path) -> None:
        """run.sh must forward remaining args via $@ per §4.3."""
        content = run_sh_path.read_text(encoding="utf-8")
        assert '"$@"' in content


# ---------------------------------------------------------------------------
# 3. CLI default-path tests (run.sh invocation form)
# ---------------------------------------------------------------------------


class TestCLIDefaultPath:
    """CLI must handle --workspace without a subcommand (§4.3 invocation form)."""

    def test_cli_exits_1_without_brief_md(self) -> None:
        """--workspace with no brief.md and no --brief exits 1 gracefully."""
        from parallax_engine.cli import main

        with tempfile.TemporaryDirectory(prefix="parallax_p5m03_") as tmp:
            rc = main(["--workspace", tmp])
        assert rc == 1, "Expected exit code 1 when brief.md is absent"

    def test_cli_parses_workspace_flag_without_subcommand(self) -> None:
        """build_parser() must accept --workspace before any subcommand."""
        from parallax_engine.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["--workspace", "/tmp/ws"])
        assert args.workspace == "/tmp/ws"
        assert args.command is None

    def test_cli_parses_budget_flag(self) -> None:
        """--budget flag must be accepted at top level."""
        from parallax_engine.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["--workspace", "/tmp/ws", "--budget", "thrift"])
        assert args.budget == "thrift"

    def test_cli_parses_resume_flag(self) -> None:
        """--resume flag must be accepted at top level."""
        from parallax_engine.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["--workspace", "/tmp/ws", "--resume"])
        assert args.resume is True

    def test_cli_parses_max_budget_flag(self) -> None:
        """--max-budget USD must be accepted and parsed as float."""
        from parallax_engine.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["--workspace", "/tmp/ws", "--max-budget", "5.00"])
        assert args.max_budget == 5.00

    def test_cli_with_brief_flag_runs_pipeline(self) -> None:
        """--brief text triggers the pipeline without a pre-seeded brief.md."""
        from parallax_engine.cli import main

        with tempfile.TemporaryDirectory(prefix="parallax_p5m03_") as tmp:
            # Pre-seed storyboard.yaml so dry_run has something to work with
            storyboard_src = _PROJECT_ROOT / "tests" / "storyboards" / "example_a.yaml"
            storyboard_dst = Path(tmp) / "storyboard.yaml"
            storyboard_dst.write_text(
                storyboard_src.read_text(encoding="utf-8"), encoding="utf-8"
            )

            rc = main([
                "--workspace", tmp,
                "--brief", "Make a 10-second drone flythrough of a redwood forest",
                "--dry-run",
            ])

        # In dry-run mode with a valid storyboard the pipeline should succeed
        assert rc == 0, f"CLI exited {rc} — expected 0 in dry-run with pre-seeded storyboard"

    def test_cli_with_brief_md_runs_pipeline(self) -> None:
        """Pre-seeded brief.md triggers the pipeline without --brief flag."""
        from parallax_engine.cli import main

        with tempfile.TemporaryDirectory(prefix="parallax_p5m03_") as tmp:
            tmp_path = Path(tmp)
            # Seed brief.md and storyboard.yaml
            (tmp_path / "brief.md").write_text(
                "Show a portal in a tree opening into a neon city", encoding="utf-8"
            )
            storyboard_src = _PROJECT_ROOT / "tests" / "storyboards" / "example_c.yaml"
            (tmp_path / "storyboard.yaml").write_text(
                storyboard_src.read_text(encoding="utf-8"), encoding="utf-8"
            )

            rc = main(["--workspace", tmp, "--dry-run"])

        assert rc == 0, f"CLI exited {rc} — expected 0 with brief.md + dry-run"


# ---------------------------------------------------------------------------
# 4. workspace/out.mp4 produced (acceptance criterion 4)
# ---------------------------------------------------------------------------


class TestOutMp4Produced:
    """workspace/out.mp4 must be produced after a successful pipeline run."""

    def test_out_mp4_created_after_dry_run(self) -> None:
        """Dry-run with pre-seeded storyboard produces out.mp4 in workspace."""
        from parallax_engine.cli import main

        with tempfile.TemporaryDirectory(prefix="parallax_p5m03_") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "brief.md").write_text(
                "Make a 10-second drone flythrough of a redwood forest at golden hour",
                encoding="utf-8",
            )
            storyboard_src = _PROJECT_ROOT / "tests" / "storyboards" / "example_a.yaml"
            (tmp_path / "storyboard.yaml").write_text(
                storyboard_src.read_text(encoding="utf-8"), encoding="utf-8"
            )

            rc = main(["--workspace", tmp, "--dry-run"])
            out_mp4 = tmp_path / "out.mp4"

            # Check inside the with block — tmp dir is still alive here
            assert rc == 0, f"Pipeline exited {rc}, expected 0"
            assert out_mp4.exists(), "workspace/out.mp4 not produced after successful run"

    def test_manager_log_written(self) -> None:
        """logs/manager.log must be written during a run."""
        from parallax_engine.cli import main

        with tempfile.TemporaryDirectory(prefix="parallax_p5m03_") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "brief.md").write_text("forest flythrough", encoding="utf-8")
            storyboard_src = _PROJECT_ROOT / "tests" / "storyboards" / "example_a.yaml"
            (tmp_path / "storyboard.yaml").write_text(
                storyboard_src.read_text(encoding="utf-8"), encoding="utf-8"
            )

            main(["--workspace", tmp, "--dry-run"])
            log_path = tmp_path / "logs" / "manager.log"

            # Check inside the with block — tmp dir is still alive here
            assert log_path.exists(), "logs/manager.log not written"
            content = log_path.read_text(encoding="utf-8")
            assert "run started" in content
            assert "run finished" in content

    def test_out_mp4_path_returned_in_result(self) -> None:
        """ProjectManager.run() result.output_path == workspace/out.mp4."""
        from parallax_engine.manager import ProjectManager

        with tempfile.TemporaryDirectory(prefix="parallax_p5m03_") as tmp:
            tmp_path = Path(tmp)
            brief_path = tmp_path / "brief.md"
            brief_path.write_text("forest flythrough", encoding="utf-8")
            storyboard_src = _PROJECT_ROOT / "tests" / "storyboards" / "example_a.yaml"
            (tmp_path / "storyboard.yaml").write_text(
                storyboard_src.read_text(encoding="utf-8"), encoding="utf-8"
            )

            mgr = ProjectManager(
                workspace_dir=tmp_path,
                brief_path=brief_path,
                dry_run=True,
            )
            result = mgr.run()

            # Check inside the with block — tmp dir is still alive here
            assert result.success, "Manager run failed unexpectedly"
            assert result.output_path is not None
            assert result.output_path.name == "out.mp4"
            assert result.output_path.exists()


# ---------------------------------------------------------------------------
# 5. Licensing gate (the sole validation_command)
# ---------------------------------------------------------------------------


def test_validate_licensing_exits_0() -> None:
    """python tools/validate_licensing.py must exit 0 (sole validation_command)."""
    import subprocess

    result = subprocess.run(
        ["python", "tools/validate_licensing.py"],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"validate_licensing.py exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "6/6 checks passed" in result.stdout, (
        f"Expected '6/6 checks passed' in output:\n{result.stdout}"
    )
