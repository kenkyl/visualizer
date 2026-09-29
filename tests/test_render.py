import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from _helpers import EXAMPLE, GOLDEN, component, example_model, minimal_model, viz

UPDATE = os.environ.get("VIZ_UPDATE_GOLDEN") == "1"
FORBIDDEN = [r"%%\{", r"@\{", r"\bfa:", r"^\s*click\b", r"\blinkStyle\b", r"architecture-beta", r"block-beta",
             r"^\s*C4", r"^---$"]


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = viz.main([str(a) for a in argv])
    return code, out.getvalue() + err.getvalue()


def mermaid_blocks(text):
    return [code for _, code in viz.mermaid_blocks(text)]


class GoldenTests(unittest.TestCase):
    """The shipped example, rendered into a copy of arcade-test's README at cc1197d."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs" / "architecture").mkdir(parents=True)
        self.model = self.root / "docs" / "architecture" / "architecture.json"
        shutil.copy(EXAMPLE, self.model)
        shutil.copy(GOLDEN / "README.before.md", self.root / "README.md")

    def tearDown(self):
        self.tmp.cleanup()

    def test_golden_output_and_rerun(self):
        code, out = run("render", self.model, "--root", self.root, "--readme-insert")
        self.assertEqual(code, 0, out)
        for name, path in (("ARCHITECTURE.md", self.root / "docs/architecture/ARCHITECTURE.md"),
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

    def test_readme_block_appears_once_before_quick_start(self):
        run("render", self.model, "--root", self.root, "--readme-insert")
        run("render", self.model, "--root", self.root, "--readme-insert")
        readme = (self.root / "README.md").read_text()
        self.assertEqual(readme.count("<!-- visualizer:start"), 1)
        self.assertEqual(readme.count("<!-- visualizer:end -->"), 1)
        self.assertLess(readme.index("<!-- visualizer:end -->"), readme.index("## Quick start"))
        self.assertIn("path=docs/architecture/architecture.json", readme)

    def test_expected_content(self):
        ids = {c["id"] for c in example_model()["components"]}
        self.assertTrue({"steps", "agent-core", "arcade-helpers", "memory", "redis", "anthropic",
                         "arcade-engine", "token-vault", "mcp-gateway", "my-server", "github-api",
                         "github-app"} <= ids)
        text = (GOLDEN / "ARCHITECTURE.md").read_text()
        self.assertIn("OAuth tokens never leave Arcade", text)
        self.assertIn("## Where state lives", text)
        self.assertEqual(len(mermaid_blocks(text)), 3)

    def test_no_secrets_or_sample_values_in_output(self):
        for name in ("ARCHITECTURE.md", "README.md"):
            text = (GOLDEN / name).read_text()
            for needle in ("sk-ant", "arc_", "Your tools can have secrets injected at runtime"):
                self.assertNotIn(needle, text, f"{needle!r} in {name}")


class SafeSubsetTests(unittest.TestCase):
    def all_diagrams(self, model):
        views = viz.resolve_views(model)
        return ([viz.flowchart(model, v) for v in views]
                + [viz.sequence(model, f) for f in model.get("flows") or []])

    def test_only_safe_mermaid(self):
        for code in self.all_diagrams(example_model()):
            self.assertRegex(code.splitlines()[0], r"^(flowchart (TB|LR|RL|BT)|sequenceDiagram)$")
            for pattern in FORBIDDEN:
                self.assertIsNone(re.search(pattern, code, re.M), pattern)
            ids = set(re.findall(r"\b([cb]_[A-Za-z0-9_]+)", code))
            self.assertTrue(all(re.match(r"^[cb]_[a-z0-9_]+$", i) for i in ids), ids)

    def test_labels_are_always_quoted(self):
        code = viz.flowchart(example_model(), viz.resolve_views(example_model())[0])
        for line in code.splitlines():
            if re.match(r"\s+c_\w+[\[(]", line):
                self.assertRegex(line, r'^\s+c_\w+(\[\(|\(\[|\[\[|\[)"[^"]*"(\)\]|\]\)|\]\]|\])$')
            if "|" in line:
                self.assertRegex(line, r'\|"[^"]*"\|')


class EscapeTests(unittest.TestCase):
    def test_flow_escapes(self):
        self.assertEqual(viz.esc_flow('say "hi" <b> & C# `x`'),
                         "say #quot;hi#quot; #lt;b#gt; #amp; C#35; #96;x#96;")
        self.assertEqual(viz.esc_flow("end; ünïcødé → ok"), "end; ünïcødé → ok")

    def test_seq_escapes_semicolon_and_hash_in_one_pass(self):
        self.assertEqual(viz.esc_seq("a;b#c"), "a#59;b#35;c")
        self.assertEqual(viz.esc_seq("#59;"), "#35;59#59;")

    def test_hostile_names_render(self):
        m = minimal_model()
        component(m, "cli")["name"] = 'end "quoted" <tag> & #1; `code`'
        component(m, "api")["tech"] = "subgraph; graph TD"
        m["edges"][0]["label"] = "--> not an arrow"
        m["flows"] = [{"id": "f", "name": "F", "steps": [
            {"from": "cli", "to": "api", "msg": "end; Note over x: y #z", "kind": "call"}]}]
        flow = viz.flowchart(m, viz.resolve_views(m)[0])
        self.assertIn('c_cli(["<b>end #quot;quoted#quot; #lt;tag#gt; #amp; #35;1; #96;code#96;</b>"])', flow)
        self.assertIn('|"1. --#gt; not an arrow"|', flow)
        seq = viz.sequence(m, m["flows"][0])
        self.assertIn("participant c_cli as end #quot;quoted#quot; #lt;tag#gt; #amp; #35;1#59; #96;code#96;", seq)
        self.assertIn("c_cli->>c_api: end#59; Note over x: y #35;z", seq)


class MarkdownTests(unittest.TestCase):
    def render(self, model):
        return viz.architecture_md(model, "docs/arch/architecture.json", "docs/arch")

    def test_table_cells_escape_pipes_and_html(self):
        m = minimal_model()
        component(m, "cli")["responsibility"] = "Reads a|b and List<T>, not `x|y`"
        text = self.render(m)
        self.assertIn("Reads a\\|b and List&lt;T&gt;, not `x\\|y`", text)

    def test_relative_evidence_links(self):
        text = self.render(minimal_model())
        self.assertIn("[`cli.py`](../../cli.py)", text)
        self.assertIn("[`api/`](../../api/)", text)
        self.assertIn("[`README.md#storage`](../../README.md#storage)", text)

    def test_inferred_components(self):
        m = minimal_model()
        component(m, "api")["inferred"] = True
        text = self.render(m)
        self.assertIn("<b>API (inferred)</b>", text)
        self.assertIn("style c_api stroke-dasharray:3 3", text)
        self.assertIn("## Assumptions", text)
        self.assertNotIn("## Assumptions", self.render(minimal_model()))

    def test_notes_are_kept_verbatim(self):
        m = minimal_model()
        m["meta"]["notes"] = [{"text": "Focus on auth.\n\n- <b>not</b> escaped | kept", "added": "2026-09-29"}]
        text = self.render(m)
        self.assertIn("> Focus on auth.\n>\n> - <b>not</b> escaped | kept\n>\n> <sub>Added 2026-09-29</sub>", text)

    def test_footer_hash_and_hand_edit_detection(self):
        text = self.render(minimal_model())
        self.assertEqual(viz.generated_state(text), "clean")
        self.assertEqual(viz.generated_state(text.replace("\n", "\r\n")), "clean")
        self.assertEqual(viz.generated_state(text.replace("Runs commands.", "Runs things.")), "edited")
        self.assertEqual(viz.generated_state("# My own notes\n"), "foreign")
        self.assertRegex(text, r"\n\n---\n\n<sub>Generated by visualizer .* Content hash `[0-9a-f]{12}`\.</sub>\n$")

    def test_footer_uses_model_sha_not_reviewed_sha(self):
        m = minimal_model()
        m["meta"].update(model_sha="a" * 40, model_date="2026-09-29", reviewed_sha="b" * 40)
        text = self.render(m)
        self.assertIn("Model as of commit `aaaaaaa` (2026-09-29)", text)
        m["meta"]["reviewed_sha"] = "c" * 40
        self.assertEqual(text, self.render(m))


class RenderCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.model = self.root / "architecture.json"
        m = minimal_model()
        m["meta"]["readme"] = {"embed": True}
        self.model.write_text(json.dumps(m))

    def tearDown(self):
        self.tmp.cleanup()

    def test_hand_edits_are_not_overwritten(self):
        self.assertEqual(run("render", self.model, "--root", self.root)[0], 0)
        arch = self.root / "ARCHITECTURE.md"
        arch.write_text(arch.read_text().replace("Runs commands.", "Runs things."))
        code, out = run("render", self.model, "--root", self.root)
        self.assertEqual(code, 3)
        self.assertIn("edited by hand", out)
        self.assertIn("Runs things.", arch.read_text())
        self.assertEqual(run("render", self.model, "--root", self.root, "--force")[0], 0)
        self.assertNotIn("Runs things.", arch.read_text())

    def test_foreign_architecture_md_is_not_overwritten(self):
        (self.root / "ARCHITECTURE.md").write_text("# Hand-written\n")
        code, out = run("render", self.model, "--root", self.root)
        self.assertEqual(code, 3)
        self.assertIn("wasn't generated by visualizer", out)

    def test_missing_readme_and_markers(self):
        code, out = run("render", self.model, "--root", self.root)
        self.assertEqual(code, 0)
        self.assertIn("doesn't exist; not created", out)
        self.assertFalse((self.root / "README.md").exists())

        (self.root / "README.md").write_text("# Demo\n")
        code, out = run("render", self.model, "--root", self.root)
        self.assertIn("no visualizer markers; not changed", out)
        self.assertEqual((self.root / "README.md").read_text(), "# Demo\n")

        run("render", self.model, "--root", self.root, "--readme-insert")
        self.assertIn("<!-- visualizer:start", (self.root / "README.md").read_text())

    def test_readme_create(self):
        run("render", self.model, "--root", self.root, "--readme-create")
        text = (self.root / "README.md").read_text()
        self.assertTrue(text.startswith("# demo\n\n<!-- visualizer:start path=architecture.json hash="))

    def test_dry_run_writes_nothing(self):
        code, out = run("render", self.model, "--root", self.root, "--dry-run", "--readme-create")
        self.assertEqual(code, 0)
        self.assertIn("would be created", out)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["architecture.json"])

    def test_invalid_model_is_not_rendered(self):
        m = json.loads(self.model.read_text())
        m["edges"][0]["to"] = "ghost"
        self.model.write_text(json.dumps(m))
        code, out = run("render", self.model, "--root", self.root)
        self.assertEqual(code, 1)
        self.assertFalse((self.root / "ARCHITECTURE.md").exists())

    def test_preview_page(self):
        out = self.root / "p.html"
        code, _ = run("preview", self.model, "--out", out, "--mermaid", "11.17.2")
        self.assertEqual(code, 0)
        page = out.read_text()
        self.assertIn("cdn.jsdelivr.net/npm/mermaid@11.17.2/", page)
        self.assertNotIn("</script>\",", page)
        data = re.search(r'<script type="application/json" id="diagrams">(.*?)</script>', page, re.S).group(1)
        self.assertEqual([d["title"] for d in json.loads(data)], ["Overview"])


if __name__ == "__main__":
    unittest.main()
