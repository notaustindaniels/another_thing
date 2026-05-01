"""parallax_engine.qa — tiered QA critic for director-era harness.

Public re-exports:
    critique          — level-dispatching QA entry point (§11.8)
    CritiqueResult    — typed return dataclass
    QACriticError     — raised on unrecoverable errors
"""
from __future__ import annotations

from parallax_engine.qa.critic import (
    QACriticError,
    CritiqueResult,
    critique,
)

__all__ = ["critique", "CritiqueResult", "QACriticError"]
