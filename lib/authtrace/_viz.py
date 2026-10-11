"""viz.py's secret patterns, shared with authtrace's lint.

authtrace imports viz; viz never imports authtrace. scripts/ is appended to sys.path, never
prepended, so lib/ stays first and the scripts/authtrace.py launcher can't shadow this package.
"""

import sys
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.append(_SCRIPTS)

import viz  # noqa: E402,F401
