"""parallax_engine.asset.generator — casting-aware asset generation.

Implements SPEC.md §11.7 kind dispatch and §11.14 step 7.

Kind dispatch contract
----------------------
canonical
    No-op.  The canonical SVG already exists (produced in a prior scene by a
    ``produce_canonical`` run).  Returns the path from the manifest entry
    without touching the filesystem.

produce_canonical
    Reads *only* the casting entry's ``canonical_description``,
    ``palette_locked``, and the project's ``shape_language``.  Does **not**
    read the scene's local context (§11.7.1 intentional constraint).  Calls
    the SVG generation backend and writes to ``assets/canon/<id>.svg``.

local
    Current (pre-director) behaviour.  Generates from the manifest entry's
    ``purpose`` field using the same SVG backend as before.

variant
    Applies a transformation spec to the canonical SVG (§11.7.2).  The
    canonical SVG is never modified; a per-scene copy is written to
    ``assets/variants/<id>_scene<N>.svg``.  Transformation supported:
      - ``scale``          : float (0 < s ≤ 2.0; default 1.0)
      - ``lighting_tint``  : str in TINT_FILTERS (default "none")
      - ``position_hint``  : str in POSITION_HINTS (default "center")

Determinism
-----------
- SVG placeholder fill colors are derived from MD5(description).
- No ``random`` module is used; no PRNG is seeded here.
- Output paths are purely deterministic from inputs.

SPEC anchors: §11.7, §11.7.1, §11.7.2, §11.8.2, §11.14 step 7
"""
from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Literal, TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Retry cap per asset (§11.8.1).  Enforced in Python by the project manager.
MAX_ASSET_RETRIES_PER_ASSET: int = 3

AssetKind = Literal["canonical", "produce_canonical", "local", "variant"]

# Lighting tint → SVG feColorMatrix values (hue-shift style).
# Format: "R 0 0 0 Roff  0 G 0 0 Goff  0 0 B 0 Boff  0 0 0 A 0"
TINT_FILTERS: dict[str, str] = {
    "none": "1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 1 0",
    "cool_shadow": "0.75 0 0.05 0 0  0 0.82 0.1 0 0  0.05 0.1 1.05 0 0  0 0 0 0.9 0",
    "warm_glow": "1.1 0.05 0 0 0.05  0.05 1.0 0 0 0.02  0 0 0.8 0 0  0 0 0 1 0",
    "golden_hour": "1.1 0.1 0 0 0.04  0.02 1.0 0.02 0 0.01  0 0 0.7 0 0  0 0 0 1 0",
    "blue_night": "0.6 0 0.1 0 0  0 0.7 0.2 0 0  0.1 0.2 1.2 0 0  0 0 0 0.85 0",
    "overcast": "0.85 0 0 0 0.05  0 0.85 0 0 0.05  0 0 0.85 0 0.1  0 0 0 0.9 0",
}

# Position hint → (tx_fraction, ty_fraction) of canvas width/height.
POSITION_HINTS: dict[str, tuple[float, float]] = {
    "center": (0.5, 0.5),
    "upper_left": (0.15, 0.15),
    "upper_right": (0.75, 0.15),
    "upper_center": (0.5, 0.1),
    "lower_left": (0.15, 0.75),
    "lower_right": (0.75, 0.75),
    "lower_center": (0.5, 0.8),
    "lower_third_left": (0.15, 0.67),
    "lower_third_center": (0.5, 0.67),
    "lower_third_right": (0.65, 0.67),
    "upper_third_left": (0.15, 0.33),
    "upper_third_center": (0.5, 0.33),
    "upper_third_right": (0.65, 0.33),
    "mid_left": (0.1, 0.5),
    "mid_right": (0.85, 0.5),
}


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


