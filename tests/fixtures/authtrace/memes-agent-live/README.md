# memes-agent live run, captured with `--redaction strict`

The M3 fixture: the first real capture by `authtrace run`, the input `build` must turn into a trace.

- Captured 2026-10-11 from memes-agent at `5595a41`, with Keycloak and memes-mcp already running
  (`run.json` is the capture's own, minus `cwd`):
  `authtrace.py run --redaction strict -- uv run pytest -m live -q` (4 passed, 39 exchanges).
- Covers PLAN.md Appendix A's flow (token, MCP discover 400, initialize, tools/list, Claude turns,
  tools/call, DELETE) four times over, plus the newer tests: a token without `memes:read`, an
  `offline_access` grant with a refresh-token exchange, and a revoke followed by a refused reuse.
- `strict` means: tokens are fingerprints with decoded claims, `sub` and other identity claims are
  hashed (consistently, so tokens still link), and bodies keep protocol fields only. No prompts,
  tool arguments, Claude replies or tool results.
- memes-mcp was already running, so its side (token validation, the JWKS fetch) isn't here. M3
  must show it as `inferred`/`declared`, not drop it.
