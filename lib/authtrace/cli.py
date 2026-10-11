"""authtrace: trace a running system's OAuth, OIDC and MCP-auth traffic.

  authtrace.py doctor                  what this machine can run, and what's missing
  authtrace.py render TRACE [-o OUT]   trace.json → a single-file HTML viewer

Planned (see docs/authtrace/PLAN.md §15):

  run -- CMD   M2   run CMD with in-process capture hooks
  build        M3   exchanges → trace.json (decode, classify, findings)
  explain      M3   decode and explain a JWT, masking it in the output
  check        M4   validate a trace: schema, evidence, token and secret lint
  live         M6   serve the viewer locally with live capture

Stdlib only, Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .doctor import diagnose, format_text
from .render import RenderError, render
from .trace import load

PLAN = "docs/authtrace/PLAN.md §15"

# EXTEND: when a command lands, remove it here and add a real subparser in main().
PLANNED = {
    "run": ("M2", "run a command with in-process capture hooks"),
    "build": ("M3", "build trace.json from captured exchanges"),
    "explain": ("M3", "decode and explain a JWT"),
    "check": ("M4", "validate a trace"),
    "live": ("M6", "serve the viewer with live capture"),
}


def cmd_doctor(args) -> int:
    result = diagnose()
    if args.json:
        print(json.dumps({"version": __version__, **result}, indent=2))
    else:
        print(format_text(result, __version__))
    return 0


def cmd_render(args) -> int:
    src = Path(args.trace)
    out = Path(args.output) if args.output else src.with_name("flow.html")
    try:
        page = render(load(src))
    except (OSError, ValueError) as e:
        print(f"authtrace render: can't read {src}: {e}", file=sys.stderr)
        return 1
    except RenderError as e:
        print(f"authtrace render: {src} isn't renderable:", file=sys.stderr)
        for problem in e.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    if out.exists() and out.read_text(encoding="utf-8") == page:
        print(f"{out}: unchanged")
        return 0
    out.write_text(page, encoding="utf-8")
    print(f"{out}: written")
    return 0


def cmd_planned(args) -> int:
    milestone, _ = PLANNED[args.command]
    print(f"authtrace {args.command}: not built yet; planned for {milestone} ({PLAN})", file=sys.stderr)
    return 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="authtrace.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"authtrace {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("doctor", help="report what this machine can run")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("render", help="render trace.json as a single-file HTML viewer")
    p.add_argument("trace", help="path to trace.json")
    p.add_argument("-o", "--output", help="where to write the page (default: flow.html next to the trace)")
    p.set_defaults(func=cmd_render)

    for name, (milestone, text) in PLANNED.items():
        p = sub.add_parser(name, help=f"{text} (planned, {milestone})")
        p.add_argument("args", nargs=argparse.REMAINDER)
        p.set_defaults(func=cmd_planned)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return args.func(args)
