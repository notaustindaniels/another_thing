"""
parallax_engine/projection.py
==============================
Perspective-divide projection math for the parallax-engine renderer.

Two public functions:

    project_points(x_centered, y_centered, z_cam, perspective_px,
                   canvas_width, canvas_height)
        -> (x_screen, y_screen)

        The *inner* perspective-divide function.  This is the "locked API
        contract" tested by tools/validate_projection.py.  Inputs are
        already in camera-frame coordinates (i.e., world-to-camera
        transform has been applied before calling this).

        Formula (SPEC.md §2.1):
            s         = perspective_px / (perspective_px - z_cam)
            x_screen  = canvas_width  / 2 + x_centered * s
            y_screen  = canvas_height / 2 + y_centered * s

    project_world_points(points_xyz, cam, perspective_px, origin_xy)
        -> (s, screen_xy, z_cam)

        The *full* SPEC.md §2.1 function.  Applies the world-to-camera
        rotation R = Rz(roll) @ Rx(pitch) @ Ry(yaw), translates by the
        camera position, then calls project_points internally.  Returns
        the scale factors s, screen coordinates as (N,2), and camera-space
        z values as (N,).

        This function is used by the renderer pipeline for every layer,
        mask path, and debug overlay.  It is the ONLY place perspective
        math lives in the engine (SPEC.md §9.2).

SPEC anchors: §2.1, §7, §9.2
"""

from __future__ import annotations

import numpy as np


# ---------------------------------------------------------------------------
# Inner projection function — locked API (tested by validate_projection.py)
# ---------------------------------------------------------------------------

