# visualizer

A Claude Code plugin that draws how systems fit together: architecture diagrams that stay up to date, tables of the data models a system keeps, and (in preview) traces of how identity and access flow through a running system.

## Architecture

`/visualizer:architecture` reads a repository and writes a small JSON model of its components, data flows, trust boundaries and state. It renders that model as Mermaid that GitHub displays natively, in light and dark mode.

- `ARCHITECTURE.md`: overview diagram and legend, a component table with evidence links, where state lives, and sequence diagrams for the key flows.
- `README.md`: the overview, between `<!-- visualizer:start -->` and `<!-- visualizer:end -->` markers, plus a link to the full page.

Claude does the reasoning. [`scripts/viz.py`](scripts/viz.py), stdlib-only Python 3.9+, does the deterministic parts. It validates the model (ids, references, limits, evidence paths that must exist, a secret lint) and renders it the same way every time.

**Updating.** Run it again after the code changes. `viz.py status` lists what moved since the model was last reviewed, so Claude re-reads only that. Claude writes a candidate model, and `viz.py update` replaces the model with it:

- the previous model and its page are kept under `archive/<date>-<sha>/`, next to `ARCHITECTURE.md`;
- `architecture.changelog.md` gets an entry, newest first: the commits, Claude's summary of what changed and why, and what was added, removed or changed;
- nothing is written if the candidate is invalid, the summary is missing, or a generated file was edited by hand.

Components keep their ids, so a changelog reads "changed", not "removed and added". Bumping only the reviewed commit writes no archive and no entry.

**Status:** early development. First runs and updates work. Retention for old archives, and a history link inside `ARCHITECTURE.md`, are next.

## Data models

`/visualizer:data-models` documents what a project stores, as plain Markdown tables. It reads docs, migrations and ORM models, plus live metadata from the databases you point it at:

- a table of stores;
- for each store, an index of its tables, collections, key patterns and search indexes;
- for each entity, its fields, types, nullability, defaults, keys and indexes;
- relationships, including cross-store links;
- **drift** between what the code declares and what the database holds.

The outputs are `DATA_MODELS.md` and a summary table in the README, between `<!-- visualizer:data-models:start -->` and `<!-- visualizer:data-models:end -->` markers. Re-running it refreshes the model, keeps every description, and reports what changed.

| Source | Reads | Needs |
|---|---|---|
| Postgres | catalog: tables, views, columns, keys, indexes, comments, row estimates | `uv` (fetches psycopg) |
| SQLite | schema and pragmas | nothing |
| MongoDB | collections, validators, indexes, field shapes from a `$sample` | `uv` (fetches pymongo) |
| Redis | key patterns from a capped `SCAN`, types, TTLs, hash fields, Search schemas | `uv` (fetches redis-py) |
| Code | models declared only in code or docs: payloads, DTOs, JSON Schemas | nothing |

Every source is **read-only, enforced in code**:

- Postgres runs in a read-only transaction;
- SQLite opens the file read-only;
- MongoDB and Redis go through method and command allowlists.

Nothing it writes contains a stored value. Snapshots and models hold names, types, counts and ratios, and Redis keys become patterns like `session:{id}`. Connections are given by environment variable name, never as URLs. [`scripts/datamodels.py`](scripts/datamodels.py), launching [`lib/datamodels/`](lib/datamodels/), does the deterministic parts. It's stdlib-only, and drivers are fetched on demand. [`references/sources.md`](skills/data-models/references/sources.md) covers each source and how to add one.

**Status:** new. MySQL and ER diagrams are next.

## Auth flows (preview)

`authtrace`, in [`lib/authtrace/`](lib/authtrace/), watches a running system's OAuth, OIDC and MCP-authorization traffic and renders it as an interactive step-through: which tokens exist, where they travel, who checks which claim, and how sure it is about each fact (observed, decoded, verified, declared, inferred or unknown). It never changes the system it watches, and it never stores raw tokens.

