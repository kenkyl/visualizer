"""The tour a trace gets before anyone narrates it: one step per exchange, built from facts only.

It lives in render's derived data, never in trace.json, so `narrative` stays Claude's alone. Each
step says what crossed the wire, nothing more; the why is the narrative's job.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

FAMILY = {"oauth": "OAuth", "oidc": "OIDC", "mcp": "MCP", "llm": "Model API", "http": "HTTP"}
WIRE_BODY = 500


def _short(obj) -> str:
    text = json.dumps(obj, ensure_ascii=False)
    return text if len(text) <= WIRE_BODY else text[:WIRE_BODY] + "…"


def short_path(url: str) -> str:
    """A step title's path: the last two segments, so long endpoint paths don't wrap the card."""
    parts = [p for p in (urlsplit(url).path or "/").split("/") if p]
    return "/" + "/".join(parts) if len(parts) <= 2 else "…/" + "/".join(parts[-2:])


def _wire(x: dict) -> str:
    lines = [f"→ {x['method']} {x['url']}"]
    auth = (x.get("req", {}).get("headers") or {}).get("authorization")
    if auth:
        lines.append(f"  Authorization: {auth}")
    if x.get("req", {}).get("body") is not None:
        lines.append("  " + _short(x["req"]["body"]))
    lines.append(f"← {x.get('status') or x.get('error', 'no response')}")
    if x.get("res", {}).get("body") is not None:
        lines.append("  " + _short(x["res"]["body"]))
    return "\n".join(lines)


def _what(x: dict, tokens: dict) -> str:
    c = x["class"]
    spec = f" ({', '.join(c['spec'])})" if c.get("spec") else ""
    parts = [f"**{FAMILY.get(c['family'], c['family'])}** `{c['op']}`{spec}."]
    sent = sorted({tokens[r["token"]]["type"] for r in x["tokens"] if r["where"].startswith(("req", "url"))})
    got = sorted({tokens[r["token"]]["type"] for r in x["tokens"] if r["where"].startswith("res")})
    if sent:
        parts.append(f"Sends: {', '.join(sent)} token.")
    if got:
        parts.append(f"Returns: {', '.join(got)} token{'s' if len(got) > 1 else ''}.")
    if x.get("note"):
        parts.append(x["note"])
    return " ".join(parts)


def default_narrative(trace: dict) -> dict:
    tokens = {t["id"]: t for t in trace["tokens"]}
    edge = {}
    for e in trace["edges"]:
        edge.setdefault((e["from"], e["to"]), e["id"])
    steps = []
    for x in trace["exchanges"]:
        eid = edge[(x["from"], x["to"])]
        out_tok = any(r["where"].startswith(("req", "url")) for r in x["tokens"])
        back_tok = any(r["where"].startswith("res") for r in x["tokens"])
        failed = (x.get("status") or 500) >= 400
        steps.append({
            "id": f"capture-{x['id']}", "title": f"{x['method']} {short_path(x['url'])} → {x.get('status') or 'no response'}",
            "time": f"+{x['t0']:.3f} s", "exchanges": [x["id"]], "nodes": [x["from"], x["to"]],
            "segs": [[eid, 1, x["class"]["op"], "tok" if out_tok else "dat"],
                     [eid, -1, str(x.get("status") or "×"), "err" if failed else ("tok" if back_tok else "dat")]],
            "what": _what(x, tokens), "wire": _wire(x), "goals": [],
        })
    meta = trace["meta"]
    return {
        "header": {"eyebrow": f"{meta.get('target') or 'capture'} · authtrace capture", "title": meta.get("target") or "Capture",
                   "intro": " · ".join(filter(None, [f"`{meta['command']}`" if meta.get("command") else None,
                                                    f"{len(steps)} exchanges", f"redaction {meta['redaction']}" if meta.get("redaction") else None])),
                   "chip": f"{sum(1 for v in (meta.get('rules') or {}).values() if v == 'fired')} finding rule(s) fired"},
        "flows": [{"id": "capture", "name": "Captured exchanges", "short": "Capture", "steps": [s["id"] for s in steps],
                   "banner": "Generated from the capture: one step per exchange, facts only. A narrative replaces this tour."}],
        "steps": steps,
    }
