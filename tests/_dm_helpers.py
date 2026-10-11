"""Shared setup for the datamodels tests: lib/ first on sys.path, the example, and small models."""

import contextlib
import copy
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = str(ROOT / "lib")
if LIB in sys.path:
    sys.path.remove(LIB)
sys.path.insert(0, LIB)  # ahead of scripts/, whose launchers would shadow the packages

from datamodels import cli, merge, model, render, shapes, sources  # noqa: E402,F401
from datamodels._viz import viz  # noqa: E402,F401

EXAMPLE = ROOT / "skills" / "data-models" / "examples" / "shop.data-models.json"
SNAPSHOTS = ROOT / "tests" / "fixtures" / "snapshots"
GOLDEN = ROOT / "tests" / "fixtures" / "golden" / "shop"


def example_model() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def snapshot(name: str) -> dict:
    return json.loads((SNAPSHOTS / name).read_text(encoding="utf-8"))


def observed(fields, **extra) -> dict:
    return dict({"fields": fields, "indexes": []}, **extra)


def minimal_model(**overrides) -> dict:
    """A valid model: one Postgres store with users and orders, plus a code store."""
    users = observed([{"name": "id", "type": "bigint", "nullable": False, "key": "pk"},
                      {"name": "email", "type": "text", "nullable": False, "key": "unique"}], estimate=10)
    orders = observed([{"name": "id", "type": "bigint", "nullable": False, "key": "pk"},
                       {"name": "user_id", "type": "bigint", "nullable": False}], estimate=20)
    m = {
        "meta": {"generator": "visualizer", "model": "data-models", "schema_version": 1,
                 "model_sha": None, "model_date": None},
        "system": {"name": "demo", "summary": "Demo keeps users and orders."},
        "stores": [
            {"id": "db", "name": "Database", "engine": "postgres", "role": "System of record.",
             "connection": {"env": "DATABASE_URL"}, "snapshot": {"taken": "2026-10-11", "server_version": "16.4"}},
            {"id": "events", "name": "Events", "engine": "code", "role": "Message payloads."},
        ],
        "entities": [
            {"id": "users", "store": "db", "name": "public.users", "kind": "table", "description": "Accounts.",
             "observed": users, "observed_hash": model.observed_hash(users)},
            {"id": "orders", "store": "db", "name": "public.orders", "kind": "table", "description": "Checkouts.",
             "observed": orders, "observed_hash": model.observed_hash(orders)},
            {"id": "order-placed", "store": "events", "name": "OrderPlaced", "kind": "model",
             "description": "Sent at checkout.",
             "declared": {"evidence": ["app/events.py"], "fields": [{"name": "order_id", "type": "int"}]}},
        ],
        "relationships": [{"from": "orders.user_id", "to": "users.id", "kind": "fk"}],
    }
    m = copy.deepcopy(m)
    m.update(overrides)
    return m


def check(m, root=None):
    rep = viz.Report()
    model.validate(m, rep)
    if root is not None and rep.ok:
        model.check_repo(m, Path(root), rep)
    return rep


def entity(m, eid):
    return next(e for e in m["entities"] if e["id"] == eid)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(a) for a in argv])
    return code, out.getvalue() + err.getvalue()
