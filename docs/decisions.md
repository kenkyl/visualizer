# Decisions

## 2026-10-10: authtrace lives in visualizer

authtrace was planned as its own plugin and repo (`docs/authtrace/PLAN.md` §17, decision 5). It now ships inside visualizer as the `auth-flow` and `explain-token` skills.

**Why:** both tools follow the same pattern (Claude reasons, a stdlib script validates and renders), share a secret lint and evidence discipline, and draw the same systems. One plugin lets an auth trace seed its nodes and boundaries from an existing `architecture.json`, and lets `ARCHITECTURE.md` link to a trace later. One install, one marketplace.

**Cost:** the plugin's surface and release cadence are now shared. The auth skills stay `disable-model-invocation: true` while in preview, so they never trigger for someone who only wants architecture diagrams.

## 2026-10-10: stdlib core, capture dependencies on demand

The plan assumed a uv-installed package with extras. Plugins don't install Python packages, so dependencies would surface on first use anyway. Instead:

| Capability | Needs |
|---|---|
| Architecture; authtrace decode, build, check, render, HAR import, explain | `python3` 3.9+ only |
| Capture a Python app (in-process hooks) | Nothing new: the hooks run inside the target's own environment |
| Capture a browser flow | `uv`, plus Chrome or Playwright's Chromium (`uvx --with playwright`) |
| Capture any language (forward proxy) | `uv` (`uvx --with mitmproxy`), plus a CA trusted per process |
| Live mode | `uv` (`uvx --with starlette --with uvicorn`) |

`authtrace.py doctor` reports which rows this machine meets, and the skill offers only those. JWT signature checks (M3) will use pure-Python RS256. Other algorithms will be reported as `decoded`, not `verified`, until there's a reason to add a crypto dependency.

The code lives in `lib/authtrace/` (a package, launched by `scripts/authtrace.py`) rather than PLAN §14's `src/` layout, and targets Python 3.9+ like `viz.py` rather than 3.13.

## 2026-10-10: unittest, not pytest

The repo already tests with stdlib unittest and has no `pyproject.toml`. authtrace follows it. Tests that need a running reference system will be skipped unless an env var points at it, like `ARCADE_TEST_REPO` for the architecture example.

## 2026-10-10: skill names

`/visualizer:auth-flow` (the trace procedure) and `/visualizer:explain-token` (decode a pasted JWT). Live mode and diagram review become further skills when they land. "authtrace" stays the name of the package and the CLI.

## 2026-10-10: reference material is committed

The handoff's `PLAN.md` and `reference/` captures live in `docs/authtrace/`. They're the UI spec and the source for golden fixtures. Before committing, the dev passwords hardcoded in `reference/trace_live.py` and `reference/browser-flow/drive.py` were replaced with environment variables. `tests/test_reference_lint.py` keeps raw JWTs, secrets and one-time claim values out of that folder.

## 2026-10-10: authtrace outputs stay out of the target's history

Traces go to `.authtrace/` in the target (gitignored) or a path outside it. The architecture skill is different on purpose: its diagram belongs in the repo.

## 2026-10-10: trace schema v0 choices (M1)

The renderer follows PLAN §6, with these changes, made while porting `reference/memes-agent-flow.html`:

