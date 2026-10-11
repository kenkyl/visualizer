"""data-models.json validation: schema, references, ownership of observed facts, evidence and secrets."""

import tempfile
import unittest
from pathlib import Path

from _dm_helpers import check, entity, example_model, minimal_model, model


class SchemaTests(unittest.TestCase):
    def assertError(self, m, fragment):
        rep = check(m)
        self.assertTrue(any(fragment in e for e in rep.errors), rep.errors)

    def test_minimal_and_example_are_valid(self):
        for m in (minimal_model(), example_model()):
            rep = check(m)
            self.assertEqual(rep.errors, [])
            self.assertEqual(rep.warnings, [])

    def test_meta(self):
        m = minimal_model()
        m["meta"]["model"] = "architecture"
        self.assertError(m, "meta.model: must be \"data-models\"")
        m = minimal_model()
        m["meta"]["detail"] = "verbose"
        self.assertError(m, "meta.detail")

    def test_unknown_fields_fail_loudly(self):
        m = minimal_model()
        entity(m, "users")["descripton"] = "typo"
        self.assertError(m, "entities[users].descripton: unknown field")

    def test_annotations_required(self):
        m = minimal_model()
        m["system"]["summary"] = ""
        m["stores"][0]["role"] = " "
        rep = check(m)
        self.assertIn("system.summary: must not be empty: say what data the system keeps, and where", rep.errors)
        self.assertIn("stores[db].role: must not be empty: say what this store is for", rep.errors)

    def test_ids_and_references(self):
        m = minimal_model()
        entity(m, "orders")["id"] = "Orders"
        self.assertError(m, "must be kebab-case")
        m = minimal_model()
        entity(m, "orders")["store"] = "nope"
        self.assertError(m, "unknown store 'nope'")
        m = minimal_model()
        m["relationships"].append({"from": "orders.user_id", "to": "ghost.id", "kind": "reference"})
        self.assertError(m, "unknown entity 'ghost'")
        m = minimal_model()
        m["relationships"][0]["to"] = "users"
        self.assertError(m, "must be \"<entity id>.<field>\"")
        m = minimal_model()
        m["relationships"][0]["to"] = "users.uuid"
        self.assertIn("relationships[0].to: entity 'users' has no field 'uuid'", check(m).warnings)

    def test_embedded_points_at_a_whole_entity(self):
        m = minimal_model()
        m["relationships"].append({"from": "users.email", "to": "order-placed", "kind": "embedded"})
        self.assertEqual(check(m).errors, [])
        m["relationships"][-1]["kind"] = "reference"
        self.assertTrue(any("must be \"<entity id>.<field>\"" in e for e in check(m).errors))
        m["relationships"][-1].update(kind="embedded", to="ghost")
        self.assertTrue(any("unknown entity 'ghost'" in e for e in check(m).errors))

    def test_on_delete(self):
        m = minimal_model()
        m["relationships"][0]["on_delete"] = "cascade"
        self.assertEqual(check(m).errors, [])
        m["relationships"][0]["on_delete"] = "explode"
        self.assertTrue(any("on_delete: must be one of" in e for e in check(m).errors))
        m = minimal_model()
        m["relationships"].append({"from": "orders.user_id", "to": "users.id", "kind": "reference",
                                   "on_delete": "cascade"})
        self.assertTrue(any("only foreign keys have a delete action" in e for e in check(m).errors))

    def test_durability_and_declared_redis_facts(self):
        m = minimal_model()
        m["stores"][0]["durability"] = "Durable; Docker volume pgdata"
        entity(m, "order-placed")["declared"].update(type="hash", ttl="8h")
        self.assertEqual(check(m).errors, [])
        m["stores"][0]["durability"] = "x" * 121
        self.assertTrue(any("durability: is 121 characters" in e for e in check(m).errors))

    def test_key_pattern_placeholders_are_fields(self):
        m = example_model()
        self.assertIn("{id}", model.field_names(entity(m, "cart-id")))

    def test_entity_needs_facts_or_declaration(self):
        m = minimal_model()
        del entity(m, "order-placed")["declared"]
        self.assertError(m, "needs observed facts (from merge) or a declared block")

    def test_code_store_is_declared_only(self):
        m = minimal_model()
        m["stores"][1]["snapshot"] = {"taken": "2026-10-11"}
        self.assertError(m, "a code store has no connection or snapshot")

    def test_declared_needs_evidence(self):
        m = minimal_model()
        entity(m, "order-placed")["declared"]["evidence"] = []
        self.assertError(m, "cite the code or docs that declare this entity")

    def test_missing_descriptions_are_one_warning(self):
        m = minimal_model()
        for e in m["entities"]:
            e.pop("description")
        self.assertIn("entities: 3 without a description: users, orders, order-placed", check(m).warnings)


class OwnershipTests(unittest.TestCase):
    def test_hand_edited_observed_fails(self):
        m = minimal_model()
        entity(m, "users")["observed"]["fields"][1]["type"] = "varchar"
        rep = check(m)
        self.assertTrue(any("doesn't match observed_hash" in e for e in rep.errors), rep.errors)

    def test_hash_without_observed(self):
        m = minimal_model()
        del entity(m, "users")["observed"]
        self.assertTrue(any("observed_hash: set without observed facts" in e for e in check(m).errors))

    def test_hash_ignores_key_order(self):
        a = {"fields": [], "indexes": [], "estimate": 1}
        b = {"estimate": 1, "indexes": [], "fields": []}
        self.assertEqual(model.observed_hash(a), model.observed_hash(b))


class SecretTests(unittest.TestCase):
    def test_connection_strings_and_secrets_are_rejected(self):
        for value in ("postgres://app@db.internal:5432/shop", "redis://:pw@cache:6379",
                      "postgresql+psycopg://app@localhost:5432/app", "postgresql+asyncpg://localhost/app",
                      "mongodb+srv://u:p@cluster0.example.net/db", "sk-ant-api03-abcdefghijklmnop"):
            m = minimal_model()
            m["stores"][0]["role"] = f"Reached at {value}"
            self.assertFalse(check(m).ok, value)

    def test_connection_names_a_variable(self):
        m = minimal_model()
        m["stores"][0]["connection"] = {"env": "postgres://x/y"}
        self.assertTrue(any("must be the name of an environment variable" in e for e in check(m).errors))


class RepoTests(unittest.TestCase):
    def test_evidence_must_exist_and_not_be_a_secrets_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            (root / "app" / "events.py").write_text("class OrderPlaced: ...\n")
            m = minimal_model()
            self.assertEqual(check(m, root).errors, [])
            entity(m, "users")["evidence"] = ["app/models.py", ".env"]
            errors = check(m, root).errors
            self.assertIn(f"entities[users].evidence[0]: 'app/models.py' does not exist in {root}", errors)
            self.assertIn("entities[users].evidence[1]: '.env' is a secrets file; never read or cite it", errors)

    def test_sample_secret_from_env_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            (root / "app" / "events.py").write_text("")
            (root / ".env.example").write_text("DB_PASSWORD=changeme-123\n")
            m = minimal_model()
            m["stores"][0]["role"] = "Login is changeme-123"
            self.assertTrue(any("sample value of DB_PASSWORD" in e for e in check(m, root).errors))


if __name__ == "__main__":
    unittest.main()
