"""End to end on a real SQLite file: introspect, merge, annotate, render; then change the schema and refresh."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from _dm_helpers import check, entity, run

SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE, status TEXT DEFAULT 'pending');
CREATE TABLE legacy_carts (id INTEGER PRIMARY KEY);
CREATE INDEX orders_user ON orders (user_id);
CREATE VIEW pending AS SELECT * FROM orders WHERE status = 'pending';
INSERT INTO users (email) VALUES ('bob@example.com');
"""


class SqliteEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "app.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript(SCHEMA)
        (self.root / "README.md").write_text("# demo\n\nIntro.\n\n## Usage\n\nRun it.\n")
        (self.root / "models.py").write_text("class Order: ...\n")
        self.model = self.root / "docs" / "data-models" / "data-models.json"
        self.snap = self.root / "snap.json"

    def tearDown(self):
        self.tmp.cleanup()

    def introspect(self):
        code, out = run("introspect", "sqlite", "--store", "app-db", "--path", self.db, "--out", self.snap)
        self.assertEqual(code, 0, out)
        return json.loads(self.snap.read_text())

    def load(self):
        return json.loads(self.model.read_text())

    def save(self, m):
        self.model.write_text(json.dumps(m, indent=2))

    def test_first_run_then_refresh(self):
        snap = self.introspect()
        self.assertEqual([e["name"] for e in snap["entities"]], ["legacy_carts", "orders", "pending", "users"])
        self.assertNotIn("bob@example.com", self.snap.read_text())
        self.assertEqual(snap["relationships"], [{"from": ["orders", "user_id"], "to": ["users", "id"], "kind": "fk",
                                                  "on_delete": "cascade"}])

        code, out = run("merge", self.model, "--snapshot", self.snap)
        self.assertEqual(code, 2)
        self.assertIn("pass --init --name NAME", out)
        code, out = run("merge", self.model, "--snapshot", self.snap, "--init", "--name", "demo")
        self.assertEqual(code, 0, out)
        self.assertIn("app-db (sqlite", out)
        self.assertIn("4 added", out)

        code, out = run("check", self.model, "--root", self.root)
        self.assertEqual(code, 1, out)  # not annotated yet
        self.assertIn("stores[app-db].role: must not be empty", out)

        m = self.load()
        m["system"]["summary"] = "Demo keeps users and orders in SQLite."
        m["stores"][0].update(name="App database", role="Everything the demo stores.")
        for e in m["entities"]:
            e["description"] = f"The {e['name']} entity."
        orders = entity(m, "orders")
        orders["field_descriptions"] = {"status": "pending, paid or shipped"}
        orders["declared"] = {"evidence": ["models.py"],
                              "fields": [{"name": "id"}, {"name": "user_id"}, {"name": "status"}]}
        self.save(m)
        code, out = run("check", self.model, "--root", self.root)
        self.assertEqual(code, 0, out)
        self.assertIn("0 drift findings", out)

        code, out = run("render", self.model, "--root", self.root, "--readme-insert")
        self.assertEqual(code, 0, out)
        doc = (self.root / "docs" / "data-models" / "DATA_MODELS.md").read_text()
        self.assertIn("| `user_id` | `INTEGER` | no |  | FK → [`users`](#users).`id`, on delete cascade |  |", doc)
        self.assertIn("pending, paid or shipped", doc)
        self.assertIn("**Indexes:** `orders_user` on `user_id`", doc)
        readme = (self.root / "README.md").read_text()
        self.assertIn("<!-- visualizer:data-models:start path=docs/data-models/data-models.json hash=", readme)
        self.assertLess(readme.index("Intro."), readme.index("## Data models"))
        self.assertLess(readme.index("<!-- visualizer:data-models:end -->"), readme.index("## Usage"))

        # The schema changes: a column is added, a table dropped, a declared field never migrated.
        with sqlite3.connect(self.db) as conn:
            conn.executescript("ALTER TABLE orders ADD COLUMN total_cents INTEGER; DROP TABLE legacy_carts; "
                               "CREATE TABLE refunds (id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id));")
        m = self.load()
        entity(m, "orders")["declared"]["fields"].append({"name": "coupon"})
        self.save(m)
        self.introspect()
        code, out = run("merge", self.model, "--snapshot", self.snap)
        self.assertEqual(code, 0, out)
        self.assertIn("1 added, 1 removed, 2 changed", out)
        self.assertIn("  + refunds", out)
        self.assertIn("  + fk refunds.order_id -> orders.id", out)
        self.assertIn("  - legacy_carts", out)
        self.assertIn("  ~ orders: + field total_cents (INTEGER)", out)
        self.assertIn("  ~ pending: + field total_cents (INTEGER)", out)  # the view is SELECT *

        m = self.load()
        self.assertEqual([e["id"] for e in m["entities"]], ["orders", "pending", "users", "refunds"])
        orders = entity(m, "orders")
        self.assertEqual(orders["description"], "The orders entity.")  # annotations survive
        self.assertEqual(orders["field_descriptions"], {"status": "pending, paid or shipped"})
        self.assertEqual(m["relationships"], [
            {"from": "orders.user_id", "to": "users.id", "kind": "fk", "on_delete": "cascade"},
            {"from": "refunds.order_id", "to": "orders.id", "kind": "fk"}])

        code, out = run("check", self.model, "--root", self.root)
        self.assertEqual(code, 0, out)
        self.assertIn("drift: orders.coupon: declared in code, not in the store", out)
        self.assertIn("drift: orders.total_cents: in the store, not declared in code", out)
        code, out = run("render", self.model, "--root", self.root)
        self.assertEqual(code, 0, out)
        self.assertIn("README.md: updated block", out)
        doc = (self.root / "docs" / "data-models" / "DATA_MODELS.md").read_text()
        self.assertIn("## Drift", doc)
        self.assertIn("`coupon` <sub>declared only</sub>", doc)

    def test_dropped_table_that_code_declares_is_kept_as_drift(self):
        self.introspect()
        run("merge", self.model, "--snapshot", self.snap, "--init", "--name", "demo")
        m = self.load()
        entity(m, "legacy-carts")["declared"] = {"evidence": ["models.py"]}
        entity(m, "legacy-carts")["description"] = "Old carts."
        self.save(m)
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP TABLE legacy_carts")
        self.introspect()
        code, out = run("merge", self.model, "--snapshot", self.snap)
        self.assertIn("~ legacy_carts: no longer in the store; kept because code declares it", out)
        e = entity(self.load(), "legacy-carts")
        self.assertNotIn("observed", e)
        self.assertEqual(e["description"], "Old carts.")

    def test_scope_limits_what_counts_as_removed(self):
        self.introspect()
        run("merge", self.model, "--snapshot", self.snap, "--init", "--name", "demo")
        code, out = run("introspect", "sqlite", "--store", "app-db", "--path", self.db, "--out", self.snap,
                        "--include", "orders")
        self.assertEqual(code, 0, out)
        code, out = run("merge", self.model, "--snapshot", self.snap)
        self.assertIn("0 added, 0 removed, 0 changed", out)
        self.assertEqual(len(self.load()["entities"]), 4)

    def test_migration_bookkeeping_is_left_out_unless_asked(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
        self.assertNotIn("alembic_version", [e["name"] for e in self.introspect()["entities"]])
        code, out = run("introspect", "sqlite", "--store", "app-db", "--path", self.db, "--out", self.snap,
                        "--all-tables")
        self.assertEqual(code, 0, out)
        self.assertIn("alembic_version", [e["name"] for e in json.loads(self.snap.read_text())["entities"]])

    def test_read_only(self):
        from _dm_helpers import sources
        src = sources.load("sqlite")
        conn = src.connect(str(self.db))
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("INSERT INTO users (email) VALUES ('x@y.z')")
        conn.execute("PRAGMA query_only = OFF")  # even then, the file was opened mode=ro
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM users")
        src.close(conn)

    def test_merge_refuses_engine_mismatch(self):
        snap = self.introspect()
        snap["engine"] = "postgres"
        self.snap.write_text(json.dumps(snap))
        run("merge", self.model, "--snapshot", self.snap, "--init", "--name", "demo")
        snap["engine"] = "sqlite"
        self.snap.write_text(json.dumps(snap))
        code, out = run("merge", self.model, "--snapshot", self.snap)
        self.assertEqual(code, 1)
        self.assertIn("store 'app-db' is postgres, but the snapshot is sqlite", out)


if __name__ == "__main__":
    unittest.main()
