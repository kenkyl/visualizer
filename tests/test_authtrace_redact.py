"""Redaction (PLAN.md §13): what a capture may write down, at each level."""

import base64
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from authtrace.redact import Redactor, decode_jwt, fingerprint, mask  # noqa: E402
from authtrace_fakes import jwt  # noqa: E402

ACCESS = jwt({"iss": "http://idp/realms/demo", "aud": "http://rs/mcp", "sub": "user-123", "exp": 1791006672})
ID_TOKEN = jwt({"sub": "user-123", "email": "a@example.com", "nonce": "n0nce-value-that-is-long",
                "at_hash": "hash-value-that-is-long", "aud": "demo-web"})
REFRESH = "opaque-refresh-token-value-0123456789"


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


class BasicsTests(unittest.TestCase):
    def test_fingerprint_and_mask(self):
        self.assertRegex(fingerprint("abc"), r"^fp:[0-9a-f]{8}$")
        self.assertEqual(mask("abcdefgh"), "abcd…(8)")
        self.assertEqual(mask(""), "")

    def test_decode_jwt(self):
        header, claims = decode_jwt(ACCESS)
        self.assertEqual(header["alg"], "RS256")
        self.assertEqual(claims["aud"], "http://rs/mcp")
        self.assertIsNone(decode_jwt("not.a.jwt"))
        self.assertIsNone(decode_jwt(REFRESH))

    def test_unknown_level(self):
        with self.assertRaises(ValueError):
            Redactor("loose")


class DevTests(unittest.TestCase):
    def setUp(self):
        self.r = Redactor("dev")

    def test_token_response(self):
        scan = self.r.scan()
        body = scan.body(dumps({"access_token": ACCESS, "id_token": ID_TOKEN, "refresh_token": REFRESH,
                                "expires_in": 900}), "application/json", "res.body")
        text = dumps(body) + dumps(scan.refs)
        for raw in (ACCESS, ID_TOKEN, REFRESH, "n0nce-value-that-is-long", "hash-value-that-is-long"):
            self.assertNotIn(raw, text)
        self.assertEqual(body["access_token"], f"<jwt {fingerprint(ACCESS)}>")
        self.assertEqual(body["refresh_token"], f"<token {fingerprint(REFRESH)}>")
        self.assertEqual(body["expires_in"], 900)
        id_ref = next(r for r in scan.refs if r["kind"] == "id_token")
        self.assertEqual(id_ref["claims"]["nonce"], "n0nc…(24)")
        self.assertEqual(id_ref["claims"]["email"], "a@example.com")  # claims stay readable in dev
        self.assertEqual(id_ref["where"], "res.body:id_token")

    def test_known_token_is_caught_later_anywhere(self):
        self.r.scan().body(dumps({"refresh_token": REFRESH}), "application/json", "res.body")
        scan = self.r.scan()
        body = scan.body(dumps({"messages": [{"content": f"debug {REFRESH}"}]}), "application/json", "req.body")
        self.assertNotIn(REFRESH, dumps(body))
        self.assertEqual(scan.refs[0]["fp"], fingerprint(REFRESH))
        self.assertEqual(scan.refs[0]["where"], "req.body:messages:content")

    def test_headers(self):
        basic = base64.b64encode(b"demo-web:s3cret").decode()
        scan = self.r.scan()
        h = scan.headers([("Authorization", f"Bearer {ACCESS}"), ("X-Api-Key", "sk-test-abcdefghijkl"),
                          ("Cookie", "session=abc123; KEYCLOAK_IDENTITY=" + ID_TOKEN),
                          ("Mcp-Session-Id", "dae75ef7043344909379938a496bd5f9"),
                          ("WWW-Authenticate", 'Bearer resource_metadata="http://rs/.well-known/x"'),
                          ("Content-Type", "application/json")], "req")
        self.assertEqual(h["authorization"], f"Bearer <jwt {fingerprint(ACCESS)}>")
        self.assertEqual(h["x-api-key"], "sk-t…(20)")
        self.assertIn("session=<cookie fp:", h["cookie"])
        self.assertIn(f"KEYCLOAK_IDENTITY=<jwt {fingerprint(ID_TOKEN)}>", h["cookie"])
        self.assertEqual(h["mcp-session-id"], "dae7…(32)")
        self.assertEqual(h["www-authenticate"], 'Bearer resource_metadata="http://rs/.well-known/x"')
        self.assertEqual(h["content-type"], "application/json")
        self.assertEqual(scan.headers([("Authorization", f"Basic {basic}")], "req")["authorization"],
                         "Basic demo-web:***")

    def test_repeated_headers_become_a_list(self):
        h = self.r.scan().headers([("Set-Cookie", "a=1; HttpOnly"), ("Set-Cookie", "b=2")], "res")
        self.assertEqual(len(h["set-cookie"]), 2)
        self.assertTrue(h["set-cookie"][0].endswith("; HttpOnly"))

    def test_form_body(self):
        body = self.r.scan().body("grant_type=authorization_code&code=abcdefgh1234&code_verifier=verifier-xyz"
                                  "&client_secret=s3cret&redirect_uri=http%3A%2F%2Flocalhost%2Fcb",
                                  "application/x-www-form-urlencoded", "req.body")
        self.assertEqual(body["grant_type"], "authorization_code")
        self.assertEqual(body["code"], "abcd…(12)")
        self.assertEqual(body["code_verifier"], "veri…(12)")
        self.assertEqual(body["client_secret"], "***")
        self.assertEqual(body["redirect_uri"], "http://localhost/cb")

    def test_url(self):
        scan = self.r.scan()
        url = scan.url(f"http://idp/logout?id_token_hint={ID_TOKEN}&state=abcdefgh&post_logout_redirect_uri=http://app/")
        self.assertNotIn(ID_TOKEN, url)
        self.assertIn(f"id_token_hint=<jwt {fingerprint(ID_TOKEN)}>", url)
        self.assertIn("state=abcd…(8)", url)
        self.assertIn("post_logout_redirect_uri=http://app/", url)
        self.assertEqual(scan.url("http://rs/mcp"), "http://rs/mcp")

    def test_event_stream(self):
        events = self.r.scan().body('event: message\ndata: {"jsonrpc": "2.0", "id": 1, "result": {}}\n\n'
                                    "event: ping\ndata: hello\n\n", "text/event-stream", "res.body")
        self.assertEqual(events[0], {"event": "message", "data": {"jsonrpc": "2.0", "id": 1, "result": {}}})
        self.assertEqual(events[1], {"event": "ping", "data": "hello"})

    def test_other_bodies_keep_only_their_size_but_tokens_are_noticed(self):
        scan = self.r.scan()
        body = scan.body(f"<html><script>var t = '{ACCESS}'</script></html>", "text/html", "res.body")
        self.assertEqual(body["type"], "text/html")
        self.assertEqual(scan.refs[0]["fp"], fingerprint(ACCESS))
        self.assertEqual(self.r.scan().body(b"\xff\xfe", "application/octet-stream", "x"), {"binary": 2})


