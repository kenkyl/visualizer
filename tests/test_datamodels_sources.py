"""The driver-backed sources against fakes: read-only first, catalog-only queries, allowlists, no values out.

Live runs against real servers are a manual check (see README, Develop); these need no driver.
"""

import re
import unittest

from _dm_helpers import sources

postgres = sources.load("postgres")
mongodb = sources.load("mongodb")
redis = sources.load("redis")


class FakePgCursor:
    def __init__(self, rows):
        self.rows, self.executed, self.result = rows, [], []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        tag = re.match(r"\s*/\* datamodels:(\w+) \*/", sql)
        self.result = self.rows.get(tag.group(1), []) if tag else []

    def fetchall(self):
        return self.result

    def fetchone(self):
        return self.result[0]


class FakePgConn:
    def __init__(self, rows):
        self.cur = FakePgCursor(rows)
        self.rolled_back = False

    def cursor(self):
        return self.cur

    def rollback(self):
        self.rolled_back = True


PG_ROWS = {
    "version": [("16.4 (Debian 16.4-1)",)],
    "tables": [("public", "orders", "r", 1200, None), ("public", "users", "r", -1, "Accounts"),
               ("public", "recent", "v", 0, None), ("audit", "log", "r", 5, None)],
    "columns": [("public", "orders", "id", "bigint", False, "nextval('orders_id_seq'::regclass)", None),
                ("public", "orders", "user_id", "bigint", False, None, "Who placed it"),
                ("public", "users", "id", "bigint", False, None, None),
                ("public", "users", "email", "text", False, None, None),
                ("public", "recent", "id", "bigint", True, None, None)],
    "constraints": [("public", "orders", "p", "orders_pkey", " ", "PRIMARY KEY (id)", ["id"], None, None, []),
                    ("public", "orders", "f", "orders_user_fk", "c", "FOREIGN KEY ...", ["user_id"],
                     "public", "users", ["id"]),
                    ("public", "orders", "c", "ck_orders_total", " ", "CHECK ((total >= 0))", ["total"],
                     None, None, []),
                    ("public", "users", "p", "users_pkey", " ", "PRIMARY KEY (id)", ["id"], None, None, []),
                    ("public", "users", "u", "users_email_key", " ", "UNIQUE (email)", ["email"], None, None, [])],
    "indexes": [("public", "orders", "orders_user_idx", False, "btree", ["user_id"]),
                ("public", "users", "users_email_key", True, "btree", ["email"]),
                ("public", "orders", "orders_meta_gin", False, "gin", ["meta"])],
}


class PostgresTests(unittest.TestCase):
    def run_pg(self, scope=None):
        conn = FakePgConn(PG_ROWS)
        body = postgres.introspect(conn, scope or sources.Scope())
        return conn, body

    def test_read_only_first_and_catalog_only(self):
        conn, _ = self.run_pg()
        statements = [sql for sql, _ in conn.cur.executed]
        self.assertEqual(statements[0], "SET TRANSACTION READ ONLY")
        self.assertTrue(conn.rolled_back)
        for sql in statements[1:]:
            self.assertRegex(sql, r"^/\* datamodels:\w+ \*/\s*(SELECT|SHOW)\b")
            self.assertNotRegex(sql.upper(), r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE|GRANT|COPY)\b")
            for table in re.findall(r"\b(?:FROM|JOIN)\s+([a-z_.]+)", sql):
                self.assertTrue(table.startswith("pg_") or table in ("unnest", "generate_series"), table)

    def test_schema_filter_is_a_parameter(self):
        conn, _ = self.run_pg(sources.Scope(schemas=["public"]))
        filtered = [(sql, p) for sql, p in conn.cur.executed if "ANY(%s)" in sql]
        self.assertEqual(len(filtered), 4)
        self.assertTrue(all(p == (["public"],) for _, p in filtered))

    def test_build(self):
        _, body = self.run_pg(sources.Scope(include=["public.*"]))
        self.assertEqual(body["server_version"], "16.4")
        by = {e["name"]: e for e in body["entities"]}
        self.assertEqual(set(by), {"public.orders", "public.users", "public.recent"})
        orders, users = by["public.orders"]["observed"], by["public.users"]["observed"]
        self.assertEqual(orders["estimate"], 1200)
        self.assertIsNone(users["estimate"])  # reltuples -1: never analyzed
        self.assertEqual(users["comment"], "Accounts")
        self.assertEqual(by["public.recent"]["kind"], "view")
        self.assertNotIn("estimate", by["public.recent"]["observed"])
        self.assertEqual(orders["fields"][0]["key"], "pk")
        self.assertEqual(orders["fields"][1]["comment"], "Who placed it")
        self.assertEqual(users["fields"][1]["key"], "unique")
        self.assertEqual([ix["name"] for ix in users["indexes"]], [])  # the UNIQUE's own index is implied
        self.assertEqual(orders["indexes"][1], {"name": "orders_meta_gin", "fields": ["meta"], "unique": False,
                                                "method": "gin"})
        self.assertEqual(orders["checks"], [{"name": "ck_orders_total", "definition": "CHECK ((total >= 0))"}])
        self.assertNotIn("checks", users)
        self.assertEqual(body["relationships"], [{"from": ["public.orders", "user_id"], "to": ["public.users", "id"],
                                                  "kind": "fk", "on_delete": "cascade"}])

    def test_type_names_are_shortened(self):
        for long, short in (("timestamp with time zone", "timestamptz"), ("timestamp(3) without time zone",
                            "timestamp(3)"), ("character varying(32)", "varchar(32)"), ("character(2)", "char(2)"),
                            ("time with time zone[]", "timetz[]"), ("double precision", "double precision"),
                            ("numeric(10,6)", "numeric(10,6)")):
            self.assertEqual(postgres.short_type(long), short)

    def test_sqlalchemy_urls_are_normalized(self):
        for url in ("postgresql+psycopg://u:p@h:5432/db", "postgres://u:p@h/db", "POSTGRESQL+asyncpg://h/db",
                    "postgresql://h/db"):
            self.assertTrue(postgres.normalize_url(url).startswith("postgresql://"), url)
        self.assertEqual(postgres.normalize_url("postgresql+psycopg://u:p@h:5432/db"), "postgresql://u:p@h:5432/db")


