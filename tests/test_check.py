import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _helpers import check, component, example_model, minimal_model, viz


def has(messages, *fragments):
    return any(all(f in m for f in fragments) for m in messages)


class SchemaTests(unittest.TestCase):
    def test_example_model_is_valid(self):
        rep = check(example_model())
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.warnings, [])

    def test_minimal_model_is_valid(self):
        self.assertEqual(check(minimal_model()).errors, [])

    def test_unknown_field_is_an_error(self):
        m = minimal_model()
        m["components"][0]["colour"] = "red"
        self.assertTrue(has(check(m).errors, "components[cli].colour", "unknown field"))

    def test_generator_and_schema_version(self):
        m = minimal_model()
        m["meta"]["generator"] = "other"
        m["meta"]["schema_version"] = 2
        errors = check(m).errors
        self.assertTrue(has(errors, "meta.generator"))
        self.assertTrue(has(errors, "meta.schema_version"))

    def test_ids_must_be_kebab_case(self):
        for bad in ("Agent", "agent_core", "agent--core", "-agent", "9lives"):
            m = minimal_model()
            m["components"][0]["id"] = bad
            m["edges"][0]["from"] = bad
            self.assertTrue(has(check(m).errors, "kebab-case"), bad)

    def test_duplicate_ids(self):
        m = minimal_model()
        m["components"][1]["id"] = "cli"
        self.assertTrue(has(check(m).errors, "duplicate component id"))

    def test_edge_endpoints_must_exist(self):
        m = minimal_model()
        m["edges"][0]["to"] = "nowhere"
        self.assertTrue(has(check(m).errors, "edges[0].to", "unknown component 'nowhere'"))

    def test_self_edge(self):
        m = minimal_model()
        m["edges"][0]["to"] = "cli"
        self.assertTrue(has(check(m).errors, "two different components"))

    def test_bool_is_not_a_step_number(self):
        m = minimal_model()
        m["edges"][0]["step"] = True
        self.assertTrue(has(check(m).errors, "edges[0].step", "an integer"))

    def test_duplicate_step(self):
        m = minimal_model()
        m["edges"][1]["step"] = 1
        self.assertTrue(has(check(m).errors, "step 1 is already used"))

    def test_text_limits(self):
        m = minimal_model()
        m["components"][0]["responsibility"] = "x" * 121
        m["edges"][0]["label"] = "y" * 41
        m["edges"][1]["label"] = "z" * 30
        rep = check(m)
        self.assertTrue(has(rep.errors, "responsibility", "limit is 120"))
        self.assertTrue(has(rep.errors, "edges[0].label", "limit is 40"))
        self.assertTrue(has(rep.warnings, "edges[1].label", "28 or fewer"))

    def test_single_line_text(self):
        m = minimal_model()
        m["edges"][0]["label"] = "two\nlines"
        self.assertTrue(has(check(m).errors, "single line"))

    def test_node_limits(self):
        def with_nodes(n):
            m = minimal_model(edges=[])
            m["components"] = [{"id": f"n{i}", "name": f"N{i}", "kind": "module", "responsibility": "r",
                                "evidence": ["x.py"]} for i in range(n)]
            return check(m)
        self.assertFalse(has(with_nodes(15).warnings, "nodes"))
        self.assertTrue(has(with_nodes(16).warnings, "16 nodes"))
        self.assertTrue(has(with_nodes(26).errors, "26 nodes", "limit is 25"))

    def test_edge_limits(self):
        m = minimal_model()
        m["components"] = [{"id": f"n{i}", "name": f"N{i}", "kind": "module", "responsibility": "r",
                            "evidence": ["x.py"]} for i in range(10)]
        pairs = [(a, b) for a in range(10) for b in range(10) if a != b]
        m["edges"] = [{"from": f"n{a}", "to": f"n{b}", "label": "x", "kind": "sync"} for a, b in pairs[:41]]
        self.assertTrue(has(check(m).errors, "41 edges"))

    def test_boundary_rules(self):
        m = minimal_model()
        m["boundaries"] = [{"id": f"b{i}", "name": f"B{i}", "kind": "local"} for i in range(7)]
        for c in m["components"]:
            c["boundary"] = "b0"
        self.assertTrue(has(check(m).errors, "boundaries", "limit is 6"))

        m = minimal_model()
        m["boundaries"][0]["parent"] = "cloud"
        m["boundaries"][1]["parent"] = "local"
        self.assertTrue(has(check(m).errors, "cycle"))

        m = minimal_model()
        m["components"][0]["boundary"] = "mars"
        self.assertTrue(has(check(m).errors, "unknown boundary 'mars'"))

    def test_holds_only_on_stores(self):
        m = minimal_model()
        component(m, "api")["holds"] = [{"data": "cache", "lifetime": "1h"}]
        self.assertTrue(has(check(m).errors, "components[api].holds", "datastore"))

    def test_evidence_required_unless_inferred(self):
        m = minimal_model()
        component(m, "api")["evidence"] = []
        self.assertTrue(has(check(m).errors, "components[api].evidence", "at least one"))
        component(m, "api")["inferred"] = True
        rep = check(m)
        self.assertEqual(rep.errors, [])
        self.assertTrue(has(rep.warnings, "inferred component"))

    def test_evidence_paths_stay_in_repo(self):
        for bad in ("/etc/passwd", "../other/x.py", "doc:..\\x.md"):
            m = minimal_model()
            component(m, "cli")["evidence"] = [bad]
            self.assertTrue(has(check(m).errors, "inside the repo"), bad)

    def test_flow_rules(self):
        m = minimal_model(flows=[{"id": "f", "name": "F", "steps": [
            {"from": "cli", "to": "ghost", "msg": "hi", "kind": "call"},
            {"from": "cli", "to": "cli", "msg": "self", "kind": "call"},
            {"from": "api", "to": "api", "msg": "thinks", "kind": "note"},
        ]}])
        errors = check(m).errors
        self.assertTrue(has(errors, "unknown component 'ghost'"))
        self.assertTrue(has(errors, "steps[1]", "use a note"))
        self.assertFalse(has(errors, "steps[2]"))

    def test_flow_limits(self):
        step = {"from": "cli", "to": "api", "msg": "x", "kind": "call"}
        m = minimal_model(flows=[{"id": f"f{i}", "name": "F", "steps": [step]} for i in range(4)])
        self.assertTrue(has(check(m).errors, "flows", "limit is 3"))
        m = minimal_model(flows=[{"id": "f", "name": "F", "steps": [step] * 13}])
        self.assertTrue(has(check(m).errors, "13 steps"))

    def test_views(self):
        m = minimal_model(views=[{"id": "v", "title": "V", "include": ["cli", "nope"], "direction": "UP"}])
        errors = check(m).errors
        self.assertTrue(has(errors, "include[1]", "unknown component 'nope'"))
        self.assertTrue(has(errors, "views[v].direction"))

    def test_readme_placement(self):
        m = minimal_model()
        m["meta"]["readme"] = {"embed": True, "placement": "middle"}
        self.assertTrue(has(check(m).errors, "meta.readme.placement"))
        m["meta"]["readme"] = {"embed": True, "placement": "before:Quick start"}
        self.assertEqual(check(m).errors, [])


