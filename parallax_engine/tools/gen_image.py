"""
parallax_engine/tools/gen_image.py -- MCP tool wrapper for image generation.

Provides gen_image, the tool that generates an SVG layer asset from a
natural-language text prompt.  The asset-generator subagent calls this
through the harness as mcp__parallax_render__gen_image.

Implementation
--------------
Backend priority:

1. Anthropic API (optional): if the anthropic SDK is installed and
   ANTHROPIC_API_KEY (or CLAUDE_CODE_OAUTH_TOKEN) is set, sends a prompt to
   claude-haiku-4-5 asking it to write a complete SVG.  Produces vector art
   that scales to any resolution.

2. Placeholder fallback: if no backend is available, writes a minimal valid
   SVG rectangle whose fill color is deterministically derived from the prompt
   hash.  The placeholder embeds the prompt text as a comment so the pipeline
   can continue without a real image generation service.

Both backends write an .svg file.  PNG output is reserved for future
raster-model integrations.

No new pip dependencies are required -- the Anthropic SDK import is guarded
with a try/except and the fallback uses only Python stdlib.

SPEC anchors: section 3.3, section 5.1
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def gen_image(
    prompt: str,
    output_path: str | Path,
    width: int = 1920,
    height: int = 1080,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Generate an SVG layer asset from a natural-language text prompt.

    Parameters
    ----------
    prompt:
        Natural-language description of the layer content.
    output_path:
        Destination path for the generated SVG file.  Parent directories
        are created automatically.  Should end with .svg.
    width, height:
        SVG viewport dimensions in pixels.  Default: 1920x1080.
    seed:
        Reserved for future use by stochastic backends.

    Returns
    -------
    dict with keys:
        ok : bool
        path : str  (absolute path to written file)
        width, height : int
        format : str  ("svg")
        backend : str  ("anthropic" or "placeholder")
        message : str
    """
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    result = _try_anthropic_backend(prompt, out, width, height)
    if result["ok"]:
        return result

    return _placeholder_svg(prompt, out, width, height)


def _try_anthropic_backend(
    prompt: str,
    out: Path,
    width: int,
    height: int,
) -> dict[str, Any]:
    """
    Try to generate an SVG via the Anthropic API.

    Returns ok=False (without raising) when:
    - the anthropic package is not installed
    - no API key is available
    - the API call fails for any reason
    """
    try:
        import anthropic  # optional; not in pyproject.toml
    except ImportError:
        return {"ok": False, "message": "anthropic SDK not installed"}

    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
        "CLAUDE_CODE_OAUTH_TOKEN"
    )
    if not api_key:
        return {"ok": False, "message": "ANTHROPIC_API_KEY not set; skipping Anthropic backend"}

    try:
        client = anthropic.Anthropic(api_key=api_key)
        sys_req = (
            "Generate a complete, valid SVG for a 2.5D parallax animation layer.\n"
            "The layer depicts: " + prompt + "\n\n"
            "Requirements:\n"
            "- viewBox: 0 0 " + str(width) + " " + str(height) + "\n"
            "- Use solid fills and simple geometric shapes; no external images\n"
            "- Keep complexity low (fewer than 20 shape elements) for fast rasterisation\n"
            "- Output ONLY the SVG XML starting with <svg and ending with </svg>\n"
            "- No markdown code fences, no explanation -- raw SVG only"
        )
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": sys_req}],
        )
        svg_text = response.content[0].text.strip()

        if not (svg_text.startswith("<svg") or svg_text.startswith("<?xml")):
            return {
                "ok": False,
                "message": "Anthropic returned non-SVG content",
            }

        out.write_text(svg_text, encoding="utf-8")
        n_chars = len(svg_text)
        logger.info("gen_image: Anthropic backend wrote %s (%d chars)", out, n_chars)

        return {
            "ok": True,
            "path": str(out),
            "width": width,
            "height": height,
            "format": "svg",
            "backend": "anthropic",
            "message": "Generated " + str(n_chars) + "-char SVG via Anthropic claude-haiku-4-5",
        }

    except Exception as exc:
        logger.warning("gen_image: Anthropic backend failed: %s", exc)
        return {"ok": False, "message": "Anthropic API error: " + str(exc)}


def _placeholder_svg(
    prompt: str,
    out: Path,
    width: int,
    height: int,
) -> dict[str, Any]:
    """
    Generate a minimal placeholder SVG with a deterministic fill color.

    Color is derived from an MD5 hash of the prompt so that different layers
    in the same scene get distinct but reproducible colors.
    """
    digest = hashlib.md5(prompt.encode("utf-8")).hexdigest()
    raw_r = int(digest[0:2], 16)
    raw_g = int(digest[2:4], 16)
    raw_b = int(digest[4:6], 16)
    r = min(255, raw_r + 60)
    g = min(255, raw_g + 60)
    b = min(255, raw_b + 60)
    fill = "#{:02x}{:02x}{:02x}".format(r, g, b)

    safe = (
        prompt[:120]
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    label = safe[:60]
    cx = width // 2
    cy = height // 2

    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg"',
        ' viewBox="0 0 {w} {h}"'.format(w=width, h=height),
        ' width="{w}" height="{h}">'.format(w=width, h=height),
        "  <!-- gen_image placeholder: " + safe + " -->",
        '  <rect width="{w}" height="{h}" fill="{fill}" opacity="0.85"/>'.format(
            w=width, h=height, fill=fill
        ),
        "  <text",
        '   x="{cx}" y="{cy}"'.format(cx=cx, cy=cy),
        '   font-family="sans-serif" font-size="48"',
        '   fill="white" text-anchor="middle" dominant-baseline="middle"',
        "   >" + label + "</text>",
        "</svg>",
        "",
    ]
    svg = "\n".join(lines)

    out.write_text(svg, encoding="utf-8")
    logger.info("gen_image: placeholder backend wrote %s", out)

    return {
        "ok": True,
        "path": str(out),
        "width": width,
        "height": height,
        "format": "svg",
        "backend": "placeholder",
        "message": "Generated placeholder SVG (no AI backend available). Prompt: " + repr(prompt[:80]),
    }
