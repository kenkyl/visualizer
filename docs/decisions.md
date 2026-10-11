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
