# browser-flow: the observed full v1 run (2026-10-03)

The second memes-agent capture: headless Chrome through login → onboarding → meme → logout, with
traced copies of memes-web and memes-mcp. Published as https://claude.ai/artifact/3mfsQgQaabgLp4e91Qy8bc
(`memes-agent-browser-flow.html` here is its source). Plan context: `../../PLAN.md` §1, §5, Appendix A2.

## Files

| File | What it is | Seeds (PLAN milestone) |
|---|---|---|
| `tracecommon.py` | Stdlib-only redaction and JSONL emit: token fingerprints, URL/form masking, JWT decode, Starlette session-cookie decode, `Set-Cookie` parsing, an in-memory token registry for leak scans | `redact.py`, `decode/jwt.py`, token registry (M2/M3) |
| `traced.py` | Runs memes-web or memes-mcp with outside-in hooks: patches `httpx`/`httpx2` `AsyncClient.send`, adds a pure-ASGI inbound logger, and wraps `TokenStore`, `ProfileStore`, `MCPToolGateway`, `ClaudeLLM`, authlib's callback, Arcade's `validate_token` and JWKS fetch | `capture/hooks/` for `authtrace run` (M2) |
| `drive.py` | Playwright with the **installed Chrome** (`channel="chrome"`, no browser download) through the full flow. Logs browser network events, cookie-jar snapshots, and Keycloak admin-API session snapshots | Browser capture (M5), Keycloak sessions adapter (M6) |
| `capture/*.jsonl` | Raw redacted event streams: `browser`, `web`, `mcp`. One-time claim values (`nonce`, `state_checker`, `at_hash`) were masked after the fact (see "Known gaps") | Golden fixtures for M2/M3/M5 |
| `capture/timeline.txt` | The three streams merged by timestamp, with the Keycloak snapshots marked `observer` | Expected "build" output |
| `memes-agent-browser-flow.html` | The artifact: same UI as `../memes-agent-flow.html`, plus phases, a session-layers matrix, a four-way token table and a "what this run proved" panel | UI spec additions (M1, M6) |

## How it was run (none of this changes the target repo)

```bash
# in ~/Workspace/claude-code/memes-agent, with Keycloak up and the user's own servers stopped
TRACE_OUT=…/mcp.jsonl TRACE_PROC=mcp uv run python traced.py mcp &
DB_PATH=…/profiles.db TRACE_OUT=…/web.jsonl TRACE_PROC=web uv run python traced.py web &
# from this folder (separate env, so the target's lockfile is untouched)
TRACE_OUT=…/browser.jsonl TRACE_PROC=browser uvx --with playwright --with httpx python drive.py
```

- Ports 8000/8001 were required: Keycloak's registered `redirect_uri` is `http://localhost:8000/auth/callback`,
  so the traced memes-web had to replace the running one, not run alongside it.
- `DB_PATH` pointed memes-web at a scratch SQLite file, so the real profiles DB was untouched.
- `drive.py` uses the dev-only Keycloak admin login and `dummyuser1`, as documented in the memes-agent README.
  Their passwords come from `KC_ADMIN_PASSWORD` and `MEMES_TEST_PASSWORD` (the original run hardcoded them;
  this copy reads the environment so no credentials are committed).

## Known gaps, each a lesson for authtrace

1. **Hooking `AsyncClient.send` sees the request before httpx's auth flow runs.** The token
   call's `Authorization: Basic` (authlib's `client_secret_basic`) was never captured. Hook the
   transport (`AsyncHTTPTransport.handle_async_request`) to see the final wire request.
2. **Streamed responses** (MCP POSTs read via `client.stream`) have no body at the hook. Results
   came from wrapping `MCPToolGateway` instead. A transport-level hook could tee the stream.
3. **Claims-level redaction was missing.** Decoded JWT claims kept the full `nonce`,
   `state_checker` and `at_hash`. Redaction must cover values inside decoded tokens, not only
   headers, forms and URLs.
4. **memes-mcp has no inbound request log** (the Arcade app is built inside `MCPApp.run`). The
   `validate_token` and `_fetch_jwks` wrappers covered it. A generic tool needs a proxy or ASGI
   injection point for servers it didn't build.
5. **Browser asset filtering** dropped images, CSS and JS (11 requests), keeping only a count and the hosts.
