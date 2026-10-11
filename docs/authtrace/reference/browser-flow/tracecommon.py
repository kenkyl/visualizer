"""Shared redaction + JSONL emit for the scratch tracers. Stdlib only. Never writes raw secrets."""

import base64
import hashlib
import json
import os
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MASK_PARAMS = {"code", "state", "nonce", "code_challenge", "session_state", "session_code",
               "tab_id", "execution", "client_data"}
SECRET_FIELDS = {"client_secret", "password", "code_verifier", "refresh_token", "client_assertion"}
TOKEN_KEYS = {"access_token", "id_token", "refresh_token"}

_out = None
KNOWN_TOKENS: dict[str, str] = {}  # raw -> fp, process memory only (never written)


def out():
    global _out
    if _out is None:
        _out = open(os.environ["TRACE_OUT"], "a", buffering=1)
    return _out


def emit(kind, **kw):
    rec = {"t": round(time.time(), 4), "proc": os.environ.get("TRACE_PROC", "?"), "kind": kind, **kw}
    out().write(json.dumps(rec, default=str) + "\n")


def fp(v: str) -> str:
    return "fp:" + hashlib.sha256(v.encode()).hexdigest()[:8]


def mask(v: str, keep: int = 6) -> str:
    return f"{v[:keep]}…({len(v)})" if v else v


def b64json(part: str):
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def jwt_view(tok: str) -> dict:
    KNOWN_TOKENS.setdefault(tok, fp(tok))
    parts = tok.split(".")
    if len(parts) == 3:
        try:
            return {"jwt": True, "fp": fp(tok), "header": b64json(parts[0]), "claims": b64json(parts[1])}
        except Exception:
            pass
    return {"opaque": True, "fp": fp(tok), "len": len(tok)}


def redact_url(url: str) -> str:
    s = urlsplit(url)
    if not s.query:
        return url
    q = []
    for k, v in parse_qsl(s.query, keep_blank_values=True):
        if k in MASK_PARAMS:
            v = mask(v)
        elif k == "id_token_hint":
            v = f"<id_token {fp(v)}>"
        q.append((k, v))
    return urlunsplit((s.scheme, s.netloc, s.path, urlencode(q, safe="/:<> "), s.fragment))


def redact_form(text: str) -> dict:
    res = {}
    for k, v in parse_qsl(text, keep_blank_values=True):
        if k in SECRET_FIELDS:
            res[k] = "***" if k in {"password", "client_secret"} else mask(v)
        elif k == "code":
            res[k] = mask(v)
        else:
            res[k] = v
    return res


def walk(obj):
    if isinstance(obj, dict):
        r = {}
        for k, v in obj.items():
            if k in TOKEN_KEYS and isinstance(v, str):
                r[k] = jwt_view(v)
            elif k in SECRET_FIELDS and isinstance(v, str):
                r[k] = mask(v)
            else:
                r[k] = walk(v)
        return r
    if isinstance(obj, list):
        return [walk(x) for x in obj]
    if isinstance(obj, str) and len(obj) > 1500:
        return obj[:1500] + f"…(+{len(obj) - 1500})"
    return obj


def body_view(ctype: str, raw: bytes | str | None):
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode()
        except UnicodeDecodeError:
            return {"binary": len(raw)}
    if not raw:
        return None
    ctype = (ctype or "").lower()
    if "json" in ctype or raw[:1] in "{[":
        try:
            return walk(json.loads(raw))
        except Exception:
            pass
    if "x-www-form-urlencoded" in ctype:
        return redact_form(raw)
    return {"text_len": len(raw), "ctype": ctype.split(";")[0]}


def token_refs(*texts: str) -> list[dict]:
    """Which known tokens appear in these strings (fingerprints only)."""
    hits = []
    for raw, f in KNOWN_TOKENS.items():
        for i, t in enumerate(texts):
            if t and raw in t:
                hits.append({"fp": f, "where": i})
    return hits


def decode_starlette_session(value: str):
    """Starlette SessionMiddleware cookie = b64(json) '.' timestamp '.' signature (signed, not encrypted)."""
    try:
        data = json.loads(base64.b64decode(value.split(".")[0]))
    except Exception:
        return None
    red = {}
    for k, v in data.items():
        if k.startswith("_state_"):
            name, state = k.rsplit("_", 1)
            d = v.get("data", {}) if isinstance(v, dict) else {}
            red[f"{name}_{mask(state)}"] = {
                "data": {kk: (mask(vv) if kk in {"code_verifier", "nonce"} else
                              redact_url(vv) if kk == "url" else vv) for kk, vv in d.items()},
                "exp": v.get("exp") if isinstance(v, dict) else None,
            }
        else:
            red[k] = v
    return red


def cookie_header_view(header: str | None):
    if not header:
        return None
    res = {}
    for part in header.split(";"):
        if "=" not in part:
            continue
        k, v = part.strip().split("=", 1)
        res[k] = {"session": decode_starlette_session(v)} if k == "session" else fp(v)
    return res


def set_cookie_view(header: str):
    first, *attrs = [p.strip() for p in header.split(";")]
    name, _, value = first.partition("=")
    view = {"name": name, "attrs": attrs}
    if name == "session":
        view["session"] = decode_starlette_session(value)
    elif value.count(".") == 2 and value.startswith("ey"):
        view["jwt"] = jwt_view(value)
    else:
        view["value"] = fp(value) if value else ""
    return view
