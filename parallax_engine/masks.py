"""
parallax_engine/masks.py — Mask alpha building and compositing (§2.4).

Public API
----------
- build_mask_alpha(mask, scene, cam, frame_resolution, t)  -> float32 (H, W)
- composite_with_mask(scene, mask, S, D, cam, frame_resolution, t) -> float32 (H, W, 4)
- alpha_over(dst, src, dx, dy)         in-place premultiplied-alpha composite

All pixel buffers are premultiplied-alpha ``float32`` RGBA unless noted.

Compositing rule (§2.4):
    F = D * M[..., None] + S * (1 - M[..., None])
where S = source stack, D = destination stack, M = scalar alpha matte in [0, 1].

Near-cull handoff (§2.4):
    When the mask's anchor layer near-culls to opacity < 0.001, M = 1 everywhere
    (the portal flips fully open).

L2 in-front-of-mask rule (§2.4):
    After compositing, source-stack layers with z_cam > mask-layer z_cam
    (i.e., closer to camera) are re-rendered unmasked on top of F.
    Implemented as stubs that accept pre-rendered layer sprites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

from parallax_engine.projection import compute_near_cull, project_points

if TYPE_CHECKING:
    from parallax_engine.scene import MaskSpec, Scene


# ===========================================================================
# Utilities
# ===========================================================================

def alpha_over(
    dst: np.ndarray,
    src: np.ndarray,
    dx: int,
    dy: int,
) -> None:
    """
    In-place premultiplied-alpha 'over' operation (§2.5).

    Places *src* at pixel offset ``(dx, dy)`` in *dst*.  Both arrays are
    premultiplied RGBA ``float32`` of shapes ``(H_dst, W_dst, 4)`` and
    ``(H_src, W_src, 4)`` respectively.  Out-of-bounds regions are clipped.

    Formula: ``dst = src + dst * (1 - src.alpha)``
    """
    h_src, w_src = src.shape[:2]
    H, W = dst.shape[:2]
    x0 = max(int(dx), 0)
    y0 = max(int(dy), 0)
    x1 = min(int(dx) + w_src, W)
    y1 = min(int(dy) + h_src, H)
    if x1 <= x0 or y1 <= y0:
        return
    sx0 = x0 - int(dx)
    sy0 = y0 - int(dy)
    sx1 = sx0 + (x1 - x0)
    sy1 = sy0 + (y1 - y0)
    s = src[sy0:sy1, sx0:sx1]          # shape (h, w, 4)
    a = s[:, :, 3:4]                    # shape (h, w, 1) — alpha channel
    dst[y0:y1, x0:x1] = s + dst[y0:y1, x0:x1] * (1.0 - a)


# ===========================================================================
# Mask alpha builders — per anchor × growth kind
# ===========================================================================

def _build_perspective_matte(
    mask: "MaskSpec",
    scene: "Scene",
    cam: dict,
    H: int,
    W: int,
) -> np.ndarray:
    """
    World-anchored perspective mask (§2.4 anchor=world, growth=perspective).

    The mask path is a polygon (convex hull of control points approximated
    from the mask's SVG bounding box, or an explicit polygon field if present).
    Rasterized via cv2.fillPoly after projecting through the camera.

    For v1 we use a unit-square polygon in the layer's local plane and scale
    it by the plate_size.  The renderer will later supply the actual SVG path
    vertices.
    """
    layer = scene.find_layer(mask.attached_to_layer)
    xyz = np.array(layer.scene_xyz, dtype=np.float64)

    # Half-extents of the plate in scene space
    pw, ph = float(layer.plate_size[0]) / 2.0, float(layer.plate_size[1]) / 2.0

    # Build four corners of the plate at the layer's z-depth
    corners_3d = np.array([
        [xyz[0] - pw, xyz[1] - ph, xyz[2]],
        [xyz[0] + pw, xyz[1] - ph, xyz[2]],
        [xyz[0] + pw, xyz[1] + ph, xyz[2]],
        [xyz[0] - pw, xyz[1] + ph, xyz[2]],
    ], dtype=np.float64)

    # Project to screen
    cx = float(cam["cx"]); cy_c = float(cam["cy"]); cz = float(cam["cz"])
    yaw = float(cam.get("yaw", 0.0))
    pitch = float(cam.get("pitch", 0.0))
    roll = float(cam.get("roll", 0.0))
    pp = float(scene.meta.perspective_px)
    ox, oy = float(scene.meta.origin[0]), float(scene.meta.origin[1])

    from parallax_engine.projection import project_world_points
    _s, screen_xy, _zc = project_world_points(
        corners_3d,
        {"cx": cx, "cy": cy_c, "cz": cz,
         "yaw": yaw, "pitch": pitch, "roll": roll},
        pp,
        (ox, oy),
    )

    pts = screen_xy.astype(np.int32)
    matte = np.zeros((H, W), dtype=np.uint8)
    cv2.fillPoly(matte, [pts], 255)
    return (matte / 255.0).astype(np.float32)


def _build_radius_matte(
    mask: "MaskSpec",
    t: float,
    H: int,
    W: int,
) -> np.ndarray:
    """
    Screen-anchored radius (iris) mask (§2.4 anchor=screen, growth=radius).

    Animates from radius r0 at t0 to r1 at t1, centered at screen center.
    Optional feather_px applies a Gaussian blur to the hard circle edge.
    """
    g = mask.growth
    t0, t1 = float(g.t0), float(g.t1)
    r0, r1 = float(g.r0), float(g.r1)

    u = float(np.clip((t - t0) / max(t1 - t0, 1e-6), 0.0, 1.0))
    radius = r0 + u * (r1 - r0)

    cx_px, cy_px = W // 2, H // 2

    # Vectorized distance map (no pixel loop)
    ys, xs = np.mgrid[0:H, 0:W]
    dist = np.sqrt(
        (xs.astype(np.float32) - cx_px) ** 2 +
        (ys.astype(np.float32) - cy_px) ** 2
    )
    # Handle degenerate radius (before animation starts)
    if radius <= 0.0:
        return np.zeros((H, W), dtype=np.float32)

    matte = (dist <= radius).astype(np.float32)

    feather = g.feather_px
    if feather and feather > 0.0:
        sigma = float(feather) / 3.0  # feather_px is roughly 3σ
        matte = cv2.GaussianBlur(matte, (0, 0), sigma)

    return matte.astype(np.float32)


def _build_gradient_matte(
    mask: "MaskSpec",
    t: float,
    H: int,
    W: int,
) -> np.ndarray:
    """
    Screen-anchored gradient sweep (§2.4 anchor=screen, growth=gradient).

    Sweeps a linear ramp along 'axis' from t0 to t1.
    At t < t0: fully source.  At t > t1: fully dest (all-ones matte).
    """
    g = mask.growth
    t0, t1 = float(g.t0), float(g.t1)
    u = float(np.clip((t - t0) / max(t1 - t0, 1e-6), 0.0, 1.0))

    if g.axis == "x":
        # Ramp along x-axis; at u=0 the gradient is fully to the right,
        # at u=1 the whole frame is revealed.
        ramp = np.linspace(0.0, 1.0, W, dtype=np.float32)
        matte = np.tile(ramp, (H, 1))
        # Shift the ramp so that at progress u the reveal front is at u*W
        threshold = u
        matte = np.clip(matte / max(threshold, 1e-6), 0.0, 1.0) if threshold > 0 else np.zeros((H, W), dtype=np.float32)
    else:  # y
        ramp = np.linspace(0.0, 1.0, H, dtype=np.float32)
        matte = np.tile(ramp[:, None], (1, W))
        threshold = u
        matte = np.clip(matte / max(threshold, 1e-6), 0.0, 1.0) if threshold > 0 else np.zeros((H, W), dtype=np.float32)

    return matte.astype(np.float32)


def _build_displaced_edge_matte(
    mask: "MaskSpec",
    t: float,
    H: int,
    W: int,
) -> np.ndarray:
    """
    Screen-anchored displaced-edge mask (§2.4 anchor=screen,
    growth=displaced_edge).

    Sweeps a diagonal edge across the frame, displaced by a noise map.
    If the displacement_map asset is not available, uses a plain diagonal wipe.
    """
    g = mask.growth
    t0, t1 = float(g.t0), float(g.t1)
    amp = float(g.amp) if g.amp else 40.0
    u = float(np.clip((t - t0) / max(t1 - t0, 1e-6), 0.0, 1.0))

    # Diagonal coordinate: (x + y) / (W + H) in [0, 1]
    ys, xs = np.mgrid[0:H, 0:W]
    diag = (xs + ys).astype(np.float32) / float(W + H)

    # Try to load displacement map; fall back to smooth noise if unavailable
    disp = _load_displacement_or_noise(g.displacement_map, H, W, amp)

    threshold = u
    matte = np.clip((diag + disp / float(W + H) - threshold) / 0.05 + 0.5, 0.0, 1.0)
    return matte.astype(np.float32)


def _load_displacement_or_noise(
    path: str | None,
    H: int,
    W: int,
    amp: float,
) -> np.ndarray:
    """Load a displacement map PNG or fall back to zero displacement."""
    if path:
        try:
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                img = cv2.resize(img, (W, H))
                return (img.astype(np.float32) / 127.5 - 1.0) * amp
        except Exception:
            pass
    # Fallback: zero displacement (plain diagonal wipe)
    return np.zeros((H, W), dtype=np.float32)


def _build_matte_seq_matte(
    mask: "MaskSpec",
    t: float,
    H: int,
    W: int,
) -> np.ndarray:
    """
    Screen-anchored matte sequence (§2.4 anchor=screen, growth=matte_seq).

    Placeholder: returns a full-frame matte at u=1 when t >= t1, else zeros.
    Full implementation loads a frame sequence from the assets directory.
    """
    g = mask.growth
    t0 = float(g.t0) if g.t0 is not None else 0.0
    t1 = float(g.t1) if g.t1 is not None else 1.0
    u = float(np.clip((t - t0) / max(t1 - t0, 1e-6), 0.0, 1.0))
    # Stub: linear ramp from black to white
    return np.full((H, W), u, dtype=np.float32)


# ===========================================================================
# build_mask_alpha  (§2.4)
# ===========================================================================

def build_mask_alpha(
    mask: "MaskSpec",
    scene: "Scene",
    cam: dict,
    frame_resolution: tuple[int, int],
    t: float,
) -> np.ndarray:
    """
    Build the mask alpha matte ``M`` for one frame (§2.4).

    Parameters
    ----------
    mask:
        :class:`~parallax_engine.scene.MaskSpec` instance.
    scene:
        Parsed :class:`~parallax_engine.scene.Scene`.
    cam:
        Camera pose dict with keys ``cx, cy, cz, yaw, pitch, roll``.
    frame_resolution:
        ``(H, W)`` in pixels.
    t:
        Current time in seconds.

    Returns
    -------
    ``(H, W)`` float32 array with values in ``[0, 1]``.
    ``1`` means 100 % destination stack; ``0`` means 100 % source stack.

    Near-cull handoff
    -----------------
    If the anchor layer's near-cull opacity drops below 0.001,
    return an all-ones matte (portal fully open).
    """
    H, W = frame_resolution

    # --- Near-cull handoff (§2.4) ---
    if mask.attached_to_layer is not None:
        layer = scene.find_layer(mask.attached_to_layer)
        xyz = np.array(layer.scene_xyz, dtype=np.float64)
        # Project the layer center to get z_cam
        from parallax_engine.projection import project_world_points
        _s, _screen, z_cam_arr = project_world_points(
            xyz[None, :],
            cam,
            float(scene.meta.perspective_px),
            (float(scene.meta.origin[0]), float(scene.meta.origin[1])),
        )
        z_cam = float(z_cam_arr[0])
        cull_opacity = compute_near_cull(z_cam, float(scene.meta.perspective_px))
        if cull_opacity < 0.001:
            return np.ones((H, W), dtype=np.float32)

    # --- Build matte by anchor + growth kind ---
    anchor = mask.anchor
    kind = mask.growth.kind

    if anchor == "world":
        M = _build_perspective_matte(mask, scene, cam, H, W)
    elif anchor == "screen":
        if kind == "radius":
            M = _build_radius_matte(mask, t, H, W)
        elif kind == "gradient":
            M = _build_gradient_matte(mask, t, H, W)
        elif kind == "displaced_edge":
            M = _build_displaced_edge_matte(mask, t, H, W)
        elif kind == "matte_seq":
            M = _build_matte_seq_matte(mask, t, H, W)
        else:
            # perspective on screen-anchor: static full-frame disc
            M = np.zeros((H, W), dtype=np.float32)
    else:  # layer-plane
        # Project the layer position and use it as a screen-space mask origin
        M = _build_layer_plane_matte(mask, scene, cam, H, W, t)

    # --- Optional feathering via growth.feather_px ---
    feather = mask.growth.feather_px
    if feather and feather > 0.0 and kind not in ("radius",):
        # radius already applies feather internally
        sigma = float(feather) / 3.0
        M = cv2.GaussianBlur(M, (0, 0), sigma)

    # --- Optional inversion ---
    if mask.invert:
        M = 1.0 - M

    return np.clip(M, 0.0, 1.0).astype(np.float32)


def _build_layer_plane_matte(
    mask: "MaskSpec",
    scene: "Scene",
    cam: dict,
    H: int,
    W: int,
    t: float,
) -> np.ndarray:
    """
    Layer-plane anchor: the mask path lives in the layer's local 2D space
    but the layer's z is constant.  Projects to screen then rasterizes.
    Delegates to the radius builder using projected screen coords.
    """
    # For v1: treat as a screen-space radius at the projected layer center
    layer = scene.find_layer(mask.attached_to_layer)
    xyz = np.array(layer.scene_xyz, dtype=np.float64)
    from parallax_engine.projection import project_world_points
    _s, screen_xy, _z = project_world_points(
        xyz[None, :], cam,
        float(scene.meta.perspective_px),
        (float(scene.meta.origin[0]), float(scene.meta.origin[1])),
    )
    # Fall back to radius growth if defined, else full-frame matte
    g = mask.growth
    if g.kind == "radius" and g.r0 is not None and g.r1 is not None:
        return _build_radius_matte(mask, t, H, W)
    return np.zeros((H, W), dtype=np.float32)


# ===========================================================================
# composite_with_mask  (§2.4)
# ===========================================================================

def composite_with_mask(
    scene: "Scene",
    mask: "MaskSpec",
    S: np.ndarray,
    D: np.ndarray,
    cam: dict,
    frame_resolution: tuple[int, int],
    t: float,
    layer_sprites: dict | None = None,
) -> np.ndarray:
    """
    Apply one mask to produce the composited frame ``F`` (§2.4).

    Compositing rule::

        F = D * M[..., None] + S * (1 - M[..., None])

    After compositing, the "in-front-of-mask" (L2) rule re-renders any
    source-stack layers whose ``z_cam`` is greater than the mask layer's
    ``z_cam`` (i.e., they are closer to the camera), placing them unmasked
    on top of F.

    Parameters
    ----------
    scene:
        Parsed scene.
    mask:
        Mask specification.
    S:
        Source stack RGBA buffer, premultiplied float32, shape ``(H, W, 4)``.
    D:
        Destination stack RGBA buffer, same shape and dtype.
    cam:
        Camera pose dict.
    frame_resolution:
        ``(H, W)``.
    t:
        Current time in seconds.
    layer_sprites:
        Optional dict mapping ``"<stack>.<layer_id>"`` to pre-rendered
        ``(H, W, 4)`` float32 sprites.  Used by the L2 rule.  If None,
        the L2 rule is a no-op (sprites are rendered by render.py).

    Returns
    -------
    ``(H, W, 4)`` float32 premultiplied-RGBA composite.
    """
    H, W = frame_resolution
    M = build_mask_alpha(mask, scene, cam, frame_resolution, t)  # (H, W) f32

    # Core composite: F = D * M + S * (1 - M)
    m4 = M[:, :, None]                 # broadcast to (H, W, 1)
    F = D * m4 + S * (1.0 - m4)        # (H, W, 4) float32

    # --- L2 in-front-of-mask rule (§2.4) ---
    if mask.attached_to_layer is not None and layer_sprites:
        from parallax_engine.projection import project_world_points

        layer_mask = scene.find_layer(mask.attached_to_layer)
        xyz_mask = np.array(layer_mask.scene_xyz, dtype=np.float64)
        _, _, z_mask_arr = project_world_points(
            xyz_mask[None, :], cam,
            float(scene.meta.perspective_px),
            (float(scene.meta.origin[0]), float(scene.meta.origin[1])),
        )
        z_mask = float(z_mask_arr[0])

        src_stack = scene.stacks[mask.src_stack]
        for layer in src_stack.layers:
            xyz_L = np.array(layer.scene_xyz, dtype=np.float64)
            _, screen_L, z_L_arr = project_world_points(
                xyz_L[None, :], cam,
                float(scene.meta.perspective_px),
                (float(scene.meta.origin[0]), float(scene.meta.origin[1])),
            )
            z_L = float(z_L_arr[0])
            if z_L > z_mask:    # layer is closer to camera than mask layer
                key = f"{mask.src_stack}.{layer.id}"
                sprite = layer_sprites.get(key)
                if sprite is not None:
                    # Place sprite at its projected screen center
                    sx = float(screen_L[0, 0])
                    sy = float(screen_L[0, 1])
                    sw, sh = sprite.shape[1], sprite.shape[0]
                    dx = int(sx - sw / 2)
                    dy = int(sy - sh / 2)
                    alpha_over(F, sprite, dx, dy)

    return F.astype(np.float32)
