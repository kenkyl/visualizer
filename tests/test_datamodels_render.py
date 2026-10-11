"""DATA_MODELS.md and the README block: the shop golden, idempotence, hand-edit safety, and README coexistence."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from _dm_helpers import EXAMPLE, GOLDEN, SNAPSHOTS, example_model, merge, minimal_model, render, run, viz

UPDATE = os.environ.get("VIZ_UPDATE_GOLDEN") == "1"


class GoldenTests(unittest.TestCase):
    """The shipped example, rendered into a README that already has an architecture block."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs" / "data-models").mkdir(parents=True)
        self.model = self.root / "docs" / "data-models" / "data-models.json"
        shutil.copy(EXAMPLE, self.model)
        shutil.copy(GOLDEN / "README.before.md", self.root / "README.md")

    def tearDown(self):
        self.tmp.cleanup()

    def test_golden_output_and_rerun(self):
        code, out = run("render", self.model, "--root", self.root, "--readme-insert")
        self.assertEqual(code, 0, out)
        for name, path in (("DATA_MODELS.md", self.root / "docs/data-models/DATA_MODELS.md"),
                           ("README.md", self.root / "README.md")):
            actual = path.read_bytes()
            if UPDATE:
                (GOLDEN / name).write_bytes(actual)
            self.assertEqual(actual, (GOLDEN / name).read_bytes(),
                             f"{name} differs from the golden file; rerun with VIZ_UPDATE_GOLDEN=1 if intended")

        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        code, out = run("render", self.model, "--root", self.root, "--readme-insert")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("unchanged"), 2, out)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_blocks_side_by_side(self):
        run("render", self.model, "--root", self.root, "--readme-insert")
        readme = (self.root / "README.md").read_text()
        self.assertLess(readme.index("<!-- visualizer:end -->"), readme.index("<!-- visualizer:data-models:start"))
        self.assertLess(readme.index("<!-- visualizer:data-models:end -->"), readme.index("## Quick start"))
        # The architecture renderer still sees exactly one block of its own.
        lines = readme.splitlines(keepends=True)
        self.assertEqual([len(x) for x in viz._marker_lines(lines)], [1, 1])

    def test_hand_edits_stop_the_render(self):
        run("render", self.model, "--root", self.root, "--readme-insert")
        doc = self.root / "docs/data-models/DATA_MODELS.md"
        doc.write_text(doc.read_text().replace("System of record", "Sistema"))
        code, out = run("render", self.model, "--root", self.root)
        self.assertEqual(code, 3)
        self.assertIn("was edited by hand", out)
        code, out = run("render", self.model, "--root", self.root, "--force")
        self.assertEqual(code, 0, out)

    def test_example_matches_its_snapshots(self):
        """The example's observed facts are exactly what merging the recorded snapshots produces."""
        m = example_model()
        for name in sorted(p.name for p in SNAPSHOTS.glob("*.json")):
            changes = merge.merge(m, json.loads((SNAPSHOTS / name).read_text()))
            self.assertEqual((changes["added"], changes["removed"], changes["changed"]), ([], [], {}), name)
        self.assertEqual(m, example_model())


class ReadmePlacementTests(unittest.TestCase):
    ARCH = ("# t\n\nIntro.\n\n<!-- visualizer:start path=a.json hash=000000000000 -->\n## Architecture\n\nx\n"
            "<!-- visualizer:end -->\n\n## Usage\n\ny\n")

    def test_after_intro_never_lands_inside_another_block(self):
        new, status = viz.upsert_readme(self.ARCH, "## Data models\n\nz", "d.json", insert=True,
                                        marker=render.MARKER)
        self.assertEqual(status, "inserted")
        self.assertIn("<!-- visualizer:end -->\n\n<!-- visualizer:data-models:start", new)

    def test_before_a_heading_inside_another_block_goes_before_it(self):
        new, _ = viz.upsert_readme(self.ARCH, "## Data models\n\nz", "d.json", insert=True,
                                   placement="before:Architecture", marker=render.MARKER)
        self.assertIn("<!-- visualizer:data-models:end -->\n\n<!-- visualizer:start", new)

    def test_architecture_insert_skips_a_data_models_block(self):
        text = ("# t\n\nIntro.\n\n<!-- visualizer:data-models:start path=d.json hash=000000000000 -->\n"
                "## Data models\n\nz\n<!-- visualizer:data-models:end -->\n\n## Usage\n")
        new, _ = viz.upsert_readme(text, "## Architecture\n\nx", "a.json", insert=True)
        self.assertIn("<!-- visualizer:data-models:end -->\n\n<!-- visualizer:start path=a.json", new)


