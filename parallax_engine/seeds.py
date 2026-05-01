"""
parallax_engine/seeds.py — Stable SeedSequence channel IDs (§9.3).

The ONLY randomness root in a scene is ``scene.meta.seed`` (an integer).
Every other RNG in the system derives from it via::

    np.random.SeedSequence(seed).spawn(N)[channel_id]

This module documents the stable channel-ID assignments and provides
helper functions.  **Never reorder existing entries; only append.**

Usage
-----
    from parallax_engine.seeds import spawn_channel, CAMERA_NOISE
    child_ss = spawn_channel(scene.meta.seed, CAMERA_NOISE)
    noise = SeededNoise(child_ss)          # in camera.py
"""

from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Channel ID table — APPEND ONLY.  Reordering breaks determinism.
# ---------------------------------------------------------------------------

CAMERA_NOISE: int = 0   # SeededNoise for drone camera wobble (camera.py)
GRAIN: int = 1          # per-frame Gaussian grain  (render.py)
LIGHT_LEAKS: int = 2    # light-leak sprite jitter  (render.py)
ASSET_GEN: int = 3      # asset-generator LLM seed hint (harness)
QA_SAMPLING: int = 4    # QA frame-sampling randomness (harness)

# IDs 5–15 reserved for future renderer channels.
# IDs 16+ for harness / director-tier channels.

_N_BLOCK: int = 16      # spawn at least this many children per call


def spawn_channel(seed: int, channel_id: int) -> np.random.SeedSequence:
    """
    Return the stable ``SeedSequence`` child for *channel_id*.

    Spawning is done in blocks of :data:`_N_BLOCK` for efficiency;
    ``children[channel_id]`` always refers to the same child regardless
    of block size, because NumPy's spawn is positionally stable.

    Parameters
    ----------
    seed:
        ``scene.meta.seed`` integer.
    channel_id:
        One of the named channel constants in this module.

    Returns
    -------
    A ``numpy.random.SeedSequence`` suitable for
    ``np.random.default_rng()`` or ``SeededNoise()``.
    """
    if channel_id < 0:
        raise ValueError(
            f"channel_id must be non-negative; got {channel_id!r}"
        )
    n = max(channel_id + 1, _N_BLOCK)
    children = np.random.SeedSequence(seed).spawn(n)
    return children[channel_id]


def make_rng(seed: int, channel_id: int) -> np.random.Generator:
    """
    Convenience wrapper — spawn a channel and return a ``Generator``.

    Equivalent to ``np.random.default_rng(spawn_channel(seed, channel_id))``.
    """
    return np.random.default_rng(spawn_channel(seed, channel_id))
