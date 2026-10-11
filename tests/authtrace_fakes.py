"""A small stand-in for memes-agent, for capture tests: an IdP, an MCP-style resource server and an
LLM endpoint on one local port, plus client scripts that drive it the way memes-agent does.

Tokens are minted per run (random signatures), so no token literal lives in the repo.
"""

import base64
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLIENT_ID, CLIENT_SECRET, PASSWORD = "demo-web", "s3cret-" + "value", "hunter" + "22"


def b64(data) -> str:
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def jwt(claims: dict) -> str:
    return f"{b64({'alg': 'RS256', 'typ': 'JWT', 'kid': 'test-kid'})}.{b64(claims)}.{b64(secrets.token_bytes(64))}"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    issued: dict = {}

    def log_message(self, *a):
        pass

    def base(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def reply(self, status, body=b"", ctype="application/json", headers=()):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_POST(self):  # noqa: N802
        body = self.body()
        if self.path.endswith("/token"):
            if self.headers.get("Authorization", "").split(" ")[0] != "Basic":
                return self.reply(401, {"error": "invalid_client"})
            iss = f"{self.base()}/realms/demo"
            access = jwt({"iss": iss, "aud": f"{self.base()}/mcp", "sub": "user-123", "azp": CLIENT_ID,
                          "scope": "openid memes:read", "iat": 1791005772, "exp": 1791006672})
            idt = jwt({"iss": iss, "aud": CLIENT_ID, "sub": "user-123", "nonce": secrets.token_urlsafe(24),
                       "at_hash": secrets.token_urlsafe(16), "email": "dummy@example.com"})
            refresh = secrets.token_urlsafe(40)
            Handler.issued = {"access": access, "id": idt, "refresh": refresh}
            return self.reply(200, {"access_token": access, "id_token": idt, "refresh_token": refresh,
                                    "token_type": "Bearer", "expires_in": 900})
        if self.path == "/mcp":
            if not self.headers.get("Authorization", "").startswith("Bearer "):
                return self.reply(401, {"error": "unauthorized"}, headers=[
                    ("WWW-Authenticate", f'Bearer resource_metadata="{self.base()}/.well-known/oauth-protected-resource/mcp"')])
            msg = json.loads(body or b"{}")
            event = json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"protocolVersion": "2025-11-25"}})
            return self.reply(200, f"event: message\ndata: {event}\n\n".encode(), "text/event-stream",
                              [("Mcp-Session-Id", secrets.token_hex(16))])
        if self.path == "/v1/messages":
            return self.reply(200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]})
        self.reply(404, {"error": "not found"})

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/.well-known/oauth-protected-resource"):
            return self.reply(200, {"resource": f"{self.base()}/mcp", "authorization_servers": [f"{self.base()}/realms/demo"]})
        if self.path == "/mcp":  # a chunked event stream, as MCP's GET stream is
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for i in range(3):
                chunk = f"event: message\ndata: {json.dumps({'n': i})}\n\n".encode()
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        if self.path == "/login":
            code, state = secrets.token_urlsafe(32), secrets.token_urlsafe(16)
            return self.reply(302, b"", "text/plain", [("Location", f"/callback?code={code}&state={state}"),
                                                       ("Set-Cookie", f"session={secrets.token_urlsafe(30)}; HttpOnly; SameSite=Lax")])
        if self.path.startswith("/callback"):
            return self.reply(200, b"<html>signed in</html>", "text/html")
        self.reply(404, {"error": "not found"})


class FakeSystem:
    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    @property
    def issued(self) -> dict:
        return dict(Handler.issued)


# The memes-agent flow, through urllib (http.client): token, no-token probe, PRM, MCP initialize,
# the GET event stream, an LLM call that leaks the refresh token, and a child process's login.
URLLIB_CLIENT = r'''
import base64, json, subprocess, sys, urllib.error, urllib.request
base, code = sys.argv[1], int(sys.argv[2])

def call(method, path, body=None, headers=None, ctype="application/json"):
    data = body if body is None or isinstance(body, bytes) else (json.dumps(body).encode() if ctype == "application/json" else body.encode())
    req = urllib.request.Request(base + path, data=data, method=method, headers={**({"Content-Type": ctype} if data else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        e.read()
        return e.code, b""

basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
_, tok = call("POST", "/realms/demo/protocol/openid-connect/token",
              f"grant_type=password&username=dummy&password={PASSWORD}&scope=openid",
              {"Authorization": "Basic " + basic}, "application/x-www-form-urlencoded")
tok = json.loads(tok)
bearer = {"Authorization": "Bearer " + tok["access_token"]}
call("POST", "/mcp", {"jsonrpc": "2.0", "method": "initialize", "id": 0})
call("GET", "/.well-known/oauth-protected-resource/mcp")
call("POST", "/mcp", {"jsonrpc": "2.0", "method": "initialize", "id": 1}, bearer)
call("GET", "/mcp", headers=bearer)
call("POST", "/v1/messages", {"messages": [{"role": "user", "content": "debug: " + tok["refresh_token"]}]},
     {"x-api-key": "sk-test-" + "0" * 40})
subprocess.run([sys.executable, "-c", "import urllib.request,sys; urllib.request.urlopen(sys.argv[1] + '/login').read()", base], check=True)
sys.exit(code)
'''

# The same token call through httpx's auth flow (auth= adds the Basic header after send() is
# called: the 2026-10-03 gap), then an async streamed GET, through httpx and httpx2 if present.
HTTPX_CLIENT = r'''
import asyncio, importlib.util, sys
base = sys.argv[1]
mods = [m for m in ("httpx", "httpx2") if importlib.util.find_spec(m)]

async def main(mod):
    lib = __import__(mod)
    with lib.Client(auth=(CLIENT_ID, CLIENT_SECRET)) as c:
        tok = c.post(base + "/realms/demo/protocol/openid-connect/token",
                     data={"grant_type": "client_credentials"}).json()
    async with lib.AsyncClient(headers={"Authorization": "Bearer " + tok["access_token"]}) as c:
        async with c.stream("GET", base + "/mcp") as r:
            async for _ in r.aiter_bytes():
                pass

for m in mods:
    asyncio.run(main(m))
print(",".join(mods))
'''


def client_source(template: str) -> str:
    return f"CLIENT_ID, CLIENT_SECRET, PASSWORD = {CLIENT_ID!r}, {CLIENT_SECRET!r}, {PASSWORD!r}\n" + template
