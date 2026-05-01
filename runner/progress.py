"""
Milestone progress tracking for parallax-engine.

Reads phase_milestones.json (created by the initializer agent in session 1)
and computes per-phase counts of passing milestones. Used by the runner to
print between-session summaries.

This module never modifies phase_milestones.json. The agent owns writes;
the harness only reads.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def _file(project_dir: Path) -> Path:
    return project_dir / "phase_milestones.json"


def load(project_dir: Path) -> dict[str, Any] | None:
    """Return the parsed phase_milestones.json, or None if it doesn't exist
    yet (which is the expected state before session 1 completes)."""
    path = _file(project_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"[progress] phase_milestones.json is malformed: {e}")
        return None


def counts(project_dir: Path) -> tuple[int, int]:
    """(passing, total) across all milestones."""
    data = load(project_dir)
    if data is None:
        return 0, 0
    milestones = data.get("milestones", [])
    total = len(milestones)
    passing = sum(1 for m in milestones if m.get("passes", False))
    return passing, total


def by_phase(project_dir: Path) -> dict[str, tuple[int, int]]:
    """{phase_label: (passing, total)} grouped by phase. Phases are sorted
    in their natural numeric order (1, 2, 3, 4, 4.5, 5, 6)."""
    data = load(project_dir)
    if data is None:
        return {}

    buckets: dict[float, dict[str, int]] = defaultdict(lambda: {"pass": 0, "total": 0})
    for m in data.get("milestones", []):
        phase = m.get("phase")
        try:
            key = float(phase)
        except (TypeError, ValueError):
            continue
        buckets[key]["total"] += 1
        if m.get("passes", False):
            buckets[key]["pass"] += 1

    result: dict[str, tuple[int, int]] = {}
    for key in sorted(buckets.keys()):
        label = f"Phase {int(key) if key == int(key) else key}"
        result[label] = (buckets[key]["pass"], buckets[key]["total"])
    return result


def next_unblocked(project_dir: Path) -> dict[str, Any] | None:
    """Find the next milestone the coding agent SHOULD work on, by the same
    selection algorithm specified in the coding prompt. Returned for logging
    only — the agent does this lookup itself."""
    data = load(project_dir)
    if data is None:
        return None

    milestones = data.get("milestones", [])
    by_id = {m["id"]: m for m in milestones if "id" in m}

    candidates = []
    for m in milestones:
        if m.get("passes", False):
            continue
        blocked_by = m.get("blocked_by", [])
        if all(by_id.get(b, {}).get("passes", False) for b in blocked_by):
            candidates.append(m)

    def sort_key(m: dict[str, Any]) -> tuple[float, str]:
        try:
            return (float(m.get("phase", 999)), str(m.get("id", "")))
        except (TypeError, ValueError):
            return (999.0, str(m.get("id", "")))

    candidates.sort(key=sort_key)
    return candidates[0] if candidates else None


def summary(project_dir: Path) -> str:
    """Multi-line human-readable progress summary for the runner to print."""
    data = load(project_dir)
    if data is None:
        return "  (phase_milestones.json not yet created — session 1 has not run)"

    passing, total = counts(project_dir)
    if total == 0:
        return "  phase_milestones.json exists but contains no milestones."
    pct = passing / total * 100

    lines = [f"  Overall: {passing}/{total} milestones passing ({pct:.0f}%)"]
    for label, (p, t) in by_phase(project_dir).items():
        bar = "█" * int(p / t * 20) + "░" * (20 - int(p / t * 20)) if t > 0 else ""
        lines.append(f"    {label:10s}  {p:>3d}/{t:<3d}  {bar}")

    nxt = next_unblocked(project_dir)
    if nxt:
        lines.append(f"  Next:    {nxt.get('id')} — {nxt.get('title', '(no title)')}")
    else:
        if passing == total:
            lines.append("  Status:  ALL MILESTONES PASSING")
        else:
            lines.append("  Status:  NO UNBLOCKED MILESTONES (build is stuck)")

    return "\n".join(lines)
