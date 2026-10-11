"""HAR import (PLAN.md §5): a browser's front channel, redacted on the way in."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from authtrace.har import records  # noqa: E402
from authtrace_fakes import jwt  # noqa: E402
from authtrace.lint import RAW_JWT  # noqa: E402

ID_TOKEN = jwt({"sub": "user-123", "aud": "demo-web", "nonce": "n0nce-value-that-is-long"})
CODE = "one-time-code-0123456789abcdef"


def entry(t, method, url, status, req_headers=(), res_headers=(), post=None, body=None, mime="text/html", kind=None):
    e = {"startedDateTime": f"2026-10-03T05:36:{t:06.3f}Z", "time": 12.5,
         "request": {"method": method, "url": url, "headers": [{"name": k, "value": v} for k, v in req_headers]},
         "response": {"status": status, "headers": [{"name": k, "value": v} for k, v in res_headers],
                      "content": {"mimeType": mime, **({"text": body} if body is not None else {})}}}
    if post:
        e["request"]["postData"] = post
    if kind:
        e["_resourceType"] = kind
    return e


HAR = {"log": {"entries": [
    entry(1.0, "GET", "http://localhost:8000/login", 302, res_headers=[
        ("Location", "http://localhost:8080/realms/memes/protocol/openid-connect/auth?response_type=code"
                     "&client_id=memes-web&state=st4te-value-123&nonce=n0nce-123456&code_challenge=chal-0123456789"
                     "&code_challenge_method=S256"),
        ("Set-Cookie", "session=signed-cookie-value-123; HttpOnly; Path=/")], kind="document"),
    entry(1.5, "GET", "http://localhost:8000/static/app.css", 200, mime="text/css", kind="stylesheet"),
    entry(2.0, "POST", "http://localhost:8080/realms/memes/login-actions/authenticate", 302,
          req_headers=[(":authority", "localhost:8080"), ("Cookie", "AUTH_SESSION_ID=abc123def456")],
          res_headers=[("Location", f"http://localhost:8000/auth/callback?state=st4te-value-123&code={CODE}")],
          post={"mimeType": "application/x-www-form-urlencoded", "text": "username=dummyuser1&password=pw-secret-1"},
          kind="document"),
    entry(3.0, "GET", f"http://localhost:8080/realms/memes/protocol/openid-connect/logout?id_token_hint={ID_TOKEN}",
          302, kind="document"),
]}}


class HarTests(unittest.TestCase):
    def test_records(self):
        rs = records(HAR)
        xs = [r for r in rs if r["kind"] == "exchange"]
        self.assertEqual([x["method"] for x in xs], ["GET", "POST", "GET"])
        self.assertEqual([x["t0"] for x in xs], [0.0, 1.0, 2.0])
        self.assertTrue(all(x["seen_by"] == "har" and x["proc"] == "browser" for x in xs))
        login, auth, logout = xs
        location = login["res"]["headers"]["location"]
        self.assertIn("code_challenge_method=S256", location)
        self.assertIn("state=st4t…(15)", location)
        self.assertIn("session=<cookie fp:", login["res"]["headers"]["set-cookie"])
        self.assertNotIn(":authority", auth["req"]["headers"])
        self.assertEqual(auth["req"]["body"], {"username": "dummyuser1", "password": "***"})
        self.assertIn("code=one-…(30)", auth["res"]["headers"]["location"])
        self.assertEqual(logout["tokens"][0]["claims"]["nonce"], "n0nc…(24)")
        self.assertEqual(rs[-1], {"kind": "har-skipped", "assets": 1, "hosts": ["localhost:8000"]})
        text = json.dumps(rs)
        for raw in (ID_TOKEN, CODE, "pw-secret-1", "signed-cookie-value-123", "n0nce-value-that-is-long"):
            self.assertNotIn(raw, text)
        self.assertIsNone(RAW_JWT.search(text))

    def test_empty_har(self):
        self.assertEqual(records({"log": {"entries": []}}), [])

    def test_cli(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "flow.har").write_text(json.dumps(HAR), encoding="utf-8")
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "authtrace.py"), "import-har",
                                 tmp / "flow.har", "-o", tmp / "out"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("3 exchange(s)", result.stdout)
        self.assertEqual((tmp / "out" / "exchanges.jsonl").stat().st_mode & 0o077, 0)

        bad = subprocess.run([sys.executable, str(ROOT / "scripts" / "authtrace.py"), "import-har",
                              tmp / "missing.har", "-o", tmp / "out2"], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("can't read", bad.stderr)


if __name__ == "__main__":
    unittest.main()
