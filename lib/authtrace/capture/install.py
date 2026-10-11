"""Patch HTTP clients when the traced program imports them, not before.

A meta-path finder wraps the loader of each watched module, so the hook runs right after the
module executes. Programs that never import httpx pay nothing for it, and authtrace never imports
a third-party library itself.
"""

from __future__ import annotations

import importlib.abc
import sys

from . import http_client, httpx_hook, recorder


def _hook_http_client(mod, rec):
    http_client.install(mod, rec)


def _hook_httpx(mod, rec):
    httpx_hook.install(mod, rec, mod.__name__)


# EXTEND: aiohttp (ClientSession._request), grpc, or another client's lowest send path.
WATCH = {"http.client": _hook_http_client, "httpx": _hook_httpx, "httpx2": _hook_httpx}


class _Loader(importlib.abc.Loader):
    def __init__(self, inner, hook, rec):
        self.inner, self.hook, self.rec = inner, hook, rec

    def create_module(self, spec):
        return self.inner.create_module(spec)

    def exec_module(self, module):
        self.inner.exec_module(module)
        try:
            self.hook(module, self.rec)
        except Exception:
            self.rec.hook_error(f"install {module.__name__}")


class _Finder(importlib.abc.MetaPathFinder):
    def __init__(self, rec):
        self.rec = rec

    def find_spec(self, name, path, target=None):
        hook = WATCH.get(name)
        if hook is None:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(name, path, target)
            if spec is not None:
                if spec.loader is not None and hasattr(spec.loader, "exec_module"):
                    spec.loader = _Loader(spec.loader, hook, self.rec)
                return spec
        return None


def install() -> bool:
    rec = recorder.get()
    if rec is None:
        return False
    for name, hook in WATCH.items():
        if name in sys.modules:
            hook(sys.modules[name], rec)
    sys.meta_path.insert(0, _Finder(rec))
    return True
