"""
parallax_engine/subagents.py -- AgentDefinitions for all five subagents.

Each AgentDefinition captures the static contract for a subagent:
  - name         string identifier used in dispatch
  - model        Claude model string
  - description  one-liner the lead matches against during dispatch
  - allowed_tools strictly-scoped tool list (no Agent tool per ss9.6)
  - system_prompt stub system prompt; returns a canned status string
                  matching the ss3.3 return contract

These are NOT live agent instances -- they are pure-data declarations
consumed by the lead orchestrator (lead.py) to configure ClaudeSDKClient
calls.  The stub prompts will be replaced with full prompts in a later
milestone; for now they guarantee the return contract is respected.

Model constants
---------------
SONNET = "claude-sonnet-4-5"  (lead uses this too, per ss3.1)
HAIKU  = "claude-haiku-4-5"   (asset-generator, mask-author)

SPEC anchors: ss3.1, ss3.3, ss9.6
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Model constants (pin here; bump when Anthropic releases new versions)
# ---------------------------------------------------------------------------

#: Sonnet model used by the lead and creative subagents (ss3.1).
SONNET: str = "claude-sonnet-4-5"

#: Haiku model used for parallel, narrow-scope subagents.
HAIKU: str = "claude-haiku-4-5"


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
        Full system prompt text.  Stub implementation returns a canned
        status string matching the ss3.3 return contract; the real prompt
        is a future milestone.
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
    system_prompt=(
        "You are the scene-designer subagent for parallax-engine.\n"
        "\n"
        "INPUTS:\n"
        "  workspace/brief.md  -- creative brief\n"
        "\n"
        "OUTPUTS:\n"
        "  workspace/scene.yaml  -- scene manifest (layers, masks, camera stub)\n"
        "\n"
        "RETURN VALUE (last line of your response, exactly):\n"
        "  scene written: <N> layers, <M> masks, duration <T>s\n"
        "\n"
        "[STUB] Return: scene written: 0 layers, 0 masks, duration 0s"
    ),
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
    system_prompt=(
        "You are the asset-generator subagent for parallax-engine.\n"
        "\n"
        "INPUTS:\n"
        "  workspace/scene.yaml       -- full scene manifest\n"
        "  LAYER_ID env var           -- which layer to generate\n"
        "\n"
        "OUTPUTS:\n"
        "  workspace/assets/<layer>.svg        -- SVG artwork\n"
        "  workspace/assets/<layer>.meta.json  -- raster hints\n"
        "\n"
        "RETURN VALUE (last line of your response, exactly):\n"
        "  ok: assets/<layer>.svg\n"
        "\n"
        "[STUB] Return: ok: assets/stub_layer.svg"
    ),
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
    system_prompt=(
        "You are the mask-author subagent for parallax-engine.\n"
        "\n"
        "INPUTS:\n"
        "  workspace/assets/<silhouette>.svg  -- existing SVG\n"
        "\n"
        "OUTPUTS:\n"
        "  workspace/assets/<silhouette>.svg  -- updated with id='silhouette'\n"
        "                                        and id='hole' paths\n"
        "\n"
        "RETURN VALUE (last line of your response, exactly):\n"
        "  ok: silhouette + hole paths added to assets/<file>.svg\n"
        "\n"
        "[STUB] Return: ok: silhouette + hole paths added to assets/stub.svg"
    ),
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
    system_prompt=(
        "You are the camera-pather subagent for parallax-engine.\n"
        "\n"
        "INPUTS:\n"
        "  workspace/scene.yaml  -- scene manifest (no camera block yet)\n"
        "  workspace/brief.md    -- creative brief\n"
        "\n"
        "OUTPUTS:\n"
        "  workspace/scene.yaml  -- updated with camera: block\n"
        "\n"
        "RETURN VALUE (last line of your response, exactly):\n"
        "  camera path written: <M> keyframes / drone path with <K> control points\n"
        "\n"
        "[STUB] Return: camera path written: 2 keyframes / drone path with 4 control points"
    ),
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
    system_prompt=(
        "You are the qa-critic subagent for parallax-engine.\n"
        "\n"
        "INPUTS:\n"
        "  workspace/frames/         -- rendered frames\n"
        "  workspace/scene.yaml      -- scene manifest\n"
        "  workspace/brief.md        -- creative brief\n"
        "\n"
        "OUTPUTS:\n"
        "  workspace/qa/pass_NN_report.md  -- QA report\n"
        "\n"
        "RETURN VALUE (last line of your response, exactly):\n"
        "  PASS\n"
        "  -- OR --\n"
        "  FAIL: <comma-separated issues>, see qa/pass_NN_report.md\n"
        "\n"
        "[STUB] Return: PASS"
    ),
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
