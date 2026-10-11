"""authtrace build: a capture → trace.json facts and findings (PLAN.md §15, M3)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from authtrace.build import build, load_records  # noqa: E402
from authtrace.confidence import step_confidence  # noqa: E402
from authtrace.decode import classify  # noqa: E402
from authtrace.render import render  # noqa: E402
from authtrace.trace import problems  # noqa: E402

UPDATE = os.environ.get("AUTHTRACE_UPDATE_GOLDEN") == "1"
LIVE = ROOT / "tests" / "fixtures" / "authtrace" / "memes-agent-live"
LAUNCHER = ROOT / "scripts" / "authtrace.py"


def payload_of(page: str) -> dict:
    return json.loads(page.split('<script type="application/json" id="trace">', 1)[1].split("</script>", 1)[0])


def live_trace() -> dict:
    return build(*load_records(LIVE), target="memes-agent")


class LiveFixtureTests(unittest.TestCase):
    """PLAN.md §15 M3 acceptance, on the strict capture of memes-agent's live suite."""

    @classmethod
    def setUpClass(cls):
        cls.trace = live_trace()
        cls.nodes = {n["name"]: n for n in cls.trace["nodes"]}

    def test_renderable_and_golden(self):
        self.assertEqual(problems(self.trace), [])
        text = json.dumps(self.trace, ensure_ascii=False, indent=1) + "\n"
        golden = LIVE / "trace.json"
        if UPDATE:
            golden.write_text(text, encoding="utf-8")
        self.assertEqual(text, golden.read_text(encoding="utf-8"),
                         "trace.json differs from the golden file; rerun with AUTHTRACE_UPDATE_GOLDEN=1 if intended")

    def test_deterministic(self):
        self.assertEqual(live_trace(), self.trace)

    def test_roles(self):
        self.assertEqual(self.nodes["localhost:8080"]["roles"], ["authorization-server"])
        self.assertEqual(self.nodes["127.0.0.1:8001"]["roles"], ["resource-server"])
        self.assertEqual(self.nodes["api.anthropic.com"]["kind"], "llm-api")
        self.assertEqual(self.nodes["pytest"]["roles"], ["client"])
        for name in ("localhost:8080", "127.0.0.1:8001", "api.anthropic.com"):
            self.assertEqual(self.nodes[name]["confidence"], "inferred")
            self.assertTrue(self.nodes[name]["why"])

    def test_unseen_jwks_fetch_is_inferred_not_dropped(self):
        keys = [e for e in self.trace["edges"] if e["confidence"] == "inferred"]
        self.assertEqual(len(keys), 1)
        self.assertEqual((keys[0]["from"], keys[0]["to"]), (self.nodes["127.0.0.1:8001"]["id"], self.nodes["localhost:8080"]["id"]))
        self.assertTrue(keys[0]["dashed"])
        self.assertIn("authtrace run", keys[0]["why"])

    def test_classification(self):
        ops = [x["class"]["op"] for x in self.trace["exchanges"]]
        self.assertEqual(ops[:6], ["token/password", "server/discover", "initialize", "notifications/initialized", "stream", "tools/list"])
        self.assertEqual(ops.count("token/refresh_token"), 2)
        self.assertEqual(ops.count("revoke"), 1)
        self.assertEqual(ops.count("session/end"), 3)
        self.assertTrue(all(op.startswith("messages/") for x, op in zip(self.trace["exchanges"], ops) if "anthropic" in x["url"]))

    def test_tokens(self):
        tokens = self.trace["tokens"]
        self.assertEqual(len({t["fingerprint"] for t in tokens}), len(tokens))
        access = next(t for t in tokens if t["type"] == "access")
        self.assertEqual(access["confidence"], "decoded")
        self.assertEqual(access["audience"], ["http://127.0.0.1:8001/mcp"])
        self.assertEqual(access["exp"] - access["iat"], 900)
        self.assertTrue(access["subject"].startswith("fp:"))  # strict capture: identity hashed
        self.assertGreater(len(access["seen_in"]), 5)
        offline = [t for t in tokens if t["type"] == "refresh" and t["claims"].get("typ") == "Offline"]
        self.assertTrue(offline)

    def test_findings(self):
        rules = self.trace["meta"]["rules"]
        self.assertEqual(sorted(k for k, v in rules.items() if v == "fired"), ["HOST-ALIAS", "MCP-VERSION-FALLBACK", "OAUTH-ROPC"])
        self.assertEqual(rules["TOKEN-LEAK-LLM"], "passed")
        self.assertEqual(rules["REFRESH-TO-RS"], "passed")

    def test_renders_with_the_default_tour(self):
        page = render(self.trace)
        data = payload_of(page)
        tour = data["derived"]["narrative"]
        self.assertEqual(len(tour["steps"]), len(self.trace["exchanges"]))
        self.assertEqual(tour["steps"][1]["segs"][1][3], "err")  # the discover 400
        self.assertEqual(data["derived"]["steps"][tour["steps"][0]["id"]]["confidence"], "observed")
        self.assertNotIn("narrative", data["trace"])


