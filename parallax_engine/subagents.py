"""
parallax_engine/subagents.py -- AgentDefinitions for all five subagents.

Each AgentDefinition captures the static contract for a subagent:
  - name         string identifier used in dispatch
  - model        Claude model string
  - description  one-liner the lead matches against during dispatch
  - allowed_tools strictly-scoped tool list (no Agent tool per ss9.6)
  - system_prompt full production prompt loaded from parallax_engine/prompts/

System prompts are loaded from markdown files in the prompts/ directory at
import time.  Each subagent reads from:
  parallax_engine/prompts/<name_with_underscores>.md

The lead orchestrator's prompt is in:
  parallax_engine/prompts/lead.md

Model constants
---------------
SONNET = "claude-sonnet-4-5"  (lead uses this too, per ss3.1)
HAIKU  = "claude-haiku-4-5"   (asset-generator, mask-author)

SPEC anchors: ss3.1, ss3.3, ss3.4, ss9.6
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Prompt loader
# ---------------------------------------------------------------------------

#: Directory containing all prompt markdown files.
_PROMPTS_DIR: pathlib.Path = pathlib.Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    """Load a prompt from parallax_engine/prompts/<name>.md.

    Parameters
    ----------
    name:
        Filename without the ``.md`` extension.  Use underscores, not hyphens,
        for multi-word names (e.g. ``"scene_designer"``).

    Returns
    -------
    str
        The full prompt text.

    Raises
    ------
    FileNotFoundError
        If the prompt file does not exist.
    """
    path = _PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# Model constants (pin here; bump when Anthropic releases new versions)
# ---------------------------------------------------------------------------

#: Sonnet model used by the lead and creative subagents (ss3.1).
SONNET: str = "claude-sonnet-4-5"

#: Haiku model used for parallel, narrow-scope subagents.
HAIKU: str = "claude-haiku-4-5"

#: Full lead orchestrator prompt loaded from prompts/lead.md at import time.
LEAD_PROMPT: str = load_prompt("lead")


# ---------------------------------------------------------------------------
# AgentDefinition dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentDefinition:
    """
    Static declaration of a subagent's contract.

    Attributes
    ----------
    name:
        Identifier used by the lead to log and route results.
    model:
        Claude model string passed to ClaudeSDKClient.
    description:
        One-line description; the lead uses this when selecting which
        subagent to dispatch (mirrors the SDK Agent tool description field).
    allowed_tools:
        Strictly-scoped tool list.  Must NOT contain 'Agent' (ss9.6).
    system_prompt:
        Full production prompt text loaded from
        ``parallax_engine/prompts/<name_underscored>.md`` at import time.
        Replaces the stub strings used in Phase 3.
    """

    name: str
    model: str
    description: str
    allowed_tools: tuple[str, ...]
    system_prompt: str

    def __post_init__(self) -> None:
        if "Agent" in self.allowed_tools:
            raise ValueError(
                f"AgentDefinition '{self.name}' must not include 'Agent' "
                f"in allowed_tools (ss9.6)."
            )


# ---------------------------------------------------------------------------
# The five subagent definitions (ss3.3)
# ---------------------------------------------------------------------------

SCENE_DESIGNER = AgentDefinition(
    name="scene-designer",
    model=SONNET,
    description=(
        "Reads brief.md and writes scene.yaml (manifest only, no assets). "
        "Returns 'scene written: N layers, M masks, duration Ts'."
    ),
    allowed_tools=("Read", "Write"),
    system_prompt=load_prompt("scene_designer"),
)

ASSET_GENERATOR = AgentDefinition(
    name="asset-generator",
    model=HAIKU,
    description=(
        "Given one layer from scene.yaml, writes assets/<layer>.svg and "
        "assets/<layer>.meta.json. Returns 'ok: assets/<layer>.svg'."
    ),
    allowed_tools=(
        "Read",
        "Write",
        "Bash",
        "mcp__parallax_render__gen_image",
        "mcp__parallax_masks__autosegment",
    ),
    system_prompt=load_prompt("asset_generator"),
)

MASK_AUTHOR = AgentDefinition(
    name="mask-author",
    model=HAIKU,
    description=(
        "Adds id='silhouette' and id='hole' paths to a silhouette SVG. "
        "Returns 'ok: silhouette + hole paths added to assets/<file>.svg'."
    ),
    allowed_tools=(
        "Read",
        "Write",
        "mcp__parallax_masks__alpha_refine",
    ),
    system_prompt=load_prompt("mask_author"),
)

CAMERA_PATHER = AgentDefinition(
    name="camera-pather",
    model=SONNET,
    description=(
        "Reads scene.yaml and brief.md, then writes the camera: block "
        "back to scene.yaml. Returns 'camera path written: M keyframes / "
        "drone path with K control points'."
    ),
    allowed_tools=("Read", "Write"),
    system_prompt=load_prompt("camera_pather"),
)

QA_CRITIC = AgentDefinition(
    name="qa-critic",
    model=SONNET,
    description=(
        "Reviews rendered frames in workspace/frames/ against scene.yaml "
        "and brief.md. Writes qa/pass_NN_report.md. Returns 'PASS' or "
        "'FAIL: <issues>, see qa/pass_NN_report.md'."
    ),
    allowed_tools=(
        "Read",
        "Write",
        "Glob",
        "mcp__parallax_qa__diff_frames",
        "mcp__parallax_qa__ssim_score",
    ),
    system_prompt=load_prompt("qa_critic"),
)


# ---------------------------------------------------------------------------
# Registry: ordered list of all five subagents
# ---------------------------------------------------------------------------

#: All subagent definitions in dispatch order (matches ss3.4 workflow).
ALL_SUBAGENTS: tuple[AgentDefinition, ...] = (
    SCENE_DESIGNER,
    ASSET_GENERATOR,
    MASK_AUTHOR,
    CAMERA_PATHER,
    QA_CRITIC,
)

#: Lookup by name for fast dispatch.
SUBAGENT_BY_NAME: dict[str, AgentDefinition] = {
    s.name: s for s in ALL_SUBAGENTS
}
