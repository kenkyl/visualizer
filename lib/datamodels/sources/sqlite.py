"""SQLite: the schema from sqlite_master and the table pragmas. Stdlib only.

Read-only twice over: the file is opened with mode=ro, and query_only refuses writes on the connection.
Row counts would need a full scan, so v1 doesn't report them. EXTEND: CHECK constraints (they're only in
sqlite_master.sql, so they'd need parsing).
"""

from __future__ import annotations

from pathlib import Path

ENGINE = "sqlite"
REQUIRES = ()
SAMPLED = False
DEFAULT_SAMPLE = None
ON_DELETE = ("cascade", "set null", "set default", "restrict")  # NO ACTION is the default; it's left out

QUERIES = {
    "tables": "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') "
              "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name",
    "columns": "SELECT name, type, \"notnull\", dflt_value, pk FROM pragma_table_info(?) ORDER BY cid",
    "fks": "SELECT \"table\", \"from\", \"to\", on_delete FROM pragma_foreign_key_list(?) ORDER BY id, seq",
    "indexes": "SELECT name, \"unique\", origin FROM pragma_index_list(?) ORDER BY name",
    "index_columns": "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
}


def connect(target: str):
    import sqlite3

    path = Path(target).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"no SQLite file at {target}")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def close(conn) -> None:
    conn.close()


def introspect(conn, scope) -> dict:
    import sqlite3

    entities, relationships = [], []
    for name, kind in conn.execute(QUERIES["tables"]).fetchall():
        if not scope.wants(name):
            continue
        fields, pks = [], []
        for col, ctype, notnull, default, pk in conn.execute(QUERIES["columns"], (name,)).fetchall():
            f = {"name": col, "type": ctype or "", "nullable": not (notnull or pk)}
            if default is not None:
                f["default"] = str(default)
            if pk:
                f["key"] = "pk"
                pks.append(col)
            fields.append(f)

        indexes = []
        if kind == "table":
            for ix_name, unique, origin in conn.execute(QUERIES["indexes"], (name,)).fetchall():
                if origin == "pk":
                    continue
                cols = [r[0] or "(expression)" for r in conn.execute(QUERIES["index_columns"], (ix_name,)).fetchall()]
                if unique and len(cols) == 1:
                    for f in fields:
                        if f["name"] == cols[0] and "key" not in f:
                            f["key"] = "unique"
                    if origin == "u":  # an inline UNIQUE's autoindex: the key column already says it
                        continue
                indexes.append({"name": ix_name, "fields": cols, "unique": bool(unique)})
            for target, col, target_col, on_delete in conn.execute(QUERIES["fks"], (name,)).fetchall():
                if target_col is None:  # REFERENCES t with no column means t's primary key
                    target_pk = [r[0] for r in conn.execute(QUERIES["columns"], (target,)).fetchall() if r[4]]
                    target_col = target_pk[0] if len(target_pk) == 1 else "rowid"
                rel = {"from": [name, col], "to": [target, target_col], "kind": "fk"}
                if on_delete and on_delete.lower() in ON_DELETE:
                    rel["on_delete"] = on_delete.lower()
                relationships.append(rel)

        entities.append({"name": name, "kind": "view" if kind == "view" else "table",
                         "observed": {"fields": fields, "indexes": indexes}})
    return {"server_version": sqlite3.sqlite_version, "entities": entities, "relationships": relationships}
