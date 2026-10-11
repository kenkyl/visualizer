"""Postgres: tables, views, columns, keys, indexes and comments from pg_catalog. Driver: psycopg 3.

Read-only three ways: the session starts with default_transaction_read_only, the transaction is
declared READ ONLY before any query, and it's rolled back at the end. Only catalog tables are
queried; row counts are the planner's estimates (reltuples), so no user table is ever scanned.
"""

from __future__ import annotations

import re

ENGINE = "postgres"
REQUIRES = ("psycopg[binary]>=3.1,<4",)
SAMPLED = False
DEFAULT_SAMPLE = None

TIMEOUT_MS = 15000
SYSTEM_SCHEMAS = "n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\\_%'"
RELKINDS = {"r": "table", "p": "table", "f": "table", "v": "view", "m": "view"}
ON_DELETE = {"c": "cascade", "n": "set null", "d": "set default", "r": "restrict"}  # "a" (no action) is the default
URL_SCHEME_RE = re.compile(r"^postgres(?:ql)?(?:\+[a-z0-9]+)?://", re.I)
# format_type's SQL-standard spellings, shortened to the names people write, so tables stay narrow.
SHORT_TYPES = [(re.compile(p), r) for p, r in (
    (r"\btimestamp(\(\d+\))? with time zone", r"timestamptz\1"),
    (r"\btimestamp(\(\d+\))? without time zone", r"timestamp\1"),
    (r"\btime(\(\d+\))? with time zone", r"timetz\1"),
    (r"\btime(\(\d+\))? without time zone", r"time\1"),
    (r"\bcharacter varying\b", "varchar"),
    (r"\bbit varying\b", "varbit"),
    (r"\bcharacter\b", "char"),
)]


def short_type(name: str) -> str:
    for pattern, repl in SHORT_TYPES:
        name = pattern.sub(repl, name)
    return name

# Each query starts with a tag comment, so tests can answer it with a fake cursor.
# {where} becomes the schema filter. EXTEND: enum values (pg_enum).
QUERIES = {
    "version": "/* datamodels:version */ SHOW server_version",
    "tables": """/* datamodels:tables */
        SELECT n.nspname, c.relname, c.relkind, c.reltuples::bigint, obj_description(c.oid, 'pg_class')
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p', 'f', 'v', 'm') AND NOT c.relispartition AND {where}
        ORDER BY 1, 2""",
    "columns": """/* datamodels:columns */
        SELECT n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid), col_description(c.oid, a.attnum)
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r', 'p', 'f', 'v', 'm')
          AND NOT c.relispartition AND {where}
        ORDER BY 1, 2, a.attnum""",
    "constraints": """/* datamodels:constraints */
        SELECT n.nspname, c.relname, con.contype, con.conname, con.confdeltype, pg_get_constraintdef(con.oid),
               ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(num, ord)
                     JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.num ORDER BY k.ord),
               fn.nspname, fc.relname,
               ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(num, ord)
                     JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.num ORDER BY k.ord)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_class fc ON fc.oid = con.confrelid
        LEFT JOIN pg_namespace fn ON fn.oid = fc.relnamespace
        WHERE con.contype IN ('p', 'u', 'f', 'c') AND {where}
        ORDER BY 1, 2, con.conname""",
    "indexes": """/* datamodels:indexes */
        SELECT n.nspname, t.relname, i.relname, ix.indisunique, am.amname,
               ARRAY(SELECT pg_get_indexdef(ix.indexrelid, k, true) FROM generate_series(1, ix.indnatts) AS k)
        FROM pg_index ix
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_class t ON t.oid = ix.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_am am ON am.oid = i.relam
        WHERE NOT ix.indisprimary AND {where}
        ORDER BY 1, 2, 3""",
}


def normalize_url(url: str) -> str:
    """libpq takes postgresql:// only; accept the SQLAlchemy forms apps keep in .env (postgresql+psycopg://)."""
    return URL_SCHEME_RE.sub("postgresql://", url, count=1)


