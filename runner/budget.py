"""
Budget tracking for the parallax-engine autonomous build.

Tracks input/output/cache tokens per session, computes USD cost from a
model-prefix pricing table, persists state to workspace/.harness/budget.json,
and exposes a check that the runner consults between sessions to decide
whether to continue or stop.

Persistence is critical: the run may be resumed days later, and the cap
applies across the entire run, not per session.

Pricing table reflects Anthropic public rates as of late April 2026
(see SPEC.md §11.12.2 for the analysis). Update the table if rates change.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Pricing (USD per million tokens)
# ---------------------------------------------------------------------------
#
# Match by model-string prefix. The first matching prefix wins, so order
# matters: more specific prefixes first.
#
# Cache reads are billed at 10% of the input rate.
# Cache creations are billed at 125% of the input rate (5-minute TTL).
# Anthropic's batch processing API offers 50% off but this harness uses
# interactive (streaming) requests; batch rates do not apply.

PRICING: list[tuple[str, dict[str, float]]] = [
    # (model_prefix, {input, output, cache_read, cache_create})
    ("claude-opus-4-7",   {"input":  5.00, "output": 25.00, "cache_read": 0.50, "cache_create":  6.25}),
    ("claude-opus-4-6",   {"input":  5.00, "output": 25.00, "cache_read": 0.50, "cache_create":  6.25}),
    ("claude-opus-4-1",   {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_create": 18.75}),
    ("claude-sonnet-4-6", {"input":  3.00, "output": 15.00, "cache_read": 0.30, "cache_create":  3.75}),
    ("claude-sonnet-4-5", {"input":  3.00, "output": 15.00, "cache_read": 0.30, "cache_create":  3.75}),
    ("claude-haiku-4-5",  {"input":  1.00, "output":  5.00, "cache_read": 0.10, "cache_create":  1.25}),
    ("claude-haiku-4",    {"input":  1.00, "output":  5.00, "cache_read": 0.10, "cache_create":  1.25}),
]

DEFAULT_PRICING = {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_create": 3.75}


def price_for_model(model: str) -> dict[str, float]:
    """Return the per-MTok pricing for a model string. Falls back to Sonnet rates."""
    for prefix, rates in PRICING:
        if model.startswith(prefix):
            return rates
    return DEFAULT_PRICING


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class SessionUsage:
    """Per-session usage record. Aggregated across multiple assistant messages."""
    session_index: int
    started_at: float                 # unix seconds
    ended_at: float | None = None
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_create_tokens: int = 0
    cost_usd: float = 0.0
    milestone_id: str | None = None   # set by the runner when known
    outcome: str | None = None        # "completed" | "stuck" | "error" | "budget"


@dataclass
class BudgetState:
    """Run-level budget state. Persisted to workspace/.harness/budget.json."""
    run_started_at: float
    cap_usd: float
    wallclock_cap_hours: float
    sessions: list[SessionUsage] = field(default_factory=list)
    # "oauth" if CLAUDE_CODE_OAUTH_TOKEN was used at run start (Max plan
    # billing — no per-token charge, dollar cap is informational only).
    # "api" if ANTHROPIC_API_KEY was used (pay-as-you-go, dollar cap enforced).
    # Detected on first load and persisted across resume.
    auth_mode: str = "api"

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.sessions)

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens + s.cache_read_tokens + s.cache_create_tokens
                   for s in self.sessions)

    @property
    def total_output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.sessions)

    @property
    def elapsed_hours(self) -> float:
        return (time.time() - self.run_started_at) / 3600.0


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _state_path(project_dir: Path) -> Path:
    return project_dir / "workspace" / ".harness" / "budget.json"


def _detect_auth_mode() -> str:
    """Detect which auth credential is being used. Mirrors runner's auth
    precedence (CLAUDE_CODE_OAUTH_TOKEN wins over ANTHROPIC_API_KEY)."""
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "oauth"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    return "unknown"


def load_state(project_dir: Path, cap_usd: float, wallclock_cap_hours: float) -> BudgetState:
    """Load existing state from disk, or initialize a new state. The cap
    parameters apply only on first creation; subsequent loads ignore the
    passed values and use what's persisted (so resumes are consistent)."""
    path = _state_path(project_dir)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            # Strip derived/informational fields that aren't dataclass kwargs
            data = {k: v for k, v in data.items() if not k.startswith("_")}
            sessions = [SessionUsage(**s) for s in data.pop("sessions", [])]
            # Backward compat: older state files won't have auth_mode
            data.setdefault("auth_mode", _detect_auth_mode())
            return BudgetState(sessions=sessions, **data)
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            # Corrupted state; back it up and start fresh. Better to lose
            # accounting accuracy than to crash the runner mid-build.
            backup = path.with_suffix(f".corrupt.{int(time.time())}.json")
            path.rename(backup)
            print(f"[budget] state file corrupt ({e}); backed up to {backup}")

    path.parent.mkdir(parents=True, exist_ok=True)
    state = BudgetState(
        run_started_at=time.time(),
        cap_usd=cap_usd,
        wallclock_cap_hours=wallclock_cap_hours,
        auth_mode=_detect_auth_mode(),
    )
    save_state(project_dir, state)
    return state


