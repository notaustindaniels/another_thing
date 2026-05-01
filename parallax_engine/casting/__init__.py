"""parallax_engine.casting — casting-bible read/append API.

Public surface::

    from parallax_engine.casting import CastingBible, load_casting_bible

The casting bible persists recurring visual elements (characters, props, motifs,
environment elements) across scenes.  The ``CastingBible`` class is the single
point of access: it never overwrites the canonical_svg path once set, and all
writes are atomic (write-to-temp + os.replace).
"""
from __future__ import annotations

from parallax_engine.casting.bible import CastingBible, load_casting_bible

__all__ = ["CastingBible", "load_casting_bible"]
