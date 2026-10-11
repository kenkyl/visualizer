"""Findings v0: each rule fires on a small synthetic trace (the live fixture covers the passes)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from authtrace.rules import RULES, run_rules  # noqa: E402

AT = {"id": "t1", "type": "access", "audience": ["https://api.example.com"], "iat": 0, "exp": 900, "header": {"alg": "RS256"}}
RT = {"id": "t2", "type": "refresh", "header": {"alg": "HS512"}}


def trace(*exchanges, tokens=(AT, RT)):
    nodes = [{"id": "rs", "roles": ["resource-server"]}, {"id": "as", "roles": ["authorization-server"]},
             {"id": "llm", "roles": ["llm-api"]}]
    return {"exchanges": list(exchanges), "tokens": [dict(t) for t in tokens], "nodes": nodes}


def x(xid, url, to="rs", family="mcp", op="tools/call", tokens=(), status=200, body=None):
    return {"id": xid, "url": url, "to": to, "status": status, "class": {"family": family, "op": op},
            "tokens": [{"token": t, "where": w} for t, w in tokens], "res": {"body": body}}


BEARER = ("t1", "req.headers:authorization")


class RuleTests(unittest.TestCase):
    def fired(self, t):
        findings, results = run_rules(t)
        self.assertEqual(set(results), set(RULES))
        return {f["rule"]: f for f in findings}

    def test_clean_trace_passes_everything(self):
        self.assertEqual(self.fired(trace(x("x1", "https://api.example.com/mcp", tokens=[BEARER]))), {})

    def test_token_leak_llm(self):
        f = self.fired(trace(x("x1", "https://api.anthropic.com/v1/messages", to="llm", family="llm", op="messages",
                               tokens=[("t2", "req.body:messages:content")])))
        self.assertEqual(f["TOKEN-LEAK-LLM"]["severity"], "critical")
        self.assertEqual(f["TOKEN-LEAK-LLM"]["exchanges"], ["x1"])

    def test_refresh_to_rs(self):
        f = self.fired(trace(x("x1", "https://api.example.com/mcp", tokens=[("t2", "req.headers:authorization")])))
        self.assertIn("REFRESH-TO-RS", f)

    def test_aud_mismatch(self):
        f = self.fired(trace(x("x1", "https://other.example.com/mcp", tokens=[BEARER])))
        self.assertIn("AUD-MISMATCH", f)

    def test_at_lifetime(self):
        f = self.fired(trace(x("x1", "https://api.example.com/mcp", tokens=[BEARER]), tokens=({**AT, "exp": 86400}, RT)))
        self.assertIn("AT-LIFETIME", f)

    def test_jwt_alg(self):
        f = self.fired(trace(x("x1", "https://api.example.com/mcp", tokens=[BEARER]), tokens=({**AT, "header": {"alg": "none"}}, RT)))
        self.assertIn("JWT-ALG", f)
        # A refresh token signed HS512 is the AS's own business: no finding.
        self.assertNotIn("JWT-ALG", self.fired(trace(x("x1", "https://idp/token", to="as", family="oauth", op="token/refresh_token",
                                                     tokens=[("t2", "req.body:refresh_token")]))))

    def test_http_tokens(self):
        f = self.fired(trace(x("x1", "http://api.example.com/mcp", tokens=[BEARER])))
        self.assertIn("HTTP-TOKENS", f)
        self.assertNotIn("HTTP-TOKENS", self.fired(trace(x("x2", "http://127.0.0.1:8001/mcp", tokens=[]))))

    def test_token_in_url(self):
        f = self.fired(trace(x("x1", "https://idp/logout?id_token_hint=…", to="as", family="oauth", op="logout",
                               tokens=[("t1", "url:id_token_hint")])))
        self.assertIn("TOKEN-IN-URL", f)

    def test_ropc_fallback_and_alias(self):
        f = self.fired(trace(x("x1", "http://localhost:8080/token", to="as", family="oauth", op="token/password"),
                             x("x2", "http://127.0.0.1:8001/mcp", status=400, body={"error": {"message": "Unsupported protocol version: 2026-07-28"}})))
        self.assertEqual(set(f), {"OAUTH-ROPC", "MCP-VERSION-FALLBACK", "HOST-ALIAS"})


if __name__ == "__main__":
    unittest.main()
