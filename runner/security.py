"""
Security Hook for parallax-engine Autonomous Build
==================================================

Pre-tool-use bash command validator. Adapted from the autonomous-coding
template with these changes for parallax-engine:

1. **Licensing block layer.** Any bash command containing forbidden strings
   (libx264, python-lottie, cairosvg, pycairo, ffmpeg-python) is blocked.
   This is belt-and-braces with `tools/validate_licensing.py` — the validator
   catches the result; this hook tries to prevent the action.

2. **Allowlist tuned for Python/FFmpeg work** rather than Node.js/web. Adds
   python, pip, pytest, ffmpeg, ffprobe. Removes npm, node (not used).

3. **Phase-gate enforcement.** The agent cannot run end-to-end pipeline
   commands before Phase 2 (the portal-equivalence gate) has passed. State
   is read from phase_milestones.json at hook time.

4. **Protected paths.** rm/mv/cp into tools/, references/, SPEC.md, or
   evidence/ is blocked.

5. **No network egress.** curl and wget are blocked entirely. Dependency
   fetches go through pip (whose targets are validated separately).
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

ALLOWED_COMMANDS = {
    # File inspection
    "ls", "cat", "head", "tail", "wc", "grep", "find", "tree", "diff", "stat", "file",
    # Directory and pwd
    "pwd", "cd", "echo",
    # File operations
    "cp", "mv", "mkdir", "chmod", "rm",
    # Python ecosystem
    "python", "python3", "pip", "pip3", "pytest",
    # Media tools (LGPL FFmpeg only)
    "ffmpeg", "ffprobe",
    # Build tools
    "make",
    # Version control
    "git",
    # Process management
    "ps", "lsof", "sleep", "kill", "pkill",
    # Init scripts
    "init.sh", "bash",
    # Hashing / determinism
    "sha256sum", "md5sum",
    # Archives (for evidence packaging)
    "tar", "gzip", "gunzip",
    # JSON tools
    "jq",
}

COMMANDS_NEEDING_EXTRA_VALIDATION = {
    "rm", "pkill", "kill", "chmod", "init.sh", "bash", "pip", "pip3",
    "python", "python3", "mv", "cp",
}


# ---------------------------------------------------------------------------
# Forbidden licensing patterns
# ---------------------------------------------------------------------------

LICENSE_FORBIDDEN_SUBSTRINGS = [
    "libx264",
    "python-lottie",
    "pylottie",
    "cairosvg",
    "pycairo",
    "ffmpeg-python",
]

PIP_FORBIDDEN_PATTERNS = [
    re.compile(r"\bpython-lottie\b", re.I),
    re.compile(r"\bpylottie\b", re.I),
    re.compile(r"\bcairosvg\b", re.I),
    re.compile(r"\bpycairo\b", re.I),
    re.compile(r"\bffmpeg-python\b", re.I),
    re.compile(r"\blibx264\b", re.I),
]

FFMPEG_FORBIDDEN_PATTERNS = [
    re.compile(r"-c:v\s+libx264", re.I),
    re.compile(r"-vcodec\s+libx264", re.I),
    re.compile(r"-codec:v\s+libx264", re.I),
    re.compile(r"--enable-libx264", re.I),
]

# Read-only commands that may legitimately reference forbidden strings
# (e.g., `grep libx264 SPEC.md` while the agent reads the spec).
READ_ONLY_COMMANDS = {"grep", "cat", "head", "tail", "find", "ls", "wc", "diff", "echo", "tree"}


# ---------------------------------------------------------------------------
# Dangerous patterns (regex, message)
# ---------------------------------------------------------------------------

DANGEROUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+/(\s|$)"),
        "rm -rf at filesystem root"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+/[a-zA-Z]"),
        "rm -rf at absolute path"),
    (re.compile(r"\bcurl\b"),
        "curl is not in the allowlist (no network egress from agent shell)"),
    (re.compile(r"\bwget\b"),
        "wget is not in the allowlist (no network egress from agent shell)"),
    (re.compile(r"\beval\s+"),
        "eval is forbidden"),
    (re.compile(r">\s*tools/"),
        "writing to tools/ is forbidden (validators are immutable)"),
    (re.compile(r">>\s*tools/"),
        "appending to tools/ is forbidden"),
    (re.compile(r">\s*SPEC\.md"),
        "writing to SPEC.md is forbidden (spec is immutable)"),
    (re.compile(r">>\s*SPEC\.md"),
        "appending to SPEC.md is forbidden"),
    (re.compile(r">\s*references/"),
        "writing to references/ is forbidden"),
]

PROTECTED_PATHS = (
    "tools/", "tools",
    "references/", "references",
    "SPEC.md",
    "evidence/", "evidence",
    "phase_milestones.json",
)


# ---------------------------------------------------------------------------
# Phase-gate state
# ---------------------------------------------------------------------------

def _get_phase_state(project_dir: Path) -> dict[float, bool]:
    """
    Read phase_milestones.json and return a dict mapping each phase number
    to whether all milestones in that phase have passes=True. Returns empty
    dict if file missing or unparseable.
    """
    path = project_dir / "phase_milestones.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

    by_phase: dict[float, list[bool]] = {}
    for m in data.get("milestones", []):
        phase = m.get("phase")
        if phase is None:
            continue
        by_phase.setdefault(float(phase), []).append(bool(m.get("passes", False)))

    return {
        phase: (len(passes_list) > 0 and all(passes_list))
        for phase, passes_list in by_phase.items()
    }


def _phase_gate_blocks(command: str, project_dir: Path) -> tuple[bool, str]:
    """Block end-to-end pipeline runs before Phase 2 has passed."""
    pipeline_markers = [
        "python -m parallax_engine",
        "python3 -m parallax_engine",
        "parallax-engine render",
        "parallax-engine run",
    ]
    if not any(marker in command for marker in pipeline_markers):
        return False, ""

    phase_state = _get_phase_state(project_dir)
    if phase_state.get(2.0):
        return False, ""

    return True, (
        "End-to-end pipeline invocation is gated until Phase 2 (portal "
        "equivalence) is fully passing. See SPEC.md §8 and the coding prompt: "
        "'Do not run the full renderer pipeline before Phase 2 passes'."
    )


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------

def split_command_segments(command_string: str) -> list[str]:
    """Split on &&, ||, ; into individual command segments."""
    segments = re.split(r"\s*(?:&&|\|\|)\s*", command_string)
    result: list[str] = []
    for segment in segments:
        sub_segments = re.split(r'(?<!["\'])\s*;\s*(?!["\'])', segment)
        for sub in sub_segments:
            sub = sub.strip()
            if sub:
                result.append(sub)
    return result


def extract_commands(command_string: str) -> list[str]:
    """Extract base command names from a shell command, handling pipes and chains."""
    commands: list[str] = []
    segments = re.split(r'(?<!["\'])\s*;\s*(?!["\'])', command_string)

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        try:
            tokens = shlex.split(segment)
        except ValueError:
            return []

        if not tokens:
            continue

        expect_command = True
        for token in tokens:
            if token in ("|", "||", "&&", "&"):
                expect_command = True
                continue
            if token in ("if", "then", "else", "elif", "fi", "for", "while",
                         "until", "do", "done", "case", "esac", "in", "!", "{", "}"):
                continue
            if token.startswith("-"):
                continue
            if "=" in token and not token.startswith("="):
                continue
            if expect_command:
                cmd = os.path.basename(token)
                commands.append(cmd)
                expect_command = False

    return commands


# ---------------------------------------------------------------------------
# Per-command validators
# ---------------------------------------------------------------------------

def _check_protected_paths(paths: list[str], action: str) -> tuple[bool, str]:
    """Block if any path is a protected file or under a protected directory."""
    for p in paths:
        normalized = p.rstrip("/")
        for protected in PROTECTED_PATHS:
            protected_norm = protected.rstrip("/")
            if normalized == protected_norm or normalized.startswith(protected_norm + "/"):
                return False, f"{action} of protected path forbidden: {p}"
    return True, ""


def validate_pip_command(command_string: str) -> tuple[bool, str]:
    for pattern in PIP_FORBIDDEN_PATTERNS:
        if pattern.search(command_string):
            return False, (
                f"pip command references a forbidden package "
                f"(matches {pattern.pattern!r}). See SPEC.md §5."
            )
    return True, ""


def validate_python_command(command_string: str) -> tuple[bool, str]:
    for pattern in PIP_FORBIDDEN_PATTERNS:
        if pattern.search(command_string):
            return False, (
                f"python invocation references a forbidden package "
                f"(matches {pattern.pattern!r}). See SPEC.md §5."
            )
    return True, ""


def validate_ffmpeg_command(command_string: str) -> tuple[bool, str]:
    for pattern in FFMPEG_FORBIDDEN_PATTERNS:
        if pattern.search(command_string):
            return False, (
                f"ffmpeg invocation requests libx264 (matches "
                f"{pattern.pattern!r}). parallax-engine must use libopenh264 "
                f"per SPEC.md §5."
            )
    return True, ""


def validate_pkill_command(command_string: str) -> tuple[bool, str]:
    allowed = {"python", "python3", "pytest", "ffmpeg"}
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "could not parse pkill command"
    if not tokens:
        return False, "empty pkill"
    args = [t for t in tokens[1:] if not t.startswith("-")]
    if not args:
        return False, "pkill requires a process name"
    target = args[-1]
    if " " in target:
        target = target.split()[0]
    if target in allowed:
        return True, ""
    return False, f"pkill only allowed for {sorted(allowed)}"


def validate_kill_command(command_string: str) -> tuple[bool, str]:
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "could not parse kill command"
    args = [t for t in tokens[1:] if not t.startswith("-")]
    if not args:
        return False, "kill requires a PID"
    for a in args:
        if not a.isdigit():
            return False, f"kill argument {a!r} is not a numeric PID"
    return True, ""


def validate_chmod_command(command_string: str) -> tuple[bool, str]:
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "could not parse chmod"
    if not tokens or tokens[0] != "chmod":
        return False, "not a chmod command"
    mode = None
    files: list[str] = []
    for t in tokens[1:]:
        if t.startswith("-"):
            return False, "chmod flags not allowed"
        elif mode is None:
            mode = t
        else:
            files.append(t)
    if mode is None:
        return False, "chmod requires a mode"
    if not files:
        return False, "chmod requires at least one file"
    if not re.match(r"^[ugoa]*\+x$", mode):
        return False, f"chmod only +x mode allowed, got {mode!r}"
    return True, ""


def validate_rm_command(command_string: str) -> tuple[bool, str]:
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "could not parse rm"
    paths = [t for t in tokens[1:] if not t.startswith("-")]
    if not paths:
        return False, "rm requires a target"
    for p in paths:
        if p.startswith("/"):
            return False, f"rm of absolute path forbidden: {p}"
        if p == "..":
            return False, "rm of parent directory forbidden"
    return _check_protected_paths(paths, action="rm")


def validate_mv_command(command_string: str) -> tuple[bool, str]:
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "could not parse mv"
    paths = [t for t in tokens[1:] if not t.startswith("-")]
    if len(paths) < 2:
        return False, "mv requires source and destination"
    return _check_protected_paths(paths, action="mv")


def validate_cp_command(command_string: str) -> tuple[bool, str]:
    """cp into a protected destination is forbidden; reading from protected
    sources is allowed."""
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "could not parse cp"
    paths = [t for t in tokens[1:] if not t.startswith("-")]
    if len(paths) < 2:
        return False, "cp requires source and destination"
    # Only the destination (last arg) is checked for protected-path writes
    return _check_protected_paths([paths[-1]], action="cp into")


def validate_init_script(command_string: str) -> tuple[bool, str]:
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "could not parse"
    if not tokens:
        return False, "empty"
    script = tokens[0]
    if script == "./init.sh" or script.endswith("/init.sh"):
        return True, ""
    return False, f"only init.sh allowed, got {script!r}"


def validate_bash_command(command_string: str) -> tuple[bool, str]:
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "could not parse"
    if not tokens or tokens[0] != "bash":
        return False, "not a bash command"
    if len(tokens) < 2:
        return False, "bash requires a target script"
    target = tokens[1]
    if target == "./init.sh" or target.endswith("/init.sh"):
        return True, ""
    return False, f"bash only allowed for init.sh, got {target!r}"


# ---------------------------------------------------------------------------
# Top-level licensing check
# ---------------------------------------------------------------------------

def _licensing_block_check(command_string: str, base_command: str) -> tuple[bool, str]:
    """
    Hard block on forbidden licensing substrings in non-read-only commands.
    Read-only commands (grep, cat, etc.) are allowed to reference the strings
    so the agent can read SPEC.md and validators that mention them.
    """
    if base_command in READ_ONLY_COMMANDS:
        return False, ""

    lowered = command_string.lower()
    for forbidden in LICENSE_FORBIDDEN_SUBSTRINGS:
        if forbidden in lowered:
            return True, (
                f"command contains forbidden licensing substring "
                f"{forbidden!r}. See SPEC.md §5 for the allowed stack. "
                f"If you only need to read references to this term, use "
                f"grep/cat/find."
            )
    return False, ""


# ---------------------------------------------------------------------------
# Hook entry point
# ---------------------------------------------------------------------------

async def bash_security_hook(input_data, tool_use_id=None, context=None):
    """PreToolUse hook for the Bash tool."""
    if input_data.get("tool_name") != "Bash":
        return {}

    command = input_data.get("tool_input", {}).get("command", "")
    if not command:
        return {}

    # 0. Dangerous-pattern hard block
    for pattern, reason in DANGEROUS_PATTERNS:
        if pattern.search(command):
            return {"decision": "block", "reason": f"dangerous pattern: {reason}"}

    # 1. Parse commands
    commands = extract_commands(command)
    if not commands:
        return {
            "decision": "block",
            "reason": f"could not parse command for security validation: {command!r}",
        }

    segments = split_command_segments(command)

    # 2. Per-command checks
    for cmd in commands:
        if cmd not in ALLOWED_COMMANDS:
            return {
                "decision": "block",
                "reason": f"command {cmd!r} is not in the allowlist",
            }

        # Locate the segment containing this command
        cmd_segment = command
        for seg in segments:
            if cmd in extract_commands(seg):
                cmd_segment = seg
                break

        # Licensing block (skipped for read-only)
        is_blocked, reason = _licensing_block_check(cmd_segment, cmd)
        if is_blocked:
            return {"decision": "block", "reason": reason}

        # Per-command validators
        if cmd in COMMANDS_NEEDING_EXTRA_VALIDATION:
            validators = {
                "pip":      validate_pip_command,
                "pip3":     validate_pip_command,
                "python":   validate_python_command,
                "python3":  validate_python_command,
                "pkill":    validate_pkill_command,
                "kill":     validate_kill_command,
                "chmod":    validate_chmod_command,
                "rm":       validate_rm_command,
                "mv":       validate_mv_command,
                "cp":       validate_cp_command,
                "init.sh":  validate_init_script,
                "bash":     validate_bash_command,
            }
            validator = validators.get(cmd)
            if validator is not None:
                ok, reason = validator(cmd_segment)
                if not ok:
                    return {"decision": "block", "reason": reason}

        # ffmpeg: codec selection check regardless of allowlist
        if cmd in ("ffmpeg", "ffprobe"):
            ok, reason = validate_ffmpeg_command(cmd_segment)
            if not ok:
                return {"decision": "block", "reason": reason}

    # 3. Phase-gate check
    project_dir = Path(os.environ.get("PARALLAX_PROJECT_DIR", "."))
    blocked, reason = _phase_gate_blocks(command, project_dir)
    if blocked:
        return {"decision": "block", "reason": reason}

    return {}
