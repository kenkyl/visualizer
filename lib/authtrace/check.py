"""`authtrace check`: is this trace safe to share, and does it claim only what its evidence supports?

Run after Claude writes the narrative and before anything is rendered or published (PLAN.md §4,
§12 step 8). It reads the file as text and as JSON:

  lint        the whole file, narrative included: no raw token, secret or one-time value (lint.py)
  schema      trace.problems(), plus the shape of each narrative field the viewer reads
  facts       meta.facts_hash still matches, so no fact was edited after `build`
  confidence  a narrative part never claims more than its evidence: code lifts it to `declared`
              at most, exchanges to their own level; a step's level is derived, never stored
  evidence    every cited path exists under the target's root, and a `:line` is inside the file
  location    a trace inside a git repo must be ignored by it

It also lists what's still `inferred` or `unknown`, so the deliver step can say what would
confirm each one.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ._viz import viz
from .confidence import RANK, step_confidence
from .lint import lint_text
from .trace import drawn, facts_hash, problems

EVIDENCE = re.compile(r"^(?P<path>[^:#]+?)(?::(?P<a>\d+)(?:-(?P<b>\d+))?)?$")
STR, LIST, INT = "string", "list of strings", "list of goal numbers"
STEP_FIELDS = {"title": STR, "time": STR, "what": STR, "wire": STR, "inv": STR, "note": STR, "roles": STR,
               "code": LIST, "nodes": LIST, "exchanges": LIST, "tokens": LIST, "goals": INT,
               "checks": "list of [who, what] pairs", "quiz": "[question, answer]"}


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.open: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.errors


def default_root(path: Path) -> Path | None:
    """The target repo a trace belongs to: the folder holding its `.authtrace/`."""
    for parent in Path(path).resolve().parents:
        if parent.name == ".authtrace":
            return parent.parent
    return None


def check(path: Path, root: Path | None = None, location: bool = True) -> Report:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        trace = json.loads(text)
    except ValueError as e:
        rep = Report()
        rep.errors += [f"line {line}: looks like {what}; redact it" for line, what in lint_text(text)]
        rep.errors.append(f"not JSON: {e}")
        return rep
    return check_trace(trace, text, root or default_root(path), path if location else None)


def check_trace(trace: dict, text: str, root: Path | None, located: Path | None = None) -> Report:
    """Every check on a parsed trace. `text` is what will be written or shared; `located` is the
    file's path when its git status should be checked too. `narrate` runs this before it writes."""
    rep = Report()
    _lint(trace, text, rep)
    meta = trace.get("meta") or {}
    if meta.get("redaction") == "raw":
        rep.errors.append("meta.redaction is raw: token values may be in the trace. Capture again with dev or strict.")
    rep.errors += problems(trace)
    _facts(trace, rep)
    nar = trace.get("narrative") or {}
    if not nar.get("flows"):
        rep.warnings.append("no narrative tours yet: the page will show one step per exchange (write narrative.json, then narrate)")
    _shape(nar, rep)
    _confidence(trace, rep)
    if root is None:
        rep.warnings.append("evidence paths not checked: the trace isn't in a target's .authtrace/; pass --root")
    else:
        _evidence(trace, Path(root), rep)
    if located is not None:
        _location(located, rep)
    names = nar.get("names") or {}
    nodes = {n.get("id"): n for n in drawn(trace)["nodes"]}
    named = lambda nid: names.get(nid) or (nodes.get(nid) or {}).get("name") or nid  # noqa: E731
    for part in [*drawn(trace)["nodes"], *drawn(trace)["edges"]]:
        if part.get("confidence") in ("inferred", "unknown"):
            label = named(part["id"]) if "from" not in part else f"{named(part['from'])} → {named(part['to'])}: {part.get('label')}"
            why = part.get("why") or "no reason given"
            rep.open.append(f"{label} ({part['confidence']}): {'; '.join(why) if isinstance(why, list) else why}")
    return rep