def connect(target: str):
    import psycopg

    return psycopg.connect(normalize_url(target), connect_timeout=10, application_name="visualizer-datamodels",
                           options=f"-c default_transaction_read_only=on -c statement_timeout={TIMEOUT_MS}")


def close(conn) -> None:
    conn.close()


def _where(scope):
    if scope.schemas:
        return "n.nspname = ANY(%s)", (list(scope.schemas),)
    return SYSTEM_SCHEMAS, ()


def introspect(conn, scope) -> dict:
    cur = conn.cursor()
    try:
        cur.execute("SET TRANSACTION READ ONLY")
        where, params = _where(scope)

        def rows(name):
            sql = QUERIES[name].replace("{where}", where)
            cur.execute(sql, params if "%s" in sql else None)
            return cur.fetchall()

        cur.execute(QUERIES["version"])
        version = cur.fetchone()[0]
        tables = [r for r in rows("tables") if scope.wants(f"{r[0]}.{r[1]}")]
        columns, constraints, indexes = rows("columns"), rows("constraints"), rows("indexes")
    finally:
        conn.rollback()
    return build(version, tables, columns, constraints, indexes)


def build(version, tables, columns, constraints, indexes) -> dict:
    """The snapshot body from catalog rows. Kept apart from the queries so tests can feed it rows."""
    entities, by_name = [], {}
    for schema, table, relkind, reltuples, comment in tables:
        name = f"{schema}.{table}"
        observed = {"fields": [], "indexes": []}
        if RELKINDS[relkind] == "table":
            observed["estimate"] = int(reltuples) if reltuples is not None and reltuples >= 0 else None
        if comment:
            observed["comment"] = comment
        by_name[name] = observed
        entities.append({"name": name, "kind": RELKINDS[relkind], "observed": observed})

    for schema, table, col, ctype, nullable, default, comment in columns:
        observed = by_name.get(f"{schema}.{table}")
        if observed is None:
            continue
        f = {"name": col, "type": short_type(ctype), "nullable": bool(nullable)}
        if default is not None:
            f["default"] = default
        if comment:
            f["comment"] = comment
        observed["fields"].append(f)

    relationships = []
    for schema, table, contype, conname, deltype, definition, cols, fschema, ftable, fcols in constraints:
        name = f"{schema}.{table}"
        observed = by_name.get(name)
        if observed is None:
            continue
        fields = {f["name"]: f for f in observed["fields"]}
        if contype == "p":
            for c in cols:
                if c in fields:
                    fields[c]["key"] = "pk"
        elif contype == "u" and len(cols) == 1 and cols[0] in fields and "key" not in fields[cols[0]]:
            fields[cols[0]]["key"] = "unique"
        elif contype == "c":
            observed.setdefault("checks", []).append({"name": conname, "definition": definition})
        elif contype == "f":
            for c, fc in zip(cols, fcols):
                rel = {"from": [name, c], "to": [f"{fschema}.{ftable}", fc], "kind": "fk"}
                if deltype in ON_DELETE:
                    rel["on_delete"] = ON_DELETE[deltype]
                relationships.append(rel)

    for schema, table, ix_name, unique, method, cols in indexes:
        observed = by_name.get(f"{schema}.{table}")
        if observed is None:
            continue
        cols = list(cols)
        field = next((f for f in observed["fields"] if len(cols) == 1 and f["name"] == cols[0]), None)
        if unique and field is not None and field.get("key") == "unique":
            continue  # the index behind a one-column UNIQUE: the key column already says it
        ix = {"name": ix_name, "fields": cols, "unique": bool(unique)}
        if method != "btree":
            ix["method"] = method
        observed["indexes"].append(ix)

    return {"server_version": str(version).split(" ")[0], "entities": entities, "relationships": relationships}
