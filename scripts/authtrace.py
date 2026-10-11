#!/usr/bin/env python3
"""Launcher for authtrace, the package in ../lib/authtrace.

  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/authtrace.py doctor

Stdlib only, Python 3.9+.
"""

import sys
from pathlib import Path

if __name__ != "__main__":
    # scripts/ is on sys.path in the viz tests; importing this file as `authtrace` would shadow
    # the real package. Put lib/ first instead.
    raise ImportError("scripts/authtrace.py is a launcher; put lib/ on sys.path to import authtrace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from authtrace.cli import main  # noqa: E402

sys.exit(main())
