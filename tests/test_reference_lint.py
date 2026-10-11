"""Keep raw tokens, secrets and one-time claim values out of docs/authtrace/ (PLAN.md §16)."""

import re
import unittest

from _helpers import ROOT, viz

DOCS = ROOT / "docs" / "authtrace"
TEXT_SUFFIXES = {".md", ".py", ".html", ".log", ".txt", ".jsonl", ".json"}

RAW_JWT = re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}")
# One-time or binding claims must be masked inside decoded tokens. A masked value is short
# ("abcd…(43)"), redacted, or a placeholder; anything 16+ characters of token alphabet is not.
ONE_TIME_CLAIM = re.compile(r"""["']?(nonce|at_hash|c_hash|state_checker)["']?\s*[:=]\s*["']([\w-]{16,})["']""")
PASSWORD_LITERAL = re.compile(r"""["']?password["']?\s*[:=]\s*["']([^"'*…\s]{3,})["']""", re.I)
# The plan's prose names key prefixes (`sk-ant-`, `arc_`) as lint examples; a bare prefix isn't a key.
BARE_PREFIX = re.compile(r"(sk-ant-|arc_)[`'\",.;:)…]*")


def files():
    for path in sorted(DOCS.rglob("*")):
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            yield path, path.read_text(encoding="utf-8", errors="replace")


class ReferenceLintTests(unittest.TestCase):
    def test_has_files(self):
        self.assertTrue(any(True for _ in files()))

    def test_no_raw_jwts(self):
        for path, text in files():
            self.assertIsNone(RAW_JWT.search(text), f"{path.relative_to(ROOT)} contains a raw JWT")

    def test_no_secret_patterns(self):
        for path, text in files():
            for what, pattern in viz.SECRET_PATTERNS:
                hits = [m.group() for m in pattern.finditer(text) if not BARE_PREFIX.fullmatch(m.group())]
                self.assertEqual(hits, [], f"{path.relative_to(ROOT)} looks like it contains {what}")

    def test_one_time_claims_are_masked(self):
        for path, text in files():
            m = ONE_TIME_CLAIM.search(text)
            self.assertIsNone(m, f"{path.relative_to(ROOT)} has an unmasked {m and m.group(1)}")

    def test_no_password_literals(self):
        for path, text in files():
            m = PASSWORD_LITERAL.search(text)
            self.assertIsNone(m, f"{path.relative_to(ROOT)} has a password literal")


if __name__ == "__main__":
    unittest.main()