- **Tours live in `narrative.flows`, not top-level `flows`.** A tour is a choice of steps, what to dim, and a banner. All of that is Claude's, and the web tour has no captured exchanges at all. The core will describe a capture run in facts when it has one to describe.
- **Captions are narrative.** The lines under a node or edge name ("holds the token, adds Bearer") go in `narrative.captions`. The fact keeps its `label` and `kind`, and the viewer falls back to those.
- **`layout` is required until auto-layout lands.** Every boundary, node and edge needs a box or a path. `trace.problems()` says which are missing. The role-column layout (PLAN §11) is an `# EXTEND:` point in `trace.py` and `viewer.html`.
- **Narrative prose is a markdown subset, never HTML:** `` `code` ``, `**bold**`, `*italic*` and `[text](#anchor)` links to in-page anchors only. The viewer escapes everything else, and the embedded JSON escapes `<`, `>` and `&`, so a trace can't inject markup, whether Claude wrote it or someone dropped it in. Wire blocks are literal text.
- **Step confidence is derived, not stored.** `render` computes it with `confidence.step_confidence()` and embeds it next to the trace. The trace itself never holds a step-level confidence, so nothing can claim more certainty than the facts support. M4's `check` will reuse the same function.
- **Two node kinds beyond §6:** `endpoint` (one of an AS's URLs, drawn as a single mono line) and `component` (a part inside a service, such as middleware).

The memes-agent fixture was assembled once from the artifact's data (`narrative`, `layout`) and Appendix A plus the traced log (`exchanges`, `tokens`). Token fingerprints are `null` because the raw tokens were never kept. The golden test pins the output. A parity test checks that the step titles, goals and claims match the artifact.

## 2026-10-11: how a step's confidence is ranked

`step_confidence()` ranks levels from strongest to weakest: verified, observed, decoded, declared, inferred, unknown. A step takes the weakest level among the exchanges it cites, and citing code lifts anything below `declared` up to `declared`.

- **verified above observed:** a checked signature proves who minted a value. Seeing it on the wire doesn't.
- **decoded below observed:** the bytes were seen, but what they claim (a JWT's `sub` or `aud`) is unchecked and could be forged.
- **A step that cites nothing is `inferred`,** because it's Claude's reasoning. `unknown` is kept for explicit gaps in the facts.

In the memes-agent trace, live step 4 (signature verification) is `declared`, because the JWKS fetch wasn't observed but `server.py:29` is cited. The five web-tour steps that cite no code are `inferred`.

## 2026-10-11: how `authtrace run` captures (M2)

- **A `sitecustomize` on PYTHONPATH, installed lazily.** `run` puts `lib/authtrace/capture/boot/` (which holds only `sitecustomize.py`) and `lib/` ahead of the target's PYTHONPATH. A meta-path finder patches each HTTP client right after the target imports it, so authtrace never imports a third-party library, and a process that never uses httpx pays nothing. Whatever `sitecustomize` the environment already had still runs afterwards. Children inherit the environment, so a test runner's subprocesses are captured too. `python -I` and `-E` ignore PYTHONPATH; the summary says when no Python process reported in.
- **Hooks at the wire:** httpx and httpx2 at `HTTPTransport.handle_request` and `AsyncHTTPTransport.handle_async_request`, below the auth flow (this fixes the missed `Authorization: Basic` from 2026-10-03). The stdlib `http.client` covers urllib, urllib3 and requests. Response bodies are teed as the caller reads them, so a long-lived MCP event stream never blocks. A body that's still open at exit is written and marked `incomplete`. gzip and deflate are decoded in the recorder, because both hooks sit below client-side decompression. Brotli and zstd are reported with their size, since the stdlib has no decoder for them. aiohttp is an `# EXTEND:` point.
- **Redaction happens in the traced process, before the write.** Capture is the only moment raw values exist, so JWT headers and claims are decoded there. One-time claims are masked, and identity claims are hashed in `strict`. Raw values stay in memory only, so a token that reappears later (in an LLM request, a URL or a page) is still flagged by fingerprint. Files are 0600.
- **`exchanges.jsonl` schema 0:** one `exchange` line per request (ids are `<pid>-<seq>`, times are seconds since the run started), plus `process` and `hook-error` lines. M3's `build` turns these into trace.json.
- **Outputs:** `.authtrace/run-<time>/` by default, with a `.authtrace/.gitignore` of `*` so the target's `git status` is unchanged. An `-o` that git would track is refused.
- **HAR import** redacts each entry on the way in and never copies the HAR. Static assets are counted, not kept.
- **Tests run against a stdlib fake system** (`tests/authtrace_fakes.py`: an IdP, an MCP-style resource server and an LLM endpoint). The httpx tests skip unless httpx is installed. CI's 3.13 job installs httpx and httpx2 for them; locally, run `uv run --no-project --with httpx --with httpx2 python -m unittest discover -s tests`.
- **The memes-agent acceptance (PLAN Appendix A) has not been run yet.** It needs Keycloak, memes-mcp and an Anthropic key. It's a manual step, `authtrace.py run -- uv run pytest -m live` in memes-agent. Its live suite has grown since 2026-10-03, so expect more than the 10 exchanges in Appendix A.
