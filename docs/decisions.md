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

## 2026-10-11: strict redaction keeps protocol structure

Strict dropped what M3 needs: JSON-RPC `error.code`/`error.message`, the tool names in `tools/list` and in Claude's `tool_use` blocks, and the model name. It also let form bodies skip its field filter, so the password grant's `username` survived. Now strict walks into `content` and `tools` containers and into `error` objects, and every free-text and argument field inside them is still dropped: prompts, tool arguments, Claude's text, tool results. Form bodies go through the same filter. Checked on a real run: the capture holds none of the test's prompts, hints, username or email, and `sub` is hashed the same way in every token, so tokens still link.

## 2026-10-11: M3, a lean `build`

`authtrace build CAPTURE` turns `exchanges.jsonl` into `trace.json` facts, deterministically. This first version proves the design and leaves the rest behind `# EXTEND:` points:

- **Classification** is a list of small decoders (`decode.py`): OAuth token (by grant), revoke and introspect, discovery, JWKS, PRM and the Bearer challenge, MCP (JSON-RPC method, stream, session end), Anthropic. The first match wins.
- **Nodes** are one per capturing process and one per server origin. Roles are assigned by rule, each with a `why`: an origin serving OAuth endpoints is an authorization server; one receiving Bearer tokens whose `aud` names it, or that challenges without one, is a resource server; a model API is `llm-api`. Roles are `inferred`; a process's existence is `observed`.
- **Traffic that must exist but wasn't captured** becomes a dashed `inferred` edge with its reason and how to confirm it. The first case: a resource server accepting RS256 tokens must fetch its issuer's keys.
- **Findings v0 are the ten rules in `rules.py`.** `meta.rules` records each rule as `passed` or `fired`, so "no token reached the LLM" is a recorded fact.
- **Layout** is role columns (`layout.py`), so a built trace renders with no hand-made layout.
- **The default tour lives in render's derived data, not in `trace.json`:** one step per exchange, facts only (`tour.py`). `narrative` stays Claude's, and an un-narrated trace still renders.
- **Steps can cite `tokens`,** and `step_confidence` counts them. M1's live step 2 is now `decoded`, as it should be.
- **`explain`** decodes one token from stdin and never echoes it. That makes `/visualizer:explain-token` work.

Deferred:
- **JWKS signature checks.** They need raw tokens, which only exist inside the traced process, so verification will run in the recorder when the JWKS is fetched in the same process.
- Sessions (MCP session ids are masked).
- Cookie and PKCE rules (M5).
- Merging several captures.
- Containers and parent/child nodes in the layout.
- An automatic token inspector.

On memes-agent's live fixture, `build` finds Keycloak as the authorization server, memes-mcp as the resource server and pytest as the client. It infers memes-mcp's unseen JWKS fetch. `OAUTH-ROPC`, `MCP-VERSION-FALLBACK` and `HOST-ALIAS` fire, and `TOKEN-LEAK-LLM` passes.

## 2026-10-11: data-models skill

`/visualizer:data-models` documents a project's data models as Markdown tables: stores, entities, fields, keys, indexes, relationships, and drift between code and database. It follows the architecture pattern. Claude annotates a JSON model, and a stdlib package (`lib/datamodels/`, launched by `scripts/datamodels.py`) introspects, merges, validates and renders it.

**v1 sources:** Postgres, SQLite, MongoDB and Redis, plus `code` for models that exist only in code. MySQL/MariaDB was cut to keep the first build small. It's the first planned addition, behind the same source interface.

**Deferred**, with `# EXTEND:` notes:

- ER diagrams; v1 is tables only, as asked;
- type-level drift;
- a persisted change history;
- the link to `architecture.json` datastores;
- Redis streams, JSON paths, memory stats and Cluster;
- Postgres check constraints and enums;
- automated live-database tests.

## 2026-10-11: data-model drivers through uv, not CLIs or stdlib protocols

Live stores are read with their Python drivers (psycopg, pymongo, redis-py), run with `uv run --no-project --with <driver>`. `introspect` re-runs itself that way. uv caches the drivers on first use, nothing is installed into the project, and the architecture skill still needs only `python3`.

**Rejected:**

- **Native CLIs** (psql, mongosh, redis-cli): fragile output parsing, they're often not installed, and read-only is harder to guarantee.
- **Stdlib wire-protocol clients:** Postgres SCRAM and TLS, and Mongo's BSON, would mean a lot of auth code to own. A stdlib Redis client remains possible later.

**Rule:** drivers are imported only inside functions in `lib/datamodels/sources/`. A test checks that every module-level import in the package is stdlib.

## 2026-10-11: read-only and value-free, enforced per source

Each source enforces read-only itself instead of trusting the role it's given:

- **Postgres:** `default_transaction_read_only`, `SET TRANSACTION READ ONLY`, and a rollback;
- **SQLite:** `mode=ro` plus `query_only`;
- **MongoDB and Redis,** which have no read-only session: method and command allowlists.

Snapshots and models keep names, types, counts and ratios only. Mongo samples and Redis key names exist only in memory. Redis keys are clustered into patterns, and the patterns are all that's kept.

Connections are named by environment variable. The script can read that one variable from a dotenv file, so Claude never opens `.env`. `check` rejects connection URLs anywhere in a model.

## 2026-10-11: facts and annotations are owned separately

`merge` writes the facts: `observed`, `observed_hash`, `snapshot` and `fk` relationships. Claude writes everything else. `check` recomputes `observed_hash`, so a hand edit to a fact fails, as authtrace's facts and narrative are kept apart.

Refresh matches entities by (store, name), so ids and annotations survive. Unchanged relationships keep their place, so a refresh with no schema change leaves the file byte-identical.