class SecretLintTests(unittest.TestCase):
    def lint(self, text):
        m = minimal_model()
        component(m, "cli")["tech"] = text
        return check(m).errors

    def test_flags_secrets_and_placeholders(self):
        for text in ("sk-ant-...", "sk-ant-api03-abc", "sk-" + "a" * 20, "ghp_" + "A1b2" * 6,
                     "github_pat_" + "x" * 30, "arc_...", "AKIA" + "ABCDEFGHIJKLMNOP",
                     "redis://user:hunter2@host:6379", "xoxb-1234567890-abc",
                     "-----BEGIN RSA PRIVATE KEY-----"):
            self.assertTrue(has(self.lint(text), "never put secrets"), text)

    def test_ordinary_text_passes(self):
        for text in ("arcade_helpers", "desk-ant-farm", "redis://localhost:6379/0", "task-runner",
                     "https://api.github.com", "search_arc_index"):
            self.assertEqual(self.lint(text), [], text)

    def test_notes_are_linted_too(self):
        m = minimal_model()
        m["meta"]["notes"] = [{"text": "key is sk-ant-xyz", "added": "2026-09-29"}]
        self.assertTrue(has(check(m).errors, "meta.notes[0].text"))


class RepoCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "cli.py").write_text("print()\n")
        (self.root / "api").mkdir()
        (self.root / "api" / "main.py").write_text("app = 1\n")
        (self.root / "README.md").write_text("# Demo\n\n## Storage\n\n```\n# not a heading\n```\n")
        (self.root / ".env.example").write_text(
            "API_TOKEN=\"tok-live-abcdef\"\nREDIS_URL=redis://localhost:6379/0\nSHORT_KEY=abc\n")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})

    def test_valid_without_git(self):
        rep = check(minimal_model(), self.root)
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.warnings, [])

    def test_missing_evidence(self):
        m = minimal_model()
        component(m, "cli")["evidence"] = ["cli2.py"]
        self.assertTrue(has(check(m, self.root).errors, "'cli2.py' does not exist"))

    def test_anchors(self):
        m = minimal_model()
        component(m, "db")["evidence"] = ["doc:README.md#not-a-heading"]
        self.assertTrue(has(check(m, self.root).errors, "#not-a-heading"))
        component(m, "db")["evidence"] = ["doc:cli.py#main"]
        self.assertTrue(has(check(m, self.root).errors, "only supported on Markdown"))

    def test_secret_files_are_never_evidence(self):
        for path in (".env", "config/.env.local", "certs/server.pem", "id_ed25519", "keys/app.key"):
            m = minimal_model()
            component(m, "cli")["evidence"] = [path]
            self.assertTrue(has(check(m, self.root).errors, "secrets file"), path)
        m = minimal_model()
        component(m, "cli")["evidence"] = [".env.example"]
        self.assertEqual(check(m, self.root).errors, [])

    def test_sample_secret_values_are_flagged(self):
        m = minimal_model()
        component(m, "api")["tech"] = "uses tok-live-abcdef"
        self.assertTrue(has(check(m, self.root).errors, "sample value of API_TOKEN", ".env.example"))
        component(m, "api")["tech"] = "redis://localhost:6379/0, abc"  # not secret-named, or too short
        self.assertEqual(check(m, self.root).errors, [])

    def test_untracked_evidence_warns(self):
        self.git("init", "-q")
        self.git("add", "cli.py", "README.md", ".env.example")
        self.git("commit", "-q", "-m", "init")
        rep = check(minimal_model(), self.root)
        self.assertEqual(rep.errors, [])
        self.assertTrue(has(rep.warnings, "'api/' isn't tracked"))
        self.git("add", "api/main.py")
        self.assertEqual(check(minimal_model(), self.root).warnings, [])


