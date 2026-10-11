"""Document shapes, key-pattern clustering and TTL summaries: pure functions that must never leak values."""

import datetime
import unittest

from _dm_helpers import shapes


class InferShapeTests(unittest.TestCase):
    def test_nested_arrays_mixed_types_and_presence(self):
        docs = [
            {"_id": 1, "name": "a", "address": {"city": "x"}, "tags": ["t", {"label": "y"}]},
            {"_id": 2, "name": None, "address": {"city": "y", "zip": "1"}},
            {"_id": 3, "name": "c", "at": datetime.datetime(2026, 1, 1)},
            {"_id": 4, "name": "d"},
        ]
        fields, truncated = shapes.infer_shape(docs)
        by = {f["name"]: f for f in fields}
        self.assertFalse(truncated)
        self.assertEqual([f["name"] for f in fields],
                         ["_id", "name", "address", "address.city", "tags", "tags[]", "tags[].label",
                          "address.zip", "at"])
        self.assertEqual(by["name"]["type"], "null | string")
        self.assertEqual(by["name"]["presence"], 1.0)
        self.assertEqual(by["address.city"]["presence"], 0.5)
        self.assertEqual(by["address.zip"]["presence"], 0.25)
        self.assertEqual(by["tags[]"]["type"], "object | string")
        self.assertEqual(by["at"]["type"], "date")

    def test_no_values_survive(self):
        fields, _ = shapes.infer_shape([{"email": "bob@example.com", "secret": "hunter2", "n": 42}])
        text = repr(fields)
        for value in ("bob@example.com", "hunter2", "42"):
            self.assertNotIn(value, text)

    def test_depth_and_field_caps(self):
        deep = {"a": {"b": {"c": {"d": {"e": 1}}}}}
        names = [f["name"] for f in shapes.infer_shape([deep], max_depth=3)[0]]
        self.assertEqual(names, ["a", "a.b", "a.b.c"])
        fields, truncated = shapes.infer_shape([{f"k{i}": i for i in range(10)}], max_fields=4)
        self.assertEqual(len(fields), 4)
        self.assertTrue(truncated)

    def test_empty_sample(self):
        self.assertEqual(shapes.infer_shape([]), ([], False))


class ClusterKeysTests(unittest.TestCase):
    def test_ids_uuids_emails_dates_and_hashes_become_placeholders(self):
        names = ([f"session:{i}" for i in range(30)]
                 + ["user:6f1c2a9e-7b1d-4c3e-9a2b-0c1d2e3f4a5b:profile", "user:bob@example.com:prefs",
                    "rate:2026-10-11", "cache:9f86d081884c7d65", "order:ord_8x7AbC12", "config"])
        patterns = shapes.cluster_keys(names)
        self.assertEqual(len(patterns["session:{id}"]), 30)
        for p in ("user:{uuid}:profile", "user:{email}:prefs", "rate:{date}", "cache:{hash}", "order:{id}", "config"):
            self.assertIn(p, patterns)
        for raw in ("bob@example.com", "6f1c2a9e", "9f86d081", "ord_8x7AbC12"):
            self.assertFalse(any(raw in p for p in patterns), raw)

    def test_many_distinct_words_collapse(self):
        names = [f"lock:{w}" for w in "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo".split()]
        self.assertEqual(list(shapes.cluster_keys(names)), ["lock:{*}"])

    def test_few_distinct_words_stay_as_schema(self):
        names = ["user:{}:profile".format(i) for i in range(3)] + ["user:{}:cart".format(i) for i in range(3)]
        self.assertEqual(set(shapes.cluster_keys(names)), {"user:{id}:profile", "user:{id}:cart"})

    def test_bytes_and_order(self):
        patterns = shapes.cluster_keys([b"a:1", b"a:2", b"b:1"])
        self.assertEqual(list(patterns), ["a:{id}", "b:{id}"])  # biggest group first
        self.assertEqual(patterns["a:{id}"], [b"a:1", b"a:2"])  # the caller's names, for its own commands


class TtlTests(unittest.TestCase):
    def test_summaries(self):
        self.assertEqual(shapes.summarize_ttl([]), "none")
        self.assertEqual(shapes.summarize_ttl([-1, -1]), "none")
        self.assertEqual(shapes.summarize_ttl([1_800_000, 60_000]), "up to 30m")
        self.assertEqual(shapes.summarize_ttl([-1, 7_200_000]), "up to 2h on 50% of keys")
        self.assertEqual(shapes.duration(3 * 86_400_000), "3d")
        self.assertEqual(shapes.duration(10), "1s")


if __name__ == "__main__":
    unittest.main()
