"""exchanges.jsonl → trace.json facts (PLAN.md §6). Deterministic: same capture, same trace.

  exchanges  each record, classified (decode.py), with ids x1… in time order
  tokens     one per fingerprint, typed by where it first appeared, with its decoded claims
  nodes      one per capturing process and one per server origin; roles by rule, with reasons
  edges      one per (process, origin) pair seen, plus traffic that must exist but wasn't seen
  findings   rules.py
  layout     layout.py

No narrative: render derives a one-step-per-exchange tour until Claude writes one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .decode import classify
from .layout import layout
from .rules import LOOPBACK, run_rules
from .run import exchange_lines
from .trace import facts_hash

KIND_TYPES = {"access_token": "access", "id_token": "id", "refresh_token": "refresh", "bearer": "access",
              "cookie": "session-cookie", "device_code": "code", "subject_token": "access", "logout_token": "logout"}
KEEP = ("lib", "error", "incomplete", "truncated", "note", "resource_type")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def load_records(path: Path) -> tuple[list, dict]:
    """The capture's lines, and run.json beside it if there is one."""
    path = Path(path)
    if path.is_dir():
        path = path / "exchanges.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    run = path.with_name("run.json")
    return records, (json.loads(run.read_text(encoding="utf-8")) if run.exists() else {})


def build(records: list, run: dict | None = None, target: str | None = None) -> dict:
    run = run or {}
    xs = sorted((r for r in records if r.get("kind") == "exchange"), key=lambda r: (r["t0"], r.get("id", "")))
    tokens, by_fp = [], {}
    exchanges = []
    procs, origins = {}, {}
    for i, r in enumerate(xs, 1):
        xid = f"x{i}"
        proc = r.get("proc") or "process"
        procs.setdefault(proc, {"seen_by": r.get("seen_by"), "ids": []})["ids"].append(xid)
        o = origin(r["url"])
        origins.setdefault(o, []).append(xid)
        refs = []
        for ref in r.get("tokens") or []:
            tid = _token(tokens, by_fp, ref, xid)
            refs.append({"token": tid, "where": ref["where"]})
        x = {"id": xid, "capture_id": r.get("id"), "t0": r["t0"], "t1": r.get("t1", r["t0"]), "from": proc, "to": o,
             "method": r.get("method"), "url": r["url"], "status": r.get("status"), "req": r.get("req") or {},
             "res": r.get("res") or {}, "class": classify(r), "tokens": refs, "seen_by": r.get("seen_by", "run-hooks"),
             "confidence": r.get("confidence", "observed")}
        x.update({k: r[k] for k in KEEP if k in r})
        exchanges.append(x)

    tok_by_id = {t["id"]: t for t in tokens}
    nodes = _process_nodes(procs, exchanges, tok_by_id) + _origin_nodes(origins, exchanges, tok_by_id)
    ids = {n["key"]: n["id"] for n in nodes}
    for x in exchanges:
        x["from"], x["to"] = ids[("proc", x["from"])], ids[("origin", x["to"])]
    for n in nodes:
        del n["key"]
    trace = {
        "meta": {"schema_version": 0, "tool": "authtrace", "version": __version__,
                 "captured_at": run.get("started"), "target": target or Path(run.get("cwd", "capture")).name,
                 "command": " ".join(run.get("command", [])) or None,
                 "mechanisms": run.get("mechanisms") or sorted({x["seen_by"] for x in exchanges}),
                 "redaction": run.get("redaction"), "exit_code": run.get("exit_code"), "seconds": run.get("seconds")},
        "boundaries": [{"id": "machine", "name": "Your machine", "token_zone": True, "parent": None}],
        "nodes": nodes, "edges": _edges(exchanges, nodes, tok_by_id), "exchanges": exchanges, "tokens": tokens,
        "sessions": [],  # EXTEND: MCP sessions and cookies (needs unmasked session ids or an IdP adapter, M6)
        "checks": [], "findings": [], "logs": _logs(records),
    }
    trace["findings"], trace["meta"]["rules"] = run_rules(trace)
    if not any(n.get("boundary") == "machine" for n in nodes):
        trace["boundaries"] = []  # a HAR alone: nothing ran on this machine under capture
    trace["meta"]["facts_hash"] = facts_hash(trace)
    trace["layout"] = layout(trace)
    return trace