def _strings(value, where: str):
    """(JSON path, a small JSON text holding one value and its key) for every scalar, so a hit
    can be reported by where it is, with its key for the patterns that need one."""
    if isinstance(value, dict):
        for k, v in value.items():
            path = f"{where}.{k}" if where else str(k)
            if isinstance(v, (dict, list)):
                yield from _strings(v, path)
            else:
                yield path, json.dumps({k: v}, ensure_ascii=False)
                if isinstance(v, str):
                    yield path, v
    elif isinstance(value, list):
        for i, v in enumerate(value):
            if isinstance(v, (dict, list)):
                yield from _strings(v, f"{where}[{i}]")
            else:
                yield f"{where}[{i}]", v if isinstance(v, str) else json.dumps(v)


def _lint(trace: dict, text: str, rep: Report) -> None:
    """Lint every value where it sits, so a hit names its JSON path, then the whole text, so
    nothing outside a value (or split across values) slips through. Never shows the value."""
    found, said = set(), set()
    for where, piece in _strings(trace, ""):
        for _, what in lint_text(piece):
            found.add(what)
            if (where, what) not in said:
                said.add((where, what))
                rep.errors.append(f"{where}: looks like {what}; redact it (the value is not shown here)")
    for line, what in lint_text(text):
        if what not in found:
            rep.errors.append(f"line {line}: looks like {what}; redact it (the value is not shown here)")


def _facts(trace: dict, rep: Report) -> None:
    stored = (trace.get("meta") or {}).get("facts_hash")
    if stored is None:
        rep.warnings.append("no meta.facts_hash: the facts weren't made by `build`, so check can't prove they're unedited")
    elif stored != facts_hash(trace):
        rep.errors.append("facts changed after build (meta.facts_hash doesn't match). Facts are the core's: "
                          "rebuild, and put your changes in the narrative.")


def _shape(nar: dict, rep: Report) -> None:
    """The narrative fields the viewer reads, with the types it expects."""
    def bad(where, want):
        rep.errors.append(f"{where} must be a {want}")

    for s in nar.get("steps") or []:
        for key, want in STEP_FIELDS.items():
            if key not in s:
                continue
            v = s[key]
            ok = {STR: isinstance(v, str),
                  LIST: isinstance(v, list) and all(isinstance(x, str) for x in v),
                  INT: isinstance(v, list) and all(isinstance(x, int) for x in v)}.get(want)
            if want == STEP_FIELDS["checks"]:
                ok = isinstance(v, list) and all(isinstance(c, list) and len(c) == 2 and all(isinstance(x, str) for x in c) for c in v)
            if want == STEP_FIELDS["quiz"]:
                ok = isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) for x in v)
            if not ok:
                bad(f"step {s.get('id')}.{key}", want)
    for n, g in (nar.get("goals") or {}).items():
        if not (str(n).isdigit() and isinstance(g, list) and len(g) == 2 and all(isinstance(x, str) for x in g)):
            bad(f"narrative.goals.{n}", "[name, description] under a numeric key")
    for kind in ("nodes", "edges"):
        for cid, lines in ((nar.get("captions") or {}).get(kind) or {}).items():
            if not (isinstance(lines, list) and all(isinstance(x, str) for x in lines)):
                bad(f"narrative.captions.{kind}.{cid}", LIST)
    for c in (nar.get("inspector") or {}).get("claims") or []:
        if not (isinstance(c, dict) and isinstance(c.get("claim"), str)):
            bad("narrative.inspector.claims[]", "{claim, checked_by, why}")
    if nar.get("steps") and not nar.get("flows"):
        rep.errors.append("narrative has steps but no flows: put them in a tour, or the viewer ignores them")


