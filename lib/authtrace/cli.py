"""authtrace: trace a running system's OAuth, OIDC and MCP-auth traffic.

  authtrace.py doctor                  what this machine can run, and what's missing
  authtrace.py run [-o DIR] -- CMD     run CMD with capture hooks → DIR/exchanges.jsonl
  authtrace.py import-har HAR [-o DIR] a browser HAR → DIR/exchanges.jsonl, redacted
  authtrace.py build CAPTURE [-o OUT]  exchanges.jsonl (or its folder) → trace.json: facts and findings
  authtrace.py show TRACE [ID ...]     a trace's ids, one line each; or those exchanges and tokens
  authtrace.py narrate TRACE NARRATIVE put Claude's narrative.json into the trace, and lay it out again
  authtrace.py check TRACE [--root DIR] lint, schema, unedited facts, confidence, evidence paths
  authtrace.py render TRACE [-o OUT]   trace.json → a single-file HTML viewer
  authtrace.py explain [--json] < TOKEN decode one token from stdin; never echoes it

Captures go to .authtrace/<run>/ by default (git-ignored). Redaction: --redaction dev (default),
strict (for sharing) or raw (local only; token values are written).

Planned (see docs/authtrace/PLAN.md §15):

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
from .build import build, load_records
from .har import import_har
from .redact import LEVELS
from .render import RenderError, render
from .run import RunError, default_out, run, summarize
from .trace import load

PLAN = "docs/authtrace/PLAN.md §15"

# EXTEND: when a command lands, remove it here and add a real subparser in main().
PLANNED = {
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


def cmd_run(args) -> int:
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    out = Path(args.output) if args.output else default_out(Path.cwd())
    try:
        code, path = run(cmd, out, args.redaction)
    except RunError as e:
        print(f"authtrace run: {e}", file=sys.stderr)
        return 2
    print(f"\nauthtrace: command exited {code}", file=sys.stderr)
    print(summarize(path), file=sys.stderr)
    return code


def cmd_import_har(args) -> int:
    out = Path(args.output) if args.output else default_out(Path.cwd())
    try:
        from .run import ensure_ignored

        ensure_ignored(out)
        path = import_har(Path(args.har), out, args.redaction)
    except RunError as e:
        print(f"authtrace import-har: {e}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError) as e:
        print(f"authtrace import-har: can't read {args.har}: {e}", file=sys.stderr)
        return 1
    print(summarize(path))
    return 0


def cmd_build(args) -> int:
    src = Path(args.capture)
    try:
        records, run_meta = load_records(src)
    except (OSError, ValueError) as e:
        print(f"authtrace build: can't read {src}: {e}", file=sys.stderr)
        return 1
    trace = build(records, run_meta, args.target)
    out = Path(args.output) if args.output else (src if src.is_dir() else src.parent) / "trace.json"
    from .run import ensure_ignored, write_private

    try:
        ensure_ignored(out.parent)
    except RunError as e:
        print(f"authtrace build: {e}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    write_private(out, json.dumps(trace, ensure_ascii=False, indent=1) + "\n")
    rules = trace["meta"]["rules"]
    print(f"{out}: {len(trace['exchanges'])} exchanges, {len(trace['tokens'])} tokens, {len(trace['nodes'])} nodes")
    for n in trace["nodes"]:
        print(f"  {n['name']:<24} {n['kind']:<21} {n['confidence']:<9} {'; '.join(n['why'])}")
    for e in trace["edges"]:
        if e["confidence"] != "observed":
            print(f"  {e['from']} → {e['to']}: {e['label']} ({e['confidence']})")
    fired = [f for f in trace["findings"]]
    print(f"  rules: {sum(v == 'passed' for v in rules.values())} passed, {len(fired)} fired")
    for f in fired:
        print(f"    {f['severity']:<8} {f['rule']:<21} {f['title']} ({', '.join(f['exchanges'][:6])}{'…' if len(f['exchanges']) > 6 else ''})")
    return 0


def cmd_show(args) -> int:
    from .show import detail, index

    try:
        trace = load(Path(args.trace))
    except (OSError, ValueError) as e:
        print(f"authtrace show: can't read {args.trace}: {e}", file=sys.stderr)
        return 1
    print(detail(trace, args.ids) if args.ids else index(trace))
    return 0


def cmd_narrate(args) -> int:
    from .check import check_trace, default_root
    from .layout import layout
    from .run import write_private

    src = Path(args.trace)
    try:
        trace = load(src)
        narrative = json.loads(Path(args.narrative).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"authtrace narrate: can't read: {e}", file=sys.stderr)
        return 1
    if not isinstance(narrative, dict):
        print("authtrace narrate: the narrative file must hold one JSON object (the narrative section)", file=sys.stderr)
        return 1
    # The narrative is Claude's, the facts the core's: replace only the narrative, then lay out
    # again, since the narrative may add parts and captions that change the boxes.
    trace["narrative"] = narrative
    trace["layout"] = layout(trace)
    # Everything check does except the git location, so a bad narrative never lands in the trace.
    text = json.dumps(trace, ensure_ascii=False, indent=1) + "\n"
    rep = check_trace(trace, text, Path(args.root) if args.root else default_root(src))
    if not rep.ok:
        print(f"authtrace narrate: {args.narrative} doesn't fit {src}; nothing written:", file=sys.stderr)
        for problem in rep.errors:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    write_private(src, text)
    flows = narrative.get("flows") or []
    print(f"{src}: narrative from {args.narrative}: {len(flows)} tour(s), {len(narrative.get('steps') or [])} step(s), "
          f"{len(narrative.get('nodes') or [])} added node(s), {len(narrative.get('edges') or [])} added edge(s). "
          f"Next: check.")
    return 0


def cmd_check(args) -> int:
    from .check import check, format_report

    src = Path(args.trace)
    try:
        rep = check(src, Path(args.root) if args.root else None)
    except OSError as e:
        print(f"authtrace check: can't read {src}: {e}", file=sys.stderr)
        return 1
    redaction = None
    if rep.ok:
        redaction = (load(src).get("meta") or {}).get("redaction")
    print(format_report(src, rep, redaction))
    return 0 if rep.ok else 1


def cmd_explain(args) -> int:
    from .explain import explain, format_text

    if sys.stdin.isatty():
        print("authtrace explain: paste the token on stdin, e.g. pbpaste | authtrace.py explain", file=sys.stderr)
        return 2
    raw = sys.stdin.read()
    if not raw.strip():
        print("authtrace explain: no token on stdin", file=sys.stderr)
        return 2
    result = explain(raw)
    print(json.dumps(result, indent=1, ensure_ascii=False) if args.json else format_text(result))
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

    p = sub.add_parser("run", help="run a command with in-process capture hooks",
                       usage="authtrace.py run [-o DIR] [--redaction LEVEL] -- CMD ...")
    p.add_argument("-o", "--output", help="capture directory (default: .authtrace/run-<time>/ here)")
    p.add_argument("--redaction", choices=LEVELS, default="dev", help="dev (default), strict or raw")
    p.add_argument("cmd", nargs=argparse.REMAINDER, help="the command, after --")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("import-har", help="import a browser HAR as redacted exchanges")
    p.add_argument("har", help="path to the .har file")
    p.add_argument("-o", "--output", help="capture directory (default: .authtrace/run-<time>/ here)")
    p.add_argument("--redaction", choices=LEVELS, default="dev", help="dev (default), strict or raw")
    p.set_defaults(func=cmd_import_har)

    p = sub.add_parser("build", help="build trace.json from a capture")
    p.add_argument("capture", help="exchanges.jsonl, or the capture folder holding it")
    p.add_argument("-o", "--output", help="where to write trace.json (default: next to the capture)")
    p.add_argument("--target", help="name of the traced system (default: the captured command's folder)")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("show", help="list a trace's ids, or show some exchanges and tokens")
    p.add_argument("trace", help="path to trace.json")
    p.add_argument("ids", nargs="*", help="exchange or token ids (x3, t1) to show in full")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("narrate", help="put a narrative into a trace, replacing any earlier one")
    p.add_argument("trace", help="path to trace.json (rewritten in place)")
    p.add_argument("narrative", help="a JSON file holding the narrative section")
    p.add_argument("--root", help="the target repo that evidence paths are relative to "
                                  "(default: the folder holding the trace's .authtrace/)")
    p.set_defaults(func=cmd_narrate)

    p = sub.add_parser("check", help="validate a trace before rendering or sharing it")
    p.add_argument("trace", help="path to trace.json")
    p.add_argument("--root", help="the target repo that evidence paths are relative to "
                                  "(default: the folder holding the trace's .authtrace/)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("explain", help="decode one token from stdin, masking it in the output")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.set_defaults(func=cmd_explain)

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
