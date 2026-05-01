"""
parallax_engine.tools
=====================

In-process MCP tool wrappers for the lead orchestrator.

These functions are called directly by the harness (no subprocess / no
network).  They wrap the deterministic Phase 1 renderer, per-frame QA
helpers, image generation, and background segmentation.

Modules
-------
parallax_engine.tools.render       -- render_scene (mcp__parallax_render__render_scene)
parallax_engine.tools.qa           -- diff_frames, ssim_score (mcp__parallax_qa__*)
parallax_engine.tools.gen_image    -- gen_image (mcp__parallax_render__gen_image)
parallax_engine.tools.autosegment  -- autosegment (mcp__parallax_masks__autosegment)

SPEC anchors: ss3.1, ss3.3
"""
from __future__ import annotations

__all__ = ["render", "qa", "gen_image", "autosegment"]
