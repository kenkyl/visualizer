"""`authtrace show`: a trace's ids at a glance, for writing a narrative without reading all of it.

  show TRACE          nodes, edges, tokens, exchanges and findings, one line each, by id
  show TRACE x3 t1    those exchanges' wire blocks (redacted, as the default tour shows them)
                      and those tokens' decoded header and claims
"""

from __future__ import annotations

import json

from .tour import _wire, short_path
from .trace import drawn


def index(trace: dict) -> str:
    parts = drawn(trace)
    meta = trace.get("meta") or {}
    run = ", ".join(f"{k} {meta[k]}" for k in ("captured_at", "exit_code", "seconds", "redaction") if meta.get(k) is not None)
    out = [f"{meta.get('target')}: `{meta.get('command') or 'no command'}`" + (f" ({run})" if run else ""), "nodes:"]
    out += [f"  {n['id']:<26} {n['name']:<26} {n['kind']:<21} {n['confidence']}" + (f"  in {n['parent']}" if n.get("parent") else "")
            for n in parts["nodes"]]
    out.append("edges:")
    out += [f"  {e['id']:<44} {e['from']} → {e['to']}: {e['label']} ({e['confidence']})" for e in parts["edges"]]
    out.append("tokens:")
    for t in trace.get("tokens") or []:
        life = f"{t['exp'] - t['iat']} s" if isinstance(t.get("exp"), int) and isinstance(t.get("iat"), int) else "?"
        aud = ",".join(t.get("audience") or []) or "-"
        out.append(f"  {t['id']:<5} {t['type']:<15} {t['format']:<7} aud {aud}  scope {' '.join(t.get('scope') or []) or '-'}"
                   f"  life {life}  seen {','.join(t.get('seen_in') or [])}")
    out.append("exchanges:")
    for x in trace.get("exchanges") or []:
        toks = ",".join(sorted({r["token"] for r in x.get("tokens") or []}))
        out.append(f"  {x['id']:<5} +{x['t0']:.3f}s {x['from']} → {x['to']}  {x['method']} {short_path(x['url'])} "
                   f"→ {x.get('status') or x.get('error', 'no response')}  {x['class']['family']} {x['class']['op']}"
                   + (f"  tokens {toks}" if toks else ""))
    out.append("findings:")
    out += [f"  {f['rule']:<22} {f['severity']:<6} {f['title']} ({', '.join(f['exchanges'])})" for f in trace.get("findings") or []]
    rules = (trace.get("meta") or {}).get("rules") or {}
    out.append("  passed: " + (", ".join(r for r, v in rules.items() if v == "passed") or "-"))
    return "\n".join(out)


def detail(trace: dict, ids: list[str]) -> str:
    xs = {x["id"]: x for x in trace.get("exchanges") or []}
    ts = {t["id"]: t for t in trace.get("tokens") or []}
    out = []
    for i in ids:
        if i in xs:
            x = xs[i]
            out.append(f"{i}: {x['class']['family']} {x['class']['op']} ({', '.join(x['class'].get('spec') or [])})"
                       + (f"  note: {x['note']}" if x.get("note") else ""))
            out.append(_wire(x))
        elif i in ts:
            t = ts[i]
            out.append(f"{i}: {t['type']} token, {t['format']}, {t['confidence']}")
            out.append(json.dumps({"header": t.get("header"), "claims": t.get("claims")}, indent=1, ensure_ascii=False))
        else:
            out.append(f"{i}: no exchange or token with that id")
        out.append("")
    return "\n".join(out).rstrip()
