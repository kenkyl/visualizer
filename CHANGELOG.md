# Changelog

## 0.5.0 (2026-10-12)

### Architecture

- Update mode. Running `/visualizer:architecture` where a model already exists now brings the diagram up to date with the code, instead of only offering a re-render. Claude re-reads just what moved, writes a candidate model, and `viz.py update` does the rest.
- `viz.py status MODEL`: what changed since the model was last reviewed (`--json` available). It lists changed files, the components whose evidence changed, and changed files no component cites. It says when the baseline is unknown, so a full discovery is the answer. It ignores the README block this tool wrote itself, and text output caps long file lists (`--json` has them all).
- `viz.py update MODEL NEXT --summary "..."` replaces the model with a candidate:
  - the old model and its page are kept under `<output dir>/archive/<date>-<sha>/`;
  - `<output dir>/architecture.changelog.md` gets a new entry, newest first, with the commits, the summary and what was added, removed or changed;
  - the model, `ARCHITECTURE.md` and the README block are then rendered;
  - nothing is written when the candidate is invalid, the summary is missing, or a generated file was edited by hand (`--force` overrides the last, as with `render`).
- Changes are matched by id, and bumping `reviewed_sha` alone writes no archive and no entry.
- `render` is unchanged and its output is byte-identical; internally it is now `plan_render` and `apply_writes`.
- Skill: a new "Updating an existing model" section, with the update step in the model-schema reference.
- Tests: the diff, `update` end to end (archive, links, changelog stacking, no-change, summary, secret, invalid candidate, hand edit, README markers, dry run), `status` against real git repos, and a changelog golden.

## 0.4.0 (2026-10-11)

### Auth flows

- authtrace M4: the full `/visualizer:auth-flow` procedure. After capture and build, Claude investigates every error, inferred part and finding from source, writes a narrative, checks it, renders it, and delivers it (a private artifact if you agree). It also shows that the target's `git status` is unchanged.
- `authtrace.py narrate TRACE NARRATIVE` puts Claude's `narrative.json` into a trace and lays the diagram out again. Claude never edits `trace.json`. A narrative can add the parts a capture can't see (modules, middleware, endpoints, a browser), each `declared` from code or `inferred` with a reason, and give fact nodes real names.
- `narrate` runs the same checks as `check`, except the git one, and writes nothing if they fail.
- `authtrace.py check TRACE [--root DIR]` must pass before render. It checks:
  - a secret lint over the whole file, narrative included;
  - the schema and references;
  - that the facts are unedited since `build` (`meta.facts_hash`);
  - that narrative confidence never exceeds its evidence;
  - that evidence paths and line numbers exist in the target;
  - that the trace is git-ignored.

  It also lists what's still inferred or unknown, under the narrative's names. Lint hits name their JSON path (`narrative.steps[2].wire`), never the value.
- `authtrace.py show TRACE [ID ...]` lists a trace's ids, or prints exchanges as redacted wire text and tokens' claims.
- `show` starts with the run's command, exit code and duration.
- Layout: children are drawn inside their parent and inherit its token zone, boxes are sized for their captions, edges between siblings go down the parent's edge with their labels outside it, and boundaries wrap what they hold. Endpoints are one line, with no kind tag. Step links in prose (`[see](#live-4)`) now navigate.
- `build` records a facts hash, the run's exit code and duration, and a redacted capture log. It also refuses a tracked output folder, and creates the folder if it's missing.
- Skill references: `procedure.md` now covers investigate, narrate, check and deliver in full, and the new `narrative.md` documents every narrative field.
- The secret lint lives in `lib/authtrace/lint.py`, shared by `check` and the reference-lint test. It also catches unmasked secret fields, query parameters and Bearer values.
- Acceptance: a fresh Claude session ran the skill in memes-agent on the strict live capture, using only SKILL.md and its references. Its narrative passed `check`, with every evidence path verified, and memes-agent's `git status` stayed empty. The narrative and its page are now `tests/fixtures/authtrace/memes-agent-live/narrative.json` and `flow.html`, the golden. The session's feedback shaped the docs and the fixes above.
- Tests: check, narrate, show, the nested layout, and the acceptance golden.

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
