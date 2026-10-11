"""Run memes-web or memes-mcp with outside-in tracing. No repo files are modified.

usage: TRACE_OUT=… TRACE_PROC=web|mcp uv run python traced.py web|mcp   (cwd = memes-agent repo)
"""

import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import httpx  # noqa: E402
import httpx2  # noqa: E402

from tracecommon import (  # noqa: E402
    body_view, cookie_header_view, emit, fp, jwt_view, redact_url, set_cookie_view, token_refs,
)

MODE = sys.argv[1]


def describe_request(request):
    try:
        raw = request.content
    except Exception:
        raw = None
    h = request.headers
    auth = h.get("authorization", "")
    text = raw.decode(errors="replace") if isinstance(raw, bytes) else ""
    view = {
        "method": request.method,
        "url": redact_url(str(request.url)),
        "auth": (f"Bearer {jwt_view(auth[7:])['fp']}" if auth.startswith("Bearer ") else
                 "Basic ***" if auth.startswith("Basic ") else None),
        "headers": {k: v for k, v in h.items() if k.lower() in
                    {"content-type", "mcp-session-id", "mcp-protocol-version", "accept"}},
        "body": body_view(h.get("content-type", ""), raw),
        # Token-leak scan: known tokens anywhere other than the Authorization header.
        "token_refs_outside_auth": token_refs(str(request.url), text),
    }
    if "x-api-key" in h:
        view["api_key"] = "present (redacted)"
    return view


def patch_client(mod, label):
    orig = mod.AsyncClient.send

    async def send(self, request, *a, **kw):
        t0 = time.time()
        req = describe_request(request)
        try:
            resp = await orig(self, request, *a, **kw)
        except Exception as e:
            emit("http.out", lib=label, t0=t0, t1=time.time(), req=req, error=repr(e))
            raise
        streamed = kw.get("stream", False)
        rb = None if streamed else body_view(resp.headers.get("content-type", ""), resp.content)
        emit("http.out", lib=label, t0=t0, t1=time.time(), req=req, status=resp.status_code,
             res={"headers": {k: v for k, v in resp.headers.items() if k.lower() in
                              {"content-type", "mcp-session-id", "www-authenticate", "location"}},
                  "body": rb, "streamed": streamed})
        return resp

    mod.AsyncClient.send = send


patch_client(httpx, "httpx")
patch_client(httpx2, "httpx2")


