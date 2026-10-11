"""Role-column layout (PLAN.md §11), simple on purpose.

Columns, left to right: user agents, clients, authorization and resource servers, external APIs.
Nodes stack in each column. An edge between neighbouring columns gets one elbow; anything else
runs along a lane under the diagram. Positions use the same shape a hand-made `layout` does,
so Claude can pin any of them later.
"""

from __future__ import annotations

COLUMNS = {"user-agent": 0, "client": 1, "agent-module": 1, "authorization-server": 2, "resource-server": 2,
           "unknown": 2, "llm-api": 3, "upstream-idp": 3}
ORDER = {"authorization-server": 0, "resource-server": 1, "unknown": 2}
W, H, GAP_X, GAP_Y, M = 220, 64, 120, 46, 40


def layout(trace: dict) -> dict:
    # EXTEND: boundaries as containers, parent/child nodes, and pinned positions merged in.
    cols: dict = {}
    for n in sorted(trace["nodes"], key=lambda n: (COLUMNS.get(n["kind"], 2), ORDER.get(n["kind"], 3), n["id"])):
        cols.setdefault(COLUMNS.get(n["kind"], 2), []).append(n["id"])
    used = sorted(cols)
    boxes, col_of = {}, {}
    for ci, c in enumerate(used):
        for ri, nid in enumerate(cols[c]):
            boxes[nid] = [M + ci * (W + GAP_X), M + 30 + ri * (H + GAP_Y), W, H]
            col_of[nid] = ci
    bottom = max(y + h for _, y, _, h in boxes.values())
    # Edges leaving a node's right side get their own port, spread down the box, so neither the
    # lines nor their labels sit on top of each other.
    right = {}
    for e in trace["edges"]:
        if col_of[e["to"]] == col_of[e["from"]] + 1:
            right.setdefault(e["from"], []).append(e["id"])
    edges, lane = {}, 0
    for e in trace["edges"]:
        (fx, fy, fw, fh), (tx, ty, tw, th) = boxes[e["from"]], boxes[e["to"]]
        cf, ct = col_of[e["from"]], col_of[e["to"]]
        if cf == ct:  # stacked: a vertical hop, offset from the centre so it clears its sibling edges
            x = fx + fw * 0.75
            top, low = (fy, ty + th) if fy > ty else (fy + fh, ty)
            edges[e["id"]] = {"path": [[x, top], [x, low]], "label": [x + 6, (top + low) / 2]}
        elif ct == cf + 1:
            ports = right[e["from"]]
            x1, y1 = fx + fw, fy + fh * (ports.index(e["id"]) + 1) / (len(ports) + 1)
            x2, y2 = tx, ty + th / 2
            mid = x1 + (x2 - x1) * (0.35 + 0.3 * ports.index(e["id"]) / max(len(ports) - 1, 1))
            edges[e["id"]] = {"path": [[x1, y1], [mid, y1], [mid, y2], [x2, y2]], "label": [x1 + 6, y1 - 5]}
        else:
            lane += 1
            y = bottom + 18 * lane
            x1, x2 = fx + fw / 2, tx + tw / 2
            edges[e["id"]] = {"path": [[x1, fy + fh], [x1, y], [x2, y], [x2, ty + th]], "label": [min(x1, x2) + 12, y - 5]}
    width = M * 2 + len(used) * W + (len(used) - 1) * GAP_X
    height = bottom + 18 * lane + M
    inside = [boxes[n["id"]] for n in trace["nodes"] if n.get("boundary") == "machine"]
    bounds = {}
    if inside:
        x0 = min(b[0] for b in inside) - 16
        x1 = max(b[0] + b[2] for b in inside) + 16
        bounds["machine"] = [x0, M - 8, x1 - x0, bottom + 18 * lane + 14 - (M - 8)]
    return {"viewbox": [width, height], "boundaries": bounds, "nodes": boxes, "edges": edges}
