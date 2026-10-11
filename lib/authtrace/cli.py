"""authtrace: trace a running system's OAuth, OIDC and MCP-auth traffic.

  authtrace.py doctor        what this machine can run, and what's missing

Planned (see docs/authtrace/PLAN.md §15):

  run -- CMD   M2   run CMD with in-process capture hooks
  build        M3   exchanges → trace.json (decode, classify, findings)
  explain      M3   decode and explain a JWT, masking it in the output
  check        M4   validate a trace: schema, evidence, token and secret lint
  render       M1   trace.json → a single-file HTML viewer
  live         M6   serve the viewer locally with live capture

Stdlib only, Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .doctor import diagnose, format_text

PLAN = "docs/authtrace/PLAN.md §15"

# EXTEND: when a command lands, remove it here and add a real subparser in main().
PLANNED = {
    "run": ("M2", "run a command with in-process capture hooks"),
    "build": ("M3", "build trace.json from captured exchanges"),
    "explain": ("M3", "decode and explain a JWT"),
    "check": ("M4", "validate a trace"),
    "render": ("M1", "render trace.json as an HTML viewer"),
    "live": ("M6", "serve the viewer with live capture"),
}


def cmd_doctor(args) -> int:
    result = diagnose()
    if args.json:
        print(json.dumps({"version": __version__, **result}, indent=2))
    else:
        print(format_text(result, __version__))
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

    for name, (milestone, text) in PLANNED.items():
        p = sub.add_parser(name, help=f"{text} (planned, {milestone})")
        p.add_argument("args", nargs=argparse.REMAINDER)
        p.set_defaults(func=cmd_planned)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return args.func(args)
