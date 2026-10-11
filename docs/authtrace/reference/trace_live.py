"""Scratch tracer: the same flow as tests/test_live.py, printing what crosses each boundary.

Imports the project's modules unchanged; only wraps them from the outside. Tokens are redacted.
"""

import asyncio
import base64
import json
import os
import time

import httpx
import httpx2

import memes_agent.tools as tools_mod
from memes_agent.config import Settings
from memes_agent.llm import ClaudeLLM
from memes_agent.loop import run_agent

T0 = time.perf_counter()


def log(tag, msg=""):
    print(f"[{time.perf_counter() - T0:6.3f}s] {tag:<10} {msg}")


def b64json(part):
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def redact(text):
    return text if len(text) < 40 else text[:12] + "…(redacted)"


# --- 1. Token from Keycloak (same password grant as dev_token) ---
# MEMES_TEST_PASSWORD: dev-only test user password from the memes-agent README.
settings = Settings()
r = httpx.post(
    f"{settings.oidc_issuer}/protocol/openid-connect/token",
    data={"grant_type": "password", "client_id": "memes-dev", "username": "dummyuser1",
          "password": os.environ["MEMES_TEST_PASSWORD"], "scope": "openid"},
)
log("KEYCLOAK", f"POST /token -> {r.status_code}")
tok = r.json()
access = tok["access_token"]
hdr, payload, _ = access.split(".")
log("TOKEN", f"header={b64json(hdr)}")
claims = b64json(payload)
keep = ["iss", "aud", "sub", "azp", "scope", "exp", "iat", "preferred_username", "typ"]
log("TOKEN", "claims=" + json.dumps({k: claims.get(k) for k in keep}))
log("TOKEN", f"lifetime={claims['exp'] - claims['iat']}s, keys in response={sorted(tok)}")
jwks = httpx.get(f"{settings.oidc_issuer}/protocol/openid-connect/certs").json()
log("JWKS", "kids=" + json.dumps([(k["kid"], k.get("alg"), k.get("use")) for k in jwks["keys"]]))


# --- 2. Wrap the MCP HTTP client to see each JSON-RPC message ---
async def on_request(req):
    body = req.content.decode() if req.content else ""
    try:
        m = json.loads(body)
        body = json.dumps({k: m[k] for k in ("method", "id", "params") if k in m})
    except Exception:
        pass
    auth = req.headers.get("authorization", "")
    sess = req.headers.get("mcp-session-id", "-")
    proto = req.headers.get("mcp-protocol-version", "-")
    log("MCP ->", f"{req.method} auth={'Bearer …' if auth else 'NONE'} session={sess[:8]} "
        f"proto={proto} {body[:400]}")


async def on_response(resp):
    extra = ""
    if resp.status_code >= 400:
        await resp.aread()
        extra = resp.text[:400]
    www = resp.headers.get("www-authenticate")
    log("MCP <-", f"{resp.status_code} ct={resp.headers.get('content-type')} {extra}"
        + (f" WWW-Authenticate={www}" if www else ""))


class TracingClient(httpx2.AsyncClient):
    def __init__(self, *a, **kw):
        super().__init__(*a, event_hooks={"request": [on_request], "response": [on_response]}, **kw)


tools_mod.httpx2 = type("M", (), {"AsyncClient": TracingClient})


class TracingGateway(tools_mod.MCPToolGateway):
    async def schemas(self):
        s = await super().schemas()
        log("GATEWAY", "schemas=" + json.dumps(s)[:600])
        return s

    async def call(self, name, args):
        log("GATEWAY", f"call {name} {args}")
        res = await super().call(name, args)
        log("GATEWAY", f"result is_error={res.is_error} data={res.data}")
        return res


class TracingLLM(ClaudeLLM):
    n = 0

    async def __call__(self, messages, tools, system):
        TracingLLM.n += 1
        log("CLAUDE ->", f"turn {self.n}: {len(messages)} messages, tools={[t['name'] for t in tools]}")
        log("", "last message=" + json.dumps(messages[-1])[:500])
        turn = await super().__call__(messages, tools, system)
        log("CLAUDE <-", f"stop_reason={turn.stop_reason} content=" + json.dumps(turn.content)[:600])
        return turn


async def main():
    async with TracingGateway(settings.memes_mcp_url, access) as gw:
        result = await run_agent(["chaotic", "nerdy", "sleepy"], gw, TracingLLM(settings))
    log("RESULT", f"steps={result.steps} text={result.text!r}")
    log("RESULT", f"meme={result.meme}")


asyncio.run(main())

# --- 3. The negative case: no token at all ---
r = httpx.post(settings.memes_mcp_url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
log("NO TOKEN", f"{r.status_code} WWW-Authenticate={r.headers.get('www-authenticate')}")
prm = httpx.get("http://127.0.0.1:8001/.well-known/oauth-protected-resource/mcp")
log("PRM", f"{prm.status_code} {prm.text[:300]}")
print("access_token prefix (redacted):", redact(access))
