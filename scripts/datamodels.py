#!/usr/bin/env python3
"""Launcher for datamodels, the package in ../lib/datamodels.

  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/datamodels.py doctor

Stdlib only, Python 3.9+. Sources other than sqlite re-run this launcher under
`uv run --no-project --with <driver>`; nothing is installed into the repo.
"""

import sys
from pathlib import Path

if __name__ != "__main__":
    # scripts/ is on sys.path in the viz tests; importing this file as `datamodels` would shadow
    # the real package. Put lib/ first instead.
    raise ImportError("scripts/datamodels.py is a launcher; put lib/ on sys.path to import datamodels")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from datamodels.cli import main  # noqa: E402

sys.exit(main())
