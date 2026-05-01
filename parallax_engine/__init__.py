"""
parallax-engine
===============
2.5D multiplane camera animation engine.

Produces MP4 video files from natural-language briefs by combining:
  - A deterministic Python renderer (Pillow + skia-python + NumPy + FFmpeg)
  - An implementation harness (parallel-wave LLM agents for asset/mask/camera)
  - A director tier (sequential creative agents producing storyboard.yaml)

Commercial distribution requires the LGPL FFmpeg build with libopenh264.
See SPEC.md §5 for the full licensing contract.

Package structure (built phase by phase):
  parallax_engine.projection  — perspective-divide math (§2.1)
  parallax_engine.scene       — Pydantic scene schema + YAML loader (§2.2)
  parallax_engine.camera      — drone/keyframe camera tracks (§2.3)
  parallax_engine.masks       — mask compositing + L2 trick (§2.4)
  parallax_engine.render      — per-frame pipeline (§2.5-§2.7)
  parallax_engine.encode      — FFmpeg subprocess wrapper (§2.8)
  parallax_engine.seeds       — SeedSequence channel IDs (§9.3)
  parallax_engine.cli         — argparse entry point
  parallax_engine.director    — director tier (§11)
  parallax_engine.casting     — casting bible API (§11.7)
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
