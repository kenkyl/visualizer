"""The secret lint: raw tokens, secrets and one-time values that must never sit in a trace.

It reads text, not parsed JSON, so it sees exactly what would be shared: every string in every
section, the narrative included. `check` runs it over the whole trace file, and
tests/test_reference_lint.py runs it over docs/authtrace/ and the fixtures (PLAN.md §13, §16).

A redacted value is a placeholder (`<jwt fp:1a2b3c4d>`), a mask (`abcd…(43)`), or a fingerprint.
Anything that looks like the value itself is a hit.
"""

from __future__ import annotations

import re

from ._viz import viz

RAW_JWT = re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}")
# One-time or binding claims must be masked inside decoded tokens. A masked value is short
# ("abcd…(43)"), redacted, or a placeholder; anything 16+ characters of token alphabet is not.
ONE_TIME_CLAIM = re.compile(r"""["']?(nonce|at_hash|c_hash|s_hash|state_checker)["']?\s*[:=]\s*["']([\w-]{16,})["']""")
# A mask may be JSON-escaped (`"\u2026"`), so a value starting with a backslash isn't a literal.
PASSWORD_LITERAL = re.compile(r"""["']?password["']?\s*[:=]\s*["']([^"'*…\s\\]{3,})["']""", re.I)
SECRET_NAMES = r"client_secret|secret|api_key|apikey|access_token|refresh_token|id_token|code_verifier|code|token"
# Quotes may be escaped: a JSON body copied into a string (a step's wire block) is `\"key\": \"…\"`.
SECRET_FIELD = re.compile(r'\\?"(' + SECRET_NAMES + r')\\?"\s*:\s*\\?"([\w\-.~+/]{16,}=*)\\?"', re.I)
# A masked query value is percent-encoded (`abcd%E2%80%A6%2843%29`), so it stops at the first `%`.
SECRET_PARAM = re.compile(r"[?&](" + SECRET_NAMES + r")=([\w\-.~+/]{16,})", re.I)
# A challenge (`WWW-Authenticate: Bearer resource_metadata=…`) names parameters, not a token.
BEARER = re.compile(r"\bBearer\s+(?!(?:realm|resource_metadata|scope|error|error_description)=)([\w\-.~+/]{16,}=*)")
# The plan's prose names key prefixes (`sk-ant-`, `arc_`) as lint examples; a bare prefix isn't a key.
BARE_PREFIX = re.compile(r"(sk-ant-|arc_)[`'\",.;:)…]*")

PATTERNS = [
    ("a raw JWT", RAW_JWT),
    ("an unmasked one-time claim", ONE_TIME_CLAIM),
    ("a password literal", PASSWORD_LITERAL),
    ("an unmasked secret field", SECRET_FIELD),
    ("an unmasked secret in a URL", SECRET_PARAM),
    ("an unmasked Bearer token", BEARER),
] + list(viz.SECRET_PATTERNS)


def lint_text(text: str) -> list[tuple[int, str]]:
    """(line number, what) for each hit. The value itself is never part of the result."""
    hits = []
    for what, pattern in PATTERNS:
        for m in pattern.finditer(text):
            if BARE_PREFIX.fullmatch(m.group()):
                continue
            hits.append((text.count("\n", 0, m.start()) + 1, what))
    return sorted(set(hits))
