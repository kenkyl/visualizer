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
