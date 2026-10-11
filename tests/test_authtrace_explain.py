"""authtrace explain: decode one pasted token, never echo it."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from authtrace.explain import explain, format_text  # noqa: E402
from authtrace_fakes import b64, jwt  # noqa: E402

ACCESS = jwt({"iss": "http://idp", "aud": "http://rs/mcp", "typ": "Bearer", "scope": "openid", "iat": 1000, "exp": 1900})
ID_TOKEN = jwt({"iss": "http://idp", "aud": "web", "azp": "web", "typ": "ID", "nonce": "n0nce-value-that-is-long", "iat": 1000, "exp": 1900})


class ExplainTests(unittest.TestCase):
    def test_access_token(self):
        e = explain(f"Bearer {ACCESS}\n", now=1500)
        self.assertEqual((e["format"], e["type"], e["lifetime"], e["expires_in"], e["verified"]), ("jwt", "access", 900, 400, False))
        self.assertEqual(e["findings"], [])
        self.assertNotIn(ACCESS, json.dumps(e) + format_text(e))
        self.assertIn("decoded, not verified", format_text(e))

    def test_id_token_masks_one_time_claims(self):
        e = explain(ID_TOKEN, now=2000)
        self.assertEqual(e["type"], "id")
        self.assertEqual(e["claims"]["nonce"], "n0nc…(24)")
        self.assertIn("expired 100 s ago", format_text(e))

    def test_findings(self):
        unsigned = f"{b64({'alg': 'none'})}.{b64({'scope': 'x', 'iat': 0, 'exp': 86400})}."
        rules = {f["rule"]: f["severity"] for f in explain(unsigned, now=0)["findings"]}
        self.assertEqual(rules, {"JWT-ALG": "critical", "AT-LIFETIME": "warn"})

    def test_opaque(self):
        e = explain("opaque-refresh-token-value-0123456789")
        self.assertEqual(e["format"], "opaque")
        self.assertNotIn("opaque-refresh-token-value-0123456789", format_text(e))
        self.assertIn("revoke", e["note"])

    def test_cli_reads_stdin_only(self):
        run = lambda *a, stdin="": subprocess.run([sys.executable, str(ROOT / "scripts" / "authtrace.py"), "explain", *a],  # noqa: E731
                                                  input=stdin, capture_output=True, text=True)
        result = run("--json", stdin=ACCESS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["type"], "access")
        self.assertNotIn(ACCESS, result.stdout)
        self.assertEqual(run(stdin="  ").returncode, 2)


if __name__ == "__main__":
    unittest.main()
