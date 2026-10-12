import contextlib
import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _helpers import GOLDEN, component, minimal_model, viz

UPDATE = os.environ.get("VIZ_UPDATE_GOLDEN") == "1"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = viz.main([str(a) for a in argv])
    return code, out.getvalue() + err.getvalue()


def git(root, *argv):
    return subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *argv],
                          capture_output=True, text=True, check=True).stdout.strip()


def changed(old, new):
    return viz.diff_models(old, new)[0]


def keys(changes, area, op):
    return [c["key"] for c in changes if c["area"] == area and c["op"] == op]


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.old = minimal_model()
        self.new = copy.deepcopy(self.old)

    def test_identical_models_have_no_changes(self):
        self.assertEqual(viz.diff_models(self.old, self.new), ([], []))

    def test_bookkeeping_is_not_a_change(self):
        self.new["meta"].update(model_sha="a" * 40, model_date="2026-10-12", reviewed_sha="b" * 40, dirty=True)
        self.assertEqual(changed(self.old, self.new), [])

    def test_component_added_removed_changed(self):
        self.new["components"].append({"id": "worker", "name": "Worker", "kind": "module", "boundary": "cloud",
                                       "responsibility": "Runs jobs.", "evidence": ["api/"]})
        component(self.new, "api")["responsibility"] = "Serves more."
        del self.new["components"][0]
        ch = changed(self.old, self.new)
        self.assertEqual(keys(ch, "component", "added"), ["worker"])
        self.assertEqual(keys(ch, "component", "removed"), ["cli"])
        self.assertEqual(keys(ch, "component", "changed"), ["api"])
        fields = next(c for c in ch if c["op"] == "changed")["fields"]
        self.assertEqual(fields, [("responsibility", "Serves requests.", "Serves more.")])

    def test_inferred_false_equals_missing(self):
        component(self.new, "api")["inferred"] = False
        self.assertEqual(changed(self.old, self.new), [])
        component(self.new, "api")["inferred"] = True
        self.assertEqual(keys(changed(self.old, self.new), "component", "changed"), ["api"])

    def test_edges_are_matched_by_endpoints_and_kind(self):
        self.new["edges"][0]["label"] = "calls over HTTP"
        self.new["edges"].append({"from": "cli", "to": "db", "label": "reads", "kind": "sync"})
        ch = changed(self.old, self.new)
        self.assertEqual(keys(ch, "edge", "changed"), [("cli", "api", "sync")])
        self.assertEqual(keys(ch, "edge", "added"), [("cli", "db", "sync")])

    def test_boundaries_flows_views_system_and_notes(self):
        self.new["boundaries"].append({"id": "saas", "name": "SaaS", "kind": "saas"})
        self.new["flows"] = [{"id": "f", "name": "Flow", "steps": [{"from": "cli", "to": "api", "msg": "hi", "kind": "call"}]}]
        self.new["views"] = [{"id": "v", "title": "View"}]
        self.new["system"]["highlights"] = ["A fact."]
        self.new["meta"]["notes"] = [{"text": "focus on auth", "added": "2026-10-12"}]
        ch = changed(self.old, self.new)
        self.assertEqual(keys(ch, "boundary", "added"), ["saas"])
        self.assertEqual(keys(ch, "flow", "added"), ["f"])
        self.assertEqual(keys(ch, "view", "added"), ["v"])
        self.assertEqual(keys(ch, "system", "changed"), ["system"])
        self.assertEqual(len(keys(ch, "note", "added")), 1)

    def test_reordering_is_reported(self):
        self.new["components"].reverse()
        ch = changed(self.old, self.new)
        self.assertEqual(keys(ch, "order", "changed"), ["components"])
        self.assertEqual(keys(ch, "component", "changed"), [])

    def test_a_removed_and_added_component_with_one_name_warns(self):
        self.new["components"][1]["id"] = "backend"
        _, warnings = viz.diff_models(self.old, self.new)
        self.assertEqual(len(warnings), 1)
        self.assertIn("ids never change", warnings[0])

    def test_changed_list_items_are_shown_whole(self):
        a = "Only llm.py imports anthropic; the API key is held as a SecretStr and built on first call."
        b = "Inside the package only llm.py imports anthropic; the key is a SecretStr and built on first call."
        text = viz._describe_change([a], [b])
        self.assertEqual(text, f"added {b}; removed {a}")

    def test_changelog_wording(self):
        a = viz._describe_change(["x", "y"], ["y", "z"])
        self.assertEqual(a, "added z; removed x")
        self.assertEqual(viz._describe_change("a" * 200, "a" * 199 + "b"), "reworded")
        short_old, short_new = "Serves requests and reads the cache.", "Serves requests, reads the cache and queues jobs."
        self.assertEqual(viz._describe_change(short_old, short_new), f"{short_old} → {short_new}")
        self.assertEqual(viz._describe_change("old", "new"), "old → new")


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.scratch = Path(self.tmp.name) / "scratch"
        self.scratch.mkdir()
        (self.root / "docs" / "architecture").mkdir(parents=True)
        (self.root / "api").mkdir()
        (self.root / "README.md").write_text("# demo\n\nIntro.\n\n## Storage\n\nRows.\n\n## Usage\n\nRun it.\n")
        (self.root / "cli.py").write_text("print(1)\n")
        (self.root / "api" / "main.py").write_text("x = 1\n")
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "init")
        self.sha = git(self.root, "rev-parse", "HEAD")

        m = minimal_model()
        m["meta"].update(model_sha=self.sha, model_date="2026-10-01", reviewed_sha=self.sha,
                         readme={"embed": True})
        self.model = self.root / "docs" / "architecture" / "architecture.json"
        self.model.write_text(json.dumps(m, indent=2) + "\n")
        self.out = self.root / "docs" / "architecture"
        code, out = run("render", self.model, "--root", self.root, "--readme-insert")
        self.assertEqual(code, 0, out)
        self.next = self.scratch / "next.json"

    def tearDown(self):
        self.tmp.cleanup()

    def candidate(self, edit=None, **meta):
        m = json.loads(self.model.read_text())
        m["meta"].update(meta)
        if edit:
            edit(m)
        self.next.write_text(json.dumps(m, indent=2) + "\n")
        return m

    def add_worker(self, m):
        (self.root / "api" / "worker.py").write_text("y = 2\n")
        m["components"].append({"id": "worker", "name": "Worker", "kind": "module", "boundary": "cloud",
                                "responsibility": "Runs jobs.", "evidence": ["api/worker.py"]})
        m["edges"].append({"from": "api", "to": "worker", "label": "enqueues jobs", "kind": "async"})

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*")
                if p.is_file() and ".git" not in p.parts}

    def update(self, *extra):
        return run("update", self.model, self.next, "--root", self.root, "--date", "2026-10-12", *extra)

    def test_update_archives_logs_and_renders(self):
        old_text = self.model.read_text()
        self.candidate(self.add_worker, model_sha="b" * 40, model_date="2026-10-12")
        code, out = self.update("--summary", "Added a background worker.")
        self.assertEqual(code, 0, out)

        self.assertIn('"worker"', self.model.read_text())
        self.assertIn("Worker", (self.out / "ARCHITECTURE.md").read_text())
        self.assertIn("Worker", (self.root / "README.md").read_text())

        archive = self.out / "archive" / f"2026-10-01-{self.sha[:7]}"
        self.assertEqual((archive / "architecture.json").read_text(), old_text)
        page = (archive / "ARCHITECTURE.md").read_text()
        self.assertEqual(viz.generated_state(page), "clean")
        self.assertNotIn("Worker", page)
        self.assertIn("[`cli.py`](../../../../cli.py)", page)  # links resolve from the deeper folder

        log = (self.out / "architecture.changelog.md").read_text()
        self.assertTrue(log.startswith("# Architecture changelog\n"))
        self.assertIn(f"## 2026-10-12 · `{self.sha[:7]}` → `bbbbbbb`", log)
        self.assertIn("Added a background worker.", log)
        self.assertIn("- Added **Worker** (`worker`): Runs jobs.", log)
        self.assertIn("- Added `api` → `worker` (async): enqueues jobs", log)
        self.assertIn(f"[archive/2026-10-01-{self.sha[:7]}/](archive/2026-10-01-{self.sha[:7]}/)", log)

        # the result is itself a stable model: a plain render changes nothing
        before = self.snapshot()
        self.assertEqual(run("render", self.model, "--root", self.root)[0], 0)
        self.assertEqual(before, self.snapshot())

    def test_second_update_stacks_newest_first_and_never_overwrites_an_archive(self):
        self.candidate(self.add_worker, model_sha="b" * 40, model_date="2026-10-12")
        self.assertEqual(self.update("--summary", "First change.")[0], 0)
        self.candidate(lambda m: component(m, "worker").update(responsibility="Runs background jobs."),
                       model_sha="b" * 40, model_date="2026-10-12")
        self.assertEqual(self.update("--summary", "Second change.")[0], 0)
        self.candidate(lambda m: component(m, "worker").update(responsibility="Runs queued jobs."),
                       model_sha="b" * 40, model_date="2026-10-12")
        self.assertEqual(self.update("--summary", "Third change.")[0], 0)

        log = (self.out / "architecture.changelog.md").read_text()
        self.assertLess(log.index("Third change."), log.index("Second change."))
        self.assertLess(log.index("Second change."), log.index("First change."))
        self.assertEqual(log.count("# Architecture changelog"), 1)
        names = sorted(p.name for p in (self.out / "archive").iterdir())
        self.assertEqual(names, [f"2026-10-01-{self.sha[:7]}", "2026-10-12-bbbbbbb", "2026-10-12-bbbbbbb-2"])

    def test_bookkeeping_only_updates_the_model_and_nothing_else(self):
        newer = "c" * 40
        self.candidate(None, reviewed_sha=newer)
        code, out = self.update()
        self.assertEqual(code, 0, out)
        self.assertIn(newer, self.model.read_text())
        self.assertFalse((self.out / "archive").exists())
        self.assertFalse((self.out / "architecture.changelog.md").exists())
        self.assertIn("no archive or changelog entry", out)

    def test_summary_is_required_when_the_diagram_changed(self):
        self.candidate(self.add_worker)
        before = self.snapshot()
        code, out = self.update()
        self.assertEqual(code, 2)
        self.assertIn("--summary is required", out)
        self.assertEqual(before.keys() | {"api/worker.py"}, self.snapshot().keys())  # only the test's own file
        self.assertEqual(self.model.read_bytes(), before["docs/architecture/architecture.json"])

    def test_summary_is_linted_for_secrets(self):
        self.candidate(self.add_worker)
        code, out = self.update("--summary", "Switched to sk-ant-api03-abcdefghijklmnop")
        self.assertEqual(code, 2)
        self.assertNotIn("sk-ant-api03", (self.out / "architecture.json").read_text())
        self.assertFalse((self.out / "architecture.changelog.md").exists())

    def test_invalid_candidate_writes_nothing(self):
        def break_it(m):
            self.add_worker(m)
            m["edges"].append({"from": "api", "to": "ghost", "label": "x", "kind": "sync"})
        self.candidate(break_it)
        before = self.snapshot()
        code, out = self.update("--summary", "Broken.")
        self.assertEqual(code, 1)
        self.assertIn("unknown component 'ghost'", out)
        self.assertEqual(before.keys() | {"api/worker.py"}, self.snapshot().keys())
        self.assertFalse((self.out / "archive").exists())

    def test_missing_evidence_in_candidate_writes_nothing(self):
        def cite_missing(m):
            component(m, "api")["evidence"] = ["api/gone.py"]
        self.candidate(cite_missing)
        code, out = self.update("--summary", "Moved.")
        self.assertEqual(code, 1)
        self.assertIn("does not exist", out)
        self.assertFalse((self.out / "archive").exists())

    def test_hand_edited_page_stops_the_update_before_anything_is_written(self):
        arch = self.out / "ARCHITECTURE.md"
        arch.write_text(arch.read_text().replace("Serves requests.", "Serves whatever."))
        self.candidate(self.add_worker)
        before = self.snapshot()
        code, out = self.update("--summary", "Added a worker.")
        self.assertEqual(code, 3)
        self.assertIn("edited by hand", out)
        self.assertEqual(before.keys() | {"api/worker.py"}, self.snapshot().keys())
        self.assertEqual(self.model.read_bytes(), before["docs/architecture/architecture.json"])
        self.assertFalse((self.out / "archive").exists())

    def test_force_overwrites_the_edited_page(self):
        arch = self.out / "ARCHITECTURE.md"
        arch.write_text(arch.read_text().replace("Serves requests.", "Serves whatever."))
        self.candidate(self.add_worker)
        self.assertEqual(self.update("--summary", "Added a worker.", "--force")[0], 0)
        self.assertIn("Worker", arch.read_text())

    def test_missing_readme_markers_are_not_added_back(self):
        readme = self.root / "README.md"
        text = readme.read_text()
        start, end = text.index("<!-- visualizer:start"), text.index("<!-- visualizer:end -->") + len("<!-- visualizer:end -->\n")
        readme.write_text(text[:start] + text[end:])
        before = readme.read_text()
        self.candidate(self.add_worker)
        code, out = self.update("--summary", "Added a worker.")
        self.assertEqual(code, 0, out)
        self.assertIn("no visualizer markers", out)
        self.assertEqual(readme.read_text(), before)

    def test_dry_run_writes_nothing(self):
        self.candidate(self.add_worker)
        before = self.snapshot()
        code, out = self.update("--summary", "Added a worker.", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("would be archived", out)
        self.assertEqual(before.keys() | {"api/worker.py"}, self.snapshot().keys())

    def test_candidate_must_be_a_separate_file(self):
        code, out = run("update", self.model, self.model, "--root", self.root, "--summary", "x")
        self.assertEqual(code, 2)
        self.assertIn("separate candidate", out)

    def test_untied_old_model_gets_a_readable_archive_name(self):
        m = json.loads(self.model.read_text())
        m["meta"].update(model_sha=None, model_date=None)
        self.model.write_text(json.dumps(m, indent=2))
        self.assertEqual(run("render", self.model, "--root", self.root)[0], 0)
        self.candidate(self.add_worker)
        self.assertEqual(self.update("--summary", "Added a worker.")[0], 0)
        self.assertTrue((self.out / "archive" / "undated-nosha" / "architecture.json").exists())
        self.assertIn("no commit", (self.out / "architecture.changelog.md").read_text())

    def test_changelog_golden(self):
        def edit(m):
            self.add_worker(m)
            m["system"]["highlights"] = ["Jobs run off the request path."]
            component(m, "db")["evidence"] = ["doc:README.md#storage", "api/main.py"]
            component(m, "cli")["responsibility"] = "Runs commands for a user."
            m["edges"] = [e for e in m["edges"] if e["to"] != "db"]
        self.candidate(edit, model_sha="b" * 40, model_date="2026-10-12")
        self.assertEqual(self.update("--summary", "Jobs moved to a worker.")[0], 0)
        log = (self.out / "architecture.changelog.md").read_text().replace(self.sha[:7], "1111111")
        golden = GOLDEN.parent / "changelog" / "architecture.changelog.md"
        if UPDATE:
            golden.parent.mkdir(parents=True, exist_ok=True)
            golden.write_text(log)
        self.assertEqual(log, golden.read_text(), "rerun with VIZ_UPDATE_GOLDEN=1 if the format change is intended")


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs" / "architecture").mkdir(parents=True)
        (self.root / "api").mkdir()
        (self.root / "cli.py").write_text("print(1)\n")
        (self.root / "api" / "main.py").write_text("x = 1\n")
        (self.root / "README.md").write_text("# demo\n\n## Storage\n\nRows.\n")
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "init")
        self.sha = git(self.root, "rev-parse", "HEAD")
        m = minimal_model()
        m["meta"].update(model_sha=self.sha, reviewed_sha=self.sha)
        self.model = self.root / "docs" / "architecture" / "architecture.json"
        self.model.write_text(json.dumps(m))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "model")

    def tearDown(self):
        self.tmp.cleanup()

    def status(self):
        code, out = run("status", self.model, "--root", self.root, "--json")
        self.assertEqual(code, 0, out)
        return json.loads(out)

    def test_nothing_changed(self):
        r = self.status()
        self.assertEqual(r["changed"], [])
        self.assertIn("only bump reviewed_sha", r["advice"])

    def test_committed_and_uncommitted_changes_map_to_evidence(self):
        (self.root / "api" / "main.py").write_text("x = 2\n")
        git(self.root, "commit", "-qam", "change api")
        (self.root / "cli.py").write_text("print(2)\n")  # uncommitted
        (self.root / "jobs.py").write_text("z = 1\n")  # untracked, cited by nobody
        r = self.status()
        self.assertEqual(r["commits_ahead"], 2)  # the commit that added the model, and the api change
        self.assertEqual(r["changed"], ["api/main.py", "cli.py", "jobs.py"])
        self.assertEqual(sorted(r["components"]), ["api", "cli"])
        self.assertEqual(r["uncited"], ["jobs.py"])

    def test_the_models_own_outputs_are_not_changes(self):
        (self.root / "docs" / "architecture" / "ARCHITECTURE.md").write_text("generated\n")
        (self.root / "docs" / "architecture" / "archive" / "x").mkdir(parents=True)
        (self.root / "docs" / "architecture" / "archive" / "x" / "architecture.json").write_text("{}")
        self.assertEqual(self.status()["changed"], [])

    def test_unknown_baseline_recommends_a_full_discovery(self):
        m = json.loads(self.model.read_text())
        m["meta"]["reviewed_sha"] = "f" * 40
        self.model.write_text(json.dumps(m))
        r = self.status()
        self.assertIsNone(r["commits_ahead"])
        self.assertIn("full discovery", r["advice"])

    def test_untied_model_recommends_a_full_discovery(self):
        m = json.loads(self.model.read_text())
        m["meta"].update(model_sha=None, reviewed_sha=None)
        self.model.write_text(json.dumps(m))
        self.assertIn("full discovery", self.status()["advice"])

    def embed_block(self):
        m = json.loads(self.model.read_text())
        m["meta"]["readme"] = {"embed": True}
        self.model.write_text(json.dumps(m))
        self.assertEqual(run("render", self.model, "--root", self.root, "--readme-insert")[0], 0)

    def test_the_readme_block_alone_is_not_a_change(self):
        self.embed_block()
        self.assertIn("visualizer:start", (self.root / "README.md").read_text())
        self.assertEqual(self.status()["changed"], [])

    def test_block_inserted_into_a_readme_with_no_blank_lines_is_not_a_change(self):
        (self.root / "README.md").write_text("# demo\n---\n## Storage\nRows.\n")
        git(self.root, "commit", "-qam", "tight readme")
        m = json.loads(self.model.read_text())
        m["meta"].update(model_sha=git(self.root, "rev-parse", "HEAD"), reviewed_sha=git(self.root, "rev-parse", "HEAD"))
        self.model.write_text(json.dumps(m))
        self.embed_block()
        self.assertEqual(self.status()["changed"], [])

    def test_a_real_readme_edit_beside_the_block_is_a_change(self):
        self.embed_block()
        readme = self.root / "README.md"
        readme.write_text(readme.read_text() + "\nA new section.\n")
        self.assertEqual(self.status()["changed"], ["README.md"])

    def test_long_lists_are_capped_in_text_but_not_in_json(self):
        for i in range(40):
            (self.root / f"extra{i:02}.py").write_text("x\n")
        code, out = run("status", self.model, "--root", self.root)
        self.assertIn("and 15 more", out)
        self.assertEqual(len(self.status()["uncited"]), 40)

    def test_text_output(self):
        (self.root / "api" / "main.py").write_text("x = 3\n")
        code, out = run("status", self.model, "--root", self.root)
        self.assertEqual(code, 0)
        self.assertIn("components whose evidence changed:", out)
        self.assertIn("api (API): api/main.py", out)


if __name__ == "__main__":
    unittest.main()