class StrictTests(unittest.TestCase):
    def test_identity_claims_are_hashed_and_bodies_cut(self):
        scan = Redactor("strict").scan()
        body = scan.body(dumps({"id_token": ID_TOKEN, "token_type": "Bearer", "profile": {"bio": "x"}}),
                         "application/json", "res.body")
        claims = scan.refs[0]["claims"]
        self.assertEqual(claims["sub"], fingerprint("user-123"))
        self.assertEqual(claims["email"], fingerprint("a@example.com"))
        self.assertEqual(claims["aud"], "demo-web")
        self.assertEqual(body["token_type"], "Bearer")
        self.assertEqual(body["profile"], "…")


    def test_strict_keeps_protocol_structure_but_no_free_text(self):
        scan = Redactor("strict").scan()
        walk = lambda body: scan.walk(body, "res.body")  # noqa: E731
        self.assertEqual(walk({"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "Unsupported protocol"}}),
                         {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "Unsupported protocol"}})
        self.assertEqual(walk({"result": {"tools": [{"name": "Memes_MakeMeme", "description": "d", "inputSchema": {}}]}}),
                         {"result": {"tools": [{"name": "Memes_MakeMeme", "description": "…", "inputSchema": "…"}]}})
        self.assertEqual(walk({"method": "tools/call", "params": {"name": "Memes_MakeMeme", "arguments": {"hints": ["nerdy"]}}}),
                         {"method": "tools/call", "params": {"name": "Memes_MakeMeme", "arguments": "…"}})
        self.assertEqual(walk({"model": "m", "messages": [{"role": "user", "content": "Make me a meme"}]}),
                         {"model": "m", "messages": "…"})
        self.assertEqual(walk({"stop_reason": "tool_use", "content": [
            {"type": "text", "text": "Sure"}, {"type": "tool_use", "id": "toolu_1", "name": "Memes_MakeMeme", "input": {"hints": ["x"]}}]}),
            {"stop_reason": "tool_use", "content": [{"type": "text", "text": "…"},
                                                    {"type": "tool_use", "id": "toolu_1", "name": "Memes_MakeMeme", "input": "…"}]})
        self.assertEqual(walk({"content": "a plain-text reply", "message": "not an error"}),
                         {"content": "…", "message": "…"})

    def test_strict_filters_form_bodies_too(self):
        body = Redactor("strict").scan().body("grant_type=password&client_id=memes-dev&username=dummyuser1&password=pw",
                                              "application/x-www-form-urlencoded", "req.body")
        self.assertEqual(body, {"grant_type": "password", "client_id": "memes-dev", "username": "…", "password": "…"})


class RawTests(unittest.TestCase):
    def test_values_kept_but_still_fingerprinted(self):
        scan = Redactor("raw").scan()
        body = scan.body(dumps({"access_token": ACCESS, "password": "pw"}), "application/json", "req.body")
        self.assertEqual(body, {"access_token": ACCESS, "password": "pw"})
        self.assertEqual(scan.refs[0]["fp"], fingerprint(ACCESS))


if __name__ == "__main__":
    unittest.main()