def save_state(project_dir: Path, state: BudgetState) -> None:
    """Atomically persist state to disk."""
    path = _state_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    payload = {
        "run_started_at": state.run_started_at,
        "cap_usd": state.cap_usd,
        "wallclock_cap_hours": state.wallclock_cap_hours,
        "auth_mode": state.auth_mode,
        "sessions": [asdict(s) for s in state.sessions],
        # Derived (informational only; load_state ignores these on read)
        "_total_cost_usd": round(state.total_cost_usd, 4),
        "_elapsed_hours": round(state.elapsed_hours, 3),
    }
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Per-message accumulation
# ---------------------------------------------------------------------------

def session_start(state: BudgetState, model: str) -> SessionUsage:
    """Begin tracking a new session. Returns the SessionUsage to update."""
    session = SessionUsage(
        session_index=len(state.sessions) + 1,
        started_at=time.time(),
        model=model,
    )
    state.sessions.append(session)
    return session


def accumulate_message(session: SessionUsage, usage: dict[str, int] | None) -> None:
    """Add one assistant message's usage to the current session.

    `usage` is the dict from the SDK's AssistantMessage (or whatever the
    SDK exposes). Expected keys: input_tokens, output_tokens,
    cache_read_input_tokens, cache_creation_input_tokens. Missing keys
    default to 0. Unknown keys are ignored.
    """
    if not usage:
        return
    session.input_tokens        += int(usage.get("input_tokens", 0) or 0)
    session.output_tokens       += int(usage.get("output_tokens", 0) or 0)
    session.cache_read_tokens   += int(usage.get("cache_read_input_tokens", 0) or 0)
    session.cache_create_tokens += int(usage.get("cache_creation_input_tokens", 0) or 0)
    session.cost_usd = compute_cost(session)


def compute_cost(session: SessionUsage) -> float:
    """USD cost for a session's accumulated tokens, using its declared model."""
    rates = price_for_model(session.model)
    return (
        session.input_tokens        * rates["input"]
      + session.output_tokens       * rates["output"]
      + session.cache_read_tokens   * rates["cache_read"]
      + session.cache_create_tokens * rates["cache_create"]
    ) / 1_000_000.0


def session_end(session: SessionUsage, outcome: str,
                milestone_id: str | None = None) -> None:
    """Mark a session complete with an outcome label."""
    session.ended_at = time.time()
    session.outcome = outcome
    if milestone_id is not None:
        session.milestone_id = milestone_id


# ---------------------------------------------------------------------------
# Cap enforcement
# ---------------------------------------------------------------------------

@dataclass
class BudgetVerdict:
    should_continue: bool
    reason: str
    cost_usd: float
    cap_usd: float
    elapsed_hours: float
    cap_hours: float


