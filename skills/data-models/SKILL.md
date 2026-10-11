---
name: data-models
description: Document the data models of an application, library or workflow as clean Markdown tables (stores, entities, fields, types, keys, indexes, relationships, and drift between code and database) in DATA_MODELS.md, with a summary embedded in the README. Reads docs, migrations and ORM models, and, with permission, live read-only metadata from Postgres, SQLite, MongoDB and Redis. Use when the user asks to document, map, list or visualize a project's data models, schema, tables, collections or Redis keys, wants a data dictionary, or runs /visualizer:data-models. Optional free-text notes steer the run, e.g. "only the billing schema; skip redis; summary only; output to docs/data".
argument-hint: "[notes, e.g. only the public schema; skip redis; summary only; output to docs/data]"
---

# Data models

You work out what the data means; `datamodels.py` does the mechanical parts. Sources read each store's metadata into a snapshot, `merge` folds snapshots into `data-models.json`, you annotate it, and `check` and `render` validate it and write the tables. Never write the tables by hand, and **never edit an entity's `observed` block**: those facts come only from `merge`, and `check` fails if they change.

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/datamodels.py doctor
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/datamodels.py introspect ENGINE --store ID (--url-env VAR [--env-file PATH] | --path FILE) [--schema S] [--include GLOB] [--exclude GLOB] [--sample N] --out SNAP.json
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/datamodels.py merge MODEL --snapshot SNAP [--snapshot ...] [--init --name NAME]
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/datamodels.py check MODEL
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/datamodels.py render MODEL [--readme-insert] [--readme-create]
```

It needs Python 3.9 or later. Postgres, MongoDB and Redis also need `uv`, which fetches each driver into its own cache on first use; nothing is installed into the project. SQLite and code-only models need nothing else. Read [references/model-schema.md](references/model-schema.md) before annotating a model, and [references/sources.md](references/sources.md) for what each source reads. [examples/shop.data-models.json](examples/shop.data-models.json) is a complete worked example.

Notes from the user, if any: `$ARGUMENTS`

## 1. Find the target and the mode

Run `git ls-files -co --exclude-standard '*data-models.json'` and keep the files whose `meta.model` is `"data-models"`. A README containing `<!-- visualizer:data-models:start path=... -->` also names its model.

- **No model: first run.** Continue below.
- **A model exists: refresh.** Reuse each store's `connection` and `scope` from the model, ask only about stores that are new, and go to step 5.

Treat the notes as free text. Pull out:

- an output directory ("output to docs/data");
- scope: which stores, schemas or databases, and include or exclude globs ("only billing", "skip redis", "no legacy_* tables");
- detail: "summary only" means `meta.detail: "summary"`, the entity index without per-field tables;
- sample size ("sample 500 documents").

Save the notes in `meta.notes` as `{"text": <verbatim>, "added": <today>}`.

## 2. Discover statically

Read enough to know the stores and where the models are declared, not everything:

- docs and the README, which often describe the data directly;
- migrations (Alembic, Django, Rails `db/schema.rb`, Prisma, Flyway, Knex), `schema.sql`, and ORM or ODM models (SQLAlchemy, Django, Prisma, TypeORM, Mongoose);
- Redis usage: the key formats in code (`f"session:{id}"`), and any Search index definitions;
- message and payload types that exist only in code (Pydantic, dataclasses, JSON Schema, protobuf): these go in a `code` store;
- `docker-compose.yml`, Kubernetes and Terraform files for the engines, versions and connection variable names;
- `.env.example` for variable names only (`DATABASE_URL`, `REDIS_URL`), never its values;
- a visualizer `architecture.json`, if there is one: reuse its datastore names so the two docs line up.

**Never open** `.env`, `*.pem`, `*.key` or other credential files.

## 3. Ask once

Ask all of these at once, with the defaults marked as recommended:

- **Output directory** for `data-models.json` and `DATA_MODELS.md`. Default `docs/data-models/`.
- **README placement.** Show where the `## Data models` block would go by quoting the headings around it. Default: `after-intro`. If an architecture block exists, recommend `after:Architecture`. Or don't embed it.
- **Live introspection, per store you found.** For each one, offer: read its metadata live (recommended when it's reachable), or document it from code only. For live stores, ask for:
  - the **name** of the environment variable that holds the connection URL, and optionally a dotenv file the script reads it from. Never ask for the URL itself, and never echo it;
  - whether it's **production**. Catalog reads (Postgres, SQLite) are light. Sampling (MongoDB documents, Redis keys) reads real data in memory, so on production confirm the sample size, or skip it;
  - a reminder that a **read-only database role** is best, even though the sources enforce read-only themselves.

  Connecting tips to pass on when they apply:
  - The URL must work **from this machine**: `localhost:5432` with the port published, not a compose service name like `db:5432`.
  - SQLAlchemy-style URLs (`postgresql+psycopg://`) work, and so do env-file values that reference other variables (`${POSTGRES_PASSWORD}`).
  - A variable exported in another terminal isn't visible here. Name the dotenv file instead, or export it and restart Claude Code.
- **Only if the repo has no README:** create one with the title and the block, or skip the README.

## 4. First run: introspect and merge

1. Run `doctor`, and offer only the sources it says are ready.
2. For each store the user approved, run `introspect`, saving the snapshot outside the repo, in a temp directory. Each store id is kebab-case and permanent, like `main-db` or `cache`. Pass the scope from the notes. If a connection fails, show the error (the script masks the URL), and either fix the variable name or document that store from code only.
3. Run `merge MODEL --init --name <repo name> --snapshot ...`, passing every snapshot at once.

For stores you document from code only, add the store yourself (with no `snapshot` or `connection`) and give each entity a `declared` block.

## 5. Refresh: introspect and merge again

Run `introspect` again for each live store with its saved scope, then `merge MODEL --snapshot ...` without `--init`. Show the change set it prints: entities added, removed or changed, and relationships dropped. Removed entities lose their annotations. An entity that code still declares is kept, and is reported as drift.

## 6. Annotate

Write only these:

- `system.summary` (at most 5 sentences: what data the system keeps, and where) and up to 5 `highlights`. Highlights are concrete facts a reader should know: what's the system of record, what expires, what isn't enforced, and where personal data lives.
- For each store: `name`, a one-line `role`, `evidence`, and, when the code or deploy files make it clear, a one-line `durability`: what losing the store costs.
- For each entity: a one-line `description`. Describe what one row, document or key represents, not how it's built. A Postgres `COMMENT` is used when there's no description.
- `field_descriptions` for fields whose meaning isn't obvious from the name: units, states and their order, what null means.
- `declared` blocks for entities the code defines, listing **every** field the code declares, by its column, document or hash-field name. These drive the Drift section.
- For Redis key patterns the code defines, `declared.type` and `declared.ttl` as the code sets them. These matter most when the keys don't exist yet, or the sample missed them.
  - If the scan found the pattern, put the declared block **on the observed entity**, even when its placeholder differs from the code's (`dash:{uuid}` versus the code's `f"dash:{user_id}"`). Say what the placeholder is in the description.
  - Only patterns the scan didn't find get their own declared-only entity, named as the code writes it (`sess:{token}`).
