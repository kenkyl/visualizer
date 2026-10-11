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
