"""
Post-session integrity check for parallax-engine.

After each session, the runner calls verify() to confirm the agent did not
violate the milestone schema rules. If a violation is found, the runner
logs it and the next session will be told to investigate before doing new
work.

What we check:
  1. phase_milestones.json parses as JSON.
  2. Required top-level keys are present and well-typed.
  3. Every milestone has the required fields and correct types.
  4. Milestone IDs are unique.
  5. blocked_by references resolve to actual milestone IDs.
  6. The set of milestone IDs is the same as the prior snapshot — agents
     cannot add or remove milestones.
  7. For every milestone with passes=True:
        a. evidence/<id>/ exists and is non-empty.
        b. The immutable fields (title, description, acceptance_criteria,
           validation_commands, spec_anchors, blocked_by, phase) are
           byte-identical to the snapshot.

The "snapshot" is workspace/.harness/milestones_snapshot.json — written by
this module after a successful verify(), to be compared against on the next
verify() call.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Fields that, once set by the initializer, must never change.
IMMUTABLE_FIELDS = (
    "id", "phase", "phase_title", "title", "description",
    "acceptance_criteria", "validation_commands", "spec_anchors", "blocked_by",
)

# Fields the agent is allowed to modify session-to-session.
MUTABLE_FIELDS = ("passes", "evidence", "notes")


@dataclass
class IntegrityReport:
    ok: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    n_milestones: int = 0
    n_passing: int = 0

    def render(self) -> str:
        if self.ok:
            return f"  Integrity: OK ({self.n_passing}/{self.n_milestones} passing)"
        out = ["  Integrity: VIOLATIONS FOUND"]
        for v in self.violations:
            out.append(f"    ✗ {v}")
        for w in self.warnings:
            out.append(f"    ! {w}")
        return "\n".join(out)


def _milestones_path(project_dir: Path) -> Path:
    return project_dir / "phase_milestones.json"


def _snapshot_path(project_dir: Path) -> Path:
    return project_dir / "workspace" / ".harness" / "milestones_snapshot.json"


def _hash_immutable(milestone: dict[str, Any]) -> str:
    """Stable hash of the immutable fields of one milestone."""
    payload = {k: milestone.get(k) for k in IMMUTABLE_FIELDS}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _evidence_dir_nonempty(project_dir: Path, milestone_id: str) -> bool:
    d = project_dir / "evidence" / milestone_id
    if not d.exists() or not d.is_dir():
        return False
    return any(d.iterdir())


def verify(project_dir: Path) -> IntegrityReport:
    """Run the full integrity check. Returns a report; does not raise."""
    report = IntegrityReport(ok=True)
    path = _milestones_path(project_dir)

    # Step 1 — file exists and parses
    if not path.exists():
        # Acceptable before session 1 completes
        report.warnings.append("phase_milestones.json does not exist yet "
                               "(expected only before session 1 completes)")
        return report

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        report.ok = False
        report.violations.append(f"phase_milestones.json is not valid JSON: {e}")
        return report

    # Step 2 — top-level shape
    if not isinstance(data, dict):
        report.ok = False
        report.violations.append("phase_milestones.json must be a JSON object")
        return report

    milestones = data.get("milestones")
    if not isinstance(milestones, list):
        report.ok = False
        report.violations.append("phase_milestones.json: 'milestones' must be a list")
        return report

    report.n_milestones = len(milestones)

    # Step 3-4 — milestone shape and unique IDs
    seen_ids: set[str] = set()
    by_id: dict[str, dict[str, Any]] = {}
    for i, m in enumerate(milestones):
        if not isinstance(m, dict):
            report.ok = False
            report.violations.append(f"milestones[{i}]: not a JSON object")
            continue

        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            report.ok = False
            report.violations.append(f"milestones[{i}]: missing or empty 'id'")
            continue
        if mid in seen_ids:
            report.ok = False
            report.violations.append(f"milestones[{i}]: duplicate id {mid!r}")
            continue
        seen_ids.add(mid)
        by_id[mid] = m

        # Required fields
        for fname in ("phase", "title", "description", "acceptance_criteria",
                      "validation_commands", "spec_anchors", "blocked_by",
                      "passes"):
            if fname not in m:
                report.ok = False
                report.violations.append(f"{mid}: missing required field {fname!r}")

        # Type checks (only if present — missing already reported above)
        if "passes" in m and not isinstance(m["passes"], bool):
            report.ok = False
            report.violations.append(f"{mid}: 'passes' must be a boolean")
        for list_field in ("acceptance_criteria", "validation_commands",
                           "spec_anchors", "blocked_by"):
            if list_field in m and not isinstance(m[list_field], list):
                report.ok = False
                report.violations.append(
                    f"{mid}: {list_field!r} must be a list, got {type(m[list_field]).__name__}"
                )

        if m.get("passes") is True:
            report.n_passing += 1

    # Step 5 — blocked_by references resolve
    for mid, m in by_id.items():
        for ref in m.get("blocked_by", []) or []:
            if ref not in by_id:
                report.ok = False
                report.violations.append(
                    f"{mid}: blocked_by references unknown milestone {ref!r}"
                )

    # Step 7a — evidence present for passing milestones
    for mid, m in by_id.items():
        if m.get("passes") is True:
            if not _evidence_dir_nonempty(project_dir, mid):
                report.ok = False
                report.violations.append(
                    f"{mid}: passes=true but evidence/{mid}/ is missing or empty"
                )

    # Step 6 + 7b — compare to snapshot
    snap_path = _snapshot_path(project_dir)
    if snap_path.exists():
        try:
            snap = json.loads(snap_path.read_text())
        except json.JSONDecodeError:
            report.warnings.append(
                "milestones_snapshot.json is corrupt; skipping immutability check"
            )
        else:
            snap_ids = set(snap.keys())
            cur_ids = set(by_id.keys())
            added = cur_ids - snap_ids
            removed = snap_ids - cur_ids
            for x in added:
                report.ok = False
                report.violations.append(f"milestone {x!r} was ADDED — not allowed")
            for x in removed:
                report.ok = False
                report.violations.append(f"milestone {x!r} was REMOVED — not allowed")

            for mid in cur_ids & snap_ids:
                snap_hash = snap[mid]
                cur_hash = _hash_immutable(by_id[mid])
                if snap_hash != cur_hash:
                    report.ok = False
                    # Find which fields changed for a better error message
                    snap_full_path = (project_dir / "workspace" / ".harness" /
                                      "milestones_full_snapshot.json")
                    changed_fields = ["unknown"]
                    if snap_full_path.exists():
                        try:
                            full = json.loads(snap_full_path.read_text())
                            prior = full.get(mid, {})
                            changed_fields = [
                                f for f in IMMUTABLE_FIELDS
                                if prior.get(f) != by_id[mid].get(f)
                            ] or ["unknown"]
                        except (json.JSONDecodeError, OSError):
                            pass
                    report.violations.append(
                        f"{mid}: immutable field(s) changed: {', '.join(changed_fields)}"
                    )

    return report


def write_snapshot(project_dir: Path) -> None:
    """Persist the current milestones (immutable fields + full snapshot) so
    the next session's verify() can compare against this state.

    Should be called by the runner only AFTER a clean verify(). Calling it
    after a failing verify would lock in the violation."""
    path = _milestones_path(project_dir)
    if not path.exists():
        return

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return

    milestones = data.get("milestones") or []
    snap = {m["id"]: _hash_immutable(m) for m in milestones if "id" in m}
    full = {m["id"]: {k: m.get(k) for k in IMMUTABLE_FIELDS}
            for m in milestones if "id" in m}

    snap_path = _snapshot_path(project_dir)
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text(json.dumps(snap, indent=2, sort_keys=True))

    full_path = snap_path.parent / "milestones_full_snapshot.json"
    full_path.write_text(json.dumps(full, indent=2, sort_keys=True))