class MarkdownTests(unittest.TestCase):
    def test_summary_detail_drops_entity_sections(self):
        m = minimal_model()
        m["meta"]["detail"] = "summary"
        doc = render.data_models_md(m, "docs/data-models.json", "docs")
        self.assertNotIn("### ", doc)
        self.assertIn("| `public.users` | table | 10 | Accounts. |", doc)

    def test_duplicate_entity_names_get_distinct_anchors(self):
        m = minimal_model()
        m["entities"][2]["name"] = "public.users"
        doc = render.data_models_md(m, "data-models.json", "")
        self.assertIn("(#publicusers)", doc)
        self.assertIn("(#publicusers-1)", doc)
        self.assertEqual(viz.github_slugs(doc) >= {"publicusers", "publicusers-1"}, True)

    def test_embedded_shapes_delete_actions_checks_and_durability(self):
        m = minimal_model()
        users, orders = m["entities"][0], m["entities"][1]
        m["relationships"][0]["on_delete"] = "cascade"
        m["relationships"].append({"from": "users.email", "to": "order-placed", "kind": "embedded"})
        m["stores"][0]["durability"] = "Durable"
        orders["observed"]["checks"] = [{"name": "ck_total", "definition": "CHECK ((total >= 0))"}]
        orders["observed_hash"] = merge.m.observed_hash(orders["observed"])
        doc = render.data_models_md(m, "data-models.json", "")
        self.assertIn("| Store | Engine | Role | Durability | Entities | Source |", doc)
        self.assertIn("| Durable | 2 |", doc)
        self.assertIn("FK → [`public.users`](#publicusers).`id`, on delete cascade", doc)
        self.assertIn("shape → [`OrderPlaced`](#orderplaced)", doc)
        self.assertIn("| [`public.users`](#publicusers).`email` | [`OrderPlaced`](#orderplaced) | embedded shape |", doc)
        self.assertIn("| foreign key, on delete cascade |", doc)
        self.assertIn("**Checks:** `ck_total`: `CHECK ((total >= 0))`", doc)
        self.assertNotIn("Durability", render.data_models_md(minimal_model(), "data-models.json", ""))

    def test_declared_type_and_ttl_fill_in_for_key_patterns(self):
        m = minimal_model()
        m["stores"].append({"id": "cache", "name": "Cache", "engine": "redis", "role": "Sessions."})
        m["entities"].append({"id": "sess-token", "store": "cache", "name": "sess:{token}", "kind": "key-pattern",
                              "description": "A session.",
                              "declared": {"evidence": ["app/auth.py"], "type": "hash", "ttl": "8h",
                                           "fields": [{"name": "user_id"}]}})
        doc = render.data_models_md(m, "data-models.json", "")
        self.assertIn("**Type:** hash (declared) · **TTL:** 8h (declared)", doc)
        hit = minimal_model()
        e = {"id": "s", "store": "db", "name": "s", "kind": "key-pattern", "description": "x",
             "declared": {"evidence": ["a.py"], "type": "json"},
             "observed": {"type": "hash", "ttl": "up to 29m", "fields": [], "indexes": []}}
        self.assertIn("**Type:** hash (code declares json) · **TTL:** up to 29m",
                      render._entity_section(e, hit["stores"][0], [], lambda i: i, "")[4])

    def test_cells_escape_pipes_and_html(self):
        self.assertEqual(render.code("a | b"), "`a \\| b`")
        self.assertEqual(render.code("x`y"), "`` x`y ``")
        self.assertEqual(render.estimate(1234), "~1.2k")
        self.assertEqual(render.estimate(2_000_000), "~2M")
        self.assertEqual(render.estimate(None), "—")


if __name__ == "__main__":
    unittest.main()
