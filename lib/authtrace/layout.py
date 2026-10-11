"""Role-column layout (PLAN.md §11), simple on purpose.

Columns, left to right: user agents, clients, authorization and resource servers, external APIs.
Top-level nodes stack in each column, grouped by boundary. A node's children (endpoints inside an
authorization server, middleware inside a resource server) stack inside its box. An edge between
neighbouring columns gets one elbow; anything else runs along a lane under the diagram.
Boundaries are drawn around what they hold.

It lays out what the diagram draws: facts plus the narrative's additions (trace.drawn), and
sizes each box for its caption lines. Positions use the same shape a hand-made `layout` does.
"""

from __future__ import annotations

from .trace import drawn

COLUMNS = {"user-agent": 0, "client": 1, "agent-module": 1, "datastore": 1, "authorization-server": 2,
           "resource-server": 2, "gateway": 2, "component": 2, "endpoint": 2, "unknown": 2, "llm-api": 3,
           "upstream-idp": 3}
ORDER = {"authorization-server": 0, "resource-server": 1, "unknown": 2}
W, H, GAP_X, GAP_Y, M = 220, 64, 170, 46, 40
PAD, HEAD, KID_GAP = 12, 24, 8


def _lines(n: dict, captions: dict) -> int:
    return max(1, len(captions.get(n["id"]) or []))


def _no_tokens(n: dict, bounds: dict) -> bool:
    """The viewer adds a "no tokens" badge to these, so their box needs room for it. (Only
    top-level nodes are asked, so a parent's zone needn't be walked as the viewer does.)"""
    b = bounds.get(n.get("boundary"))
    while b:
        if b.get("token_zone"):
            return False
        b = bounds.get(b.get("parent"))
    return n.get("holds_tokens") is False


def _child_height(n: dict, captions: dict) -> int:
    if n["kind"] == "endpoint":
        return 30
    lines = _lines(n, captions)
    return 44 if lines == 1 else 48 + 16 * (lines - 1)


