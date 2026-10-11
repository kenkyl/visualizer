"""Injected by `authtrace run` through PYTHONPATH; this folder holds only this file.

Python imports `sitecustomize` at startup. This one installs authtrace's capture hooks, then
runs whichever sitecustomize the environment would have used without it, so the traced program
starts exactly as it normally would.
"""

import importlib
import importlib.util
import os
import sys


def _install():
    try:
        from authtrace.capture.install import install

        install()
    except Exception as e:  # never stop the program from starting
        sys.stderr.write(f"authtrace: capture hooks not installed ({type(e).__name__}: {e})\n")


def _chain():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != here]
    me = sys.modules.pop("sitecustomize", None)
    try:
        if importlib.util.find_spec("sitecustomize") is not None:
            importlib.import_module("sitecustomize")
            return
    except Exception as e:
        sys.stderr.write(f"authtrace: the environment's own sitecustomize failed ({type(e).__name__}: {e})\n")
    sys.modules["sitecustomize"] = me


if os.environ.get("AUTHTRACE_OUT"):
    _install()
_chain()