class FakeRedis:
    def __init__(self, keys, search=True):
        self.keys, self.search, self.sent = keys, search, []

    def execute_command(self, *args):
        self.sent.append(args)
        cmd = args[0]
        if cmd == "SCAN":
            cursor = int(args[1])
            batch = list(self.keys)[cursor:cursor + 3]
            nxt = cursor + 3 if cursor + 3 < len(self.keys) else 0
            return nxt, [k.encode() for k in batch]
        key = args[1].decode() if len(args) > 1 and isinstance(args[1], bytes) else None
        if cmd == "TYPE":
            return self.keys[key][0].encode()
        if cmd == "PTTL":
            return self.keys[key][1]
        if cmd == "HSCAN":
            return 0, {f.encode(): b"VALUE-NEVER-READ" for f in self.keys[key][2]}
        if cmd == "DBSIZE":
            return len(self.keys)
        if cmd == "INFO":
            return {"redis_version": "7.4.0"}
        if cmd == "FT._LIST":
            if not self.search:
                raise RuntimeError("unknown command 'FT._LIST'")
            return [b"idx:products"]
        if cmd == "FT.INFO":
            return [b"index_name", b"idx:products",
                    b"index_definition", [b"key_type", b"JSON", b"prefixes", [b"product:"]],
                    b"attributes", [[b"identifier", b"$.name", b"attribute", b"name", b"type", b"TEXT"],
                                    [b"identifier", b"$.price", b"attribute", b"price", b"type", b"NUMERIC"]],
                    b"num_docs", b"3"]
        raise AssertionError(f"unexpected command {cmd}")


REDIS_KEYS = {
    **{f"session:{i}": ("hash", 1_800_000, ["user_id", "csrf"] + (["expires"] if i % 2 else [])) for i in range(6)},
    "cart:alice@example.com": ("set", -1, []),
    "config": ("string", -1, []),
}


class RedisTests(unittest.TestCase):
    def test_allowlist(self):
        r = redis.ReadOnlyRedis(FakeRedis({}))
        for cmd in ("SET", "DEL", "FLUSHALL", "EVAL", "CONFIG", "HGETALL", "GET", "set"):
            with self.assertRaises(PermissionError):
                r.call(cmd, "x")

    def test_introspect(self):
        fake = FakeRedis(REDIS_KEYS)
        body = redis.introspect(redis.ReadOnlyRedis(fake), sources.Scope())
        by = {e["name"]: e for e in body["entities"]}
        self.assertEqual(set(by), {"session:{id}", "cart:{email}", "config", "idx:products"})
        session = by["session:{id}"]["observed"]
        self.assertEqual((session["type"], session["ttl"], session["estimate"], session["sample"]),
                         ("hash", "up to 30m", 6, 6))
        self.assertEqual({f["name"]: f["presence"] for f in session["fields"]},
                         {"csrf": 1.0, "expires": 0.5, "user_id": 1.0})
        index = by["idx:products"]
        self.assertEqual(index["kind"], "search-index")
        self.assertEqual(index["observed"]["details"], {"on": "json", "prefixes": "product:"})
        self.assertEqual(index["observed"]["fields"][0], {"name": "name", "type": "text", "comment": "indexes $.name"})
        self.assertEqual(body["server_version"], "7.4.0")
        text = repr(body)
        for leaked in ("alice@example.com", "VALUE-NEVER-READ", "session:3"):
            self.assertNotIn(leaked, text)
        self.assertTrue({a[0] for a in fake.sent} <= redis.ALLOWED)

    def test_capped_scan_estimates(self):
        keys = {f"k:{i}": ("string", -1, []) for i in range(30)}
        body = redis.introspect(redis.ReadOnlyRedis(FakeRedis(keys)), sources.Scope(max_keys=6, sample=2))
        k = body["entities"][0]["observed"]
        self.assertEqual((k["estimate"], k["sample"]), (30, 2))

    def test_without_search_module(self):
        body = redis.introspect(redis.ReadOnlyRedis(FakeRedis({"a": ("string", -1, [])}, search=False)),
                                sources.Scope())
        self.assertEqual([e["name"] for e in body["entities"]], ["a"])


