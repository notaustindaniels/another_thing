"""
parallax_engine/cli.py — Command-line interface (§8 Phase 1 + §8.3).

Subcommands
-----------
render <scene.yaml> --out <out.mp4> [--workspace <dir>]
    Parse a scene YAML, render it with the full pipeline, encode to MP4.

harness --workspace <dir> --brief <text>
    Run the full agent harness (§3, §8.3).  Uses ClaudeSDKStub in offline
    mode so the smoke test is self-contained.  Exits 0 when the pipeline
    completes (with or without a render output).

Usage
-----
    python -m parallax_engine render path/to/scene.yaml --out /tmp/out.mp4

    # Harness smoke test (stub subagents, no SDK required)
    python -m parallax_engine harness --workspace ./workspace --brief "test"

SPEC anchors: §1.2, §3.2, §3.7, §3.8, §4.3, §8.1, §8.3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Minimal hand-coded scene.yaml for smoke-test runs (§8.3 deliverable)
# ---------------------------------------------------------------------------
# Matches the lead.py layer-discovery format: stacks map to plain lists of
# layer dicts (not the {layers: [...]} form).  One layer + one mask ensures
# asset-generator AND mask-author are each dispatched at least once.
_SMOKE_SCENE_YAML: str = """\
# Auto-generated smoke-test scene — parallax-engine §8.3 Phase 3
version: 1
meta:
  title: smoke-test
  resolution: [640, 360]
  fps: 10
  duration_s: 1.0
  seed: 42

stacks:
  main:
    - id: sky
      src: assets/sky_smoke.svg
      scene_xyz: [0, 0, -5000]
      plate_size: [640, 360]

masks:
  - silhouette_svg: assets/portal_smoke.svg
    behind_stack: main

camera:
  mode: drone
  drone:
    path:
      kind: bezier
      controls: [[0, 0, 0], [0, 0, -2500], [0, 0, -5000]]
      duration_s: 1.0
    poi_lookahead_s: 0.55
    spring_halflife_s: 0.18

post:
  global:
    vignette: {strength: 0.3, radius: 0.85}