def layout(trace: dict) -> dict:
    # EXTEND: merge pinned positions in, so a hand-tuned box survives a re-layout.
    parts = drawn(trace)
    captions = ((trace.get("narrative") or {}).get("captions") or {}).get("nodes") or {}
    bounds = {b["id"]: b for b in parts["boundaries"]}
    ids = {n["id"] for n in parts["nodes"]}
    kids: dict = {}
    for n in parts["nodes"]:
        if n.get("parent") in ids:
            kids.setdefault(n["parent"], []).append(n)
    top = [n for n in parts["nodes"] if n.get("parent") not in ids]

    def path(b):  # a boundary's ancestry, outermost first, so nodes sharing one stay together
        out = []
        while b:
            out.insert(0, b)
            b = (bounds.get(b) or {}).get("parent")
        return "/".join(out)

    cols: dict = {}
    for n in sorted(top, key=lambda n: (COLUMNS.get(n["kind"], 2), ORDER.get(n["kind"], 3), path(n.get("boundary")))):
        cols.setdefault(COLUMNS.get(n["kind"], 2), []).append(n)
    used = sorted(cols)
    boxes, col_of = {}, {}
    for ci, c in enumerate(used):
        x, y = M + ci * (W + GAP_X), M + 30
        for n in cols[c]:
            head = 48 + 16 * (_lines(n, captions) - 1)
            h = max(H, head + (24 if _no_tokens(n, bounds) else 0))
            ky = y + head
            for k in _descendants(n["id"], kids):
                kh = _child_height(k, captions)
                boxes[k["id"]] = [x + PAD, ky, W - 2 * PAD, kh]
                col_of[k["id"]] = ci
                ky += kh + KID_GAP
            if ky > y + head:
                h = ky - y + PAD - KID_GAP
            boxes[n["id"]] = [x, y, W, h]
            col_of[n["id"]] = ci
            y += h + GAP_Y
    for n in parts["nodes"]:  # a parent cycle leaves nodes unplaced; trace.problems() reports it
        if n["id"] not in boxes:
            boxes[n["id"]] = [0, 0, W, H]
            col_of[n["id"]] = 0
    bottom = max(y + h for _, y, _, h in boxes.values())

    # Edges leaving a node's right side get their own port, spread down the box, so neither the
    # lines nor their labels sit on top of each other. A leftward edge is routed as its mirror.
    def ends(e):
        cf, ct = col_of[e["from"]], col_of[e["to"]]
        return (e["to"], e["from"], True) if ct == cf - 1 else (e["from"], e["to"], False)

    right = {}
    for e in parts["edges"]:
        a, b, _ = ends(e)
        if col_of[b] == col_of[a] + 1:
            right.setdefault(a, []).append(e["id"])
    nested = {n["id"] for n in parts["nodes"] if n.get("parent") in ids}
    edges, lane = {}, 0
    for e in parts["edges"]:
        a, b, flipped = ends(e)
        (fx, fy, fw, fh), (tx, ty, tw, th) = boxes[a], boxes[b]
        ca, cb = col_of[a], col_of[b]
        if ca == cb and (a in nested or b in nested):
            # Inside a parent: a bracket down the parent's right padding, clear of the boxes
            # stacked between, with the label outside the parent where no box covers it.
            right_x = M + ca * (W + GAP_X) + W
            x = right_x - PAD / 2
            y1, y2 = fy + min(fh, 44) / 2, ty + min(th, 44) / 2
            geo = {"path": [[fx + fw, y1], [x, y1], [x, y2], [tx + tw, y2]], "label": [right_x + 6, (y1 + y2) / 2 + 4]}
        elif ca == cb:  # stacked: a vertical hop, offset from the centre so it clears its sibling edges
            x = fx + fw * 0.75
            top_y, low = (fy, ty + th) if fy > ty else (fy + fh, ty)
            geo = {"path": [[x, top_y], [x, low]], "label": [x + 6, (top_y + low) / 2]}
        elif cb == ca + 1:
            ports = right[a]
            x1, y1 = fx + fw, fy + min(fh, 2 * H) * (ports.index(e["id"]) + 1) / (len(ports) + 1)
            x2, y2 = tx, ty + min(th, H) / 2
            # Elbows stay near the source, so each edge's long run sits at its target's height,
            # and its label rides on that run, clear of the other edges leaving the same box.
            mid = x1 + 14 + 14 * ports.index(e["id"])
            geo = {"path": [[x1, y1], [mid, y1], [mid, y2], [x2, y2]], "label": [mid + 10, y2 - 6]}
        else:
            lane += 1
            y = bottom + 18 * lane
            x1, x2 = fx + fw / 2, tx + tw / 2
            geo = {"path": [[x1, fy + fh], [x1, y], [x2, y], [x2, ty + th]], "label": [min(x1, x2) + 12, y - 5]}
        if flipped:
            geo["path"] = geo["path"][::-1]
        edges[e["id"]] = geo
    width = M * 2 + len(used) * W + (len(used) - 1) * GAP_X
    height = bottom + 18 * lane + M
    return {"viewbox": [width, height], "boundaries": _bounds(parts, boxes, bounds, bottom + 18 * lane),
            "nodes": boxes, "edges": edges}


def _descendants(nid: str, kids: dict, seen=None) -> list:
    seen = seen if seen is not None else {nid}
    out = []
    for k in kids.get(nid, []):
        if k["id"] not in seen:
            seen.add(k["id"])
            out += [k] + _descendants(k["id"], kids, seen)
    return out


def _bounds(parts: dict, boxes: dict, bounds: dict, low: float) -> dict:
    """Each boundary wraps its nodes and inner boundaries, innermost first. The outermost ones
    also wrap the edge lanes, as they did before nesting."""
    def depth(b):
        d = 0
        while (bounds.get(b) or {}).get("parent"):
            b, d = bounds[b]["parent"], d + 1
        return d

    rects = {}
    for b in sorted(bounds, key=depth, reverse=True):
        inside = [boxes[n["id"]] for n in parts["nodes"] if n.get("boundary") == b]
        inside += [rects[c] for c, cb in bounds.items() if cb.get("parent") == b and c in rects]
        if not inside:
            rects[b] = [0, 0, 0, 0]
            continue
        x0 = min(r[0] for r in inside) - 16
        x1 = max(r[0] + r[2] for r in inside) + 16
        y0 = min(r[1] for r in inside) - (32 if depth(b) == 0 else 26)
        y1 = max(r[1] + r[3] for r in inside) + 14
        if depth(b) == 0:
            y1 = max(y1, low + 14)
        rects[b] = [x0, y0, x1 - x0, y1 - y0]
    return rects
