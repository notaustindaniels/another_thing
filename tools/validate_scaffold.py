#!/usr/bin/env python3
"""
validate_scaffold.py
====================

Phase 1 sanity check. Verifies the project skeleton is in place: required
files exist, the package imports cleanly, the test suite runs, init.sh is
executable, git is initialized. This is fast (sub-second when clean) and
runs as part of every Phase 1 milestone's validation_commands.

WHAT GETS CHECKED
-----------------

1. Required top-level files exist and are non-empty:
     SPEC.md, pyproject.toml, README.md, .gitignore, init.sh,
     phase_milestones.json
2. Required directories exist:
     parallax_engine/, tests/, tools/, references/, prompts/, workspace/,
     evidence/
3. parallax_engine/ has an __init__.py and is a valid Python package
4. tests/ has an __init__.py
5. `python -c "import parallax_engine"` succeeds
6. init.sh is executable
7. .git/ exists (project is a git repo)
8. phase_milestones.json parses as JSON and has the expected top-level keys
9. No forbidden files exist (e.g., a stray feature_list.json suggesting
   the agent confused the schema)

EXIT CODES
----------
    0 — scaffold is complete
    1 — one or more required pieces missing
    2 — internal error in the validator

This validator is intentionally minimal. It does NOT check that any code
inside parallax_engine/ is correct — that's the projection/camera/portal
validators' jobs. It just confirms the floor is laid.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
import traceback
from pathlib import Path


REQUIRED_FILES = [
    "SPEC.md",
    "pyproject.toml",
    "README.md",
    ".gitignore",
    "init.sh",
    "phase_milestones.json",
]

REQUIRED_DIRS = [
    "parallax_engine",
    "tests",
    "tools",
    "references",
    "prompts",
    "workspace",
    "evidence",
]

REQUIRED_PACKAGE_FILES = [
    "parallax_engine/__init__.py",
    "tests/__init__.py",
]

FORBIDDEN_FILES = [
    # Suggests the agent confused parallax-engine's milestone schema with
    # Anthropic's autonomous-coding template (which uses feature_list.json)
    "feature_list.json",
    # Suggests the agent ran the renderer end-to-end before Phase 2 (the
    # forbidden output)
    "out.mp4",
]

PHASE_MILESTONES_REQUIRED_KEYS = ["schema_version", "project", "milestones"]

EVIDENCE_DIR = Path("evidence/P1")


# ---------------------------------------------------------------------------
# Result accumulator
# ---------------------------------------------------------------------------

class Failure:
    __slots__ = ("category", "what", "fix")

    def __init__(self, category: str, what: str, fix: str = "") -> None:
        self.category = category
        self.what = what
        self.fix = fix

    def render(self) -> str:
        s = f"  FAIL  [{self.category}]  {self.what}"
        if self.fix:
            s += f"\n        fix: {self.fix}"
        return s


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_required_files(project_dir: Path, failures: list[Failure]) -> None:
    for fname in REQUIRED_FILES:
        path = project_dir / fname
        if not path.exists():
            failures.append(Failure(
                "missing_file", f"required file does not exist: {fname}",
                f"create {fname} per SPEC.md and the initializer prompt",
            ))
        elif path.stat().st_size == 0:
            failures.append(Failure(
                "empty_file", f"required file is empty: {fname}",
                f"populate {fname} with appropriate content",
            ))


def check_required_dirs(project_dir: Path, failures: list[Failure]) -> None:
    for dname in REQUIRED_DIRS:
        path = project_dir / dname
        if not path.exists():
            failures.append(Failure(
                "missing_dir", f"required directory does not exist: {dname}/",
                f"mkdir -p {dname}",
            ))
        elif not path.is_dir():
            failures.append(Failure(
                "not_a_dir", f"path exists but is not a directory: {dname}",
                f"remove the file at {dname} and create it as a directory",
            ))


def check_package_files(project_dir: Path, failures: list[Failure]) -> None:
    for fname in REQUIRED_PACKAGE_FILES:
        path = project_dir / fname
        if not path.exists():
            failures.append(Failure(
                "missing_pkg_file", f"required package file: {fname}",
                f"create {fname} (can be empty)",
            ))


def check_package_imports(project_dir: Path, failures: list[Failure]) -> None:
    """Run `python -c "import parallax_engine"` in a subprocess from project_dir."""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", "import parallax_engine"],
            capture_output=True, text=True, timeout=30,
            cwd=str(project_dir),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        failures.append(Failure(
            "import_error", f"could not run python: {e}",
            "ensure Python is installed and on PATH",
        ))
        return

    if proc.returncode != 0:
        # Truncate stderr for readability
        err = proc.stderr.strip().splitlines()
        err_summary = err[-1] if err else "(no stderr)"
        failures.append(Failure(
            "import_error", f"`import parallax_engine` failed: {err_summary}",
            "fix the import error before advancing past Phase 1",
        ))


def check_init_executable(project_dir: Path, failures: list[Failure]) -> None:
    init_sh = project_dir / "init.sh"
    if not init_sh.exists():
        return  # already reported by check_required_files
    mode = init_sh.stat().st_mode
    if not (mode & stat.S_IXUSR):
        failures.append(Failure(
            "not_executable", "init.sh is not executable",
            "chmod +x init.sh",
        ))


def check_git_initialized(project_dir: Path, failures: list[Failure]) -> None:
    git_dir = project_dir / ".git"
    if not git_dir.exists():
        failures.append(Failure(
            "no_git", "project is not a git repository",
            "git init && git add . && git commit -m 'initial scaffold'",
        ))
        return

    # Verify at least one commit exists
    try:
        proc = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=str(project_dir),
        )
        if proc.returncode != 0 or proc.stdout.strip() == "0":
            failures.append(Failure(
                "no_commits", "git repository has no commits yet",
                "make at least one commit of the initial scaffold",
            ))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        failures.append(Failure(
            "git_unavailable", "git command not available or timed out",
            "ensure git is installed",
        ))


def check_phase_milestones(project_dir: Path, failures: list[Failure]) -> None:
    path = project_dir / "phase_milestones.json"
    if not path.exists():
        return  # already reported

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        failures.append(Failure(
            "milestones_invalid", f"phase_milestones.json is not valid JSON: {e}",
            "fix the JSON syntax",
        ))
        return

    if not isinstance(data, dict):
        failures.append(Failure(
            "milestones_invalid",
            "phase_milestones.json must be a JSON object at the top level",
            "wrap the content in {} per the schema",
        ))
        return

    for key in PHASE_MILESTONES_REQUIRED_KEYS:
        if key not in data:
            failures.append(Failure(
                "milestones_missing_key",
                f"phase_milestones.json missing required top-level key {key!r}",
                f"add the {key!r} field per the initializer prompt",
            ))

    milestones = data.get("milestones")
    if not isinstance(milestones, list) or not milestones:
        failures.append(Failure(
            "milestones_empty",
            "phase_milestones.json has no milestones (the array is empty or missing)",
            "populate the milestones list per SPEC.md §8",
        ))
        return

    # Per-milestone quick validation
    seen_ids: set[str] = set()
    for i, m in enumerate(milestones):
        if not isinstance(m, dict):
            failures.append(Failure(
                "milestone_invalid",
                f"milestone at index {i} is not an object",
                "each milestone must be a JSON object",
            ))
            continue

        required_fields = ("id", "phase", "title", "validation_commands",
                          "blocked_by", "passes")
        for field in required_fields:
            if field not in m:
                failures.append(Failure(
                    "milestone_missing_field",
                    f"milestone at index {i} (id={m.get('id', '?')!r}) "
                    f"missing required field {field!r}",
                    f"add the {field!r} field per the initializer prompt schema",
                ))

        mid = m.get("id")
        if mid is not None:
            if mid in seen_ids:
                failures.append(Failure(
                    "milestone_duplicate_id",
                    f"duplicate milestone id: {mid!r}",
                    "ensure all milestone ids are unique",
                ))
            seen_ids.add(mid)


def check_no_forbidden_files(project_dir: Path, failures: list[Failure]) -> None:
    for fname in FORBIDDEN_FILES:
        path = project_dir / fname
        if path.exists():
            failures.append(Failure(
                "forbidden_file",
                f"forbidden file present: {fname}",
                f"remove {fname} — see the initializer prompt for why this is wrong",
            ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _write_evidence(project_dir: Path, failures: list[Failure],
                    checks_run: list[str]) -> None:
    try:
        evidence_dir = project_dir / EVIDENCE_DIR
        evidence_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%S")
        out = evidence_dir / f"validate_scaffold_{ts}.json"
        out.write_text(json.dumps({
            "validator": "validate_scaffold",
            "timestamp": ts,
            "ok": not failures,
            "checks_run": checks_run,
            "failures": [
                {"category": f.category, "what": f.what, "fix": f.fix}
                for f in failures
            ],
        }, indent=2))
        (evidence_dir / "latest_scaffold.json").write_text(out.read_text())
    except OSError:
        pass


def main() -> int:
    project_dir = Path(os.environ.get("PARALLAX_PROJECT_DIR", ".")).resolve()
    print(f"[validate_scaffold] project: {project_dir}")
    print()

    failures: list[Failure] = []
    checks_run: list[str] = []

    check_required_files(project_dir, failures)
    checks_run.append("required_files")

    check_required_dirs(project_dir, failures)
    checks_run.append("required_dirs")

    check_package_files(project_dir, failures)
    checks_run.append("package_files")

    check_package_imports(project_dir, failures)
    checks_run.append("package_imports")

    check_init_executable(project_dir, failures)
    checks_run.append("init_executable")

    check_git_initialized(project_dir, failures)
    checks_run.append("git_initialized")

    check_phase_milestones(project_dir, failures)
    checks_run.append("phase_milestones")

    check_no_forbidden_files(project_dir, failures)
    checks_run.append("no_forbidden_files")

    if not failures:
        print(f"[validate_scaffold] PASS — all {len(checks_run)} checks clean")
        _write_evidence(project_dir, failures, checks_run)
        return 0

    print(f"[validate_scaffold] FAIL — {len(failures)} issue(s):")
    print()
    for f in failures:
        print(f.render())
        print()

    _write_evidence(project_dir, failures, checks_run)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(2)
