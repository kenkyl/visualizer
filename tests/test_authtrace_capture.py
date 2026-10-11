"""`authtrace run` end to end, against the fake system in authtrace_fakes.py (PLAN.md §15, M2)."""

import importlib.util
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

from authtrace.run import safe_argv  # noqa: E402
from authtrace_fakes import CLIENT_SECRET, HTTPX_CLIENT, PASSWORD, URLLIB_CLIENT, FakeSystem, client_source  # noqa: E402
from authtrace.lint import ONE_TIME_CLAIM, RAW_JWT  # noqa: E402

LAUNCHER = ROOT / "scripts" / "authtrace.py"


def authtrace(*args, cwd=None, env=None):
    return subprocess.run([sys.executable, str(LAUNCHER), *map(str, args)], capture_output=True, text=True,
                          cwd=cwd, env=env)


def exchanges(out: Path) -> list:
    lines = [json.loads(x) for x in (out / "exchanges.jsonl").read_text(encoding="utf-8").splitlines()]
    return sorted((r for r in lines if r["kind"] == "exchange"), key=lambda r: r["t0"])


class CaptureTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = FakeSystem().__enter__()
        self.addCleanup(self.fake.__exit__)

    def client(self, template, name="client.py") -> Path:
        path = self.tmp / name
        path.write_text(client_source(template), encoding="utf-8")
        return path

    def assertNoSecrets(self, out: Path):
        values = [*self.fake.issued.values(), CLIENT_SECRET, PASSWORD]
        for f in out.iterdir():
            text = f.read_text(encoding="utf-8")
            self.assertIsNone(RAW_JWT.search(text), f"raw JWT in {f.name}")
            self.assertIsNone(ONE_TIME_CLAIM.search(text), f"one-time claim in {f.name}")
            for v in values:
                self.assertNotIn(v, text, f"a raw secret or token in {f.name}")
            self.assertEqual(f.stat().st_mode & 0o077, 0, f"{f.name} is readable by others")