def _confidence(trace: dict, rep: Report) -> None:
    exchanges = {x["id"]: x for x in trace.get("exchanges") or [] if "id" in x}
    tokens = {t["id"]: t for t in trace.get("tokens") or [] if "id" in t}
    nar = trace.get("narrative") or {}
    for s in nar.get("steps") or []:
        if "confidence" in s:
            rep.errors.append(f"step {s.get('id')}: remove `confidence`. A step's level is derived from the "
                              f"exchanges, tokens and code it cites, so it can't claim more.")
    for kind in ("nodes", "edges"):
        for part in nar.get(kind) or []:
            where, level = f"narrative.{kind[:-1]} {part.get('id')}", part.get("confidence")
            cited = [x for x in part.get("exchanges") or [] if x in exchanges]
            for xid in set(part.get("exchanges") or []) - set(cited):
                rep.errors.append(f"{where}: unknown exchange {xid!r}")
            ceiling = step_confidence({"exchanges": cited, "code": part.get("evidence")}, exchanges, tokens)
            if level in RANK and RANK.index(level) < RANK.index(ceiling):
                rep.errors.append(f"{where}: says {level}, but its evidence supports {ceiling} at most "
                                  f"(cite exchanges to claim more than declared; cite code to claim declared)")
            if level in ("inferred", "unknown") and not part.get("why"):
                rep.errors.append(f"{where}: an {level} part needs a `why`: the reasoning, and what would confirm it")


def _evidence(trace: dict, root: Path, rep: Report) -> None:
    nar = trace.get("narrative") or {}
    refs = [(f"node {n.get('id')}", ev) for n in drawn(trace)["nodes"] for ev in n.get("evidence") or []]
    refs += [(f"edge {e.get('id')}", ev) for e in nar.get("edges") or [] for ev in e.get("evidence") or []]
    refs += [(f"check at {c.get('at')}", c["evidence"]) for c in trace.get("checks") or [] if c.get("evidence")]
    refs += [(f"step {s.get('id')}", ev) for s in nar.get("steps") or [] for ev in s.get("code") or []]
    sizes: dict = {}
    for where, ev in refs:
        m = EVIDENCE.match(str(ev))
        if not m or m["path"].startswith(("/", "~")) or ".." in m["path"].split("/"):
            rep.errors.append(f"{where}: evidence {ev!r} must be a path relative to {root}, optionally with :line or :a-b")
            continue
        rel = m["path"]
        if viz.FORBIDDEN_EVIDENCE_RE.search(rel.rstrip("/")):
            rep.errors.append(f"{where}: {rel} is a secrets file; never read or cite it")
            continue
        target = root / rel
        if not target.exists():
            rep.errors.append(f"{where}: {rel} doesn't exist in {root}")
            continue
        if m["a"]:
            if target.is_dir():
                rep.errors.append(f"{where}: {ev} gives a line, but {rel} is a folder")
                continue
            if rel not in sizes:
                sizes[rel] = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
            last = int(m["b"] or m["a"])
            if not 1 <= int(m["a"]) <= last <= sizes[rel]:
                rep.errors.append(f"{where}: {ev} is outside {rel} ({sizes[rel]} lines)")


def _location(path: Path, rep: Report) -> None:
    """A trace in a git repo must be ignored there, or the next commit could publish it."""
    folder = path.resolve().parent
    try:
        inside = subprocess.run(["git", "-C", str(folder), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    except OSError:
        return
    if inside.returncode != 0:
        return
    ignored = subprocess.run(["git", "-C", str(folder), "check-ignore", "-q", str(path.resolve())])
    if ignored.returncode != 0:
        rep.errors.append(f"{path} is inside the git repo at {inside.stdout.strip()} and isn't ignored. "
                          f"Keep traces in .authtrace/ (ignored) or outside the repo.")


def format_report(path: Path, rep: Report, redaction: str | None) -> str:
    head = f"{path}: " + ("OK" if rep.ok else f"FAILED, {len(rep.errors)} problem(s)")
    if rep.warnings:
        head += f", {len(rep.warnings)} warning(s)"
    out = [head + (f" (redaction {redaction})" if redaction else "")]
    out += [f"  error: {e}" for e in rep.errors]
    out += [f"  warning: {w}" for w in rep.warnings]
    if rep.open:
        out.append("  still inferred or unknown (say what would confirm each):")
        out += [f"    - {o}" for o in rep.open]
    return "\n".join(out)