- `relationships` the store can't show:
  - `reference` when the code proves the link (cite evidence);
  - `inferred` when it's naming convention only;
  - `embedded` when a column or field holds a shape defined in code, such as a JSONB column validated by a Pydantic model. Point it at the code model's entity: `"to": "course-config"`.

  A key pattern's placeholder counts as a field: `cart-id.{id}`.
- `meta.detail`, `meta.readme`, and each store's `connection` (variable names only) and `scope`, so refreshes repeat them.
- `meta.model_sha` is `git rev-parse HEAD` and `model_date` is that commit's date; set `dirty` if `git status --porcelain` shows changes. Outside git, use null and false.

Reorder each store's entities so related ones sit together, and the most important come first. `merge` sorts new entities by name, but keeps whatever order you set.

Name things the way the repo does. Mark an entity `inferred` if neither a store nor the code confirms it.

## 7. Check

Run `check`, fix every error, and rerun until it passes. Warnings are there for a reason: fix them unless you can say why not. The `drift:` lines are findings to report, not errors to fix. Don't paper over drift by editing `declared`, unless the code really says otherwise.

## 8. Render

First run: `render MODEL --readme-insert`, plus `--readme-create` if the user agreed to a new README. Refresh: `render MODEL`. Exit codes:

- `0`: done. It prints what it created, updated or left unchanged.
- `1`: the model is invalid. Go back to step 7.
- `3`: stopped without writing, because a generated file was edited by hand or the README markers don't match. Show the user what's there and ask: fold the edits into the model, or overwrite with `--force`. For mismatched markers, the user must fix the README by hand. Never pass `--force` without asking.

If the README markers are missing on a refresh, ask once whether to add the block back. If not, set `meta.readme.embed` to false.

## 9. Report and offer to commit

Summarize in a few lines:

- the stores and their entity counts;
- what changed since the last run, on a refresh;
- the drift findings;
- anything inferred;
- any Redis key pattern that kept a literal segment that looks like data rather than schema. Shorten it with `--exclude`, or tell the user before committing.

Then show `git status` and a short diff summary, and offer to commit the model, `DATA_MODELS.md` and the README change together. Snapshots are never committed. **Never commit without a yes.**

## Invariants

- **Read-only, always.** The sources enforce it: Postgres runs in a read-only transaction, SQLite opens the file read-only, and MongoDB and Redis go through method and command allowlists. Never run a write against a user's store by any other route, including their CLI.
- **No values leave a store.** Snapshots and models hold names, types, counts and ratios only. Never paste a row, document, key name or value into the model.
- **Connections by name only.** The model and your messages hold variable names, never URLs or passwords.
