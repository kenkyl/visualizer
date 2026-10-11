"""authtrace render: trace.json v0 → the single-file viewer (PLAN.md §15, M1)."""

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from authtrace.render import RenderError, embed_json, render  # noqa: E402
from authtrace.trace import load, problems  # noqa: E402

UPDATE = os.environ.get("AUTHTRACE_UPDATE_GOLDEN") == "1"
FIXTURE = ROOT / "tests" / "fixtures" / "authtrace" / "memes-agent"
REFERENCE = ROOT / "docs" / "authtrace" / "reference" / "memes-agent-flow.html"
LAUNCHER = ROOT / "scripts" / "authtrace.py"


def fixture() -> dict:
    return load(FIXTURE / "trace.json")


def payload(page: str) -> dict:
    m = re.search(r'<script type="application/json" id="trace">(.*?)</script>', page, re.S)
    return json.loads(m.group(1))


class GoldenTests(unittest.TestCase):
    def test_memes_agent_matches_golden(self):
        actual = render(fixture())
        golden = FIXTURE / "flow.html"
        if UPDATE:
            golden.write_text(actual, encoding="utf-8")
        self.assertEqual(actual, golden.read_text(encoding="utf-8"),
                         "flow.html differs from the golden file; rerun with AUTHTRACE_UPDATE_GOLDEN=1 if intended")

    def test_render_does_not_touch_the_trace(self):
        trace = fixture()
        before = copy.deepcopy(trace)
        page = render(trace)
        self.assertEqual(trace, before)
        self.assertEqual(payload(page)["trace"], before)


class ReferenceParityTests(unittest.TestCase):
    """The fixture carries the same tours, steps, goals and claims as today's artifact."""

    @classmethod
    def setUpClass(cls):
        cls.ref = REFERENCE.read_text(encoding="utf-8")
        cls.trace = fixture()
        cls.steps = {s["id"]: s for s in cls.trace["narrative"]["steps"]}

    def ref_titles(self, const):
        block = self.ref.split(f"const {const} = [", 1)[1].split("\n];", 1)[0]
        return re.findall(r'^\s*\{ t: "(.*?)"', block, re.M)

    def test_step_titles_in_order(self):
        flows = {f["id"]: f for f in self.trace["narrative"]["flows"]}
        for flow, const in (("live", "LIVE"), ("web", "WEB")):
            titles = [self.steps[sid]["title"] for sid in flows[flow]["steps"]]
            self.assertEqual(titles, [t.replace('\\"', '"') for t in self.ref_titles(const)], flow)

    def test_goals_and_claims(self):
        nar = self.trace["narrative"]
        self.assertEqual([g[0] for g in nar["goals"].values()],
                         re.findall(r'^\s*\d: \["(.*?)"', self.ref.split("const GOALS", 1)[1].split("};", 1)[0], re.M))
        claims_block = self.ref.split("const CLAIMS = [", 1)[1].split("];", 1)[0]
        self.assertEqual([c["claim"] for c in nar["inspector"]["claims"]],
                         re.findall(r'^\s*\["(.*?)"', claims_block, re.M))

    def test_no_inline_html_left_in_narrative(self):
        # Prose is a markdown subset; wire blocks are literal text and may hold <placeholders>.
        for s in self.trace["narrative"]["steps"]:
            for key in ("what", "note", "inv", "roles"):
                self.assertNotRegex(s.get(key, ""), r"<\w", f"{s['id']}.{key}")


class EmbedTests(unittest.TestCase):
    def test_trace_strings_cannot_close_the_script(self):
        trace = fixture()
        trace["narrative"]["steps"][0]["what"] = "</script><script>alert(1)</script><!--"
        page = render(trace)
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<!--\"", page)
        self.assertEqual(payload(page)["trace"]["narrative"]["steps"][0]["what"],
                         "</script><script>alert(1)</script><!--")

    def test_embed_json_escapes_markup(self):
        self.assertEqual(embed_json({"a": "<&>"}), '{\n "a": "\\u003c\\u0026\\u003e"\n}')

    def test_title_is_escaped(self):
        trace = fixture()
        trace["narrative"]["header"]["page_title"] = "<b>x</b>"
        self.assertIn("<title>&lt;b&gt;x&lt;/b&gt;</title>", render(trace))