def project_points(
    x_centered: np.ndarray,
    y_centered: np.ndarray,
    z_cam: np.ndarray,
    perspective_px: float,
    canvas_width: int,
    canvas_height: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Project camera-frame coordinates to screen pixels.

    Parameters
    ----------
    x_centered, y_centered : np.ndarray, shape (N,)
        Point coordinates in camera frame, relative to the principal axis
        (i.e., already translated so the optic axis passes through origin).
    z_cam : np.ndarray, shape (N,)
        Camera-space depth.  Positive z is AWAY from the camera.
        z_cam == perspective_px is the singularity (point on the image plane).
        z_cam > perspective_px is behind the viewer (s < 0).
    perspective_px : float
        Focal-length-equivalent in pixels (CSS `perspective` analogue).
        Must be > 0.
    canvas_width, canvas_height : int
        Frame dimensions in pixels.  The principal point (O_x, O_y) is
        the canvas centre: (canvas_width/2, canvas_height/2).

    Returns
    -------
    x_screen, y_screen : np.ndarray, shape (N,), same dtype as inputs
        Screen-space pixel coordinates.

    Notes
    -----
    - Singularity at z_cam == perspective_px: denom → 0, s → ±inf.
      The result will be ±inf (IEEE-754 division by zero), which is
      detectable and correct; the renderer's near-cull layer filters
      these before they reach compositing.
    - Behind-viewer (z_cam > perspective_px): s < 0, screen offsets
      flip sign.  The renderer's cull window handles filtering.
    - Input shapes must match; mismatched shapes raise ValueError.
    - dtype is preserved: float32 in → float32 out; float64 in → float64 out.
    """
    x_centered = np.asarray(x_centered)
    y_centered = np.asarray(y_centered)
    z_cam = np.asarray(z_cam)

    if x_centered.shape != y_centered.shape or x_centered.shape != z_cam.shape:
        raise ValueError(
            f"Mismatched input shapes: x={x_centered.shape}, "
            f"y={y_centered.shape}, z={z_cam.shape}"
        )

    dtype = x_centered.dtype
    p = dtype.type(perspective_px)
    ox = dtype.type(canvas_width / 2.0)
    oy = dtype.type(canvas_height / 2.0)

    denom = p - z_cam                          # perspective_px - z_cam
    s = p / denom                              # scale factor; ±inf at singularity

    x_screen = ox + x_centered * s
    y_screen = oy + y_centered * s

    return x_screen, y_screen


# ---------------------------------------------------------------------------
# Full world-to-screen projection — SPEC.md §2.1
# ---------------------------------------------------------------------------

def project_world_points(
    points_xyz: np.ndarray,
    cam: dict,
    perspective_px: float,
    origin_xy: tuple[float, float],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Project world-space points through the camera to screen pixels.

    This is the full SPEC.md §2.1 projection pipeline:
      1. Translate: p_rel = p - cam_position
      2. Rotate:    p_cam = p_rel @ R   (R = Rz(roll) @ Rx(pitch) @ Ry(yaw))
      3. Project:   call project_points on p_cam components

    The rotation matrix composition is intrinsic Z-X-Y (roll last):
        R = Rz(roll) @ Rx(pitch) @ Ry(yaw)

    Parameters
    ----------
    points_xyz : np.ndarray, shape (N, 3)
        World-space points as row vectors.
    cam : dict
        Camera pose with keys: cx, cy, cz, yaw, pitch, roll (all floats,
        angles in radians).
    perspective_px : float
        Focal length in pixels (default 1200 per §2.1).
    origin_xy : tuple[float, float]
        Principal point (O_x, O_y) in pixels.  Usually (W/2, H/2).

    Returns
    -------
    s : np.ndarray, shape (N,)
        Per-point perspective scale factors.
    screen_xy : np.ndarray, shape (N, 2)
        Screen-space pixel coordinates [[x0,y0], [x1,y1], ...].
        Column order: [x_screen, y_screen].
    z_cam : np.ndarray, shape (N,)
        Camera-space z values (positive = in front of camera, away from it).

    Notes
    -----
    This function is the ONLY place perspective math lives in the engine
    (SPEC.md §9.2).  Every layer, mask path, and debug overlay routes
    through here.  Do NOT duplicate the projection formula elsewhere.
    """
    points_xyz = np.asarray(points_xyz, dtype=np.float64)
    if points_xyz.ndim == 1:
        points_xyz = points_xyz[np.newaxis, :]   # (1, 3) for single-point callers

    cx = float(cam["cx"])
    cy = float(cam["cy"])
    cz = float(cam["cz"])
    yaw = float(cam["yaw"])
    pitch = float(cam["pitch"])
    roll = float(cam["roll"])

    # Build rotation matrices (SPEC.md §2.1: R = Rz(roll) @ Rx(pitch) @ Ry(yaw))
    cy_, sy_ = np.cos(yaw),   np.sin(yaw)
    cp_, sp_ = np.cos(pitch), np.sin(pitch)
    cr_, sr_ = np.cos(roll),  np.sin(roll)

    Ry = np.array([[ cy_,  0.0,  sy_],
                   [ 0.0,  1.0,  0.0],
                   [-sy_,  0.0,  cy_]], dtype=np.float64)

    Rx = np.array([[1.0,  0.0,  0.0],
                   [0.0,  cp_, -sp_],
                   [0.0,  sp_,  cp_]], dtype=np.float64)

    Rz = np.array([[ cr_, -sr_,  0.0],
                   [ sr_,  cr_,  0.0],
                   [ 0.0,  0.0,  1.0]], dtype=np.float64)

    R = Rz @ Rx @ Ry   # composed rotation matrix

    # Translate: p_rel = p - cam_pos  (broadcasting over N points)
    p_rel = points_xyz - np.array([cx, cy, cz], dtype=np.float64)

    # Rotate: p_cam = p_rel @ R   (row-vector convention: Rᵀ · col = row @ R)
    p_cam = p_rel @ R   # shape (N, 3)

    xc = p_cam[:, 0]
    yc = p_cam[:, 1]
    zc = p_cam[:, 2]

    # Project using the inner function.
    # origin_xy is used as the canvas-centre equivalent:
    # x_screen = origin_xy[0] + xc * s
    # y_screen = origin_xy[1] + yc * s
    # We synthesise fake canvas_width/height from origin_xy to avoid
    # duplicating the formula.  The inner function adds (W/2, H/2); we
    # need it to add origin_xy instead, so use W=2*ox, H=2*oy.
    ox, oy = float(origin_xy[0]), float(origin_xy[1])
    canvas_w = int(round(ox * 2.0))
    canvas_h = int(round(oy * 2.0))

    xs, ys = project_points(xc, yc, zc, perspective_px, canvas_w, canvas_h)

    # Compute s explicitly for callers that need it.
    p = np.float64(perspective_px)
    denom = p - zc
    s = p / denom   # ±inf at singularity; let callers handle culling

    screen_xy = np.stack([xs, ys], axis=1)   # (N, 2)

    return s, screen_xy, zc


# ---------------------------------------------------------------------------
# Near-cull helper — used by masks.py and render.py
# ---------------------------------------------------------------------------

def compute_near_cull(z_cam: float, perspective_px: float = 1200.0) -> float:
    """
    Return the near-cull opacity for a layer at camera-space depth z_cam.

    Linearly fades from 1.0 → 0.0 as z_cam approaches perspective_px.

    Default cull window (§2.6):
        cull_start = perspective_px - 720   (opacity starts fading)
        cull_end   = perspective_px - 300   (opacity reaches 0)

    Parameters
    ----------
    z_cam : float
        Camera-space depth of the layer centre.
    perspective_px : float
        Focal length, same value as used in projection.

    Returns
    -------
    float in [0.0, 1.0]
    """
    cull_start = perspective_px - 720.0
    cull_end = perspective_px - 300.0

    if z_cam <= cull_start:
        return 1.0
    if z_cam >= cull_end:
        return 0.0
    # Linear fade
    t = (z_cam - cull_start) / (cull_end - cull_start)
    return float(1.0 - t)
