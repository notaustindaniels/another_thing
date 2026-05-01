#!/usr/bin/env python3
"""
parallax-engine Autonomous Build Runner
======================================

Long-running harness that drives Claude through the parallax-engine build
specified in SPEC.md. Each session runs with a fresh context window;
progress is persisted via phase_milestones.json and git commits.

Differences from Anthropic's autonomous-coding template:
  - Phase-gated milestone schema (phase_milestones.json) instead of a
    flat feature_list.json.
  - Dollar cap and wallclock cap, both enforced between sessions and
    persisted across resume.
  - Bash hook tuned for parallax-engine (no Puppeteer, blocks forbidden
    Python/system packages per SPEC.md §5).
  - Post-session integrity verification (no agent can mutate immutable
    milestone fields, add/remove milestones, or mark passes=true without
    populated evidence/).
  - Pre-flight reference check — fail fast if references/ is empty.

Auth precedence (first non-empty wins):
  1. CLAUDE_CODE_OAUTH_TOKEN  (recommended for development)
  2. ANTHROPIC_API_KEY        (recommended for production)

Usage:
    python -m runner.autonomous_runner --project-dir parallax-engine \
        --budget-usd 8.00 --wallclock-hours 72

Resume:
    Run the same command again. State is read from
    workspace/.harness/budget.json and phase_milestones.json.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import time
from pathlib import Path

# Make sibling modules importable when run as a script.
sys.path.insert(0, str(Path(__file__).parent))

from agent import run_session
from budget import (
    load_state, save_state, session_start, session_end,
    check, warning_threshold_reached, summary as budget_summary,
)
from client import create_client
from integrity import verify, write_snapshot
from progress import summary as progress_summary, load as load_milestones
import prompts


# Module-level flag set by the signal handler. Read by the main loop between
# sessions; we never abort mid-session because that wastes already-spent tokens.
_stop_requested = False


def _install_signal_handlers() -> None:
    """SIGINT/SIGTERM set the stop flag; the next between-session check exits."""
    def handler(signum, frame):
        global _stop_requested
        if _stop_requested:
            # Second signal — let it propagate and KeyboardInterrupt the loop.
            print("\n[runner] second signal received — aborting now")
            signal.default_int_handler(signum, frame)
        else:
            print(f"\n[runner] signal {signum} received; finishing current "
                  f"session then stopping. Send signal again to abort immediately.")
            _stop_requested = True

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


DEFAULT_MODEL = "claude-sonnet-4-6"
# NOTE: SPEC.md §11.12 specifies $1.20-$2.50 per RENDER, with $8 as the
# per-render hard cap. That is NOT the build cost. The autonomous BUILD
# (writing the codebase from scratch over 24-72h) is a different budget.
# Anthropic's autonomous-coding example writes a full claude.ai clone in
# roughly $1k-$2k of model time; our build is comparable in scope. Use
# $1500 as the default and override per-run if needed.
DEFAULT_BUDGET_USD = 1500.0
DEFAULT_WALLCLOCK_HOURS = 72.0
INTER_SESSION_DELAY_SECONDS = 5
MAX_CONSECUTIVE_ERRORS = 3   # if 3 sessions in a row error out, give up


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="parallax-engine autonomous build runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--project-dir", type=Path, required=True,
                   help="Path to the parallax-engine project root (must contain "
                        "SPEC.md, references/, tools/, prompts/).")
    p.add_argument("--model", type=str, default=DEFAULT_MODEL,
                   help=f"Claude model to use (default: {DEFAULT_MODEL}).")
    p.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD,
                   help=f"Hard dollar cap across the run (default: ${DEFAULT_BUDGET_USD:.2f}). "
                        f"Ignored on resume; the persisted cap wins.")
    p.add_argument("--wallclock-hours", type=float, default=DEFAULT_WALLCLOCK_HOURS,
                   help=f"Hard wallclock cap in hours (default: {DEFAULT_WALLCLOCK_HOURS}). "
                        f"Ignored on resume.")
    p.add_argument("--max-iterations", type=int, default=None,
                   help="Optional session count limit (in addition to budget caps).")
    return p.parse_args()


def have_auth() -> tuple[bool, str]:
    """Verify auth is present. Returns (ok, message)."""
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True, "CLAUDE_CODE_OAUTH_TOKEN (dev)"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True, "ANTHROPIC_API_KEY (production)"
    return False, ""


def preflight(project_dir: Path) -> list[str]:
    """Verify the project directory is set up correctly. Returns list of
    error strings; empty list means good to go."""
    errors: list[str] = []
    if not project_dir.exists():
        errors.append(f"project directory does not exist: {project_dir}")
        return errors
    if not project_dir.is_dir():
        errors.append(f"project path is not a directory: {project_dir}")
        return errors

    required = {
        "SPEC.md":           "specification (write or copy your SPEC.md here)",
        "references":        "reference materials (drone videos, transcripts, prior repo)",
        "tools":             "validation scripts (validate_*.py)",
        "prompts":           "prompt files (initializer_prompt.md, coding_prompt.md)",
    }
    for name, desc in required.items():
        path = project_dir / name
        if not path.exists():
            errors.append(f"missing {name} — {desc}")
            continue
        if path.is_dir() and not any(path.iterdir()):
            errors.append(f"{name}/ is empty — {desc}")

    # Specific files inside prompts/
    for fname in ("initializer_prompt.md", "coding_prompt.md"):
        f = project_dir / "prompts" / fname
        if not f.exists() or f.stat().st_size == 0:
            errors.append(f"prompts/{fname} is missing or empty")

    # At least one validator must exist
    tools_dir = project_dir / "tools"
    if tools_dir.exists() and not any(tools_dir.glob("validate_*.py")):
        errors.append("tools/ contains no validate_*.py scripts")

    return errors


async def main_async(args: argparse.Namespace) -> int:
    project = args.project_dir.resolve()

    # Install signal handlers so SIGINT/SIGTERM stops cleanly between sessions
    _install_signal_handlers()

    # Preflight: auth
    ok, auth_label = have_auth()
    if not ok:
        print("[runner] no auth credentials found.")
        print("[runner] set one of:")
        print("[runner]   export CLAUDE_CODE_OAUTH_TOKEN='...'  (recommended for dev)")
        print("[runner]   export ANTHROPIC_API_KEY='...'        (recommended for production)")
        return 2
    print(f"[runner] auth: {auth_label}")

    # Preflight: project directory
    errors = preflight(project)
    if errors:
        print(f"[runner] project directory not ready: {project}")
        for e in errors:
            print(f"[runner]   ✗ {e}")
        print("[runner] fix these and rerun. See SPEC.md for setup instructions.")
        return 3
    print(f"[runner] project: {project}")

    # Budget setup (state persists across resume; caps from CLI apply only on
    # first creation).
    state = load_state(project, cap_usd=args.budget_usd,
                       wallclock_cap_hours=args.wallclock_hours)
    print(f"[runner] {budget_summary(state)}")

    # Session 1 detection: phase_milestones.json doesn't exist yet
    is_first_session = not (project / "phase_milestones.json").exists()
    if is_first_session:
        print("[runner] phase_milestones.json not found — running initializer (session 1)")
    else:
        print("[runner] resuming existing build")
        print(progress_summary(project))

    iteration = 0
    consecutive_errors = 0

    while True:
        iteration += 1

        # Stop flag from signal handler (SIGINT/SIGTERM)
        if _stop_requested:
            print(f"\n[runner] stop requested; exiting after {iteration - 1} sessions")
            break

        # Hard limit (CLI override of the run)
        if args.max_iterations is not None and iteration > args.max_iterations:
            print(f"\n[runner] reached --max-iterations ({args.max_iterations}); stopping")
            break

        # Budget gate
        verdict = check(state)
        if not verdict.should_continue:
            print(f"\n[runner] {verdict.reason}")
            print("[runner] stopping. Resume by re-running the same command "
                  "with a higher --budget-usd or --wallclock-hours, or by "
                  "deleting workspace/.harness/budget.json to start a fresh budget.")
            break
        print(f"\n[runner] {verdict.reason}")

        # Soft warning: inject into prompt if at 85% of either cap
        warning = warning_threshold_reached(state, threshold=0.85)

        # Pick prompt
        if is_first_session:
            base_prompt = prompts.initializer()
            session_label = "INITIALIZER"
        else:
            base_prompt = prompts.coding()
            session_label = "CODING"
        prompt = prompts.with_warning(base_prompt, warning)

        print("\n" + "═" * 70)
        print(f"  SESSION {iteration} ({session_label})")
        print("═" * 70 + "\n")

        # Build client (fresh per session — that's the point)
        client = create_client(project, args.model)
        session = session_start(state, args.model)

        try:
            async with client:
                outcome, _resp = await run_session(
                    client, prompt, session,
                    project_dir=project, state=state,
                )
        except Exception as e:
            print(f"[runner] session crashed before completion: {type(e).__name__}: {e}")
            outcome = "error"

        session_end(session, outcome=outcome)
        save_state(project, state)

        # Track consecutive errors so we don't burn budget retrying a broken setup
        if outcome == "error":
            consecutive_errors += 1
            print(f"[runner] session {iteration} ended with error "
                  f"({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS} consecutive)")
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(f"[runner] {MAX_CONSECUTIVE_ERRORS} consecutive errors; stopping. "
                      "Inspect the project state before resuming.")
                break
        else:
            consecutive_errors = 0

        # First session is over; subsequent sessions are coding sessions
        is_first_session = False

        # Post-session integrity check
        report = verify(project)
        print(report.render())
        if report.ok:
            write_snapshot(project)

        # Progress summary
        print(progress_summary(project))
        data = load_milestones(project)
        if data is not None:
            milestones = data.get("milestones", [])
            if milestones and all(m.get("passes") for m in milestones):
                print("\n[runner] all milestones passing — build complete.")
                break

        print(f"\n[runner] inter-session delay {INTER_SESSION_DELAY_SECONDS}s...")
        await asyncio.sleep(INTER_SESSION_DELAY_SECONDS)

    # Final summary
    print("\n" + "═" * 70)
    print("  RUN COMPLETE")
    print("═" * 70)
    print(budget_summary(state))
    print(progress_summary(project))
    return 0


def main() -> None:
    args = parse_args()
    try:
        rc = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[runner] interrupted by user; resume by running the same command")
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
