# Changelog

## 0.3.0 (2026-10-11)

### Data models

- `/visualizer:data-models` skill: documents stores, entities, fields, keys, indexes and relationships as Markdown tables in `DATA_MODELS.md`, with a summary block in the README. Free-text notes scope the run and set the detail level.
- `lib/datamodels/`, a stdlib-only package launched by `scripts/datamodels.py`, with five commands:
  - `doctor`;
  - `introspect`, read-only, for Postgres, SQLite, MongoDB and Redis, with drivers fetched by `uv run --with`;
  - `merge`, which refreshes the model, keeps annotations, prints the change set, and owns `observed` facts through a hash;
  - `check`, which validates the schema, references, evidence and the secret lint, rejects connection URLs, and reports drift;
  - `render`.
- Beyond names and types, the tables show:
  - foreign-key delete actions (`on delete cascade`), from Postgres and SQLite;
  - Postgres CHECK constraints;
  - `embedded` relationships from a column to a shape defined in code (JSONB plus Pydantic);
  - declared Redis types and TTLs for key patterns the code defines;
  - an optional Durability column per store.
- Fixes from an end-to-end run against a real app (instructor-gadget):
  - migration bookkeeping tables are excluded by default (`--all-tables` keeps them);
  - Postgres types are shortened (`varchar(32)`, `timestamptz`);
  - no false drift for Redis values the source doesn't open (JSON, strings);
  - "declared only" is marked only against fields that were actually read;
  - entities keep the reading order Claude gives them.
- Connections: SQLAlchemy-style URLs (`postgresql+psycopg://`) are normalized, and env-file values expand `${VAR}` and `${VAR:-default}` as docker compose does. `check` also rejects SQLAlchemy-style URLs.
- Redis key names are clustered into patterns (`session:{id}`), and Mongo document shapes are inferred from a `$sample`. No stored value reaches a snapshot or model.
- Model schema v1 (`skills/data-models/references/model-schema.md`), a guide to each source and to adding new ones (`references/sources.md`), and a worked example (a shop with Postgres, Redis and MongoDB) recorded from real servers, which doubles as the golden test.
- `viz.py`: `upsert_readme` takes a block marker, so several visualizer blocks can share a README without one landing inside another; `check_repo`'s evidence checks are now `check_evidence`, which datamodels reuses. Output is unchanged.
- Tests: shapes and key clustering, validation and ownership, every source against fakes (read-only first, allowlists, no values out), SQLite end to end including a refresh after a schema change, the golden, and the stdlib-only and one-way-import rules.

### Auth flows

- authtrace M1: `authtrace.py render TRACE [-o OUT]` turns a trace.json (schema v0) into a single-file viewer. The memes-agent artifact is ported into `lib/authtrace/viewer.html`, which builds the topology, tours, step cards, token inspector, goal map and logs from the embedded trace. Narrative prose is a markdown subset, escaped, never HTML. Each step card shows a confidence tag derived from the exchanges and code the step cites (`confidence.step_confidence`).
- authtrace M2: `authtrace.py run -- CMD` captures every HTTP call that Python processes make, children included, through httpx, httpx2, urllib, urllib3 or requests. It needs no change to the target: a sitecustomize injected through PYTHONPATH hooks the clients at the wire. Exchanges are redacted in-process (`--redaction dev|strict|raw`) and written to `.authtrace/run-<time>/exchanges.jsonl`, git-ignored. `authtrace.py import-har FILE` does the same for a browser HAR. The auth-flow skill can now capture; building a trace (M3) is next.
- authtrace M3: `authtrace.py build CAPTURE` turns a capture into trace.json facts. It covers classification by protocol, tokens merged by fingerprint, nodes and roles inferred by rule with reasons, unseen-but-required traffic drawn as `inferred` edges, ten findings rules with pass/fire results, and a role-column layout. `render` shows an un-narrated trace as a one-step-per-exchange tour, plus a findings section. `authtrace.py explain` decodes a token from stdin without echoing it, which powers `/visualizer:explain-token`. Strict redaction keeps protocol structure (error messages, tool names) and now filters form bodies too.
- `tests/fixtures/authtrace/memes-agent-live/`: a strict capture of memes-agent's live suite (39 exchanges) and the trace built from it.
- `tests/fixtures/authtrace/memes-agent/`: the memes-agent trace (12 exchanges, 2 tours, 24 steps) and its golden render.
- Tests: render (golden, parity with the reference artifact, embedding safety, trace problems), build, capture, redaction, rules, confidence, HAR import and explain. The reference lint now covers authtrace's fixtures too.
- CI: the httpx capture tests run on Python 3.13.

## 0.2.0 (2026-10-10)

- authtrace joins the plugin (scaffold): `/visualizer:auth-flow` and `/visualizer:explain-token` skills, in preview and run only when typed.
- `lib/authtrace/`, a stdlib-only package launched by `scripts/authtrace.py`. `doctor` reports which capture modes this machine supports; the other commands are stubs that name their milestone.
- `docs/authtrace/`: the authtrace plan and the 2026-10-03 memes-agent reference captures, with the dev passwords in the reference scripts moved to environment variables.
- `docs/decisions.md` and `CLAUDE.md`.
- Tests: the authtrace CLI and its stdlib-only rule, and a lint that keeps raw JWTs, secrets and one-time claim values out of `docs/authtrace/`.
- CI: unittest on Python 3.9 and 3.13.

## 0.1.0

- Plugin skeleton: manifests, a single-plugin marketplace, the `/visualizer:architecture` skill and `scripts/viz.py`.
- Model schema v1 (`references/model-schema.md`): meta, system, boundaries, components, edges, flows and views.
- `viz.py check`: schema, ids, references, size limits, evidence paths and doc anchors that must exist, and a secret lint (including sample values from `.env.example`).
- `viz.py render`: `ARCHITECTURE.md` (diagram, legend, component table, where state lives, flows, assumptions, notes, and a hash footer), plus a README block between markers. Hand edits and mismatched markers stop the render.
- `viz.py preview`: an HTML page that renders every diagram in light and dark with a pinned Mermaid (11.17.2).
- Style guide (`references/style-guide.md`): the GitHub-safe Mermaid subset, translucent theme-neutral colours, and rank-based link lengths that stack trust zones so diagrams fit GitHub's column.
- Worked example: arcade-test at `cc1197d`, which doubles as the golden test.