class Inbound:
    """Pure ASGI middleware: logs each inbound request and the cookies/redirects it produces."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        t0 = time.time()
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        rec = {"method": scope["method"], "path": scope["path"],
               "query": redact_url("http://x/?" + scope["query_string"].decode()).split("?", 1)[-1]
               if scope["query_string"] else "",
               "cookies": cookie_header_view(headers.get("cookie"))}
        res = {"status": None, "set_cookie": [], "location": None}

        async def wrapped(msg):
            if msg["type"] == "http.response.start":
                res["status"] = msg["status"]
                for k, v in msg.get("headers", []):
                    k, v = k.decode().lower(), v.decode()
                    if k == "set-cookie":
                        res["set_cookie"].append(set_cookie_view(v))
                    elif k == "location":
                        res["location"] = redact_url(v)
            await send(msg)

        await self.app(scope, receive, wrapped)
        emit("http.in", t0=t0, t1=time.time(), req=rec, res=res)


if MODE == "web":
    import uvicorn

    from memes_agent import auth, llm, store, tools, web

    def wrap(cls, name, before=None, after=None):
        orig = getattr(cls, name)
        is_async = name in {"__call__", "schemas", "call", "__aenter__"}

        if is_async:
            async def f(self, *a, **kw):
                if before:
                    before(self, *a, **kw)
                r = await orig(self, *a, **kw)
                if after:
                    after(self, r, *a, **kw)
                return r
        else:
            def f(self, *a, **kw):
                if before:
                    before(self, *a, **kw)
                r = orig(self, *a, **kw)
                if after:
                    after(self, r, *a, **kw)
                return r
        setattr(cls, name, f)

    def tok_summary(t):
        return {"access": jwt_view(t["access_token"])["fp"], "id": jwt_view(t["id_token"])["fp"]
                if t.get("id_token") else None,
                "refresh": jwt_view(t["refresh_token"])["fp"] if t.get("refresh_token") else None,
                "expires_at": t.get("expires_at"), "keys": sorted(t)}

    wrap(auth.TokenStore, "put", before=lambda s, sub, t: emit("tokenstore.put", sub=sub, **tok_summary(t)))
    wrap(auth.TokenStore, "access_token", after=lambda s, r, sub, *a, **k: emit(
        "tokenstore.get", sub=sub, result=fp(r) if r else None,
        seconds_left=round(s._tokens.get(sub, {}).get("expires_at", 0) - time.time())))
    wrap(auth.TokenStore, "pop", after=lambda s, r, sub: emit("tokenstore.pop", sub=sub, removed=bool(r)))
    wrap(store.ProfileStore, "get", after=lambda s, r, sub: emit(
        "profile.get", sub=sub, found=bool(r), words=r.words if r else None))
    wrap(store.ProfileStore, "save", before=lambda s, p: emit("profile.save", sub=p.sub, words=p.words,
                                                               name=p.name, email=p.email))
    wrap(tools.MCPToolGateway, "__aenter__", before=lambda s: emit(
        "gateway.open", url=s._url, token=fp(s._token)))
    wrap(tools.MCPToolGateway, "schemas", after=lambda s, r: emit("gateway.schemas", schemas=r))
    wrap(tools.MCPToolGateway, "call", before=lambda s, n, a: emit("gateway.call", name=n, args=a),
         after=lambda s, r, n, a: emit("gateway.result", is_error=r.is_error, data=r.data,
                                       content=r.content[:300]))
    wrap(llm.ClaudeLLM, "__call__",
         before=lambda s, m, t, sy: emit("llm.request", n_messages=len(m), last=m[-1],
                                         tools=[x["name"] for x in t]),
         after=lambda s, r, m, t, sy: emit("llm.response", stop_reason=r.stop_reason, content=r.content))

    from authlib.integrations.starlette_client import StarletteOAuth2App

    orig_aat = StarletteOAuth2App.authorize_access_token

    async def aat(self, request, **kw):
        t = await orig_aat(self, request, **kw)
        emit("authlib.callback", userinfo=t.get("userinfo"), scope=t.get("scope"),
             token_type=t.get("token_type"), expires_in=t.get("expires_in"),
             refresh_expires_in=t.get("refresh_expires_in"))
        return t

    StarletteOAuth2App.authorize_access_token = aat

    app = web.create_app()
    emit("start", mode="web", db=os.environ.get("DB_PATH"))
    uvicorn.run(Inbound(app), host="127.0.0.1", port=8000, log_level="warning")

else:
    from arcade_mcp_server.resource_server import ResourceServerAuth
    from arcade_mcp_server.resource_server.validators.jwks import JWKSTokenValidator

    orig_v = ResourceServerAuth.validate_token

    async def validate(self, token):
        t0 = time.time()
        try:
            owner = await orig_v(self, token)
        except Exception as e:
            emit("mcp.validate", token=fp(token), ok=False, error=f"{type(e).__name__}: {e}", t0=t0)
            raise
        emit("mcp.validate", token=fp(token), ok=True, user_id=owner.user_id,
             client_id=getattr(owner, "client_id", None), t0=t0, ms=round((time.time() - t0) * 1000, 1))
        return owner

    ResourceServerAuth.validate_token = validate

    orig_f = JWKSTokenValidator._fetch_jwks

    async def fetch(self):
        hit = bool(self._jwks_cache and (time.time() - self._cache_timestamp) < self._cache_ttl)
        r = await orig_f(self)
        emit("mcp.jwks", cache="hit" if hit else "fetched", ttl=self._cache_ttl,
             kids=[(k.get("kid", "")[:8], k.get("alg"), k.get("use")) for k in r.get("keys", [])])
        return r

    JWKSTokenValidator._fetch_jwks = fetch

    class ToJsonl(logging.Handler):
        def emit(self, record):  # noqa: A003
            emit("mcp.log", logger=record.name, msg=record.getMessage())

    lg = logging.getLogger("memes_mcp")
    lg.setLevel(logging.INFO)
    lg.addHandler(ToJsonl())

    from memes_mcp import server

    emit("start", mode="mcp")
    server.main()