class UrllibCaptureTests(CaptureTestCase):
    def test_memes_agent_shaped_flow(self):
        out = self.tmp / "out"
        result = authtrace("run", "-o", out, "--", sys.executable, self.client(URLLIB_CLIENT), self.fake.url, "3")
        self.assertEqual(result.returncode, 3, result.stderr)  # the command's exit code, passed through
        xs = exchanges(out)
        self.assertEqual([(x["method"], x["url"].split(self.fake.url)[-1].split("?")[0], x["status"]) for x in xs], [
            ("POST", "/realms/demo/protocol/openid-connect/token", 200),
            ("POST", "/mcp", 401),
            ("GET", "/.well-known/oauth-protected-resource/mcp", 200),
            ("POST", "/mcp", 200),
            ("GET", "/mcp", 200),
            ("POST", "/v1/messages", 200),
            ("GET", "/login", 302),
            ("GET", "/callback", 200),
        ])
        token, probe, _, init, stream, llm, login, callback = xs
        self.assertEqual(token["req"]["headers"]["authorization"], "Basic demo-web:***")
        self.assertEqual(token["req"]["body"]["password"], "***")
        access = next(t for t in token["tokens"] if t["kind"] == "access_token")
        self.assertEqual(access["claims"]["aud"], f"{self.fake.url}/mcp")
        self.assertEqual(init["req"]["headers"]["authorization"], f"Bearer <jwt {access['fp']}>")
        self.assertIn("resource_metadata=", probe["res"]["headers"]["www-authenticate"])
        self.assertEqual(init["res"]["body"][0]["data"]["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual([e["data"]["n"] for e in stream["res"]["body"]], [0, 1, 2])  # chunked, unframed
        refresh = next(t for t in token["tokens"] if t["kind"] == "refresh_token")
        self.assertIn(refresh["fp"], {t["fp"] for t in llm["tokens"]})  # the leak, by fingerprint
        self.assertEqual(llm["req"]["headers"]["x-api-key"][:4], "sk-t")
        self.assertNotEqual(login["pid"], token["pid"])  # the child process was captured too
        self.assertIn("session=<cookie fp:", login["res"]["headers"]["set-cookie"])
        self.assertIn("code=", callback["url"])
        self.assertTrue(all(x["seen_by"] == "run-hooks" and x["confidence"] == "observed" for x in xs))
        self.assertNoSecrets(out)
        self.assertIn("8 exchange(s)", result.stderr)
        run = json.loads((out / "run.json").read_text())
        self.assertEqual(run["exit_code"], 3)
        self.assertEqual(run["mechanisms"], ["run-hooks"])

    def test_existing_sitecustomize_still_runs(self):
        site = self.tmp / "site"
        site.mkdir()
        marker = self.tmp / "marker"
        (site / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
        env = {**os.environ, "PYTHONPATH": str(site)}
        result = authtrace("run", "-o", self.tmp / "out", "--", sys.executable, "-c",
                           f"import urllib.request; urllib.request.urlopen({self.fake.url!r} + '/login').read()", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.read_text(), "ran")
        self.assertEqual(len(exchanges(self.tmp / "out")), 2)

    def test_strict_and_raw(self):
        strict = self.tmp / "strict"
        authtrace("run", "-o", strict, "--redaction", "strict", "--", sys.executable, self.client(URLLIB_CLIENT), self.fake.url, "0")
        token = exchanges(strict)[0]
        id_ref = next(t for t in token["tokens"] if t["kind"] == "id_token")
        self.assertTrue(id_ref["claims"]["sub"].startswith("fp:"))
        self.assertNoSecrets(strict)

        raw = self.tmp / "raw"
        result = authtrace("run", "-o", raw, "--redaction", "raw", "--", sys.executable, self.client(URLLIB_CLIENT), self.fake.url, "0")
        self.assertIn("RAW capture", result.stderr)
        self.assertIn(self.fake.issued["access"], (raw / "exchanges.jsonl").read_text())
        self.assertEqual((raw / "exchanges.jsonl").stat().st_mode & 0o077, 0)


@unittest.skipUnless(importlib.util.find_spec("httpx"), "httpx not installed (run under uv run --with httpx --with httpx2)")
class HttpxCaptureTests(CaptureTestCase):
    def test_basic_auth_added_by_the_auth_flow_is_captured(self):
        out = self.tmp / "out"
        result = authtrace("run", "-o", out, "--", sys.executable, self.client(HTTPX_CLIENT), self.fake.url)
        self.assertEqual(result.returncode, 0, result.stderr)
        libs = result.stdout.strip().split(",")
        xs = exchanges(out)
        self.assertEqual([x["lib"] for x in xs], [lib for lib in libs for _ in range(2)])
        for token, stream in zip(xs[::2], xs[1::2]):
            self.assertEqual(token["req"]["headers"]["authorization"], "Basic demo-web:***")
            self.assertEqual([e["data"]["n"] for e in stream["res"]["body"]], [0, 1, 2])
            self.assertTrue(stream["req"]["headers"]["authorization"].startswith("Bearer <jwt fp:"))
        self.assertNoSecrets(out)


class RecorderTests(unittest.TestCase):
    def setUp(self):
        from authtrace.capture.recorder import Recorder

        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.rec = Recorder(str(self.tmp / "x.jsonl"))
        self.addCleanup(self.rec.pending.clear)

    def finished(self, headers, body: bytes) -> dict:
        p = self.rec.begin("test")
        p.request("GET", "http://api/x", [], None)
        p.response(200, headers)
        p.chunk(body)
        p.finish()
        return json.loads((self.tmp / "x.jsonl").read_text().splitlines()[-1])

    def test_gzip_and_deflate_bodies_are_decoded(self):
        import gzip
        import zlib

        data = json.dumps({"stop_reason": "end_turn"}).encode()
        for enc, raw in (("gzip", gzip.compress(data)), ("deflate", zlib.compress(data))):
            rec = self.finished([("Content-Type", "application/json"), ("Content-Encoding", enc)], raw)
            self.assertEqual(rec["res"]["body"], {"stop_reason": "end_turn"}, enc)

    def test_truncated_gzip_still_decodes_what_arrived(self):
        import gzip

        raw = gzip.compress(os.urandom(20000).hex().encode())
        rec = self.finished([("Content-Type", "text/plain"), ("Content-Encoding", "gzip")], raw[: len(raw) // 2])
        self.assertEqual(rec["res"]["body"]["type"], "text/plain")

    def test_brotli_is_reported_not_decoded(self):
        rec = self.finished([("Content-Encoding", "br")], b"\x0b\x02\x80")
        self.assertEqual(rec["res"]["body"], {"encoded": "br", "len": 3})

    def test_pending_requests_are_written_at_exit_as_incomplete(self):
        p = self.rec.begin("test")
        p.request("GET", "http://rs/mcp", [], None)
        p.response(200, [("Content-Type", "text/event-stream")])
        self.rec.close()
        lines = [json.loads(x) for x in (self.tmp / "x.jsonl").read_text().splitlines()]
        self.assertTrue(lines[0]["incomplete"])
        self.assertEqual(lines[1]["kind"], "process")


class UrlTests(unittest.TestCase):
    def test_wire_url(self):
        from types import SimpleNamespace as NS

        from authtrace.capture.http_client import _url

        self.assertEqual(_url(NS(host="idp", port=443, default_port=443), "/certs"), "https://idp/certs")
        self.assertEqual(_url(NS(host="127.0.0.1", port=8001, default_port=80), "/mcp"), "http://127.0.0.1:8001/mcp")
        self.assertEqual(_url(NS(host="::1", port=8080, default_port=80), "/x"), "http://[::1]:8080/x")
        self.assertEqual(_url(NS(host="proxy", port=3128, default_port=80, _tunnel_host="api", _tunnel_port=443), "/v1"),
                         "http://api:443/v1")  # tunnel through a plain proxy: scheme from the conn, host from the tunnel
        self.assertEqual(_url(NS(host="proxy", port=3128, default_port=80), "http://rs/mcp"), "http://rs/mcp")


class OutputLocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_default_output_leaves_git_status_unchanged(self):
        subprocess.run(["git", "init", "-q", str(self.tmp)], check=True)
        result = authtrace("run", "--", sys.executable, "-c", "pass", cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.tmp / ".authtrace" / ".gitignore").exists())
        self.assertEqual(len(list((self.tmp / ".authtrace").glob("run-*/run.json"))), 1)
        status = subprocess.run(["git", "-C", str(self.tmp), "status", "--porcelain"], capture_output=True, text=True)
        self.assertEqual(status.stdout, "")

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_refuses_a_tracked_location(self):
        subprocess.run(["git", "init", "-q", str(self.tmp)], check=True)
        result = authtrace("run", "-o", self.tmp / "captures", "--", sys.executable, "-c", "pass", cwd=self.tmp)
        self.assertEqual(result.returncode, 2)
        self.assertIn("isn't ignored", result.stderr)
        self.assertFalse((self.tmp / "captures").exists())

    def test_needs_a_command(self):
        result = authtrace("run", "-o", self.tmp / "out", "--")
        self.assertEqual(result.returncode, 2)
        self.assertIn("nothing to run", result.stderr)

    def test_command_not_found(self):
        result = authtrace("run", "-o", self.tmp / "out", "--", "no-such-command-authtrace")
        self.assertEqual(result.returncode, 2)
        self.assertIn("command not found", result.stderr)

    def test_non_python_command_is_explained(self):
        result = authtrace("run", "-o", self.tmp / "out", "--", "true")
        self.assertIn("No Python process reported in", result.stderr)

    def test_secret_arguments_are_masked(self):
        self.assertEqual(safe_argv(["login", "--password", "pw", "--client-secret=s", "--user", "u"]),
                         ["login", "--password", "***", "--client-secret=***", "--user", "u"])


if __name__ == "__main__":
    unittest.main()
