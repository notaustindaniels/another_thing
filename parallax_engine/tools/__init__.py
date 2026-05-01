"""
parallax_engine.tools
=====================

In-process MCP tool wrappers for the lead orchestrator.

These functions are called directly by the harness (no subprocess / no
network).  They wrap the deterministic Phase 1 renderer and per-frame QA
helpers.

Modules
-------
parallax_engine.tools.render  -- render_scene tool wrapper (ss3.3)
parallax_engine.tools.qa      -- diff_frames and ssim_score tools (ss3.3)

SPEC anchors: ss3.1, ss3.3
"""
from __future__ import annotations

__all__ = ["render", "qa"]