def _logs(records: list) -> list:
    # EXTEND: the command's own output (pytest's log), once `run` can keep it redacted.
    lines = exchange_lines(records)
    if not lines:
        return []
    return [{"id": "capture", "title": f"authtrace capture: {len(lines)} exchange(s), redacted", "text": "\n".join(lines),
             "confidence": "observed"}]


def _token(tokens: list, by_fp: dict, ref: dict, xid: str) -> str:
    tok = by_fp.get(ref["fp"])
    kind_type = KIND_TYPES.get(ref.get("kind"))
    if tok is None:
        tok = {"id": f"t{len(tokens) + 1}", "fingerprint": ref["fp"], "type": kind_type or "unknown",
               "format": ref.get("format", "opaque"), "first_seen": xid, "seen_in": [], "verified": False,
               "confidence": "observed"}
        tokens.append(tok)
        by_fp[ref["fp"]] = tok
    elif tok["type"] == "unknown" and kind_type:
        tok["type"] = kind_type
    if "claims" in ref and "claims" not in tok:
        claims = ref["claims"]
        aud = claims.get("aud")
        tok.update(format="jwt", header=ref.get("header", {}), claims=claims, issuer=claims.get("iss"),
                   audience=aud if isinstance(aud, list) else ([aud] if aud else []), subject=claims.get("sub"),
                   client=claims.get("azp") or claims.get("client_id"),
                   scope=claims["scope"].split() if isinstance(claims.get("scope"), str) else [],
                   iat=claims.get("iat"), exp=claims.get("exp"), confidence="decoded")
    if xid not in tok["seen_in"]:
        tok["seen_in"].append(xid)
    return tok["id"]


def _process_nodes(procs: dict, exchanges: list, tokens: dict) -> list:
    nodes = []
    for proc, info in procs.items():
        mine = [x for x in exchanges if x["from"] == proc]
        browser = info["seen_by"] == "har"
        got_tokens = any(r["where"].startswith("res") for x in mine for r in x["tokens"])
        asked = any(x["class"]["op"].startswith("token/") for x in mine)
        why = ["captured as a running process" if not browser else "the browser that recorded the HAR"]
        if asked:
            why.append("requested tokens from a token endpoint, so it acts as an OAuth client")
        nodes.append({"key": ("proc", proc), "id": slug(proc), "name": proc, "kind": "user-agent" if browser else "client",
                      "roles": ["client"] if asked else [], "boundary": None if browser else "machine", "parent": None,
                      "holds_tokens": got_tokens, "evidence": [], "why": why, "confidence": "observed"})
    return nodes


