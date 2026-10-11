"""authtrace check, narrate and show (PLAN.md §15, M4): a narrated trace is validated before it's
rendered or shared."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from authtrace.check import check, check_trace  # noqa: E402
from authtrace.render import render  # noqa: E402
from authtrace.layout import layout  # noqa: E402
from authtrace.trace import facts_hash, load, problems  # noqa: E402

LIVE = ROOT / "tests" / "fixtures" / "authtrace" / "memes-agent-live" / "trace.json"
NARRATED = ROOT / "tests" / "fixtures" / "authtrace" / "memes-agent" / "trace.json"
LAUNCHER = ROOT / "scripts" / "authtrace.py"
ACCEPTANCE = LIVE.with_name("narrative.json")
UPDATE = os.environ.get("AUTHTRACE_UPDATE_GOLDEN") == "1"

NARRATIVE = {
    "names": {"localhost-8080": "Keycloak", "127-0-0-1-8001": "memes-mcp"},
    "nodes": [
        {"id": "kc-token", "name": "/token", "kind": "endpoint", "parent": "localhost-8080",
         "evidence": ["infra/realm.json"], "confidence": "declared"},
        {"id": "mw", "name": "ResourceServerMiddleware", "kind": "component", "parent": "127-0-0-1-8001",
         "evidence": ["server.py:2"], "confidence": "declared"},
        {"id": "browser", "name": "Browser", "kind": "user-agent", "holds_tokens": False,
         "why": "the web app redirects a browser here; not part of this capture", "confidence": "inferred"},
    ],
    "edges": [{"id": "br-kc", "from": "browser", "to": "localhost-8080", "label": "redirects",
               "evidence": ["server.py"], "confidence": "declared"}],
    "goals": {"3": ["MCP resource server", "Protected by Bearer tokens."]},
    "flows": [{"id": "live", "name": "Live run", "steps": ["live-1", "live-2"]}],
    "steps": [
        {"id": "live-1", "title": "Password grant", "exchanges": ["x1"], "nodes": ["pytest", "kc-token"],
         "segs": [["pytest--localhost-8080", 1, "password grant", "dat"]], "what": "The test asks for tokens.",
         "goals": [3]},
        {"id": "live-2", "title": "Discover fails, token passed", "exchanges": ["x2"], "code": ["server.py:1-2"],
         "checks": [["Middleware", "token valid"]], "quiz": ["Why 400?", "Protocol version."]},
    ],
}


def target(tmp: str) -> Path:
    """A fake target repo: the files the narrative cites, and the live trace in .authtrace/."""
    root = Path(tmp) / "target"
    (root / "infra").mkdir(parents=True)
    (root / "infra" / "realm.json").write_text("{}\n")
    (root / "server.py").write_text("line one\nline two\n")
    run = root / ".authtrace" / "run-1"
    run.mkdir(parents=True)
    (root / ".authtrace" / ".gitignore").write_text("*\n")
    return run


def narrated(narrative=None) -> dict:
    trace = load(LIVE)
    trace["narrative"] = copy.deepcopy(NARRATIVE if narrative is None else narrative)
    trace["layout"] = layout(trace)
    return trace


def write(run: Path, trace: dict) -> Path:
    path = run / "trace.json"
    path.write_text(json.dumps(trace, indent=1), encoding="utf-8")
    return path


def cli(*args):
    return subprocess.run([sys.executable, str(LAUNCHER), *map(str, args)], capture_output=True, text=True)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = target(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self, trace) -> list:
        return check(write(self.run_dir, trace)).errors

    def assertError(self, trace, fragment):
        found = self.errors(trace)
        self.assertTrue(any(fragment in e for e in found), f"no error mentioning {fragment!r} in {found}")

    def test_narrated_build_passes(self):
        rep = check(write(self.run_dir, narrated()))
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.warnings, [])
        self.assertTrue(any("Browser (inferred)" in o for o in rep.open), rep.open)
        self.assertTrue(any("JWKS (not captured)" in o for o in rep.open), rep.open)

    def test_live_fixture_facts_only_passes(self):
        self.assertEqual(self.errors(load(LIVE)), [])

    def test_lint_covers_the_narrative(self):
        trace = narrated()
        trace["narrative"]["steps"][0]["wire"] = "Authorization: Bearer 0123456789abcdefABCDEF"
        trace["narrative"]["steps"][1]["what"] = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhYmMifQ.c2lnbmF0dXJlLXZhbHVl"
        found = self.errors(trace)
        self.assertTrue(any("Bearer token" in e for e in found), found)
        self.assertTrue(any("raw JWT" in e for e in found), found)
        self.assertFalse(any("0123456789abcdef" in e for e in found), "the lint must not echo the value")

    def test_lint_names_where_the_value_is(self):
        trace = narrated()
        trace["narrative"]["steps"][1]["wire"] = '{"refresh_token": "8f2d1c0b9a8e7f6d5c4b"}'
        self.assertError(trace, "narrative.steps[1].wire: looks like an unmasked secret field")

    def test_an_un_narrated_trace_warns(self):
        rep = check(write(self.run_dir, load(LIVE)))
        self.assertTrue(any("no narrative tours" in w for w in rep.warnings), rep.warnings)

    def test_open_items_use_narrative_names(self):
        rep = check(write(self.run_dir, narrated()))
        self.assertTrue(any(o.startswith("memes-mcp → Keycloak: JWKS (not captured) (inferred)") for o in rep.open), rep.open)

    def test_edited_fact_fails(self):
        trace = narrated()
        trace["exchanges"][1]["status"] = 401
        self.assertError(trace, "facts changed after build")
        trace = narrated()
        trace["nodes"][1]["confidence"] = "declared"
        self.assertError(trace, "facts changed after build")

    def test_layout_and_narrative_are_not_facts(self):
        trace = narrated()
        self.assertEqual(trace["meta"]["facts_hash"], facts_hash(trace))
        trace["layout"]["nodes"]["pytest"][0] += 5
        self.assertEqual(self.errors(trace), [])

    def test_hand_written_facts_warn(self):
        rep = check(write(self.run_dir, load(NARRATED)), root=Path(self.tmp.name), location=False)
        self.assertTrue(any("facts_hash" in w for w in rep.warnings), rep.warnings)

    def test_step_confidence_is_never_stored(self):
        trace = narrated()
        trace["narrative"]["steps"][0]["confidence"] = "verified"
        self.assertError(trace, "remove `confidence`")

    def test_additions_claim_no_more_than_their_evidence(self):
        trace = narrated()
        trace["narrative"]["nodes"][0]["confidence"] = "observed"
        self.assertError(trace, "says observed, but its evidence supports declared at most")
        trace = narrated()
        del trace["narrative"]["nodes"][1]["evidence"]
        self.assertError(trace, "says declared, but its evidence supports inferred at most")
        trace = narrated()
        del trace["narrative"]["nodes"][2]["why"]
        self.assertError(trace, "needs a `why`")

    def test_an_addition_citing_observed_exchanges_may_be_observed(self):
        trace = narrated()
        trace["narrative"]["nodes"][1].update(confidence="observed", exchanges=["x2"])
        del trace["narrative"]["nodes"][1]["evidence"]
        self.assertEqual(self.errors(trace), [])
        trace["narrative"]["nodes"][1]["exchanges"] = ["x999"]
        self.assertError(trace, "unknown exchange 'x999'")

    def test_evidence_paths(self):
        cases = [("nope.py", "doesn't exist"), ("server.py:3", "outside server.py (2 lines)"),
                 ("server.py:2-1", "outside"), ("/etc/hosts", "relative"), ("../x.py", "relative"),
                 (".env", "secrets file"), ("infra:1", "is a folder")]
        for ev, fragment in cases:
            trace = narrated()
            trace["narrative"]["steps"][1]["code"] = [ev]
            self.assertError(trace, fragment)

    def test_evidence_needs_a_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            rep = check(write(Path(tmp), narrated()))
        self.assertTrue(any("pass --root" in w for w in rep.warnings), rep.warnings)

    def test_raw_redaction_fails(self):
        trace = narrated()
        trace["meta"]["redaction"] = "raw"
        trace["meta"]["facts_hash"] = facts_hash(trace)
        self.assertError(trace, "redaction is raw")

    def test_narrative_shape(self):
        trace = narrated()
        trace["narrative"]["steps"][0]["checks"] = ["Middleware checks aud"]
        trace["narrative"]["steps"][1]["goals"] = ["G3"]
        trace["narrative"]["goals"]["x"] = ["no", "number"]
        for fragment in ("live-1.checks", "live-2.goals", "narrative.goals.x"):
            self.assertError(trace, fragment)

    def test_additions_need_new_ids_and_known_ends(self):
        trace = narrated()
        trace["narrative"]["nodes"].append({"id": "pytest", "name": "dup", "kind": "client", "confidence": "inferred", "why": "x"})
        trace["narrative"]["edges"][0]["to"] = "nowhere"
        trace["narrative"]["names"]["ghost"] = "Ghost"
        for fragment in ("'pytest' is already a fact", "unknown to node 'nowhere'", "narrative.names: unknown node 'ghost'"):
            self.assertTrue(any(fragment in p for p in problems(trace)), fragment)

    def test_a_tracked_trace_fails(self):
        repo = Path(self.tmp.name) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        rep = check(write(repo, narrated()), root=repo)
        self.assertTrue(any("isn't ignored" in e for e in rep.errors), rep.errors)
        (repo / ".gitignore").write_text("trace.json\n")
        rep = check(repo / "trace.json", root=Path(self.tmp.name) / "target")
        self.assertFalse(any("isn't ignored" in e for e in rep.errors), rep.errors)


class AcceptanceTests(unittest.TestCase):
    """The M4 acceptance run: /visualizer:auth-flow in memes-agent, narrated by a fresh session
    from the skill alone (see the fixture's README). Its narrative over the live capture must stay
    valid, and its page is the golden."""

    @classmethod
    def setUpClass(cls):
        cls.trace = narrated(json.loads(ACCEPTANCE.read_text(encoding="utf-8")))
        cls.text = json.dumps(cls.trace, ensure_ascii=False, indent=1)

    def test_passes_check(self):
        repo = os.environ.get("MEMES_AGENT_REPO")  # set it to check the evidence paths too
        rep = check_trace(self.trace, self.text, Path(repo) if repo else None)
        self.assertEqual(rep.errors, [])

    def test_matches_golden(self):
        golden = LIVE.with_name("flow.html")
        page = render(self.trace)
        if UPDATE:
            golden.write_text(page, encoding="utf-8")
        self.assertEqual(page, golden.read_text(encoding="utf-8"),
                         "flow.html differs from the golden file; rerun with AUTHTRACE_UPDATE_GOLDEN=1 if intended")

    def test_tours_and_additions(self):
        nar = self.trace["narrative"]
        self.assertEqual([len(f["steps"]) for f in nar["flows"]], [9, 3, 5])
        self.assertEqual(nar["names"]["localhost-8080"], "Keycloak")
        self.assertTrue(all(n["confidence"] == "declared" and n["evidence"] for n in nar["nodes"]))


class LayoutTests(unittest.TestCase):
    def test_children_sit_inside_their_parent(self):
        boxes = narrated()["layout"]["nodes"]
        for kid, parent in (("kc-token", "localhost-8080"), ("mw", "127-0-0-1-8001")):
            kx, ky, kw, kh = boxes[kid]
            px, py, pw, ph = boxes[parent]
            self.assertTrue(px < kx and kx + kw < px + pw and py < ky and ky + kh < py + ph, (kid, boxes[kid], boxes[parent]))

    def test_an_edge_between_siblings_goes_around_the_boxes_between(self):
        trace = narrated()
        trace["narrative"]["nodes"] += [
            {"id": f"m{i}", "name": f"module {i}", "kind": "agent-module", "parent": "pytest",
             "evidence": ["server.py"], "confidence": "declared"} for i in range(3)]
        trace["narrative"]["edges"].append({"id": "m0-m2", "from": "m0", "to": "m2", "label": "calls",
                                            "evidence": ["server.py"], "confidence": "declared"})
        geo = layout(trace)
        path, (mx, my, mw, mh) = geo["edges"]["m0-m2"]["path"], geo["nodes"]["m1"]
        self.assertTrue(all(x > mx + mw for x, _ in path[1:-1]), (path, geo["nodes"]["m1"]))
        px, _, pw, _ = geo["nodes"]["pytest"]
        self.assertGreater(geo["edges"]["m0-m2"]["label"][0], px + pw, "the label sits outside the parent")

    def test_boxes_in_a_column_do_not_overlap(self):
        boxes = narrated()["layout"]["nodes"]
        tops = [b for n, b in boxes.items() if n not in ("kc-token", "mw")]
        for i, a in enumerate(tops):
            for b in tops[i + 1:]:
                overlap = a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
                self.assertFalse(overlap, (a, b))

    def test_narrated_trace_is_renderable(self):
        self.assertEqual(problems(narrated()), [])


class CliTests(unittest.TestCase):
    def test_narrate_then_check_then_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = target(tmp)
            trace = run / "trace.json"
            trace.write_text(LIVE.read_text(encoding="utf-8"), encoding="utf-8")
            nar = run / "narrative.json"
            nar.write_text(json.dumps(NARRATIVE), encoding="utf-8")
            result = cli("narrate", trace, nar)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("1 tour(s), 2 step(s), 3 added node(s)", result.stdout)
            data = load(trace)
            self.assertEqual(data["narrative"], NARRATIVE)
            self.assertIn("mw", data["layout"]["nodes"])
            self.assertEqual(data["meta"]["facts_hash"], facts_hash(data))
            self.assertEqual(oct(trace.stat().st_mode & 0o777), "0o600")

            result = cli("check", trace)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("OK (redaction strict)", result.stdout)
            self.assertIn("still inferred or unknown", result.stdout)
            self.assertEqual(cli("render", trace).returncode, 0)
            self.assertTrue((run / "flow.html").exists())

    def test_narrate_refuses_a_narrative_that_does_not_fit(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = target(tmp)
            trace = run / "trace.json"
            before = LIVE.read_text(encoding="utf-8")
            trace.write_text(before, encoding="utf-8")
            bad = copy.deepcopy(NARRATIVE)
            bad["steps"][0]["nodes"] = ["ghost"]
            nar = run / "narrative.json"
            nar.write_text(json.dumps(bad), encoding="utf-8")
            result = cli("narrate", trace, nar)
            self.assertEqual(result.returncode, 1)
            self.assertIn("unknown node 'ghost'", result.stderr)
            self.assertEqual(trace.read_text(encoding="utf-8"), before)

    def test_narrate_runs_the_checks_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = target(tmp)
            trace = run / "trace.json"
            trace.write_text(LIVE.read_text(encoding="utf-8"), encoding="utf-8")
            bad = copy.deepcopy(NARRATIVE)
            bad["steps"][0]["confidence"] = "observed"
            bad["steps"][1]["code"] = ["missing.py:3"]
            bad["steps"][1]["what"] = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhYmMifQ.c2lnbmF0dXJlLXZhbHVl"
            nar = run / "narrative.json"
            nar.write_text(json.dumps(bad), encoding="utf-8")
            result = cli("narrate", trace, nar)
            self.assertEqual(result.returncode, 1)
            for fragment in ("remove `confidence`", "missing.py doesn't exist", "narrative.steps[1].what: looks like a raw JWT"):
                self.assertIn(fragment, result.stderr)
            self.assertNotIn("eyJhbGci", result.stderr)

    def test_check_fails_with_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = target(tmp)
            trace = narrated()
            trace["exchanges"][0]["status"] = 500
            result = cli("check", write(run, trace))
            self.assertEqual(result.returncode, 1)
            self.assertIn("FAILED", result.stdout)

    def test_show(self):
        index = cli("show", LIVE)
        self.assertEqual(index.returncode, 0, index.stderr)
        for line in ("pytest--127-0-0-1-8001", "t1    access", "x2    +1.863s", "MCP-VERSION-FALLBACK"):
            self.assertIn(line, index.stdout)
        detail = cli("show", LIVE, "x2", "t1", "zz")
        self.assertIn("Unsupported protocol version", detail.stdout)
        self.assertIn('"kid"', detail.stdout)
        self.assertIn("zz: no exchange or token", detail.stdout)


if __name__ == "__main__":
    unittest.main()