class GenerateResult(TypedDict):
    """Return type for :func:`generate`."""

    ok: bool
    kind: str
    path: str | None
    width: int
    height: int
    format: str
    backend: str
    message: str


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AssetGeneratorError(RuntimeError):
    """Raised when asset generation fails unrecoverably."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate(
    manifest_entry: dict[str, Any],
    workspace_dir: str | Path,
    *,
    casting_data: dict[str, Any] | None = None,
    shape_language: str = "",
    width: int = 1920,
    height: int = 1080,
    seed: int = 42,
) -> GenerateResult:
    """Generate or retrieve an SVG asset according to the manifest entry's kind.

    Parameters
    ----------
    manifest_entry:
        A dict with at minimum a ``kind`` key.  See module docstring for
        per-kind required fields.
    workspace_dir:
        Project workspace root.  Relative paths in ``canonical_svg_path`` are
        resolved against this.
    casting_data:
        Optional dict mapping cast id → casting entry dict (fields from
        CastingEntry: canonical_description, palette_locked, …).  Required
        for ``produce_canonical`` and ``variant`` kinds.
    shape_language:
        Project-level shape language constraint (§11.3.2).  Passed to the
        SVG backend for ``produce_canonical`` and ``local`` kinds.
    width, height:
        Canvas dimensions.
    seed:
        Determinism seed (currently used only by placeholder backend).

    Returns
    -------
    GenerateResult
        ok=True on success; ok=False if generation failed (caller should retry
        up to MAX_ASSET_RETRIES_PER_ASSET times).
    """
    workspace_dir = Path(workspace_dir)
    kind: str = manifest_entry.get("kind", "local")

    if kind == "canonical":
        return _handle_canonical(manifest_entry, workspace_dir)
    elif kind == "produce_canonical":
        return _handle_produce_canonical(
            manifest_entry, workspace_dir, casting_data or {}, shape_language, width, height
        )
    elif kind == "local":
        return _handle_local(manifest_entry, workspace_dir, shape_language, width, height)
    elif kind == "variant":
        return _handle_variant(
            manifest_entry, workspace_dir, casting_data or {}, width, height
        )
    else:
        return GenerateResult(
            ok=False,
            kind=kind,
            path=None,
            width=width,
            height=height,
            format="svg",
            backend="none",
            message=f"Unknown kind {kind!r}; expected one of canonical/produce_canonical/local/variant",
        )


# ---------------------------------------------------------------------------
# Kind handlers
# ---------------------------------------------------------------------------


def _handle_canonical(
    entry: dict[str, Any], workspace_dir: Path
) -> GenerateResult:
    """No-op: return the path that already exists (§11.7.1 step 5)."""
    path_str: str | None = entry.get("canonical_svg_path") or entry.get("path")
    if path_str is None:
        return GenerateResult(
            ok=False,
            kind="canonical",
            path=None,
            width=0,
            height=0,
            format="svg",
            backend="none",
            message="canonical entry has no canonical_svg_path or path field",
        )
    # Resolve relative paths against workspace_dir
    resolved = (workspace_dir / path_str).resolve() if not Path(path_str).is_absolute() else Path(path_str)
    logger.debug("canonical no-op for %s", path_str)
    return GenerateResult(
        ok=True,
        kind="canonical",
        path=str(resolved),
        width=0,
        height=0,
        format="svg",
        backend="no-op",
        message=f"canonical no-op; path={path_str}",
    )


def _handle_produce_canonical(
    entry: dict[str, Any],
    workspace_dir: Path,
    casting_data: dict[str, Any],
    shape_language: str,
    width: int,
    height: int,
) -> GenerateResult:
    """Produce the canonical SVG from the casting bible entry only (§11.7.1 step 3).

    Reads only: canonical_description, palette_locked, shape_language.
    Does NOT read scene local context (intentional per spec).
    """
    asset_id: str = entry.get("id", "unknown")
    cast_entry: dict[str, Any] = casting_data.get(asset_id, {})

    canonical_description: str = cast_entry.get("canonical_description", "")
    if not canonical_description:
        # Fallback: use entry-level canonical_description if casting_data missing
        canonical_description = entry.get("canonical_description", "")

    palette_locked: dict[str, str] = cast_entry.get("palette_locked", {})

    if not canonical_description:
        return GenerateResult(
            ok=False,
            kind="produce_canonical",
            path=None,
            width=width,
            height=height,
            format="svg",
            backend="none",
            message=f"produce_canonical: no canonical_description for id={asset_id!r}",
        )

    # Build prompt: only canonical_description + shape_language + palette_locked
    prompt_parts = [canonical_description]
    if shape_language:
        prompt_parts.append(f"Shape language: {shape_language}.")
    if palette_locked:
        colors = ", ".join(f"{k}={v}" for k, v in sorted(palette_locked.items()))
        prompt_parts.append(f"Palette (locked): {colors}.")
    prompt = " ".join(prompt_parts)

    # Output path: assets/canon/<id>.svg
    out_path = workspace_dir / "assets" / "canon" / f"{asset_id}.svg"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    result = _call_svg_backend(prompt, out_path, width, height)
    result["kind"] = "produce_canonical"
    return result


def _handle_local(
    entry: dict[str, Any],
    workspace_dir: Path,
    shape_language: str,
    width: int,
    height: int,
) -> GenerateResult:
    """Generate from the manifest entry's purpose/description (pre-director behavior)."""
    asset_id: str = entry.get("id", "unknown")
    prompt: str = entry.get("purpose", "") or entry.get("description", "") or asset_id
    if shape_language:
        prompt = f"{prompt} Shape language: {shape_language}."

    out_path_str: str | None = entry.get("path")
    if out_path_str:
        out_path = (workspace_dir / out_path_str) if not Path(out_path_str).is_absolute() else Path(out_path_str)
    else:
        out_path = workspace_dir / "assets" / "local" / f"{asset_id}.svg"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    result = _call_svg_backend(prompt, out_path, width, height)
    result["kind"] = "local"
    return result


