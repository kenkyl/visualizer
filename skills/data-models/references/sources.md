# Sources

A source reads one store's metadata into a snapshot. Each source is one module in [`lib/datamodels/sources/`](../../../lib/datamodels/sources/). Every source has the same three rules:

- **Read-only, enforced in code,** not just by convention.
- **No stored values in the output.** Names, types, counts and ratios only.
- **The connection URL is read by variable name** (`--url-env`, optionally from `--env-file`), never printed, and masked in errors. In an env file, `${VAR}`, `${VAR:-default}` and `$VAR` are expanded as docker compose does: from earlier lines, then the environment. Single-quoted values stay literal.

Drivers come through `uv run --no-project --with <driver>`: `introspect` re-runs itself that way, so nothing is installed into the project. `datamodels.py doctor` shows what's ready.

## Contents

- [Postgres](#postgres)
- [SQLite](#sqlite)
- [MongoDB](#mongodb)
- [Redis](#redis)
- [Code only](#code-only)
- [Adding a source](#adding-a-source)

## Postgres

**Driver:** `psycopg[binary]` 3.

| Reads | From |
|---|---|
| tables, partitioned and foreign tables, views and materialized views (as `view`) | `pg_class` |
| columns, types (`format_type`), nullability, defaults | `pg_attribute`, `pg_attrdef` |
| primary keys, one-column unique keys, foreign keys with their delete action, CHECK constraints | `pg_constraint` |
| indexes, with their method when it isn't btree | `pg_index`, `pg_am` |
| table and column comments | `obj_description`, `col_description` |
| row estimates | `pg_class.reltuples`, the planner's estimate (unknown until the table is analyzed) |

**Read-only.** The session starts with `default_transaction_read_only=on` and a 15 s `statement_timeout`. The transaction is declared `READ ONLY` before the first query, and rolled back at the end. Only catalog tables are queried, so no user table is scanned.

**URLs.** SQLAlchemy-style URLs (`postgresql+psycopg://`, `postgresql+asyncpg://`) and `postgres://` are accepted and normalized to `postgresql://` before connecting.

**Types** are shown as people write them: `varchar(32)`, `timestamptz`, `char(2)`, rather than `format_type`'s `character varying(32)`.

**Migration bookkeeping** tables (`alembic_version`, `django_migrations`, `schema_migrations`, `flyway_schema_history`, `_prisma_migrations`, ...) are excluded for Postgres and SQLite unless you pass `--all-tables`.

**Scope.** `--schema` (repeatable) limits the schemas; by default, every non-system schema is read. `--include` and `--exclude` globs match `schema.table` or `table`. Partitions are left out, and their parent stands for them.

## SQLite

**Driver:** none (stdlib `sqlite3`). Pass the file with `--path`.

Reads tables and views from `sqlite_master`, plus `pragma_table_info`, `pragma_foreign_key_list` (including delete actions) and `pragma_index_list`. Row counts would need a full scan, so they aren't reported. CHECK constraints live only in the table's SQL text, so they aren't read yet.

**Read-only.** The file is opened with `mode=ro`, and `PRAGMA query_only` is set as well.

## MongoDB

**Driver:** `pymongo` 4. Name the database in the URL path, or with `--schema`. One database per store.

| Reads | How |
|---|---|
| collections, views, time series | `listCollections`, skipping `system.*` |
| validators | the `$jsonSchema` in each collection's options, summarized |
| indexes | `listIndexes`, skipping `_id_`; text indexes list their weighted fields |
| document counts | `estimatedDocumentCount` (collection metadata; no scan) |
| field shapes | one `$sample` of `--sample` documents (default 200), inferred into paths, type sets and presence ratios |

**Read-only.** MongoDB has no read-only session, so every driver object is wrapped in `ReadOnly`, which exposes only `get_database`, `get_default_database`, `list_collections`, `get_collection`, `list_indexes`, `estimated_document_count`, `aggregate`, `server_info` and `close`. The only pipeline ever sent is a single `$sample` stage, with `maxTimeMS` 15 s. Reads prefer secondaries. Sampled documents stay in memory until their shape is taken, then they're dropped.

## Redis

**Driver:** `redis` (redis-py). The URL picks the database number.

| Reads | How |
|---|---|
| key patterns | `SCAN` with `COUNT 1000`, up to `--max-keys` (default 10,000). The names are clustered into patterns, and then the names are dropped. |
| per pattern | for up to `--sample` keys (default 20): `TYPE`, `PTTL`, and for hashes the field names from one `HSCAN` page. Hash values are discarded unread. |
| estimates | the exact count when the scan finished, otherwise extrapolated from `DBSIZE` |
| Search indexes | `FT._LIST` and `FT.INFO`: fields, their types, the key type, and prefixes |

**Read-only.** Every command goes through `ReadOnlyRedis`, which refuses anything outside `SCAN`, `TYPE`, `PTTL`, `HSCAN`, `DBSIZE`, `INFO`, `FT._LIST` and `FT.INFO` before it reaches the server. `GET`, `HGETALL` and the other value reads aren't on the list.

**Key patterns.** Segments are split on `:`. Numbers, UUIDs, hex hashes, emails, dates and mixed letter-and-digit tokens become `{id}`, `{uuid}`, `{hash}`, `{email}` and `{date}`. Any segment that still has more than 10 distinct values among otherwise-identical keys becomes `{*}`. Segments with fewer distinct values are kept as written, because they're usually schema (`user:{id}:profile`). **Review them before committing:** a small keyspace can keep a literal that's really data, such as a username. Exclude such patterns, or mention them to the user.

**Not yet:** streams, RedisJSON paths, memory stats and Redis Cluster. Each is an `# EXTEND:` point in the module.

## Code only

Not a source module. Add a store with `"engine": "code"` and give each entity a `declared` block citing the code. Use it for libraries, workflows and message payloads that have models but no database of their own.

## Adding a source

MySQL is the first planned addition.

1. Add `lib/datamodels/sources/<engine>.py`, with `ENGINE`, `REQUIRES` (pip requirement strings, or `()`), `SAMPLED`, `DEFAULT_SAMPLE`, `connect(target)`, `introspect(conn, scope)` and `close(conn)`. The module docstring in [`sources/__init__.py`](../../../lib/datamodels/sources/__init__.py) gives the snapshot format.
2. Import the driver **inside** `connect` and any function that needs it, never at module level. The stdlib test enforces this.
3. Enforce read-only in the source itself: a read-only transaction or session if the engine has one, otherwise an allowlist wrapper like `ReadOnlyRedis`.
4. Return names, types, counts and ratios only. If you sample, compute the shape in memory and drop the sample.
5. Add the engine to `SOURCES` in `sources/__init__.py`, to `ENGINES` in `model.py` (and to `SAMPLED_ENGINES` if it samples), and to `ENGINE_NAMES` and `ESTIMATE_LABELS` in `render.py`.
6. Test it against a fake connection that records what was sent, as `tests/test_datamodels_sources.py` does. Then run it once against a real server, and record the snapshot in `tests/fixtures/snapshots/`.
7. Document it here.
