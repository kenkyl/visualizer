"""Sources read one store's metadata into a snapshot. One module per engine.

Each module defines:

  ENGINE          the engine name, as in model.ENGINES
  REQUIRES        pip requirements for its driver, run with `uv run --no-project --with ...`; () for stdlib
  SAMPLED         True if its observations come from samples (documents, keys) rather than a catalog
  DEFAULT_SAMPLE  default sample size, or None
  connect(target) open a read-only connection; target is a URL, or a file path for sqlite.
                  The driver is imported here, never at module level.
  introspect(conn, scope) -> {"server_version", "entities": [...], "relationships": [...]}
  close(conn)

Every entity is {"name", "kind", "observed": {...}} with `observed` in the model's format, and
every relationship is {"from": [entity name, field], "to": [entity name, field], "kind": "fk"}.
A source must enforce read-only access itself, and must never return a stored value: names,
types, counts and ratios only. references/sources.md explains how to add one.
"""

from __future__ import annotations

import fnmatch
import importlib
from dataclasses import dataclass, field

SNAPSHOT_VERSION = 1

# EXTEND: add a source here, as lib/datamodels/sources/<engine>.py, and its engine to model.ENGINES.
SOURCES = ("postgres", "sqlite", "mongodb", "redis")

# Tables that migration tools keep for themselves. They aren't the application's data model, so
# introspect excludes them unless asked (--all-tables). EXTEND: other tools' bookkeeping tables.
BOOKKEEPING = ("alembic_version", "django_migrations", "schema_migrations", "ar_internal_metadata",
               "flyway_schema_history", "knex_migrations", "knex_migrations_lock", "_prisma_migrations",
               "goose_db_version", "__diesel_schema_migrations", "__EFMigrationsHistory")


def load(engine: str):
    if engine not in SOURCES:
        raise ValueError(f"unknown source '{engine}' (known: {', '.join(SOURCES)})")
    return importlib.import_module(f"{__name__}.{engine}")


@dataclass
class Scope:
    """What to read. schemas narrows the source's query; include and exclude are globs over entity names."""

    schemas: list = field(default_factory=list)
    include: list = field(default_factory=list)
    exclude: list = field(default_factory=list)
    sample: int | None = None
    max_keys: int | None = None

    def wants(self, name: str) -> bool:
        """Globs match the full name ("public.orders") or the part after the schema ("orders")."""
        names = {name, name.split(".", 1)[-1]}

        def hit(globs):
            return any(fnmatch.fnmatchcase(n, g) for g in globs for n in names)

        return (not self.include or hit(self.include)) and not hit(self.exclude)

    def as_dict(self) -> dict:
        out = {k: v for k, v in (("schemas", self.schemas), ("include", self.include), ("exclude", self.exclude)) if v}
        if self.sample is not None:
            out["sample"] = self.sample
        if self.max_keys is not None:
            out["max_keys"] = self.max_keys
        return out

    @classmethod
    def from_dict(cls, data: dict | None) -> "Scope":
        data = data or {}
        return cls(list(data.get("schemas") or []), list(data.get("include") or []),
                   list(data.get("exclude") or []), data.get("sample"), data.get("max_keys"))
