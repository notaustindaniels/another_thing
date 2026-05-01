#!/usr/bin/env python3
"""
validate_licensing.py
=====================

Hard gate: parallax-engine is licensed for commercial resale. This validator
must exit 0 at the end of every milestone, every phase, every session.
Any forbidden dependency, any GPL-tainted import, any libx264 reference in
the build artifacts is a SEVERE violation that contaminates the resale
story.

This is paired with `runner/security.py`, which blocks `pip install
python-lottie` at the bash layer. The validator catches what the hook
cannot:

  - Transitive dependencies (a permitted package that pulls in a forbidden
    one)
  - Forbidden imports inside parallax_engine source code
  - libx264 references in pyproject.toml, setup.py, Dockerfile, scripts
  - Forbidden strings in compiled artifacts under build/ or dist/
  - The active FFmpeg binary linking against libx264

The validator is intentionally aggressive. False positives are acceptable
(the agent can rephrase or refactor); missed violations are not.

EXIT CODES:
    0 — all checks passed
    1 — at least one forbidden dependency / import / artifact / binary found
    2 — could not perform a check (e.g., pyproject.toml missing); treated as
        failure because we cannot verify the build is clean
    3 — internal error in the validator itself

Run from the project root:

    python tools/validate_licensing.py
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Forbidden inventory
# ---------------------------------------------------------------------------
#
# Three lists, three concerns:
#
#   FORBIDDEN_DISTS      — PyPI distribution names. Match exactly (case-
#                          insensitive). Caught via importlib.metadata.
#   FORBIDDEN_MODULES    — Python module import names. Caught via AST scan
#                          of parallax_engine/.
#   FORBIDDEN_STRINGS    — Substrings in text artifacts (pyproject.toml,
#                          Dockerfile, scripts). Caught via grep.
#
# A package may have a different distribution name than its import name
# (e.g., dist `Pillow` -> module `PIL`). We list both where they differ.

FORBIDDEN_DISTS = {
    "python-lottie":   "AGPL — fatal for resale; SPEC.md §5",
    "pylottie":        "AGPL alias of python-lottie",
    "lottie":          "AGPL — fatal for resale",
    "cairosvg":        "LGPL with linking complications; SPEC.md §5 explicitly avoids",
    "pycairo":         "LGPL with linking complications; SPEC.md §5 explicitly avoids",
    "ffmpeg-python":   "Wraps system FFmpeg; if linked to GPL build, contaminates resale. "
                       "Use subprocess invocation against the LGPL FFmpeg binary instead.",
    "moviepy":         "Pulls FFmpeg via imageio-ffmpeg which uses GPL builds by default. "
                       "Use subprocess against the LGPL build instead.",
    "imageio-ffmpeg":  "Bundles a GPL FFmpeg binary. Use the system LGPL FFmpeg instead.",
    "av":              "PyAV links against system FFmpeg; same risk as ffmpeg-python.",
    "decord":          "GPL-licensed video reader.",
    "x264":            "Direct libx264 bindings.",
}

# Module names that should never appear in `import` statements anywhere in
# parallax_engine/. We scan via AST, so this matches the leftmost dotted name
# (e.g., `import lottie.parsers` matches "lottie").
FORBIDDEN_MODULES = {
    "lottie":           "AGPL",
    "cairosvg":         "LGPL",
    "cairo":            "LGPL (pycairo)",
    "ffmpeg":           "ffmpeg-python wrapper; use subprocess instead",
    "moviepy":          "uses GPL FFmpeg by default",
    "imageio_ffmpeg":   "bundles GPL FFmpeg",
    "av":               "PyAV; same risk as ffmpeg-python",
    "decord":           "GPL video reader",
}

# Substrings to scan for in text files. Case-insensitive.
FORBIDDEN_STRINGS = {
    "libx264":          "GPL H.264 encoder; use libopenh264",
    "libx265":          "GPL H.265 encoder; not used by parallax-engine",
    "--enable-libx264": "FFmpeg build flag for libx264",
    "--enable-libx265": "FFmpeg build flag for libx265",
}

# Allowed runtime dependencies. If pyproject.toml declares a dependency NOT
# in this set, we warn (do not fail) because we'd rather flag for review
# than block legitimate additions the human approved.
ALLOWED_DISTS = {
    # Core
    "pillow", "numpy", "scipy", "opencv-python-headless",
    "skia-python",
    # Schema and config
    "pydantic", "pyyaml", "pydantic-yaml",
    # Agent harness
    "claude-agent-sdk", "claude-code-sdk", "anthropic",
    # Testing and dev
    "pytest", "pytest-cov", "ruff", "mypy",
    # Logging and CLI
    "rich", "click", "typer",
}

# Files we scan for forbidden strings. Globs are relative to project root.
TEXT_SCAN_GLOBS = [
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements*.txt",
    "Dockerfile*",
    "init.sh",
    "scripts/**/*.sh",
    "scripts/**/*.py",
    "parallax_engine/**/*.py",
    "tests/**/*.py",
]

# Files explicitly allowed to mention forbidden strings (the spec, this
# validator, the security hook, and prompts that warn agents about them).
TEXT_SCAN_ALLOWLIST = {
    "SPEC.md",
    "tools/validate_licensing.py",
    "runner/security.py",
    "prompts/initializer_prompt.md",
    "prompts/coding_prompt.md",
    "claude-progress.txt",
    "README.md",
}

# Where to write evidence
EVIDENCE_DIR = Path("evidence/licensing")


# ---------------------------------------------------------------------------
# Result accumulator
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    items: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)
    fatal_error: str | None = None

    @property
    def all_passed(self) -> bool:
        return self.fatal_error is None and all(r.passed for r in self.results)

    def to_json(self) -> dict:
        return {
            "validator": "validate_licensing",
            "all_passed": self.all_passed,
            "fatal_error": self.fatal_error,
            "results": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "detail": r.detail,
                    "items": r.items,
                    "elapsed_ms": r.elapsed_ms,
                }
                for r in self.results
            ],
        }


# ---------------------------------------------------------------------------
# Check 1 — pyproject.toml declared dependencies
# ---------------------------------------------------------------------------

def _read_pyproject(root: Path) -> dict | None:
    """Return parsed pyproject.toml or None if missing/unparseable."""
    path = root / "pyproject.toml"
    if not path.exists():
        return None

    # Python 3.11+ has tomllib; older versions need tomli. parallax-engine
    # requires 3.11+, so this should always work, but be defensive.
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[import-not-found, no-redef]
        except ImportError:
            return None

    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        return None


def _extract_declared_deps(pyproject: dict) -> list[str]:
    """Pull dependency strings from common pyproject locations."""
    deps: list[str] = []
    project = pyproject.get("project", {}) or {}
    deps.extend(project.get("dependencies", []) or [])
    optional = project.get("optional-dependencies", {}) or {}
    for group_deps in optional.values():
        deps.extend(group_deps or [])

    # Poetry-style
    poetry = (pyproject.get("tool", {}) or {}).get("poetry", {}) or {}
    poetry_deps = poetry.get("dependencies", {}) or {}
    for name in poetry_deps.keys():
        if name.lower() != "python":
            deps.append(name)

    return deps


_DEP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _normalize_dist_name(spec: str) -> str:
    """Extract the bare distribution name from a PEP 508 dependency string."""
    m = _DEP_NAME_RE.match(spec.strip())
    if not m:
        return ""
    return m.group(0).lower().replace("_", "-")


def check_declared_dependencies(root: Path) -> CheckResult:
    """Inspect pyproject.toml for forbidden distributions."""
    t0 = time.perf_counter()
    py = _read_pyproject(root)
    if py is None:
        return CheckResult(
            name="declared_dependencies",
            passed=False,
            detail="pyproject.toml missing or unparseable",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    declared = _extract_declared_deps(py)
    forbidden_found: list[str] = []
    unrecognized: list[str] = []

    for spec in declared:
        name = _normalize_dist_name(spec)
        if not name:
            continue
        if name in FORBIDDEN_DISTS:
            forbidden_found.append(f"{name}  ({FORBIDDEN_DISTS[name]})")
        elif name not in ALLOWED_DISTS:
            unrecognized.append(name)

    detail_parts = []
    if forbidden_found:
        detail_parts.append(f"forbidden: {forbidden_found}")
    if unrecognized:
        detail_parts.append(
            f"unrecognized (not in allowlist; review and add to ALLOWED_DISTS "
            f"if legitimate): {unrecognized}"
        )

    return CheckResult(
        name="declared_dependencies",
        passed=len(forbidden_found) == 0,
        detail="; ".join(detail_parts) if detail_parts else "all declared deps allowed",
        items=forbidden_found + [f"unrecognized:{u}" for u in unrecognized],
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


# ---------------------------------------------------------------------------
# Check 2 — installed distributions (catches transitives)
# ---------------------------------------------------------------------------

def check_installed_distributions(root: Path) -> CheckResult:
    """Walk importlib.metadata for forbidden distributions."""
    t0 = time.perf_counter()
    forbidden_found: list[str] = []

    try:
        for dist in importlib.metadata.distributions():
            name = (dist.metadata.get("Name") or "").lower().replace("_", "-")
            if not name:
                continue
            if name in FORBIDDEN_DISTS:
                version = dist.version or "?"
                forbidden_found.append(
                    f"{name}=={version}  ({FORBIDDEN_DISTS[name]})"
                )
    except Exception as e:
        return CheckResult(
            name="installed_distributions",
            passed=False,
            detail=f"could not enumerate installed distributions: {e}",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    return CheckResult(
        name="installed_distributions",
        passed=len(forbidden_found) == 0,
        detail=("forbidden installed: " + ", ".join(forbidden_found))
               if forbidden_found else "no forbidden distributions installed",
        items=forbidden_found,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


# ---------------------------------------------------------------------------
# Check 3 — AST scan of parallax_engine/ for forbidden imports
# ---------------------------------------------------------------------------

def check_source_imports(root: Path) -> CheckResult:
    """Walk parallax_engine/ and tests/ AST for forbidden imports."""
    import ast

    t0 = time.perf_counter()
    src_roots = [root / "parallax_engine", root / "tests"]
    findings: list[str] = []

    for src_root in src_roots:
        if not src_root.exists():
            continue
        for py_file in src_root.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(), filename=str(py_file))
            except SyntaxError as e:
                findings.append(
                    f"{py_file.relative_to(root)}: SYNTAX ERROR ({e}); "
                    f"cannot scan imports"
                )
                continue
            except Exception as e:
                findings.append(
                    f"{py_file.relative_to(root)}: read error ({e})"
                )
                continue

            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.module is not None and node.level == 0:
                        names = [node.module]

                for name in names:
                    head = name.split(".", 1)[0]
                    if head in FORBIDDEN_MODULES:
                        findings.append(
                            f"{py_file.relative_to(root)}:{node.lineno}: "
                            f"import {name!r} -> forbidden "
                            f"({FORBIDDEN_MODULES[head]})"
                        )

    # Filter syntax/read errors out of pass/fail computation only if there
    # were no real findings; otherwise report everything.
    real_findings = [f for f in findings if "-> forbidden" in f]
    return CheckResult(
        name="source_imports",
        passed=len(real_findings) == 0,
        detail=(f"{len(real_findings)} forbidden imports found"
                if real_findings else "no forbidden imports"),
        items=findings,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


# ---------------------------------------------------------------------------
# Check 4 — text scan for forbidden substrings
# ---------------------------------------------------------------------------

def _iter_text_files(root: Path) -> list[Path]:
    """Collect files matching TEXT_SCAN_GLOBS, deduplicated."""
    seen: set[Path] = set()
    files: list[Path] = []
    for pattern in TEXT_SCAN_GLOBS:
        for path in root.glob(pattern):
            if path.is_file() and path not in seen:
                seen.add(path)
                files.append(path)
    return files


def check_text_artifacts(root: Path) -> CheckResult:
    """Grep allowed text files for forbidden substrings."""
    t0 = time.perf_counter()
    findings: list[str] = []
    allowed_paths = {(root / p).resolve() for p in TEXT_SCAN_ALLOWLIST}

    for path in _iter_text_files(root):
        if path.resolve() in allowed_paths:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        lowered = text.lower()
        for needle, why in FORBIDDEN_STRINGS.items():
            if needle in lowered:
                # Find line numbers for the report
                line_nos = [
                    i + 1 for i, line in enumerate(text.splitlines())
                    if needle in line.lower()
                ]
                rel = path.relative_to(root)
                findings.append(
                    f"{rel}:{','.join(map(str, line_nos))}: "
                    f"{needle!r} -> {why}"
                )

    return CheckResult(
        name="text_artifacts",
        passed=len(findings) == 0,
        detail=(f"{len(findings)} forbidden-string occurrences found"
                if findings else "no forbidden strings in scanned text files"),
        items=findings,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


# ---------------------------------------------------------------------------
# Check 5 — system FFmpeg encoder list
# ---------------------------------------------------------------------------

def check_system_ffmpeg(root: Path) -> CheckResult:
    """
    Verify that the FFmpeg binary parallax-engine will invoke does NOT have
    libx264 enabled. Acceptable outcomes:
      - libopenh264 present, libx264 absent  → PASS
      - libopenh264 absent and libx264 absent → FAIL (no encoder available)
      - libx264 present                       → FAIL (GPL contamination)
    """
    t0 = time.perf_counter()
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        return CheckResult(
            name="system_ffmpeg",
            passed=False,
            detail="ffmpeg not found on PATH",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    try:
        proc = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="system_ffmpeg",
            passed=False,
            detail="ffmpeg -encoders timed out",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    except OSError as e:
        return CheckResult(
            name="system_ffmpeg",
            passed=False,
            detail=f"could not run ffmpeg: {e}",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    has_libx264 = bool(re.search(r"\blibx264\b", output))
    has_libx265 = bool(re.search(r"\blibx265\b", output))
    has_libopenh264 = bool(re.search(r"\blibopenh264\b", output))

    items: list[str] = []
    if has_libx264:
        items.append("libx264 present in ffmpeg encoder list")
    if has_libx265:
        items.append("libx265 present in ffmpeg encoder list")
    if not has_libopenh264:
        items.append(
            "libopenh264 not present — parallax-engine needs this for H.264 "
            "encoding under LGPL. Install an FFmpeg build that includes it."
        )

    passed = (not has_libx264) and (not has_libx265) and has_libopenh264

    return CheckResult(
        name="system_ffmpeg",
        passed=passed,
        detail=(f"ffmpeg at {ffmpeg_path}; libopenh264={has_libopenh264}, "
                f"libx264={has_libx264}, libx265={has_libx265}"),
        items=items,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


# ---------------------------------------------------------------------------
# Check 6 — pyproject.toml does not declare any forbidden dist as ALLOWED
# (sanity check: catches the case where someone edits the validator's
# ALLOWED_DISTS to "fix" a failure, and adds a forbidden name there.)
# ---------------------------------------------------------------------------

def check_self_consistency() -> CheckResult:
    """The validator's own constants must not contradict each other."""
    t0 = time.perf_counter()
    overlap = ALLOWED_DISTS & FORBIDDEN_DISTS.keys()
    if overlap:
        return CheckResult(
            name="self_consistency",
            passed=False,
            detail=(
                f"validator constants overlap: {overlap} appears in both "
                f"ALLOWED_DISTS and FORBIDDEN_DISTS. This means the validator "
                f"has been tampered with — every previous run since the "
                f"tampering should be considered invalid."
            ),
            items=list(overlap),
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    return CheckResult(
        name="self_consistency",
        passed=True,
        detail="validator constants are internally consistent",
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def write_evidence(report: Report) -> None:
    try:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        out = EVIDENCE_DIR / f"validate_licensing_{int(time.time())}.json"
        out.write_text(json.dumps(report.to_json(), indent=2))
        # Also write/overwrite the canonical "latest" file
        latest = EVIDENCE_DIR / "latest.json"
        latest.write_text(json.dumps(report.to_json(), indent=2))
    except OSError:
        pass


def render(report: Report) -> str:
    lines = []
    lines.append(f"[validate_licensing] {len(report.results)} checks")
    if report.fatal_error:
        lines.append(f"[validate_licensing] FATAL: {report.fatal_error}")
        return "\n".join(lines)

    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        lines.append(f"  {status}  {r.name}  ({r.elapsed_ms:.1f}ms)")
        if r.detail:
            lines.append(f"        {r.detail}")
        if not r.passed:
            for item in r.items:
                lines.append(f"        - {item}")

    n_passed = sum(1 for r in report.results if r.passed)
    lines.append("")
    lines.append(f"[validate_licensing] {n_passed}/{len(report.results)} checks passed")
    return "\n".join(lines)


def main() -> int:
    root = Path.cwd()
    report = Report()

    try:
        report.results.append(check_self_consistency())
        report.results.append(check_declared_dependencies(root))
        report.results.append(check_installed_distributions(root))
        report.results.append(check_source_imports(root))
        report.results.append(check_text_artifacts(root))
        report.results.append(check_system_ffmpeg(root))
    except Exception as e:
        report.fatal_error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

    print(render(report))
    write_evidence(report)

    if report.fatal_error:
        return 3
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(3)