def check(state: BudgetState) -> BudgetVerdict:
    """Decide whether to start another session.

    Returns a verdict the runner can log and act on. The runner should call
    this BEFORE starting each new session — never mid-session, since killing
    a session mid-stream wastes the tokens already spent.

    On OAuth auth (Max plan), the dollar cap is informational only — the
    plan's rolling-window and weekly limits are enforced upstream by
    Anthropic, not by this harness. The wallclock cap still fires as a
    safety net so runaway loops can't go forever.
    """
    cost = state.total_cost_usd
    elapsed = state.elapsed_hours

    # Wallclock cap fires regardless of auth mode — this is a runaway-loop
    # safety net, not a billing concern.
    if elapsed >= state.wallclock_cap_hours:
        return BudgetVerdict(
            should_continue=False,
            reason=(f"wallclock cap reached: {elapsed:.1f}h of "
                    f"{state.wallclock_cap_hours:.1f}h elapsed"),
            cost_usd=cost, cap_usd=state.cap_usd,
            elapsed_hours=elapsed, cap_hours=state.wallclock_cap_hours,
        )

    # Dollar cap only fires on API auth (pay-as-you-go). On OAuth, plan
    # limits are enforced by Anthropic; the dollar number we compute is
    # an informational estimate of equivalent API cost.
    if state.auth_mode != "oauth" and cost >= state.cap_usd:
        return BudgetVerdict(
            should_continue=False,
            reason=(f"dollar cap reached: ${cost:.2f} of ${state.cap_usd:.2f} spent"),
            cost_usd=cost, cap_usd=state.cap_usd,
            elapsed_hours=elapsed, cap_hours=state.wallclock_cap_hours,
        )

    if state.auth_mode == "oauth":
        reason = (f"~${cost:.2f} estimated API-equivalent cost (Max plan; "
                  f"actual usage governed by Anthropic plan limits), "
                  f"{elapsed:.1f}h of {state.wallclock_cap_hours:.1f}h elapsed")
    else:
        reason = (f"${cost:.2f} of ${state.cap_usd:.2f} spent, "
                  f"{elapsed:.1f}h of {state.wallclock_cap_hours:.1f}h elapsed")

    return BudgetVerdict(
        should_continue=True,
        reason=reason,
        cost_usd=cost, cap_usd=state.cap_usd,
        elapsed_hours=elapsed, cap_hours=state.wallclock_cap_hours,
    )


def warning_threshold_reached(state: BudgetState, threshold: float = 0.85) -> str | None:
    """Return a warning string if cost or wallclock has exceeded `threshold`
    fraction of its cap. The runner injects this into the agent's prompt
    so the agent can wrap up cleanly before the hard cap hits.

    On OAuth auth, only the wallclock warning is meaningful — Anthropic
    enforces plan limits upstream and we don't see them here."""
    time_frac = state.elapsed_hours / state.wallclock_cap_hours if state.wallclock_cap_hours > 0 else 0.0

    if state.auth_mode == "oauth":
        if time_frac < threshold:
            return None
        return (f"WALLCLOCK WARNING: {state.elapsed_hours:.1f}h elapsed of "
                f"{state.wallclock_cap_hours:.1f}h cap ({time_frac*100:.0f}%). "
                f"Finish the current milestone and end the session cleanly.")

    cost_frac = state.total_cost_usd / state.cap_usd if state.cap_usd > 0 else 0.0
    worst = max(cost_frac, time_frac)
    if worst < threshold:
        return None
    if cost_frac >= time_frac:
        return (f"BUDGET WARNING: ${state.total_cost_usd:.2f} spent of "
                f"${state.cap_usd:.2f} cap ({cost_frac*100:.0f}%). "
                f"Finish the current milestone and end the session cleanly.")
    return (f"WALLCLOCK WARNING: {state.elapsed_hours:.1f}h elapsed of "
            f"{state.wallclock_cap_hours:.1f}h cap ({time_frac*100:.0f}%). "
            f"Finish the current milestone and end the session cleanly.")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def summary(state: BudgetState) -> str:
    """Human-readable summary, printed by the runner between sessions."""
    if state.auth_mode == "oauth":
        cost_line = (f"  Est. cost:  ~${state.total_cost_usd:.4f}  "
                     f"(API-equivalent estimate; Max plan in use, no per-token billing)")
    else:
        cost_line = f"  Cost:       ${state.total_cost_usd:.4f}  /  ${state.cap_usd:.2f} cap"

    lines = [
        "─" * 70,
        f"Budget summary  [auth: {state.auth_mode}]",
        cost_line,
        f"  Wallclock:  {state.elapsed_hours:.2f}h  /  {state.wallclock_cap_hours:.1f}h cap",
        f"  Sessions:   {len(state.sessions)}",
        f"  Tokens:     {state.total_input_tokens:,} in (incl. cache), "
        f"{state.total_output_tokens:,} out",
    ]
    if state.sessions:
        lines.append("  Recent sessions:")
        for s in state.sessions[-5:]:
            outcome = s.outcome or "in-progress"
            mid = s.milestone_id or "(unset)"
            lines.append(
                f"    #{s.session_index:3d}  ${s.cost_usd:6.3f}  "
                f"{s.input_tokens + s.cache_read_tokens + s.cache_create_tokens:>8,}in / "
                f"{s.output_tokens:>6,}out  {mid}  [{outcome}]"
            )
    lines.append("─" * 70)
    return "\n".join(lines)