## 2026-10-11: datamodels reuses viz's helpers, one way

datamodels imports viz for README markers, evidence checks, the secret lint and Markdown escaping, rather than copying them. viz never imports datamodels, and a test enforces that.

Two small changes to `viz.py` made this possible:

- `upsert_readme` takes a `marker`, so the `visualizer:data-models` block and the architecture block can share a README. Neither block is ever inserted inside the other.
- The evidence loop moved into `check_evidence`.

The architecture goldens are byte-identical.

## 2026-10-11: what the data-models tables show, after a hand-written comparison

A detailed, hand-written data-model doc (from another project) was compared with this skill's output. Some of what it covers was adopted in the default output, because each item is cheap and tells a reader something the field list can't:

- foreign-key delete actions and CHECK constraints, from the catalog;
- an `embedded` relationship, so a `jsonb` column points at the code model that defines its shape;
- declared Redis types and TTLs, for keys that don't exist yet in a dev database;
- an optional Durability column.

Read and write paths ("when is this table written, by what") would be an opt-in annotation, written only when the user's notes ask for it. It's not built yet.

**Not adopted:**

- **Sample rows:** they break the no-values rule, and invented ones go stale.
- **Architecture and sequence diagrams:** they belong to the architecture skill.
- **A separate enum section:** enums go in declared types and field descriptions.

## 2026-10-11: M4, a lean `check` and a narrative Claude writes beside the facts

M4 turns a built trace into a narrated, checked artifact. The user skipped the lesson on the investigation step, so these choices were made without it.

- **Claude writes `narrative.json`, never `trace.json`.** `authtrace narrate TRACE NARRATIVE` swaps the file in as the `narrative` section and lays the diagram out again. Claude never has to edit a 100 KB file of facts in place. Re-narrating replaces the previous narrative.
- **Facts are hashed.** `build` stores `meta.facts_hash` over meta and every fact section. `check` recomputes it, as datamodels does with `observed_hash`. Narrative and layout aren't in the hash. A hand-written trace, such as the M1 fixture, has no hash, and `check` warns that it can't prove the facts are unedited.
- **The narrative can add parts it can't observe.** The reference diagram shows a gateway class, middleware and endpoints that no capture sees. They're `narrative.nodes`, `edges` and `boundaries`, drawn with the facts, under new ids only. `narrative.names` gives a fact node a real name ("Keycloak" for `localhost-8080`). This keeps the CLAUDE.md invariant that Claude writes only the narrative, and it keeps provenance visible: every addition says how it's known.
- **An addition claims no more than its evidence.** Its ceiling is `step_confidence` over what it cites: code lifts it to `declared`, observed exchanges to `observed`, and nothing leaves it `inferred`, which needs a `why`. A step's confidence stays derived. `check` rejects a stored one rather than trusting or clamping it.
- **A fact edge is described, not redrawn.** `build`'s dashed `inferred` edges (an unseen JWKS fetch) stay inferred. The narrative captions them, and the step that explains them cites code, so the step is `declared`. A `refines` link that would upgrade a fact edge in place is an `# EXTEND:` idea, not built.
- **`check` is one pass with errors and warnings:**
  - a secret lint over the raw file text, narrative included. It's `lint.py`, which the reference-lint test now shares; it adds unmasked secret fields, query parameters and Bearer values to the JWT, one-time-claim and password patterns, and borrows viz's key patterns.
  - `trace.problems()`, plus the types of each narrative field the viewer reads;
  - the facts hash;
  - the confidence rules;
  - evidence paths: relative to the target, existing, `:line` inside the file, and never a secrets file. The root defaults to the folder holding `.authtrace/`. Libraries are cited through the target's `.venv/`.
  - `git check-ignore` on the trace;
  - redaction `raw` is refused.

  It also lists what's still `inferred` or `unknown` with each reason, which the deliver step reports.
- **`show`** prints a trace's ids, one line each, and any exchange as wire text, so Claude can cite ids and copy redacted wire text without reading the whole trace.
- **Layout covers additions.** Role columns as before, with children stacked inside their parent's box, top-level nodes grouped by boundary, boxes sized for their captions, and boundaries drawn around what they hold. There are no pinned positions yet (`# EXTEND:`), so `narrate` replaces any hand-tuned layout.
- **`build` keeps the capture log** (one redacted line per exchange) as a `logs` fact, so the page's run-log section isn't empty. The command's own output (pytest's log) is `# EXTEND:`; `run` would have to keep it redacted.
- **Delivery** is a private artifact when the session has an Artifact tool and the user agreed, after asking again if redaction isn't `strict`. Otherwise it's the path to `flow.html`. Either way, the target's `git status` is shown.

- **`narrate` runs `check`'s checks too,** except the git location, and writes nothing if they fail. A bad narrative never lands in the trace. `check` stays the final gate on the file as written.
- **Lint hits name a JSON path.** Each value is linted where it sits, both alone and with its key. Then the whole text is linted for anything outside a value. Quotes may be escaped, since a wire block holds JSON inside a string.
- **Acceptance was run by a fresh session** that had only the skill's text. It reused the strict capture: memes-mcp was down, and a new run would have called the paid API. It wrote three tours (one per distinct flow in the suite), 11 declared additions and an inspector, and passed `check` at the first attempt. Its critique drove the fixes: the output folder for a reused capture, tours per distinct flow, the field and confidence-order docs, labels on sibling edges, the token zone for children, narrate checking, and lint paths. Its narrative is the fixture `memes-agent-live/narrative.json`, minus one dev username.

Deferred: pinned layout positions, `refines` for fact edges, structured `checks` in the narrative (steps carry `checks` pairs and `code` refs instead), the command's own log, and a `--share` mode that requires `strict`.
