"""trace.json v0: load it, and find the problems that would stop a render.

The contract is PLAN.md §6, with two v0 choices recorded in docs/decisions.md: tours live in
`narrative.flows` (Claude writes them), and `layout` is required until auto-layout lands.

  facts       meta, boundaries, nodes, edges, exchanges, tokens, sessions, checks, findings, logs
  narrative   header, goals, flows, steps, captions, inspector, logs_note   (Claude's, never the core's)
  layout      viewbox, boundaries, nodes, edges                            (positions, not meaning)

Full validation (evidence paths, the secret lint, confidence never rising) is `check`, in M4.
"""

from __future__ import annotations

import json
from pathlib import Path

from .confidence import LEVELS

SCHEMA_VERSION = 0
FACT_SECTIONS = ("boundaries", "nodes", "edges", "exchanges", "tokens", "sessions", "checks", "findings", "logs")
# Facts with an id that something can point at, and must therefore carry a confidence.
CONFIDENT = ("nodes", "edges", "exchanges", "tokens", "checks", "findings", "logs")
SEG_KINDS = ("tok", "dat", "err")


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _ids(items: list, where: str, problems: list) -> set:
    seen = set()
    for i, item in enumerate(items):
        iid = item.get("id")
        if not iid:
            problems.append(f"{where}[{i}] has no id")
        elif iid in seen:
            problems.append(f"{where}: duplicate id {iid!r}")
        seen.add(iid)
    return seen


def problems(trace: dict) -> list[str]:
    """Everything that would make the viewer draw nonsense. Empty means renderable."""
    out: list[str] = []
    meta = trace.get("meta") or {}
    if meta.get("schema_version") != SCHEMA_VERSION:
        return [f"meta.schema_version must be {SCHEMA_VERSION}, got {meta.get('schema_version')!r}"]

    ids = {name: _ids(trace.get(name) or [], name, out) for name in FACT_SECTIONS if name not in ("checks", "findings")}
    for section in CONFIDENT:
        for i, item in enumerate(trace.get(section) or []):
            if item.get("confidence") not in LEVELS:
                out.append(f"{section}[{i}] ({item.get('id', item.get('rule', '?'))}): confidence must be one of {', '.join(LEVELS)}")

    for b in trace.get("boundaries") or []:
        if b.get("parent") and b["parent"] not in ids["boundaries"]:
            out.append(f"boundary {b['id']}: unknown parent {b['parent']!r}")
    for n in trace.get("nodes") or []:
        if n.get("boundary") and n["boundary"] not in ids["boundaries"]:
            out.append(f"node {n['id']}: unknown boundary {n['boundary']!r}")
        if n.get("parent") and n["parent"] not in ids["nodes"]:
            out.append(f"node {n['id']}: unknown parent {n['parent']!r}")
    for e in trace.get("edges") or []:
        for end in ("from", "to"):
            if e.get(end) not in ids["nodes"]:
                out.append(f"edge {e.get('id')}: unknown {end} node {e.get(end)!r}")
    for x in trace.get("exchanges") or []:
        for t in x.get("tokens") or []:
            if t.get("token") not in ids["tokens"]:
                out.append(f"exchange {x['id']}: unknown token {t.get('token')!r}")

    out += _narrative_problems(trace.get("narrative") or {}, ids)
    out += _layout_problems(trace.get("layout") or {}, ids)
    return out


def _narrative_problems(nar: dict, ids: dict) -> list[str]:
    out: list[str] = []
    goals = set((nar.get("goals") or {}).keys())
    steps = _ids(nar.get("steps") or [], "narrative.steps", out)
    flows = nar.get("flows") or []
    _ids(flows, "narrative.flows", out)
    if not flows:
        out.append("narrative.flows is empty: the viewer needs at least one tour")
    for f in flows:
        if not f.get("steps"):
            out.append(f"flow {f.get('id')}: no steps")
        for sid in f.get("steps") or []:
            if sid not in steps:
                out.append(f"flow {f.get('id')}: unknown step {sid!r}")
        for nid in f.get("dim_nodes") or []:
            if nid not in ids["nodes"]:
                out.append(f"flow {f.get('id')}: dim_nodes has unknown node {nid!r}")
        for eid in f.get("dim_edges") or []:
            if eid not in ids["edges"]:
                out.append(f"flow {f.get('id')}: dim_edges has unknown edge {eid!r}")

    for s in nar.get("steps") or []:
        where = f"step {s.get('id')}"
        if not s.get("title"):
            out.append(f"{where}: no title")
        for nid in s.get("nodes") or []:
            if nid not in ids["nodes"]:
                out.append(f"{where}: unknown node {nid!r}")
        for xid in s.get("exchanges") or []:
            if xid not in ids["exchanges"]:
                out.append(f"{where}: unknown exchange {xid!r}")
        for g in s.get("goals") or []:
            if str(g) not in goals:
                out.append(f"{where}: unknown goal {g!r}")
        for seg in s.get("segs") or []:
            if len(seg) != 4 or seg[0] not in ids["edges"] or seg[1] not in (1, -1) or seg[3] not in SEG_KINDS:
                out.append(f"{where}: bad seg {seg!r} (want [edge id, 1 or -1, label, {'|'.join(SEG_KINDS)}])")

    captions = nar.get("captions") or {}
    for kind in ("nodes", "edges"):
        for cid in captions.get(kind) or {}:
            if cid not in ids[kind]:
                out.append(f"narrative.captions.{kind}: unknown id {cid!r}")
    insp = nar.get("inspector")
    if insp and insp.get("token") not in ids["tokens"]:
        out.append(f"narrative.inspector: unknown token {insp.get('token')!r}")
    return out


def _layout_problems(layout: dict, ids: dict) -> list[str]:
    # EXTEND: role-column auto-layout (PLAN.md §11) fills in whatever isn't pinned here. Until
    # then, every boundary, node and edge needs a position.
    out: list[str] = []
    if len(layout.get("viewbox") or []) != 2:
        out.append("layout.viewbox must be [width, height]")
    for kind in ("boundaries", "nodes"):
        boxes = layout.get(kind) or {}
        for iid in sorted(ids[kind] - set(boxes)):
            out.append(f"layout.{kind}: no box for {iid!r} (auto-layout isn't built yet)")
        for iid, box in boxes.items():
            if iid not in ids[kind]:
                out.append(f"layout.{kind}: unknown id {iid!r}")
            elif len(box) != 4:
                out.append(f"layout.{kind}.{iid} must be [x, y, width, height]")
    edges = layout.get("edges") or {}
    for iid in sorted(ids["edges"] - set(edges)):
        out.append(f"layout.edges: no path for {iid!r} (auto-layout isn't built yet)")
    for iid, geo in edges.items():
        if iid not in ids["edges"]:
            out.append(f"layout.edges: unknown id {iid!r}")
        elif len(geo.get("path") or []) < 2:
            out.append(f"layout.edges.{iid}.path needs at least two points")
    return out