def _handle_variant(
    entry: dict[str, Any],
    workspace_dir: Path,
    casting_data: dict[str, Any],
    width: int,
    height: int,
) -> GenerateResult:
    """Apply a transformation spec to the canonical SVG (§11.7.2).

    The canonical SVG is never modified.  A per-scene variant is written to
    ``assets/variants/<variant_of>_scene<scene_index>.svg``.
    """
    variant_of: str = entry.get("variant_of", "")
    scene_index: int = entry.get("scene_index", 0)
    canonical_path_str: str | None = entry.get("canonical_svg_path")

    if not variant_of:
        return GenerateResult(
            ok=False,
            kind="variant",
            path=None,
            width=width,
            height=height,
            format="svg",
            backend="none",
            message="variant entry missing variant_of field",
        )

    # Resolve canonical SVG path
    if canonical_path_str:
        canon_path = (
            workspace_dir / canonical_path_str
            if not Path(canonical_path_str).is_absolute()
            else Path(canonical_path_str)
        )
    else:
        canon_path = workspace_dir / "assets" / "canon" / f"{variant_of}.svg"

    if not canon_path.exists():
        return GenerateResult(
            ok=False,
            kind="variant",
            path=None,
            width=width,
            height=height,
            format="svg",
            backend="none",
            message=f"canonical SVG not found: {canon_path}",
        )

    # Read transformation spec
    transformation: dict[str, Any] = entry.get("transformation", {})
    scale: float = float(transformation.get("scale", 1.0))
    lighting_tint: str = transformation.get("lighting_tint", "none")
    position_hint: str = transformation.get("position_hint", "center")

    # Palette locked from casting data (for QA checks later)
    cast_entry = casting_data.get(variant_of, {})
    palette_locked: dict[str, str] = cast_entry.get("palette_locked", entry.get("palette_locked", {}))

    # Build variant SVG
    try:
        canonical_svg = canon_path.read_text(encoding="utf-8")
        variant_svg = _apply_variant_transform(
            canonical_svg, scale, lighting_tint, position_hint, width, height
        )
    except Exception as exc:
        return GenerateResult(
            ok=False,
            kind="variant",
            path=None,
            width=width,
            height=height,
            format="svg",
            backend="none",
            message=f"variant transform failed: {exc}",
        )

    # Write variant
    out_dir = workspace_dir / "assets" / "variants"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{variant_of}_scene{scene_index}.svg"
    out_path.write_text(variant_svg, encoding="utf-8")

    logger.debug(
        "variant written: %s (scale=%.2f tint=%s pos=%s)",
        out_path,
        scale,
        lighting_tint,
        position_hint,
    )
    return GenerateResult(
        ok=True,
        kind="variant",
        path=str(out_path),
        width=width,
        height=height,
        format="svg",
        backend="svg-transform",
        message=(
            f"variant of {variant_of!r} for scene {scene_index}: "
            f"scale={scale} tint={lighting_tint!r} pos={position_hint!r}"
        ),
    )


