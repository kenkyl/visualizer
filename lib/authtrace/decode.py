"""Classify each captured exchange: which protocol it speaks and which operation it is (PLAN.md §8).

A decoder takes an exchange record (already redacted) and returns {family, op, spec} or None. The
first decoder that answers wins, so put specific ones first. Token values are already decoded
into the record's `tokens` at capture time; here we only read protocol fields.
"""

from __future__ import annotations

from urllib.parse import urlsplit

LLM_HOSTS = {"api.anthropic.com"}  # EXTEND: other model APIs, each with its own decoder

GRANT_SPECS = {
    "password": ["RFC 6749 §4.3", "removed in OAuth 2.1"],
    "authorization_code": ["RFC 6749 §4.1", "RFC 7636"],
    "client_credentials": ["RFC 6749 §4.4"],
    "refresh_token": ["RFC 6749 §6"],
    "urn:ietf:params:oauth:grant-type:token-exchange": ["RFC 8693"],
    "urn:ietf:params:oauth:grant-type:device_code": ["RFC 8628"],
}


def path(x: dict) -> str:
    return urlsplit(x.get("url") or "").path


def header(x: dict, side: str, name: str) -> str:
    v = ((x.get(side) or {}).get("headers") or {}).get(name, "")
    return ", ".join(v) if isinstance(v, list) else v


def body(x: dict, side: str):
    return (x.get(side) or {}).get("body")


def rpc(b):
    """The JSON-RPC message in a body: plain JSON, or the first event of an SSE answer."""
    if isinstance(b, list) and b and isinstance(b[0], dict) and isinstance(b[0].get("data"), dict):
        b = b[0]["data"]
    return b if isinstance(b, dict) and b.get("jsonrpc") == "2.0" else None


def oauth_token(x):
    b = body(x, "req")
    if x.get("method") != "POST" or not path(x).endswith("/token") or not isinstance(b, dict):
        return None
    grant = b.get("grant_type", "?")
    short = grant.rsplit(":", 1)[-1] if grant.startswith("urn:") else grant
    return {"family": "oauth", "op": f"token/{short}", "spec": GRANT_SPECS.get(grant, ["RFC 6749"])}


def oauth_revoke(x):
    if x.get("method") == "POST" and path(x).endswith("/revoke"):
        return {"family": "oauth", "op": "revoke", "spec": ["RFC 7009"]}
    if x.get("method") == "POST" and path(x).endswith("/introspect"):
        return {"family": "oauth", "op": "introspect", "spec": ["RFC 7662"]}
    return None


def well_known(x):
    p = path(x)
    if "/.well-known/oauth-protected-resource" in p:
        return {"family": "oauth", "op": "protected-resource-metadata", "spec": ["RFC 9728"]}
    if p.endswith("/.well-known/openid-configuration"):
        return {"family": "oidc", "op": "discovery", "spec": ["OIDC Discovery"]}
    if "/.well-known/oauth-authorization-server" in p:
        return {"family": "oauth", "op": "discovery", "spec": ["RFC 8414"]}
    b = body(x, "res")
    if p.endswith(("/certs", "/jwks", "/jwks.json")) or (isinstance(b, dict) and isinstance(b.get("keys"), list)):
        return {"family": "oauth", "op": "jwks", "spec": ["RFC 7517"]}
    return None


def mcp(x):
    msg = rpc(body(x, "req"))
    is_mcp = msg is not None or header(x, "req", "mcp-session-id") or header(x, "req", "mcp-protocol-version")
    if not is_mcp:
        return None
    version = header(x, "req", "mcp-protocol-version")
    spec = [f"MCP {version}" if version else "MCP"]
    if msg and msg.get("method"):
        return {"family": "mcp", "op": msg["method"], "spec": spec}
    if x.get("method") == "DELETE":
        return {"family": "mcp", "op": "session/end", "spec": spec}
    if x.get("method") == "GET":
        return {"family": "mcp", "op": "stream", "spec": spec}
    return {"family": "mcp", "op": "message", "spec": spec}


def llm(x):
    host = urlsplit(x.get("url") or "").hostname or ""
    anthropic_shaped = path(x).endswith("/v1/messages") and (header(x, "req", "anthropic-version") or header(x, "req", "x-api-key"))
    if host not in LLM_HOSTS and not anthropic_shaped:
        return None
    res = body(x, "res")
    stop = res.get("stop_reason") if isinstance(res, dict) else None
    return {"family": "llm", "op": f"messages/{stop}" if stop else "messages", "spec": []}


def bearer_challenge(x):
    if x.get("status") == 401 and header(x, "res", "www-authenticate").lower().startswith("bearer"):
        return {"family": "oauth", "op": "bearer/challenge", "spec": ["RFC 6750 §3", "RFC 9728 §5.1"]}
    return None


# EXTEND: OAuth /authorize and callbacks (from HARs), userinfo, logout, SAML (M8), Arcade.
DECODERS = [oauth_token, oauth_revoke, well_known, bearer_challenge, mcp, llm]


def classify(x: dict) -> dict:
    for decode in DECODERS:
        found = decode(x)
        if found:
            return found
    return {"family": "http", "op": f"{x.get('method', '?').lower()}", "spec": []}
