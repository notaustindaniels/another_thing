"""
parallax_engine/render.py — Per-frame rendering pipeline (§2.5, §2.6, §2.7).

Public API
----------
``render_scene(scene, workspace, out_path)``
    Full render: precompute camera track + SVG cache, loop per frame,
    encode to MP4 via FFmpeg.

Pipeline (§2.5, in pinned order)
---------------------------------
Per frame:
  1. Camera pose lookup from precomputed track.
  2. For each stack: composite layers back-to-front into ``buf[stack]``.
       - Sort layers by z_cam descending (back to front).
       - For each layer: project, near-cull, resize-from-cache,
         per-layer post (DOF blur + depth fade), alpha-over.
  3. Mask compositing: F = buf[dest]*M + buf[src]*(1-M), L2 rule.
  4. Final frame = last remaining stack buffer.
  5. Global post: vignette → grain → light_leaks → fisheye → color_grade.
  6. Convert to uint8 RGBA → write to FFmpeg stdin.

SVG Rasterization (§5.3 fallback)
----------------------------------
``skia-python`` is preferred (BSD, §5.1), but when not installed the renderer
falls back to ``rsvg-convert`` (external CLI, no linking obligation).
Per-frame work is ``cv2.resize`` (INTER_AREA for shrink, INTER_LANCZOS4 for grow).

SPEC anchors: §2.5, §2.6, §2.7, §2.8, §5.3, §7, §9.1, §9.2, §9.8
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np
from PIL import Image

from parallax_engine.encode import Encoder
from parallax_engine.masks import alpha_over, composite_with_mask
from parallax_engine.projection import compute_near_cull, project_world_points
from parallax_engine.seeds import GRAIN, spawn_channel

if TYPE_CHECKING:
    from parallax_engine.scene import LayerSpec, MaskSpec, Scene


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Bump this when the rasterization pipeline changes to invalidate caches.
CACHE_VERSION: int = 1

#: Default fog depth range (scene units) for depth-fade calculation (§2.6).
_FOG_NEAR: float = 3000.0
_FOG_FAR: float = 12000.0

#: Minimum sprite dimension; prevents degenerate cv2.resize calls.
_MIN_SPRITE_PX: int = 1


# ---------------------------------------------------------------------------
# SVG raster cache
# ---------------------------------------------------------------------------

class _SvgCache:
    """
    In-memory SVG raster cache.

    Keys are ``(sha256_hex, width, height, CACHE_VERSION)`` per §P1.M06
    acceptance criteria.  Values are premultiplied ``float32`` RGBA arrays
    of shape ``(height, width, 4)``.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, int, int, int], np.ndarray] = {}

    def key(self, sha256: str, w: int, h: int) -> tuple[str, int, int, int]:
        return (sha256, w, h, CACHE_VERSION)

    def get(self, sha256: str, w: int, h: int) -> np.ndarray | None:
        return self._store.get(self.key(sha256, w, h))

    def put(self, sha256: str, w: int, h: int, raster: np.ndarray) -> None:
        self._store[self.key(sha256, w, h)] = raster

    def __len__(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# SVG rasterization (rsvg-convert fallback per §5.3)
# ---------------------------------------------------------------------------

def _rasterize_svg_bytes(svg_bytes: bytes, width: int, height: int) -> np.ndarray:
    """
    Rasterize raw SVG bytes to a premultiplied float32 RGBA array.

    Attempts ``skia-python`` first; falls back to ``rsvg-convert`` CLI (§5.3).

    Returns
    -------
    ``(height, width, 4)`` float32 premultiplied RGBA in ``[0, 1]``.
    """
    # Try skia-python (preferred, BSD license)
    try:
        import skia  # type: ignore[import]
        stream = skia.DynamicMemoryWStream()
        svg = skia.SVGDOM.MakeFromData(skia.Data.MakeWithCopy(svg_bytes))
        surface = skia.Surface(width, height)
        canvas = surface.getCanvas()
        canvas.clear(skia.ColorTRANSPARENT)
        svg.render(canvas)
        image = surface.makeImageSnapshot()
        arr = np.array(image, dtype=np.uint8).reshape(height, width, 4)
        arr_f = arr.astype(np.float32) / 255.0
        # skia returns premultiplied BGRA; swap channels
        arr_f = arr_f[:, :, [2, 1, 0, 3]]
        return arr_f
    except Exception:
        pass  # fall through to rsvg-convert

    # Fall back to rsvg-convert CLI (§5.3)
    try:
        result = subprocess.run(
            [
                "rsvg-convert",
                "--width", str(width),
                "--height", str(height),
                "--format", "png",
                "-",
            ],
            input=svg_bytes,
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout:
            img = Image.open(io.BytesIO(result.stdout)).convert("RGBA")
            img = img.resize((width, height), Image.LANCZOS)
            arr_u8 = np.array(img, dtype=np.uint8)
            arr_f = arr_u8.astype(np.float32) / 255.0
            # PIL gives straight alpha; convert to premultiplied
            a = arr_f[:, :, 3:4]
            arr_f[:, :, :3] *= a
            return arr_f
    except Exception as exc:
        warnings.warn(f"rsvg-convert failed: {exc}; using placeholder", stacklevel=3)

    # Last resort: magenta transparent placeholder
    placeholder = np.zeros((height, width, 4), dtype=np.float32)
    placeholder[:, :, 0] = 1.0  # R (premultiplied, alpha=1 → rgb visible)
    placeholder[:, :, 2] = 1.0  # B
    placeholder[:, :, 3] = 1.0  # A
    return placeholder


def _rasterize_svg_file(path: Path, width: int, height: int) -> np.ndarray:
    """Rasterize an SVG file; falls back to placeholder if file missing."""
    if path.exists():
        return _rasterize_svg_bytes(path.read_bytes(), width, height)
    warnings.warn(f"SVG not found: {path}; using placeholder", stacklevel=2)
    placeholder = np.zeros((height, width, 4), dtype=np.float32)
    placeholder[:, :, 3] = 0.0   # fully transparent placeholder
    return placeholder


def _svg_sha256(path: Path) -> str:
    """SHA-256 of SVG bytes; empty string if file missing."""
    if path.exists():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------------------
# Resize helper
# ---------------------------------------------------------------------------

def _resize_sprite(
    src: np.ndarray,
    target_w: int,
    target_h: int,
) -> np.ndarray:
    """
    Resize a premultiplied RGBA sprite using OpenCV.

    Per §2.5: INTER_AREA for shrink, INTER_LANCZOS4 for grow.
    Handles degenerate sizes gracefully.
    """
    target_w = max(_MIN_SPRITE_PX, int(target_w))
    target_h = max(_MIN_SPRITE_PX, int(target_h))
    src_h, src_w = src.shape[:2]
    if src_w == target_w and src_h == target_h:
        return src
    area = target_w * target_h
    src_area = src_w * src_h
    interp = cv2.INTER_AREA if area <= src_area else cv2.INTER_LANCZOS4
    resized = cv2.resize(src, (target_w, target_h), interpolation=interp)
    if resized.ndim == 2:
        resized = resized[:, :, np.newaxis]
    return resized.astype(np.float32)


# ---------------------------------------------------------------------------
# SVG cache pre-build
# ---------------------------------------------------------------------------

def _build_svg_cache(
    scene: "Scene",
    cam_track: np.ndarray,
    workspace: Path,
) -> tuple["_SvgCache", dict[tuple[str, str], tuple[str, int, int]]]:
    """
    Pre-rasterize all layer SVGs at their maximum required size.

    For each layer, walk the entire camera track to find the frame where the
    layer is projected largest (maximum scale factor ``s``).  Rasterize the
    SVG at ``(plate_w * s_max, plate_h * s_max)`` and store in the cache.

    Per-frame work thereafter is a single ``cv2.resize`` call.

    Returns
    -------
    cache : _SvgCache
    layer_cache_keys : dict mapping (stack_name, layer_id) → (sha256, max_w, max_h)
    """
    cache = _SvgCache()
    layer_cache_keys: dict[tuple[str, str], tuple[str, int, int]] = {}

    for stack_name, stack in scene.stacks.items():
        for layer in stack.layers:
            svg_path = workspace / layer.src
            sha = _svg_sha256(svg_path)
            pw, ph = float(layer.plate_size[0]), float(layer.plate_size[1])

            # Walk the camera track to find maximum projected scale for this layer
            s_max = 0.0
            for pose in cam_track:
                cam = {
                    "cx": pose[0], "cy": pose[1], "cz": pose[2],
                    "yaw": pose[3], "pitch": pose[4], "roll": pose[5],
                }
                xyz = np.array(layer.scene_xyz, dtype=np.float64)
                s_arr, _screen, z_cam_arr = project_world_points(
                    xyz[None, :], cam,
                    float(scene.meta.perspective_px),
                    (float(scene.meta.origin[0]), float(scene.meta.origin[1])),
                )
                z_cam = float(z_cam_arr[0])
                cull = compute_near_cull(z_cam, float(scene.meta.perspective_px))
                if cull < 0.001:
                    continue
                s = float(s_arr[0])
                if s > 0:
                    s_max = max(s_max, s)

            if s_max < 1e-6:
                s_max = 1.0   # fallback: render at native 1:1

            max_w = max(_MIN_SPRITE_PX, int(pw * s_max))
            max_h = max(_MIN_SPRITE_PX, int(ph * s_max))

            if cache.get(sha, max_w, max_h) is None:
                raster = _rasterize_svg_file(svg_path, max_w, max_h)
                cache.put(sha, max_w, max_h, raster)

            layer_cache_keys[(stack_name, layer.id)] = (sha, max_w, max_h)

    return cache, layer_cache_keys


# ---------------------------------------------------------------------------
# Per-layer post-processing (§2.6)
# ---------------------------------------------------------------------------

def _apply_layer_post(
    sprite: np.ndarray,
    dof_blur_px: float,
    depth_fade: float,
    z_cam: float,
    fog_rgb: tuple[float, float, float],
) -> np.ndarray:
    """
    Apply per-layer post-processing in place (§2.6).

    Steps (in order):
    1. DOF blur: Gaussian σ = dof_blur_px (premultiplied RGBA).
    2. Depth fade: lerp RGB toward fog_rgb based on z_cam and depth_fade.

    All operations work on premultiplied float32 RGBA.
    """
    if dof_blur_px > 0.0:
        sigma = float(dof_blur_px)
        # Apply blur to RGB and A channels separately (premultiplied is correct)
        r = cv2.GaussianBlur(sprite[:, :, 0], (0, 0), sigma)
        g = cv2.GaussianBlur(sprite[:, :, 1], (0, 0), sigma)
        b = cv2.GaussianBlur(sprite[:, :, 2], (0, 0), sigma)
        a = cv2.GaussianBlur(sprite[:, :, 3], (0, 0), sigma)
        sprite = np.stack([r, g, b, a], axis=-1)

    if depth_fade > 0.0:
        # fade = clip((|z| - fog_near) / (fog_far - fog_near), 0, 1) * depth_fade
        abs_z = abs(z_cam)
        raw_fade = (abs_z - _FOG_NEAR) / max(_FOG_FAR - _FOG_NEAR, 1e-6)
        fade = float(np.clip(raw_fade, 0.0, 1.0)) * depth_fade
        if fade > 0.0:
            # Lerp RGB channels toward fog_rgb (in premultiplied space).
            # Since fog is opaque (alpha=1) and sprite may be transparent,
            # we scale fog by sprite.alpha to stay in premultiplied domain.
            a_ch = sprite[:, :, 3:4]
            fog = np.array(fog_rgb, dtype=np.float32) * a_ch  # premult fog
            sprite = sprite.copy()
            sprite[:, :, :3] = sprite[:, :, :3] * (1.0 - fade) + fog * fade

    return sprite


# ---------------------------------------------------------------------------
# Build one stack's frame buffer (§2.5 step 2)
# ---------------------------------------------------------------------------

def _build_stack_buffer(
    stack_name: str,
    scene: "Scene",
    cam: dict,
    cache: "_SvgCache",
    layer_cache_keys: dict[tuple[str, str], tuple[str, int, int]],
    workspace: Path,
    H: int,
    W: int,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    Composite all layers of a stack back-to-front (§2.5 step 2).

    Returns
    -------
    buf : (H, W, 4) float32 premultiplied RGBA stack composite.
    layer_sprites : dict mapping "stack.layer_id" → resized sprite (H_s, W_s, 4).
        Used by the L2 in-front-of-mask rule (§2.4).
    """
    stack = scene.stacks[stack_name]
    bg_rgb = _hex_to_rgb01(scene.meta.bg_color)
    fog_rgb = bg_rgb

    # Start with transparent buffer
    buf = np.zeros((H, W, 4), dtype=np.float32)

    # Project all layers to get z_cam; sort back-to-front (largest |z_cam| first)
    layer_data: list[tuple[float, float, float, float, "LayerSpec"]] = []
    for layer in stack.layers:
        xyz = np.array(layer.scene_xyz, dtype=np.float64)
        s_arr, screen_xy, z_cam_arr = project_world_points(
            xyz[None, :], cam,
            float(scene.meta.perspective_px),
            (float(scene.meta.origin[0]), float(scene.meta.origin[1])),
        )
        s_val = float(s_arr[0])
        sx = float(screen_xy[0, 0])
        sy = float(screen_xy[0, 1])
        z_cam = float(z_cam_arr[0])
        layer_data.append((z_cam, s_val, sx, sy, layer))

    # Sort back-to-front: z_cam descending (more negative = farther from camera)
    layer_data.sort(key=lambda t: t[0])   # ascending z_cam = back first

    layer_sprites: dict[str, np.ndarray] = {}

    for z_cam, s_val, sx, sy, layer in layer_data:
        # Near-cull check
        cull = compute_near_cull(z_cam, float(scene.meta.perspective_px))
        if cull < 0.001:
            continue

        # Retrieve sprite from cache
        pw, ph = float(layer.plate_size[0]), float(layer.plate_size[1])
        cache_key = layer_cache_keys.get((stack_name, layer.id))
        if cache_key is None:
            continue
        sha, max_w, max_h = cache_key
        cached_raster = cache.get(sha, max_w, max_h)
        if cached_raster is None:
            # Cache miss (shouldn't happen after pre-build; rasterize on demand)
            cached_raster = _rasterize_svg_file(workspace / layer.src, max_w, max_h)
            cache.put(sha, max_w, max_h, cached_raster)

        # Resize to target size for this frame
        if s_val > 0:
            target_w = max(_MIN_SPRITE_PX, int(pw * s_val))
            target_h = max(_MIN_SPRITE_PX, int(ph * s_val))
        else:
            continue   # behind camera; skip

        sprite = _resize_sprite(cached_raster, target_w, target_h)

        # Per-layer post (§2.6)
        post = layer.post
        dof_blur = float(post.dof_blur_px) if post and post.dof_blur_px else 0.0
        depth_fde = float(post.depth_fade) if post and post.depth_fade else 0.0
        if dof_blur > 0.0 or depth_fde > 0.0:
            sprite = _apply_layer_post(sprite, dof_blur, depth_fde, z_cam, fog_rgb)

        # Apply near-cull opacity fade
        if cull < 1.0:
            sprite = sprite.copy()
            sprite[:, :, 3] *= cull         # alpha fade
            sprite[:, :, :3] *= cull        # premult: scale RGB too

        # Store for L2 rule
        key = f"{stack_name}.{layer.id}"
        layer_sprites[key] = sprite

        # Alpha-over onto stack buffer (§2.5)
        dx = int(sx - target_w / 2)
        dy = int(sy - target_h / 2)
        alpha_over(buf, sprite, dx, dy)

    return buf, layer_sprites


# ---------------------------------------------------------------------------
# Global post-processing (§2.7)
# ---------------------------------------------------------------------------

def _hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    """Parse "#rrggbb" → (r, g, b) in [0, 1]."""
    h = hex_color.lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return (r, g, b)


def _apply_vignette(
    frame: np.ndarray,
    strength: float,
    radius: float,
) -> np.ndarray:
    """
    Apply vignette (§2.7).

    ``rgb *= 1 - strength * smoothstep(radius, 1.0, dist_normalized)``

    dist_normalized = Euclidean distance from frame centre, normalised so
    that the midpoint of the longer edge = 1.0.
    """
    H, W = frame.shape[:2]
    # Normalized distance map: 1.0 at corners, 0.0 at centre
    ys = (np.linspace(0.0, 1.0, H, dtype=np.float32) - 0.5) * 2.0  # [-1, 1]
    xs = (np.linspace(0.0, 1.0, W, dtype=np.float32) - 0.5) * 2.0
    xg, yg = np.meshgrid(xs, ys)
    dist = np.sqrt(xg ** 2 + yg ** 2)   # max ≈ 1.414 at corners

    # Normalise so dist == 1.0 at the circle that just touches all edges
    # (diagonal of unit square is √2; scale to match CSS-style vignette)

    # smoothstep(edge0, edge1, x)
    edge0, edge1 = radius, 1.0
    t = np.clip((dist - edge0) / max(edge1 - edge0, 1e-6), 0.0, 1.0)
    smooth = t * t * (3.0 - 2.0 * t)

    mask = 1.0 - strength * smooth   # (H, W) in [0, 1]

    result = frame.copy()
    # Apply only to RGB channels; alpha stays unchanged
    # In premultiplied space, scale both rgb and alpha uniformly
    result[:, :, :3] *= mask[:, :, np.newaxis]
    return result


def _apply_grain(
    frame: np.ndarray,
    sigma: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Add Gaussian grain in luma channel (§2.7).

    Grain is added to the RGB channels only (not alpha), in luma-proportional
    amounts (equal weight across R, G, B for simplicity in v1).
    """
    H, W = frame.shape[:2]
    noise = rng.standard_normal((H, W), dtype=np.float64).astype(np.float32)
    noise *= (sigma / 255.0)   # sigma is in uint8 space
    result = frame.copy()
    # Add to RGB only; clip to [0, 1] after
    result[:, :, :3] = np.clip(result[:, :, :3] + noise[:, :, np.newaxis], 0.0, 1.0)
    return result


def _apply_light_leaks(
    frame: np.ndarray,
    sprite_path: Path,
    opacity: float,
    blend_mode: str,
) -> np.ndarray:
    """
    Alpha-over a light-leak sprite using screen blend (§2.7).

    Screen blend: ``out = 1 - (1 - dst) * (1 - src * opacity)``

    Falls back to no-op if the sprite file is missing.
    """
    H, W = frame.shape[:2]
    if not sprite_path.exists():
        return frame

    try:
        img = Image.open(str(sprite_path)).convert("RGBA")
        img = img.resize((W, H), Image.LANCZOS)
        arr_u8 = np.array(img, dtype=np.uint8)
        arr_f = arr_u8.astype(np.float32) / 255.0
        # Straight alpha → premultiplied
        a = arr_f[:, :, 3:4]
        arr_f[:, :, :3] *= a
    except Exception:
        return frame

    src_rgb = arr_f[:, :, :3] * opacity  # scaled by opacity
    dst_rgb = frame[:, :, :3]

    if blend_mode == "screen":
        out_rgb = 1.0 - (1.0 - dst_rgb) * (1.0 - src_rgb)
    else:
        # Normal blend as fallback
        src_a = arr_f[:, :, 3:4] * opacity
        out_rgb = src_rgb + dst_rgb * (1.0 - src_a)

    result = frame.copy()
    result[:, :, :3] = np.clip(out_rgb, 0.0, 1.0)
    return result


def _apply_fisheye(
    frame: np.ndarray,
    k1: float,
    k2: float = 0.0,
) -> np.ndarray:
    """
    Apply barrel / fisheye lens distortion (§2.7).

    Backward-warp: ``r_undist = r * (1 + k1·r² + k2·r⁴)``
    Resampled with ``cv2.remap(..., INTER_LANCZOS4)``.

    The pixel map is computed fresh each call.  Callers that render many
    frames with the same (k1, k2, H, W) should precompute and cache the map.
    """
    H, W = frame.shape[:2]
    cx, cy = (W - 1) / 2.0, (H - 1) / 2.0
    # Normalise by the half-diagonal so r = 1.0 at the corner
    r_max = np.sqrt(cx ** 2 + cy ** 2)

    ys = np.arange(H, dtype=np.float32)
    xs = np.arange(W, dtype=np.float32)
    xg, yg = np.meshgrid(xs, ys)   # (H, W)

    dx = (xg - cx) / r_max
    dy = (yg - cy) / r_max
    r2 = dx ** 2 + dy ** 2
    r4 = r2 ** 2
    scale = 1.0 + k1 * r2 + k2 * r4

    src_x = (dx * scale * r_max + cx).astype(np.float32)
    src_y = (dy * scale * r_max + cy).astype(np.float32)

    # cv2.remap requires each channel separately for float32 RGBA
    channels = []
    for c in range(4):
        ch = cv2.remap(
            frame[:, :, c],
            src_x, src_y,
            interpolation=cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0.0,
        )
        channels.append(ch)
    return np.stack(channels, axis=-1)


def _apply_color_grade(
    frame: np.ndarray,
    lut_path: Path,
) -> np.ndarray:
    """
    Apply a .cube 3D LUT (§2.7).

    Stub for v1: logs a warning and returns frame unchanged if the LUT
    file is missing or cannot be parsed.
    """
    if not lut_path.exists():
        warnings.warn(f"Color grade LUT not found: {lut_path}; skipping", stacklevel=2)
        return frame
    # Full .cube LUT parsing is deferred to a later milestone.
    warnings.warn(
        f"Color grade LUT parsing not yet implemented; skipping {lut_path.name}",
        stacklevel=2,
    )
    return frame


def _apply_global_post(
    frame: np.ndarray,
    scene: "Scene",
    seed: int,
    frame_idx: int,
    workspace: Path,
    grain_children: list,
) -> np.ndarray:
    """
    Apply global post-processing to one frame (§2.7).

    Pinned order (§2.5 step 5): vignette → grain → light_leaks → fisheye → color_grade.

    Parameters
    ----------
    frame:
        ``(H, W, 4)`` float32 premultiplied RGBA.
    scene:
        Parsed scene (provides ``post.global_``).
    seed:
        ``scene.meta.seed``.
    frame_idx:
        Zero-based frame index (used to select per-frame grain seed).
    workspace:
        Root for resolving asset paths.
    grain_children:
        Pre-spawned SeedSequence children for grain, one per frame.
    """
    post = scene.post
    if post is None or post.global_ is None:
        return frame

    gpost = post.global_

    # 1. Vignette
    if gpost.vignette is not None:
        v = gpost.vignette
        strength = float(v.strength) if v.strength is not None else 0.0
        radius = float(v.radius) if v.radius is not None else 0.85
        frame = _apply_vignette(frame, strength, radius)

    # 2. Grain  (seeded from SeedSequence(seed).spawn(GRAIN_CHANNEL) per frame)
    if gpost.grain is not None:
        g = gpost.grain
        sigma = float(g.sigma) if g.sigma is not None else 0.0
        if sigma > 0.0:
            grain_rng = np.random.default_rng(grain_children[frame_idx])
            frame = _apply_grain(frame, sigma, grain_rng)

    # 3. Light leaks
    if gpost.light_leaks is not None:
        ll = gpost.light_leaks
        sprite_path = workspace / str(ll.sprite) if ll.sprite else Path("")
        opacity = float(ll.opacity) if ll.opacity is not None else 1.0
        blend = str(ll.blend) if ll.blend is not None else "screen"
        frame = _apply_light_leaks(frame, sprite_path, opacity, blend)

    # 4. Fisheye lens distortion
    if gpost.fisheye is not None:
        fi = gpost.fisheye
        k1 = float(fi.k1) if fi.k1 is not None else 0.0
        k2 = float(fi.k2) if fi.k2 is not None else 0.0
        if abs(k1) > 1e-9 or abs(k2) > 1e-9:
            frame = _apply_fisheye(frame, k1, k2)

    # 5. Color grade
    if gpost.color_grade is not None:
        lut = gpost.color_grade.lut
        if lut:
            frame = _apply_color_grade(frame, workspace / str(lut))

    return frame


# ---------------------------------------------------------------------------
# Main render function (§2.5)
# ---------------------------------------------------------------------------

def render_scene(
    scene: "Scene",
    workspace: str | Path,
    out_path: str | Path,
) -> None:
    """
    Render a scene to an MP4 file.

    Parameters
    ----------
    scene:
        Parsed and validated :class:`~parallax_engine.scene.Scene`.
    workspace:
        Directory against which asset paths in the scene are resolved.
    out_path:
        Destination MP4 file path.

    Raises
    ------
    RuntimeError
        If FFmpeg encoding fails.

    Notes
    -----
    All pipeline steps execute in the pinned order defined by §2.5.
    The render is deterministic: two calls with the same (scene, workspace)
    produce byte-identical MP4 outputs when ``-threads 1`` is passed to FFmpeg.
    """
    workspace = Path(workspace)
    out_path = Path(out_path)

    W, H = int(scene.meta.resolution[0]), int(scene.meta.resolution[1])
    fps = int(scene.meta.fps)
    T = float(scene.meta.duration_s)
    n_frames = int(T * fps)
    seed = int(scene.meta.seed)

    # --- Step 0: Precompute camera track ---
    from parallax_engine.camera import drone_camera_track, kf_camera_track

    cam_track: np.ndarray
    if scene.camera.mode == "drone":
        cam_track = drone_camera_track(scene, T, fps)
    else:
        cam_track = kf_camera_track(scene, T, fps)

    # --- Step 0b: Pre-build SVG raster cache (max size per layer) ---
    cache, layer_cache_keys = _build_svg_cache(scene, cam_track, workspace)

    # --- Step 0c: Pre-spawn grain RNG children (one per frame) ---
    grain_ss = spawn_channel(seed, GRAIN)
    # Spawn n_frames children; pick child[i] for frame i
    grain_children = grain_ss.spawn(max(n_frames, 1))

    # --- Step 0d: Precompute fisheye map if needed (avoid per-frame recompute) ---
    # (fisheye is computed inside _apply_fisheye per-frame in v1; future optimisation)

    # Background colour as float32
    bg_rgb = _hex_to_rgb01(scene.meta.bg_color)

    # --- Encoding loop ---
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Encoder(out_path, W, H, fps) as enc:
        for frame_idx in range(n_frames):
            pose = cam_track[frame_idx]
            cam = {
                "cx": float(pose[0]), "cy": float(pose[1]), "cz": float(pose[2]),
                "yaw": float(pose[3]), "pitch": float(pose[4]), "roll": float(pose[5]),
            }

            # --- Step 2: Composite each stack ---
            stack_bufs: dict[str, np.ndarray] = {}
            all_layer_sprites: dict[str, dict[str, np.ndarray]] = {}
            for stack_name in scene.stacks:
                buf, lsprites = _build_stack_buffer(
                    stack_name, scene, cam, cache, layer_cache_keys, workspace, H, W
                )
                stack_bufs[stack_name] = buf
                all_layer_sprites[stack_name] = lsprites

            # --- Step 3: Apply masks ---
            for mask in (scene.masks or []):
                src = stack_bufs.get(mask.src_stack, np.zeros((H, W, 4), dtype=np.float32))
                dst = stack_bufs.get(mask.dest_stack, np.zeros((H, W, 4), dtype=np.float32))
                t = frame_idx / fps
                # Merge layer_sprites for the L2 rule
                merged_sprites: dict[str, np.ndarray] = {}
                for sn, sprites in all_layer_sprites.items():
                    for lid, sp in sprites.items():
                        merged_sprites[f"{sn}.{lid}"] = sp
                result = composite_with_mask(
                    scene, mask, src, dst,
                    cam, (H, W), t, merged_sprites,
                )
                # Replace dest stack buffer with composited result
                stack_bufs[mask.dest_stack] = result

            # --- Step 4: Pick final frame ---
            # The final output is the last destination stack from the last mask,
            # or the only stack if no masks.
            if scene.masks:
                final_stack = scene.masks[-1].dest_stack
                frame_buf = stack_bufs.get(final_stack, np.zeros((H, W, 4), dtype=np.float32))
            else:
                # Single stack: take the first (and only) stack
                first_stack = next(iter(scene.stacks))
                frame_buf = stack_bufs.get(first_stack, np.zeros((H, W, 4), dtype=np.float32))

            # Composite over background colour (ensures alpha = 1 everywhere)
            a = frame_buf[:, :, 3:4]
            bg = np.array(bg_rgb, dtype=np.float32)
            frame_buf_out = frame_buf.copy()
            frame_buf_out[:, :, :3] = frame_buf[:, :, :3] + bg[np.newaxis, np.newaxis, :] * (1.0 - a)
            frame_buf_out[:, :, 3] = 1.0

            # --- Step 5: Global post ---
            frame_buf_out = _apply_global_post(
                frame_buf_out, scene, seed, frame_idx, workspace, grain_children
            )

            # --- Step 6: Write to encoder ---
            enc.write(frame_buf_out)
