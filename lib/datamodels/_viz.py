"""viz.py's shared helpers (README markers, evidence checks, the secret lint, Markdown escaping).

datamodels imports viz; viz never imports datamodels. scripts/ is appended to sys.path, never
prepended, so lib/ stays first and the launchers in scripts/ can't shadow the packages.
"""

import sys
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.append(_SCRIPTS)

import viz  # noqa: E402,F401
