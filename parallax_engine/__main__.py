"""
parallax_engine/__main__.py — Entry point for `python -m parallax_engine`.

Delegates to the CLI module so that both invocation forms work:
    python -m parallax_engine render ...
    parallax-engine render ...   (when installed via pip)

SPEC anchor: §4.3
"""

import sys

from parallax_engine.cli import main

sys.exit(main())