class TourTests(unittest.TestCase):
    def test_short_titles(self):
        from authtrace.tour import short_path

        self.assertEqual(short_path("http://idp/realms/memes/protocol/openid-connect/token"), "…/openid-connect/token")
        self.assertEqual(short_path("https://api.anthropic.com/v1/messages"), "/v1/messages")
        self.assertEqual(short_path("http://rs/mcp"), "/mcp")
        self.assertEqual(short_path("http://rs"), "/")


class DecodeTests(unittest.TestCase):
    def test_families(self):
        def x(method, url, req=None, res=None, status=200):
            return {"method": method, "url": url, "status": status, "req": req or {}, "res": res or {}}

        self.assertEqual(classify(x("POST", "http://idp/token", {"body": {"grant_type": "authorization_code"}}))["op"],
                         "token/authorization_code")
        self.assertEqual(classify(x("POST", "http://idp/token", {"body": {"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange"}}))["spec"],
                         ["RFC 8693"])
        self.assertEqual(classify(x("GET", "http://idp/.well-known/openid-configuration"))["op"], "discovery")
        self.assertEqual(classify(x("GET", "http://idp/protocol/openid-connect/certs"))["op"], "jwks")
        self.assertEqual(classify(x("GET", "http://rs/.well-known/oauth-protected-resource/mcp"))["op"], "protected-resource-metadata")
        self.assertEqual(classify(x("POST", "http://rs/mcp", res={"headers": {"www-authenticate": "Bearer realm=x"}}, status=401))["op"],
                         "bearer/challenge")
        self.assertEqual(classify(x("POST", "http://rs/mcp", {"body": [{"data": {"jsonrpc": "2.0", "method": "tools/call"}}]}))["op"],
                         "tools/call")
        self.assertEqual(classify(x("POST", "https://api.anthropic.com/v1/messages", res={"body": {"stop_reason": "end_turn"}}))["op"],
                         "messages/end_turn")
        self.assertEqual(classify(x("GET", "http://elsewhere/"))["family"], "http")


class ConfidenceWithTokensTests(unittest.TestCase):
    def test_a_step_about_a_decoded_token_is_decoded(self):
        tokens = {"t1": {"confidence": "decoded"}}
        exchanges = {"x1": {"confidence": "observed"}}
        self.assertEqual(step_confidence({"tokens": ["t1"]}, exchanges, tokens), "decoded")
        self.assertEqual(step_confidence({"exchanges": ["x1"], "tokens": ["t1"]}, exchanges, tokens), "decoded")


class EndToEndTests(unittest.TestCase):
    """Capture the fake system, build, and see the leak it plants."""

    def test_capture_build_render(self):
        from authtrace_fakes import URLLIB_CLIENT, FakeSystem, client_source

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "client.py").write_text(client_source(URLLIB_CLIENT), encoding="utf-8")
        with FakeSystem() as fake:
            run = subprocess.run([sys.executable, str(LAUNCHER), "run", "-o", tmp / "cap", "--", sys.executable,
                                  tmp / "client.py", fake.url, "0"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        built = subprocess.run([sys.executable, str(LAUNCHER), "build", tmp / "cap", "--target", "fake"], capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertIn("TOKEN-LEAK-LLM", built.stdout)
        trace = json.loads((tmp / "cap" / "trace.json").read_text())
        self.assertEqual(trace["meta"]["rules"]["TOKEN-LEAK-LLM"], "fired")
        self.assertEqual(problems(trace), [])
        rendered = subprocess.run([sys.executable, str(LAUNCHER), "render", tmp / "cap" / "trace.json"], capture_output=True, text=True)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertTrue((tmp / "cap" / "flow.html").exists())


if __name__ == "__main__":
    unittest.main()
