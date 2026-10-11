"""Findings v0 (PLAN.md §9): deterministic rules with stable ids, severities and a fix hint.

Each rule reads the built facts and returns findings; an empty list means it passed. The trace
records which rules ran (`meta.rules`), so a pass is a fact too: "no token reached the LLM" is
worth as much as a finding. Severities: critical, warn, info, opt.
"""

from __future__ import annotations

from urllib.parse import urlsplit

LOOPBACK = {"localhost", "127.0.0.1", "::1"}
MAX_ACCESS_LIFETIME = 3600


def _finding(rule, severity, title, exchanges, detail, fix):
    return {"rule": rule, "severity": severity, "title": title, "exchanges": sorted(set(exchanges), key=lambda i: int(i[1:])),
            "detail": detail, "fix": fix, "confidence": "observed"}


def _role(t, nodes, role):
    return role in (nodes.get(t) or {}).get("roles", [])


def oauth_ropc(t, ctx):
    xs = [x["id"] for x in t["exchanges"] if x["class"]["op"] == "token/password"]
    return [_finding("OAUTH-ROPC", "info", "Password grant in use", xs,
                     "The client sends the user's password to the token endpoint. OAuth 2.1 removes this grant.",
                     "Use the authorization code flow with PKCE for users; keep the password grant to dev scripts.")] if xs else []


def mcp_version_fallback(t, ctx):
    xs = [x["id"] for x in t["exchanges"] if x["class"]["family"] == "mcp" and x.get("status") == 400
          and "protocol version" in str(x.get("res", {}).get("body", "")).lower()]
    return [_finding("MCP-VERSION-FALLBACK", "info", "MCP client probed an unsupported protocol version", xs,
                     "The server answered 400 to a newer protocol version, and the client fell back. Not an auth failure.",
                     "Nothing to fix unless the extra round trip matters; upgrade the server to skip it.")] if xs else []


def token_leak_llm(t, ctx):
    xs = [x["id"] for x in t["exchanges"] if x["class"]["family"] == "llm" and x.get("tokens")]
    return [_finding("TOKEN-LEAK-LLM", "critical", "A token reached an LLM API", xs,
                     "A known token's value appears in a request to a model API.",
                     "Keep credentials in the tool layer; send the model data and schemas only.")] if xs else []


def refresh_to_rs(t, ctx):
    refresh = {k for k, tok in ctx["tokens"].items() if tok["type"] == "refresh"}
    xs = [x["id"] for x in t["exchanges"] if _role(x["to"], ctx["nodes"], "resource-server")
          and any(r["token"] in refresh for r in x.get("tokens", []))]
    return [_finding("REFRESH-TO-RS", "critical", "A refresh token was sent to a resource server", xs,
                     "Refresh tokens are for the authorization server only.",
                     "Send the access token; keep the refresh token with the client.")] if xs else []


def aud_mismatch(t, ctx):
    xs = []
    for x in t["exchanges"]:
        for r in x.get("tokens", []):
            tok = ctx["tokens"][r["token"]]
            if not r["where"].endswith("authorization") or tok["type"] != "access" or not tok.get("audience"):
                continue
            target = urlsplit(x["url"])
            if not any(urlsplit(a).netloc == target.netloc for a in tok["audience"] if "://" in a):
                xs.append(x["id"])
    return [_finding("AUD-MISMATCH", "warn", "Bearer token sent to a host not in its audience", xs,
                     "The token's `aud` doesn't name the server it was sent to: it should be rejected, and if it's accepted, worse.",
                     "Request the token for this resource (RFC 8707 `resource`, or an audience mapper).")] if xs else []