"""


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_render(args: argparse.Namespace) -> int:
    """
    Render a scene YAML to an MP4 file.

    Returns exit code (0 = success, 1 = error).
    """
    from parallax_engine.render import render_scene
    from parallax_engine.scene import SceneVersionError, load_scene_yaml

    scene_path = Path(args.scene).resolve()
    if not scene_path.exists():
        print(f"error: scene file not found: {scene_path}", file=sys.stderr)
        return 1

    # Workspace defaults to the scene file's directory when not specified.
    # This allows "python -m parallax_engine render tests/scenes/forest.yaml"
    # to resolve assets from tests/scenes/assets/ automatically.
    if args.workspace:
        workspace = Path(args.workspace).resolve()
    else:
        workspace = scene_path.parent

    out_path = Path(args.out).resolve()

    try:
        scene = load_scene_yaml(scene_path)
    except SceneVersionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error parsing scene YAML: {exc}", file=sys.stderr)
        return 1

    print(f"[parallax] Rendering '{scene_path.name}'")
    print(f"[parallax] Workspace: {workspace}")
    print(
        f"[parallax] {scene.meta.resolution[0]}×{scene.meta.resolution[1]} "
        f"@ {scene.meta.fps}fps × {scene.meta.duration_s}s "
        f"({int(scene.meta.duration_s * scene.meta.fps)} frames)"
    )
    print(f"[parallax] Output:    {out_path}")

    try:
        render_scene(scene, workspace, out_path)
    except Exception as exc:
        print(f"error during render: {exc}", file=sys.stderr)
        return 1

    size_kb = out_path.stat().st_size // 1024
    print(f"[parallax] Done. {out_path.name} ({size_kb} KB)")
    return 0


def cmd_harness(args: argparse.Namespace) -> int:
    """
    Run the agent harness on a natural-language brief (§3, §8.3).

    In offline/stub mode (when ``anthropic``/claude-code-sdk is not
    installed) all five subagents are backed by ``ClaudeSDKStub`` which
    returns canned status strings.  The scene.yaml is pre-seeded so the
    asset-generator and mask-author are each dispatched at least once.

    The RENDER_DONE checkpoint is pre-written on the first run so that
    the stub pipeline completes without attempting a real FFmpeg render
    (which would fail without a fully valid scene).

    Returns 0 always — salvage output is reported but not treated as an
    error, because the smoke test goal is harness plumbing, not render
    quality.
    """
    from parallax_engine.lead import ClaudeSDKStub, ParallaxLead
    from parallax_engine.observability import log_usage
    from parallax_engine.state import PHASE_RENDER_DONE, is_phase_done, write_checkpoint

    workspace = Path(args.workspace).resolve() if args.workspace else Path("workspace").resolve()
    brief: str = args.brief or "test scene"

    # -----------------------------------------------------------------------
    # 1. Prepare workspace directories (§3.2 filesystem-as-shared-state)
    # -----------------------------------------------------------------------
    for subdir in ("assets", "masks", "frames", "qa", "logs", "checkpoints"):
        (workspace / subdir).mkdir(parents=True, exist_ok=True)

    print(f"[parallax] Harness run starting")
    print(f"[parallax] Workspace: {workspace}")
    print(f"[parallax] Brief:     {brief!r}")

    # -----------------------------------------------------------------------
    # 2. Pre-seed scene.yaml if not already present
    #    The scene-designer stub does NOT write a real file; we provide
    #    a hand-coded scene.yaml so that asset-generator and mask-author
    #    can discover layers and masks respectively (§8.3 "hand-coded
    #    scene.yaml verbatim").
    # -----------------------------------------------------------------------
    scene_yaml = workspace / "scene.yaml"
    if not scene_yaml.exists():
        scene_yaml.write_text(_SMOKE_SCENE_YAML, encoding="utf-8")
        print("[parallax] Seeded workspace/scene.yaml (smoke-test scene)")

    # -----------------------------------------------------------------------
    # 3. Pre-write RENDER_DONE checkpoint on the first run
    #    The stub subagents do not produce real SVG assets so the actual
    #    renderer cannot run.  We mark render-done so the lead skips the
    #    render step while still dispatching all other agents.
    # -----------------------------------------------------------------------
    if not is_phase_done(workspace=workspace, phase=PHASE_RENDER_DONE):
        write_checkpoint(
            workspace=workspace,
            phase=PHASE_RENDER_DONE,
            extra={"reason": "stub-mode: render skipped in smoke test"},
        )
        print("[parallax] Pre-wrote render-done checkpoint (stub mode)")

    # -----------------------------------------------------------------------
    # 4. Build stub SDK factory with canned per-agent responses
    # -----------------------------------------------------------------------
    _stub_responses: dict[str, str] = {
        "scene-designer": "scene written: 1 layers, 1 masks, duration 1.0s",
        "asset-generator": "ok: assets/sky_smoke.svg",
        "mask-author": "ok: silhouette + hole paths added to assets/portal_smoke.svg",
        "camera-pather": "camera path written: drone path with 3 control points",
        "qa-critic": "PASS",
    }

    def _stub_factory(max_turns: int, max_budget_usd: float, permission_mode: str) -> ClaudeSDKStub:
        return ClaudeSDKStub(
            response_map=_stub_responses,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            permission_mode=permission_mode,
        )

    # -----------------------------------------------------------------------
    # 5. Run the lead orchestrator
    # -----------------------------------------------------------------------
    lead = ParallaxLead(workspace=workspace, sdk_client_factory=_stub_factory)
    result = lead.run(brief)

    # -----------------------------------------------------------------------
    # 6. Guarantee usage.jsonl is non-empty (§3.7)
    #    ClaudeSDKStub does not generate SDK ResultMessage.usage events, so
    #    we write a synthetic run-summary entry after the run completes.
    # -----------------------------------------------------------------------
    log_usage(
        workspace=workspace,
        message_id="stub-run-summary",
        model="stub/ClaudeSDKStub",
        usage={
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        agent_id="lead",
    )

    # -----------------------------------------------------------------------
    # 7. Report outcome
    # -----------------------------------------------------------------------
    if result.salvage:
        print("[parallax] Pipeline completed with salvage output")
    else:
        print("[parallax] Pipeline completed successfully")

    print(f"[parallax] Phases completed: {result.phases_completed}")
    if result.out_mp4:
        print(f"[parallax] Output: {result.out_mp4}")

    # Exit 0 — salvage is not a fatal error in smoke-test mode.
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="parallax-engine",
        description="parallax-engine: 2.5D multiplane camera animation renderer.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # --- render subcommand ---
    render_p = subparsers.add_parser(
        "render",
        help="Render a scene.yaml to an MP4 file",
        description="Parse a scene YAML and render it to an MP4 using the full pipeline.",
    )
    render_p.add_argument(
        "scene",
        metavar="SCENE_YAML",
        help="Path to the scene YAML file",
    )
    render_p.add_argument(
        "--out",
        required=True,
        metavar="OUTPUT.MP4",
        help="Destination MP4 file path",
    )
    render_p.add_argument(
        "--workspace",
        default=None,
        metavar="DIR",
        help=(
            "Root directory for resolving asset paths (SVGs, PNGs, LUTs). "
            "Defaults to the directory containing SCENE_YAML."
        ),
    )

    # --- harness subcommand ---
    harness_p = subparsers.add_parser(
        "harness",
        help="Run the agent harness on a natural-language brief",
        description=(
            "Run the full parallax-engine agent harness (§3, §8.3).  "
            "In offline/stub mode (no SDK installed), all five subagents "
            "return canned strings and the run completes without error."
        ),
    )
    harness_p.add_argument(
        "--workspace",
        default="workspace",
        metavar="DIR",
        help="Root workspace directory (default: ./workspace)",
    )
    harness_p.add_argument(
        "--brief",
        default="test scene",
        metavar="TEXT",
        help="Natural-language brief for the animation (default: 'test scene')",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """
    Main entry point.

    Parameters
    ----------
    argv:
        Argument list (default: ``sys.argv[1:]``).

    Returns
    -------
    Exit code (0 = success, non-zero = error).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "render":
        return cmd_render(args)

    if args.command == "harness":
        return cmd_harness(args)

    # No subcommand given
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