def _origin_nodes(origins: dict, exchanges: list, tokens: dict) -> list:
    nodes, taken = [], set()
    for o, xids in origins.items():
        mine = [x for x in exchanges if x["to"] == o]
        roles, why = [], []
        if any(x["class"]["family"] in ("oauth", "oidc") and x["class"]["op"] not in ("bearer/challenge", "protected-resource-metadata")
               for x in mine) or any(r["where"].startswith("res.body") and tokens[r["token"]]["type"] in ("access", "refresh")
                                     for x in mine for r in x["tokens"]):
            roles.append("authorization-server")
            why.append("serves OAuth endpoints (" + ", ".join(sorted({x["class"]["op"] for x in mine if x["class"]["family"] in ("oauth", "oidc")})) + ")")
        bearers = [tokens[r["token"]] for x in mine for r in x["tokens"] if r["where"].endswith("headers:authorization")]
        aimed = [t for t in bearers if any(urlsplit(a).netloc == urlsplit(o).netloc for a in t.get("audience", []) if "://" in a)]
        challenged = any(x["class"]["op"] in ("bearer/challenge", "protected-resource-metadata") for x in mine)
        if bearers and (aimed or challenged):
            roles.append("resource-server")
            why.append("receives Bearer tokens" + (" whose `aud` names it" if aimed else "")
                       + (" and challenges requests without one" if challenged else ""))
        if any(x["class"]["family"] == "llm" for x in mine):
            roles.append("llm-api")
            why.append("answers model API calls")
        kind = roles[0] if roles else "unknown"
        if "llm-api" in roles:
            kind = "llm-api"
        host = urlsplit(o).hostname or ""
        nid = slug(urlsplit(o).netloc)
        while nid in taken:
            nid += "-x"
        taken.add(nid)
        families = sorted({x["class"]["family"] for x in mine})
        nodes.append({"key": ("origin", o), "id": nid, "name": urlsplit(o).netloc, "kind": kind, "roles": roles,
                      "boundary": "machine" if host in LOOPBACK else None, "parent": None, "origin": o,
                      "endpoints": sorted({urlsplit(x["url"]).path or "/" for x in mine}), "families": families,
                      "holds_tokens": False, "evidence": [],
                      "why": why or ["no rule matched; only the traffic below is known"],
                      "confidence": "inferred" if roles else "unknown"})
    return nodes


def _edges(exchanges: list, nodes: list, tokens: dict) -> list:
    edges, seen = [], {}
    for x in exchanges:
        key = (x["from"], x["to"])
        if key not in seen:
            seen[key] = {"id": f"{x['from']}--{x['to']}", "from": x["from"], "to": x["to"], "ops": set(), "bearer": False,
                         "channel": "front" if x["seen_by"] == "har" else "back", "dashed": False, "confidence": "observed"}
            edges.append(seen[key])
        e = seen[key]
        op = x["class"]["op"]
        e["ops"].add(x["class"]["family"] if x["class"]["family"] in ("mcp", "llm", "http") else op.split("/")[0])
        e["bearer"] |= any(r["where"].endswith("headers:authorization") for r in x["tokens"])
    for e in edges:
        names = {"mcp": "MCP", "llm": "model API", "http": "HTTP"}
        e["label"] = " · ".join(names.get(op, op) for op in sorted(e.pop("ops"))) + (" + Bearer" if e.pop("bearer") else "")
    return edges + _unseen_edges(exchanges, nodes, tokens)


def _unseen_edges(exchanges: list, nodes: list, tokens: dict) -> list:
    """Traffic that must exist but wasn't captured: a resource server checking RS256 tokens needs
    its issuer's keys. Drawn dashed and `inferred`, never dropped (PLAN.md §5, §7)."""
    by_origin = {n.get("origin"): n for n in nodes if n.get("origin")}
    out = []
    for rs in (n for n in nodes if "resource-server" in n["roles"]):
        issuers = sorted({tokens[r["token"]].get("issuer") for x in exchanges if x["to"] == rs["id"] for r in x["tokens"]
                          if r["where"].endswith("headers:authorization")
                          and str((tokens[r["token"]].get("header") or {}).get("alg", "")).startswith(("RS", "ES", "PS"))} - {None})
        for iss in issuers:
            as_node = by_origin.get(origin(iss))
            if not as_node or any(x["from"] == rs["id"] and x["to"] == as_node["id"] for x in exchanges):
                continue
            out.append({"id": f"{rs['id']}--{as_node['id']}--keys", "from": rs["id"], "to": as_node["id"],
                        "label": "JWKS (not captured)", "channel": "back", "dashed": True, "confidence": "inferred",
                        "why": f"{rs['name']} accepts asymmetric-signed tokens from {iss}, so it must fetch that issuer's "
                               f"public keys (or introspect). It wasn't run under capture, so that traffic isn't here. "
                               f"To confirm, start it under `authtrace run`."})
    return out
