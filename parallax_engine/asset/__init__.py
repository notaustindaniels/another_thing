"""parallax_engine.asset — casting-aware asset generation for director-era harness.

Public re-exports:
    generate      — kind-dispatching asset generator (§11.7)
    GenerateResult — typed return dict
    AssetGeneratorError — raised on unrecoverable errors
"""
from __future__ import annotations

from parallax_engine.asset.generator import (
    AssetGeneratorError,
    GenerateResult,
    generate,
)

__all__ = ["generate", "GenerateResult", "AssetGeneratorError"]