class SlugTests(unittest.TestCase):
    def test_github_slugs(self):
        md = "\n".join([
            "# Arcade.dev: hands-on walkthrough",
            "## Step 2: Call a tool as a user (`uv run 02_direct_call.py`)",
            "## Step 5: Same tools, zero code, in Claude Code",
            "### The fix: bring your own app",
            "## [Linked](http://x) *emphasis* heading ##",
            "## Setup",
            "## Setup",
            "```",
            "## Inside a fence",
            "```",
            "#hashtag is not a heading",
        ])
        self.assertEqual(viz.github_slugs(md), {
            "arcadedev-hands-on-walkthrough",
            "step-2-call-a-tool-as-a-user-uv-run-02_direct_callpy",
            "step-5-same-tools-zero-code-in-claude-code",
            "the-fix-bring-your-own-app",
            "linked-emphasis-heading",
            "setup",
            "setup-1",
        })


@unittest.skipUnless(os.environ.get("ARCADE_TEST_REPO"), "set ARCADE_TEST_REPO to an arcade-test checkout at cc1197d")
class ExampleAgainstRepoTests(unittest.TestCase):
    def test_example_checks_against_arcade_test(self):
        rep = check(example_model(), os.environ["ARCADE_TEST_REPO"])
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.warnings, [])


class CliTests(unittest.TestCase):
    def test_check_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "architecture.json"
            m = minimal_model()
            path.write_text(json.dumps(m))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(viz.main(["check", str(path), "--no-files"]), 0)
            m["edges"][0]["to"] = "ghost"
            path.write_text(json.dumps(m))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(viz.main(["check", str(path), "--no-files"]), 1)
            self.assertIn("unknown component 'ghost'", out.getvalue())


if __name__ == "__main__":
    unittest.main()