class ProblemTests(unittest.TestCase):
    def assertProblem(self, trace, fragment):
        found = problems(trace)
        self.assertTrue(any(fragment in p for p in found), f"no problem mentioning {fragment!r} in {found}")

    def test_fixture_is_renderable(self):
        self.assertEqual(problems(fixture()), [])

    def test_schema_version(self):
        trace = fixture()
        trace["meta"]["schema_version"] = 1
        self.assertProblem(trace, "schema_version must be 0")

    def test_every_fact_needs_a_confidence(self):
        trace = fixture()
        del trace["exchanges"][0]["confidence"]
        trace["nodes"][0]["confidence"] = "probably"
        self.assertProblem(trace, "exchanges[0] (x1): confidence")
        self.assertProblem(trace, "nodes[0] (browser): confidence")

    def test_narrative_references(self):
        trace = fixture()
        step = trace["narrative"]["steps"][0]
        step["exchanges"] = ["x99"]
        step["segs"] = [["no-such-edge", 1, "hi", "tok"]]
        step["nodes"] = ["ghost"]
        trace["narrative"]["flows"][0]["steps"].append("live-99")
        for fragment in ("unknown exchange 'x99'", "bad seg", "unknown node 'ghost'", "unknown step 'live-99'"):
            self.assertProblem(trace, fragment)

    def test_layout_required_until_auto_layout(self):
        trace = fixture()
        del trace["layout"]["nodes"]["kc"]
        del trace["layout"]["edges"]["gw-mcp"]
        self.assertProblem(trace, "no box for 'kc'")
        self.assertProblem(trace, "no path for 'gw-mcp'")

    def test_render_refuses_problems(self):
        trace = fixture()
        trace["edges"][0]["to"] = "nowhere"
        with self.assertRaises(RenderError) as cm:
            render(trace)
        self.assertTrue(any("nowhere" in p for p in cm.exception.problems))


class NoNarrativeTests(unittest.TestCase):
    def test_a_facts_only_trace_renders_with_a_derived_tour(self):
        trace = fixture()
        del trace["narrative"]
        data = payload(render(trace))
        self.assertNotIn("narrative", data["trace"])
        self.assertEqual(len(data["derived"]["narrative"]["steps"]), len(trace["exchanges"]))


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(LAUNCHER), *map(str, args)], capture_output=True, text=True)

    def test_render_writes_then_reports_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "flow.html"
            first = self.run_cli("render", FIXTURE / "trace.json", "-o", out)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("written", first.stdout)
            self.assertEqual(out.read_text(encoding="utf-8"), render(fixture()))
            second = self.run_cli("render", FIXTURE / "trace.json", "-o", out)
            self.assertIn("unchanged", second.stdout)

    def test_default_output_is_next_to_the_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "trace.json"
            src.write_text((FIXTURE / "trace.json").read_text(encoding="utf-8"), encoding="utf-8")
            result = self.run_cli("render", src)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(tmp) / "flow.html").exists())

    def test_broken_trace_lists_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = fixture()
            trace["edges"][0]["to"] = "nowhere"
            src = Path(tmp) / "trace.json"
            src.write_text(json.dumps(trace), encoding="utf-8")
            result = self.run_cli("render", src)
            self.assertEqual(result.returncode, 1)
            self.assertIn("unknown to node 'nowhere'", result.stderr)
            self.assertFalse((Path(tmp) / "flow.html").exists())

    def test_unreadable_trace(self):
        result = self.run_cli("render", ROOT / "no-such-trace.json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("can't read", result.stderr)


if __name__ == "__main__":
    unittest.main()
