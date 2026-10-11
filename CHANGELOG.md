# Changelog

## 0.2.0 (unreleased)

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
