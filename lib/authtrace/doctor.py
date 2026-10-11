"""What this machine can run: the core, and each capture mode's prerequisites.

The skill runs `doctor` first and offers only the modes whose prerequisites are met.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

MIN_PYTHON = (3, 9)

CHROME_PATHS = {
    "Darwin": ["/Applications/Google Chrome.app", "~/Applications/Google Chrome.app"],
    "Windows": [r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
                r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"],
}
CHROME_COMMANDS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def find_chrome() -> str | None:
    """An installed Chrome, which Playwright can drive with channel="chrome" (no download)."""
    for raw in CHROME_PATHS.get(platform.system(), []):
        path = Path(os.path.expandvars(os.path.expanduser(raw)))
        if path.exists():
            return str(path)
    for name in CHROME_COMMANDS:
        found = shutil.which(name)
        if found:
            return found
    return None


def playwright_dir() -> Path:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def find_playwright_chromium() -> str | None:
    root = playwright_dir()
    if not root.is_dir():
        return None
    for child in sorted(root.iterdir()):
        if child.name.startswith("chromium"):
            return str(child)
    return None


def diagnose() -> dict:
    """The environment, and one entry per mode: its milestone and what's missing."""
    python_ok = sys.version_info[:2] >= MIN_PYTHON
    uv = shutil.which("uv")
    chrome = find_chrome()
    chromium = find_playwright_chromium()
    browser_missing = []
    if not uv:
        browser_missing.append("uv (https://docs.astral.sh/uv/)")
    if not (chrome or chromium):
        browser_missing.append("Chrome, or Playwright's Chromium (one-time download, about 150 MB)")

    # EXTEND: add a mode here when a new capture mechanism lands (PLAN.md §5).
    modes = [
        {"id": "render", "name": "render a trace (HTML viewer)", "milestone": "M1", "built": True,
         "missing": [] if python_ok else [f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+"],
         "note": "stdlib only"},
        {"id": "core", "name": "build, narrate and check a trace; explain a token", "milestone": "M4", "built": True,
         "missing": [] if python_ok else [f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+"],
         "note": "stdlib only"},
        {"id": "hooks", "name": "capture a Python app (in-process hooks)", "milestone": "M2",
         "built": True, "missing": [], "note": "runs in the target's own environment; nothing extra"},
        {"id": "browser", "name": "capture a browser flow (Playwright)", "milestone": "M5",
         "built": False, "missing": browser_missing, "note": "uvx --with playwright"},
        {"id": "proxy", "name": "capture any language (forward proxy)", "milestone": "M5",
         "built": False, "missing": [] if uv else ["uv (https://docs.astral.sh/uv/)"],
         "note": "uvx --with mitmproxy; CA trusted per process, never system-wide"},
        {"id": "live", "name": "live mode (local web UI)", "milestone": "M6", "built": False,
         "missing": [] if uv else ["uv (https://docs.astral.sh/uv/)"],
         "note": "uvx --with starlette --with uvicorn"},
    ]
    return {
        "environment": {
            "python": platform.python_version(),
            "python_ok": python_ok,
            "uv": uv,
            "chrome": chrome,
            "playwright_chromium": chromium,
        },
        "modes": modes,
    }


def format_text(result: dict, version: str) -> str:
    env = result["environment"]
    lines = [
        f"authtrace {version} doctor",
        "",
        f"  python    {env['python']}{'' if env['python_ok'] else '  (too old: need 3.9+)'}",
        f"  uv        {env['uv'] or 'not found'}",
        f"  chrome    {env['chrome'] or 'not found'}",
        f"  chromium  {env['playwright_chromium'] or 'not found (Playwright cache)'}",
        "",
    ]
    width = max(len(m["name"]) for m in result["modes"])
    for m in result["modes"]:
        state = "ready" if m["built"] and not m["missing"] else (
            "planned" if not m["built"] else "blocked")
        prereq = "prerequisites met" if not m["missing"] else "missing: " + "; ".join(m["missing"])
        status = f"{state} ({m['milestone']})"
        lines.append(f"  {m['name']:<{width}}  {status:<16}  {prereq}")
    return "\n".join(lines)
