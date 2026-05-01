"""
parallax_engine/cli.py — Command-line interface (§8 Phase 1).

Subcommands
-----------
render <scene.yaml> --out <out.mp4> [--workspace <dir>]
    Parse a scene YAML, render it with the full pipeline, encode to MP4.

Usage
-----
    python -m parallax_engine render path/to/scene.yaml --out /tmp/out.mp4

    # With explicit workspace (default: directory of scene file)
    python -m parallax_engine render scene.yaml --out out.mp4 \\
        --workspace ./workspace

SPEC anchors: §1.2, §4.3, §8.1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


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

    # No subcommand given
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