def at_lifetime(t, ctx):
    long = {k for k, tok in ctx["tokens"].items() if tok["type"] == "access" and isinstance(tok.get("exp"), (int, float))
            and isinstance(tok.get("iat"), (int, float)) and tok["exp"] - tok["iat"] > MAX_ACCESS_LIFETIME}
    xs = [x["id"] for x in t["exchanges"] if any(r["token"] in long for r in x.get("tokens", []))]
    return [_finding("AT-LIFETIME", "warn", f"Access token lives longer than {MAX_ACCESS_LIFETIME // 60} minutes", xs,
                     "A leaked access token stays usable until it expires; local validators can't see revocation.",
                     "Shorten the access token lifetime and rely on refresh.")] if xs else []


def jwt_alg(t, ctx):
    weak = {k for k, tok in ctx["tokens"].items() if tok["type"] in ("access", "id")
            and str((tok.get("header") or {}).get("alg", "")).lower() in ("none", "hs256", "hs384", "hs512")}
    xs = [x["id"] for x in t["exchanges"] if any(r["token"] in weak for r in x.get("tokens", []))]
    return [_finding("JWT-ALG", "warn", "Access or ID token signed with `none` or a shared secret", xs,
                     "With `alg: none` anyone can mint the token; with HS* anyone holding the key (often every client) can.",
                     "Sign with an asymmetric algorithm (RS256, ES256) and publish the key in a JWKS.")] if xs else []


def http_tokens(t, ctx):
    xs = [x["id"] for x in t["exchanges"] if x.get("tokens") and x["url"].startswith("http://")
          and (urlsplit(x["url"]).hostname or "") not in LOOPBACK]
    return [_finding("HTTP-TOKENS", "warn", "Tokens sent over plain HTTP to a non-loopback host", xs,
                     "Anyone on the network path can read them.", "Use HTTPS.")] if xs else []


def token_in_url(t, ctx):
    xs = [x["id"] for x in t["exchanges"] if any(r["where"].startswith("url") for r in x.get("tokens", []))]
    return [_finding("TOKEN-IN-URL", "warn", "A token travels in a URL", xs,
                     "URLs end up in history, logs and Referer headers.",
                     "Send tokens in headers or POST bodies (an `id_token_hint` on logout is the usual exception).")] if xs else []


def host_alias(t, ctx):
    hosts = {(urlsplit(x["url"]).hostname or "") for x in t["exchanges"]}
    mixed = {"localhost", "127.0.0.1"} <= hosts
    xs = [x["id"] for x in t["exchanges"] if (urlsplit(x["url"]).hostname or "") in LOOPBACK] if mixed else []
    return [_finding("HOST-ALIAS", "info", "Local services are addressed as both localhost and 127.0.0.1", xs[:2],
                     "`iss` and `aud` are compared as exact strings, so the same service under two names breaks validation.",
                     "Pick one host name per service and use it in config, tokens and redirect URIs.")] if xs else []


# EXTEND: COOKIE-FLAGS, OAUTH-NO-PKCE, OIDC-NO-NONCE (M5, from browser captures), OPT-* rules,
# SCOPE-UNENFORCED (needs code evidence, so Claude adds it as inferred).
RULES = {
    "OAUTH-ROPC": oauth_ropc, "MCP-VERSION-FALLBACK": mcp_version_fallback, "TOKEN-LEAK-LLM": token_leak_llm,
    "REFRESH-TO-RS": refresh_to_rs, "AUD-MISMATCH": aud_mismatch, "AT-LIFETIME": at_lifetime, "JWT-ALG": jwt_alg,
    "HTTP-TOKENS": http_tokens, "TOKEN-IN-URL": token_in_url, "HOST-ALIAS": host_alias,
}


def run_rules(trace: dict) -> tuple[list, dict]:
    ctx = {"tokens": {tok["id"]: tok for tok in trace["tokens"]}, "nodes": {n["id"]: n for n in trace["nodes"]}}
    findings, results = [], {}
    for rule_id, rule in RULES.items():
        found = rule(trace, ctx)
        findings += found
        results[rule_id] = "fired" if found else "passed"
    return findings, results