# ---------------------------------------------------------------------------
# SVG transformation helpers
# ---------------------------------------------------------------------------


def _apply_variant_transform(
    canonical_svg: str,
    scale: float,
    lighting_tint: str,
    position_hint: str,
    width: int,
    height: int,
) -> str:
    """Wrap canonical SVG content in a transformation group.

    Strategy:
    1. Strip the outer ``<svg ...>`` wrapper.
    2. Wrap the inner content in ``<g transform="...">`` with scale + translate.
    3. Optionally add a ``<filter>`` element for lighting tint.
    4. Re-wrap in a new ``<svg ...>`` with the same viewport.

    This preserves the canonical's path topology (required by QA hash check).
    """
    # Clamp scale to a reasonable range
    scale = max(0.01, min(4.0, scale))

    # Compute translate from position hint (anchor is the transform origin)
    frac = POSITION_HINTS.get(position_hint, POSITION_HINTS["center"])
    tx = frac[0] * width
    ty = frac[1] * height

    # Extract inner SVG content (everything inside the <svg> element)
    inner = _extract_svg_inner(canonical_svg)

    # Build filter block for tint
    filter_block = ""
    filter_ref = ""
    tint_values = TINT_FILTERS.get(lighting_tint, TINT_FILTERS["none"])
    if lighting_tint and lighting_tint != "none":
        filter_block = (
            f'\n  <defs>\n'
            f'    <filter id="parallax_tint" color-interpolation-filters="sRGB">\n'
            f'      <feColorMatrix type="matrix" values="{tint_values}"/>\n'
            f'    </filter>\n'
            f'  </defs>'
        )
        filter_ref = ' filter="url(#parallax_tint)"'

    # Build transform: first scale (anchored at origin), then translate to position
    transform = f"translate({tx:.2f},{ty:.2f}) scale({scale:.4f})"

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg"',
        f'     viewBox="0 0 {width} {height}"',
        f'     width="{width}" height="{height}">',
        f'  <!-- parallax-engine variant: scale={scale} tint={lighting_tint} pos={position_hint} -->',
    ]
    if filter_block:
        lines.append(filter_block)
    lines.append(f'  <g transform="{transform}"{filter_ref}>')
    lines.append(inner)
    lines.append("  </g>")
    lines.append("</svg>")
    return "\n".join(lines)


def _extract_svg_inner(svg_text: str) -> str:
    """Extract the content between <svg ...> and </svg>.

    Falls back to the full text if parsing fails (e.g., the SVG has an
    unusual structure), so the variant is always produced.
    """
    # Try regex-based extraction first (more robust to namespaces)
    match = re.search(r"<svg[^>]*>(.*)</svg>", svg_text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # Fallback: use the full text as inner content
    return svg_text.strip()


# ---------------------------------------------------------------------------
# SVG generation backend
# ---------------------------------------------------------------------------


def _call_svg_backend(
    prompt: str,
    out_path: Path,
    width: int,
    height: int,
) -> GenerateResult:
    """Call the SVG generation backend (Anthropic if available, else placeholder)."""
    # Try Anthropic backend first (optional dependency)
    from parallax_engine.tools.gen_image import gen_image

    raw = gen_image(prompt, out_path, width=width, height=height)
    return GenerateResult(
        ok=raw.get("ok", False),
        kind="",  # filled in by caller
        path=raw.get("path"),
        width=raw.get("width", width),
        height=raw.get("height", height),
        format=raw.get("format", "svg"),
        backend=raw.get("backend", "unknown"),
        message=raw.get("message", ""),
    )
