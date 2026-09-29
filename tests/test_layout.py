import unittest

from _helpers import example_model, minimal_model, viz


def lengths(model):
    view = viz.resolve_views(model)[0]
    return {(e["from"], e["to"]): n for e, n in zip(view["edges"], viz.edge_lengths(model, view))}


class EdgeLengthTests(unittest.TestCase):
    def test_edges_leaving_a_boundary_land_below_it(self):
        got = lengths(example_model())
        # Leaving "Your machine" for Anthropic or Arcade Cloud skips past the machine's last rank.
        self.assertEqual(got[("agent-core", "anthropic")], 2)
        self.assertEqual(got[("arcade-helpers", "arcade-engine")], 2)
        self.assertEqual(got[("claude-code", "mcp-gateway")], 3)
        # Inside one boundary, links stay short.
        self.assertEqual(got[("you", "steps")], 1)
        self.assertEqual(got[("memory", "redis")], 1)
        self.assertTrue(all(1 <= n <= viz.MAX_EDGE_LENGTH for n in got.values()))

    def test_cycles_terminate_and_stay_short(self):
        m = minimal_model()
        m["edges"].append({"from": "db", "to": "cli", "label": "notifies", "kind": "async"})
        m["edges"].append({"from": "api", "to": "cli", "label": "replies", "kind": "sync"})
        got = lengths(m)
        self.assertEqual(set(got.values()), {1})  # the two boundaries call each other: no stacking

    def test_unbounded_components_count_as_their_own_zone(self):
        m = minimal_model()
        m["components"].append({"id": "ext", "name": "Ext", "kind": "external", "responsibility": "r",
                                "evidence": ["x"]})
        m["edges"].append({"from": "cli", "to": "ext", "label": "calls", "kind": "sync"})
        self.assertEqual(lengths(m)[("cli", "ext")], 1)  # the laptop only spans one rank

    def test_arrow_syntax(self):
        self.assertEqual([viz.arrow("sync", n) for n in (1, 2, 3)], ["-->", "--->", "---->"])
        self.assertEqual([viz.arrow("async", n) for n in (1, 2)], ["-.->", "-..->"])
        self.assertEqual(viz.arrow("deploy", 3), "-...->")
        self.assertEqual([viz.arrow("auth", n) for n in (1, 2)], ["==>", "===>"])


class LayoutOrderTests(unittest.TestCase):
    def test_boundaries_appear_where_their_first_component_does(self):
        m = minimal_model()
        m["boundaries"].append({"id": "inner", "name": "Inner", "kind": "container", "parent": "cloud"})
        m["components"].insert(0, {"id": "cache", "name": "Cache", "kind": "datastore", "boundary": "inner",
                                   "responsibility": "r", "evidence": ["x"],
                                   "holds": [{"data": "d", "lifetime": "l"}]})
        items = viz.layout(m, m["components"])
        self.assertEqual([i[1]["id"] for i in items], ["cloud", "local"])
        cloud_children = items[0][2]
        self.assertEqual([(i[0], i[1]["id"]) for i in cloud_children],
                         [("boundary", "inner"), ("component", "api"), ("component", "db")])

    def test_subgraphs_are_nested_in_the_flowchart(self):
        m = minimal_model()
        m["boundaries"].append({"id": "inner", "name": "Inner", "kind": "container", "parent": "cloud"})
        m["components"][2]["boundary"] = "inner"
        code = viz.flowchart(m, viz.resolve_views(m)[0])
        self.assertIn('  subgraph b_cloud["Cloud"]\n    c_api["<b>API</b>"]\n'
                      '    subgraph b_inner["Inner"]\n      c_db[("<b>Database</b>")]\n    end\n  end', code)

    def test_views_filter_components_and_edges(self):
        m = minimal_model(views=[{"id": "front", "title": "Front", "include": ["cli", "api"], "direction": "LR"}])
        view = viz.resolve_views(m)[0]
        self.assertEqual([c["id"] for c in view["components"]], ["cli", "api"])
        self.assertEqual(len(view["edges"]), 1)
        code = viz.flowchart(m, view)
        self.assertTrue(code.startswith("flowchart LR"))
        self.assertNotIn("c_db", code)


if __name__ == "__main__":
    unittest.main()