| Command | Does | Needs |
|---|---|---|
| `/visualizer:auth-flow [notes]` | Runs a flow under capture, explains it from the code, and renders a viewer | `python3`; `uv` for browser, proxy and live capture |
| `/visualizer:auth-flow doctor` | Reports what this machine can capture | `python3` |
| `/visualizer:explain-token <jwt>` | Decodes and explains a token, masking it in the output | `python3` |

**Status:** preview. The full procedure works for Python apps: capture, build facts, investigate from the code, narrate, check, render and deliver. Browser and proxy capture (M5) and live mode (M6) are next; see [`docs/authtrace/PLAN.md`](docs/authtrace/PLAN.md#15-milestones-each-one-is-also-a-lesson). Both skills run only when you type them. The reference captures that the viewer is modelled on are in [`docs/authtrace/reference/`](docs/authtrace/reference/).

## Install

```bash
claude plugin marketplace add ~/Workspace/claude-code/visualizer
```

```bash
claude plugin install visualizer@visualizer
```

Or try it for one session without installing:

```bash
claude --plugin-dir ~/Workspace/claude-code/visualizer
```

## Use

In the repository you want to diagram:

```
/visualizer:architecture
/visualizer:architecture focus on the auth flow; output to docs/arch
```

It asks where to put the files and where the README block goes. It writes the files and offers to commit; nothing is committed without a yes.

```
/visualizer:data-models
/visualizer:data-models only the public schema; skip redis; summary only; output to docs/data
```

It asks which stores to read live, and for the **name** of the variable that holds each connection URL. It asks whether a store is production before sampling it, and offers to commit, as above.

**Connecting a database:**
1. Put the URL in an environment variable, or keep it in your `.env`, e.g. `DATABASE_URL`.
2. When asked, give the variable's name and, if it's in a file, the file. The script reads that one value; Claude never sees it, and the model saves only the name.

Notes:
- SQLAlchemy-style URLs (`postgresql+psycopg://`) and compose-style references (`${POSTGRES_PASSWORD}`) work.
- The URL must reach the database from your machine, so use `localhost` and a published port, not a compose service name.
- A read-only database role is best.

## Examples

[`skills/architecture/examples/arcade-test.architecture.json`](skills/architecture/examples/arcade-test.architecture.json) is the model for [arcade-test](https://github.com/kenkyl/arcade-test) at `cc1197d`. Its rendered output is in [`tests/fixtures/golden/arcade-test/`](tests/fixtures/golden/arcade-test/ARCHITECTURE.md).

[`skills/data-models/examples/shop.data-models.json`](skills/data-models/examples/shop.data-models.json) models a small shop backed by Postgres, Redis and MongoDB. Its facts come from real servers, recorded in [`tests/fixtures/snapshots/`](tests/fixtures/snapshots/). The annotations stand in for what Claude writes from the app's code; the shop's code isn't in this repo, so the evidence paths are illustrative. The rendered output is in [`tests/fixtures/golden/shop/`](tests/fixtures/golden/shop/DATA_MODELS.md).

## Develop

```bash
python3 -m unittest discover -s tests
```

`VIZ_UPDATE_GOLDEN=1` rewrites the golden files after an intended output change. `ARCADE_TEST_REPO=<checkout at cc1197d>` also checks the example's evidence against the real repo. `scripts/viz.py preview MODEL` writes a page that renders every diagram with a pinned Mermaid, for a visual check.
`python3 scripts/authtrace.py doctor` checks the auth-flow prerequisites. `python3 scripts/datamodels.py doctor` checks which data-model sources are ready. The data-model tests run against fakes and a temporary SQLite file. After changing a source, run it once against a real server, for example a throwaway `docker run postgres:16`, and refresh its recorded snapshot. Decisions are in [`docs/decisions.md`](docs/decisions.md).

## License

[MIT](LICENSE)