class FakeMongo:
    """A client, database and collection in one object, recording every method called."""

    def __init__(self):
        self.calls = []

    def _record(self, name, *a, **k):
        self.calls.append(name)

    def get_database(self, name):
        self._record("get_database", name)
        return self

    def get_default_database(self):
        self._record("get_default_database")
        return self

    def server_info(self):
        self._record("server_info")
        return {"version": "7.0.12"}

    def list_collections(self):
        self._record("list_collections")
        return [{"name": "reviews", "type": "collection",
                 "options": {"validator": {"$jsonSchema": {"required": ["rating"], "properties": {"rating": {}}}}}},
                {"name": "recent", "type": "view", "options": {}},
                {"name": "system.views", "type": "collection", "options": {}}]

    def get_collection(self, name):
        self._record("get_collection", name)
        return self

    def aggregate(self, pipeline, **kw):
        self._record("aggregate")
        assert pipeline == [{"$sample": {"size": 200}}], pipeline
        return iter([{"_id": 1, "rating": 5, "body": "LOVED IT"}, {"_id": 2, "rating": 3}])

    def estimated_document_count(self, **kw):
        self._record("estimated_document_count")
        return 1234

    def list_indexes(self):
        self._record("list_indexes")
        return [{"name": "_id_", "key": {"_id": 1}},
                {"name": "rating_1", "key": {"rating": 1}, "unique": True},
                {"name": "body_text", "key": {"_fts": "text", "_ftsx": 1}, "weights": {"body": 1}}]

    def insert_one(self, doc):
        raise AssertionError("never reached: the wrapper refuses it")


class MongoTests(unittest.TestCase):
    def test_wrapper_refuses_writes(self):
        client = mongodb.ReadOnly(FakeMongo(), mongodb.CLIENT)
        db = client.get_database("shop")
        coll = db.get_collection("reviews")
        for obj, name in ((client, "drop_database"), (db, "drop_collection"), (db, "command"),
                          (coll, "insert_one"), (coll, "delete_many"), (coll, "find_one_and_update"),
                          (coll, "_Collection__database")):
            with self.assertRaises(PermissionError, msg=name):
                getattr(obj, name)
        with self.assertRaises(PermissionError):
            db["reviews"]

    def test_introspect(self):
        fake = FakeMongo()
        body = mongodb.introspect(mongodb.ReadOnly(fake, mongodb.CLIENT), sources.Scope(schemas=["shop"]))
        by = {e["name"]: e for e in body["entities"]}
        self.assertEqual(set(by), {"reviews", "recent"})
        reviews = by["reviews"]["observed"]
        self.assertEqual(reviews["estimate"], 1234)
        self.assertEqual(reviews["sample"], 2)
        self.assertEqual({f["name"]: f["presence"] for f in reviews["fields"]}, {"_id": 1.0, "rating": 1.0, "body": 0.5})
        self.assertEqual(reviews["fields"][0]["key"], "pk")
        self.assertEqual(reviews["indexes"], [{"name": "rating_1", "fields": ["rating"], "unique": True},
                                              {"name": "body_text", "fields": ["body"], "unique": False,
                                               "method": "text"}])
        self.assertEqual(reviews["details"], {"validator": "$jsonSchema with 1 properties, required: rating"})
        self.assertEqual(by["recent"]["kind"], "view")
        self.assertNotIn("estimate", by["recent"]["observed"])
        self.assertNotIn("LOVED IT", repr(body))
        self.assertTrue(set(fake.calls) <= {"get_database", "list_collections", "get_collection", "aggregate",
                                            "estimated_document_count", "list_indexes", "server_info"})

    def test_one_database_at_a_time(self):
        with self.assertRaisesRegex(ValueError, "one MongoDB database"):
            mongodb.introspect(mongodb.ReadOnly(FakeMongo(), mongodb.CLIENT), sources.Scope(schemas=["a", "b"]))


class ScopeTests(unittest.TestCase):
    def test_globs_match_full_or_bare_names(self):
        s = sources.Scope(include=["orders*", "billing.*"], exclude=["*_archive"])
        self.assertTrue(s.wants("public.orders"))
        self.assertTrue(s.wants("billing.invoices"))
        self.assertFalse(s.wants("public.orders_archive"))
        self.assertFalse(s.wants("public.users"))
        self.assertTrue(sources.Scope().wants("anything"))

    def test_round_trip(self):
        s = sources.Scope(["public"], [], ["tmp_*"], 50)
        self.assertEqual(sources.Scope.from_dict(s.as_dict()), s)

    def test_every_source_declares_its_interface(self):
        for engine in sources.SOURCES:
            src = sources.load(engine)
            self.assertEqual(src.ENGINE, engine)
            for name in ("REQUIRES", "SAMPLED", "DEFAULT_SAMPLE", "connect", "introspect", "close"):
                self.assertTrue(hasattr(src, name), f"{engine}.{name}")


if __name__ == "__main__":
    unittest.main()
