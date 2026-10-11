"""Keep raw tokens, secrets and one-time claim values out of docs/authtrace/ and authtrace's
fixtures (PLAN.md §16). The patterns are authtrace's own lint, the one `check` runs on a trace."""

import sys
import unittest

from _helpers import ROOT

sys.path.insert(0, str(ROOT / "lib"))

from authtrace.lint import lint_text  # noqa: E402

DOCS = ROOT / "docs" / "authtrace"
FIXTURES = ROOT / "tests" / "fixtures" / "authtrace"
TEXT_SUFFIXES = {".md", ".py", ".html", ".log", ".txt", ".jsonl", ".json"}


def files():
    for path in sorted([*DOCS.rglob("*"), *FIXTURES.rglob("*")]):
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            yield path, path.read_text(encoding="utf-8", errors="replace")


class ReferenceLintTests(unittest.TestCase):
    def test_has_files(self):
        self.assertTrue(any(True for _ in files()))

    def test_nothing_secret(self):
        for path, text in files():
            self.assertEqual(lint_text(text), [], f"{path.relative_to(ROOT)}: (line, what)")


class LintPatternTests(unittest.TestCase):
    def assertHit(self, text, what):
        self.assertIn(what, [w for _, w in lint_text(text)], text)

    def test_catches_values(self):
        self.assertHit("eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhYmMifQ.c2lnbmF0dXJlLXZhbHVl", "a raw JWT")
        self.assertHit('{"nonce": "GTrOvJabcdefghijklmn"}', "an unmasked one-time claim")
        self.assertHit('{"password": "hunter22"}', "a password literal")
        self.assertHit('{"refresh_token": "8f2d1c0b9a8e7f6d5c4b"}', "an unmasked secret field")
        self.assertHit('"wire": "{\\"refresh_token\\": \\"8f2d1c0b9a8e7f6d5c4b\\"}"', "an unmasked secret field")
        self.assertHit("/callback?state=x&code=192413aabbccddeeff00", "an unmasked secret in a URL")
        self.assertHit("Authorization: Bearer 0123456789abcdefABCDEF", "an unmasked Bearer token")
        self.assertHit("key sk-ant-api03-abcdefghijkl", "an Anthropic API key")

    def test_masks_and_placeholders_pass(self):
        for text in ('{"refresh_token": "<jwt fp:3bc2ee9e>"}', '{"password": "…"}', '{"nonce": "GTrO…(20)"}',
                     "?code=192413%E2%80%A6%2898%29", "Authorization: Bearer <jwt fp:4b64c169>",
                     'WWW-Authenticate: Bearer resource_metadata="http://127.0.0.1:8001/x"',
                     "key prefixes such as `sk-ant-` and `arc_`", '{"token": "t1"}'):
            self.assertEqual(lint_text(text), [], text)


if __name__ == "__main__":
    unittest.main()
