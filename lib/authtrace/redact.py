"""What a capture may write down (PLAN.md §13). Runs inside the traced process, before any write.

  dev     default. Tokens become fingerprints plus decoded (masked) claims; secrets, codes and
          cookies are masked; claims stay readable.
  strict  for sharing. dev, plus identity claims hashed and bodies cut to protocol fields.
  raw     opt-in, local only. Values are kept; fingerprints and decodes are still recorded.

A token is replaced by a placeholder (`<jwt fp:1a2b3c4d>`, `<token fp:…>`) and listed once in the
scan's `refs` with where it was seen. Raw values live only in `Redactor.known`, in memory, so a
token that reappears later (say, in a request to an LLM) is still recognized.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

LEVELS = ("dev", "strict", "raw")

JWT = re.compile(r"eyJ[\w-]+\.eyJ[\w-]*\.[\w-]*")
TOKEN_FIELDS = {"access_token", "id_token", "refresh_token", "subject_token", "actor_token", "assertion",
                "client_assertion", "logout_token", "token", "id_token_hint", "device_code"}
SECRET_FIELDS = {"password", "client_secret", "secret", "api_key", "apikey"}
MASK_FIELDS = {"code", "code_verifier", "code_challenge", "state", "nonce", "session_state", "session_code",
               "user_code", "tab_id", "execution", "client_data", "samlresponse", "samlrequest"}
# Claims that are one-time or bind a token to a session: masked even in dev (PLAN.md §13).
ONE_TIME_CLAIMS = {"nonce", "at_hash", "c_hash", "s_hash", "state_checker"}
IDENTITY_CLAIMS = {"sub", "email", "name", "given_name", "family_name", "preferred_username", "upn", "sid",
                   "session_state", "oid"}
SENSITIVE_HEADER = re.compile(r"key|token|secret|passw|auth|session|cookie|signature|csrf|xsrf", re.I)
# Challenges name a scheme and where to get a token (RFC 6750, RFC 9728); they carry no secret.
PUBLIC_HEADERS = {"www-authenticate", "proxy-authenticate"}
URL_HEADERS = {"location", "referer", "content-location", "origin"}
# strict keeps only fields a protocol decoder needs. EXTEND: add fields as M3's decoders need them.
STRICT_KEYS = {"grant_type", "client_id", "scope", "response_type", "redirect_uri", "code_challenge_method",
               "token_type", "expires_in", "refresh_expires_in", "error", "error_description", "jsonrpc",
               "method", "id", "protocolVersion", "resource", "authorization_servers", "issuer", "jwks_uri",
               "keys", "kid", "alg", "use", "kty", "stop_reason", "type", "name", "result", "params",
               "bearer_methods_supported", "active", "aud", "iss", "exp", "iat", "azp", "model", "isError",
               "capabilities", "serverInfo", "version"} | TOKEN_FIELDS
# Containers strict walks into but never keeps as text: their free-text fields (a prompt, a tool's
# arguments, Claude's reply, a tool result) still fall outside STRICT_KEYS and are dropped.
STRICT_CONTAINERS = {"content", "tools"}
STRICT_ERROR_KEYS = {"code", "message"}  # inside an `error` object: JSON-RPC's explanation
MAX_STRING = 2000


def fingerprint(value: str) -> str:
    return "fp:" + hashlib.sha256(value.encode()).hexdigest()[:8]


def mask(value: str) -> str:
    return f"{value[:4]}…({len(value)})" if value else value


def _b64json(part: str):
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def decode_jwt(value: str):
    """(header, claims) for a JWT-shaped string, or None. Decoded, not verified."""
    parts = value.split(".")
    if len(parts) != 3:
        return None
    try:
        header, claims = _b64json(parts[0]), _b64json(parts[1])
    except (ValueError, binascii.Error):
        return None
    return (header, claims) if isinstance(header, dict) and isinstance(claims, dict) else None


class Redactor:
    """One per process. Holds the raw values of tokens seen so far, in memory only."""

    def __init__(self, level: str = "dev"):
        if level not in LEVELS:
            raise ValueError(f"redaction level must be one of {', '.join(LEVELS)}")
        self.level = level
        self.known: dict[str, str] = {}  # raw token -> fingerprint; never written

    def scan(self) -> Scan:
        return Scan(self)


class Scan:
    """Redacts the parts of one exchange and collects the tokens it saw."""

    def __init__(self, redactor: Redactor):
        self.r = redactor
        self.raw = redactor.level == "raw"
        self.strict = redactor.level == "strict"
        self.refs: list[dict] = []

    # ---- tokens and secrets ----
    def token(self, value: str, where: str, kind: str = "token") -> str:
        fp = fingerprint(value)
        ref = {"fp": fp, "where": where, "kind": kind}
        decoded = decode_jwt(value)
        if decoded:
            ref["format"] = "jwt"
            ref["header"], ref["claims"] = decoded[0], self.claims(decoded[1])
        else:
            ref["format"], ref["len"] = "opaque", len(value)
        if len(value) >= 16:
            self.r.known.setdefault(value, fp)
        self.refs.append(ref)
        if self.raw:
            return value
        return f"<{'jwt' if decoded else 'token'} {fp}>"

    def claims(self, claims: dict) -> dict:
        if self.raw:
            return claims
        out = {}
        for k, v in claims.items():
            if k in ONE_TIME_CLAIMS and isinstance(v, str):
                out[k] = mask(v)
            elif self.strict and k in IDENTITY_CLAIMS and isinstance(v, str):
                out[k] = fingerprint(v)
            else:
                out[k] = v
        return out

    def secret(self, value: str) -> str:
        return value if self.raw else "***"

    def masked(self, value: str) -> str:
        return value if self.raw else mask(value)

    def text(self, value: str, where: str) -> str:
        """Any free text: JWTs and already-known tokens inside it become placeholders."""
        value = JWT.sub(lambda m: self.token(m.group(), where, "embedded"), value)
        for raw, fp in self.r.known.items():
            if raw in value:
                self.refs.append({"fp": fp, "where": where, "kind": "embedded", "format": "known"})
                if not self.raw:
                    value = value.replace(raw, f"<token {fp}>")
        if len(value) > MAX_STRING and not self.raw:
            value = value[:MAX_STRING] + f"…(+{len(value) - MAX_STRING})"
        return value

    def field(self, key: str, value, where: str):
        k = key.lower()
        if isinstance(value, str):
            if k in TOKEN_FIELDS:
                return self.token(value, f"{where}:{key}", k)
            if k in SECRET_FIELDS:
                return self.secret(value)
            if k in MASK_FIELDS:
                return self.masked(value)
        return self.walk(value, f"{where}:{key}")

    def walk(self, obj, where: str):
        if isinstance(obj, dict):
            if self.strict:
                return {k: (self.field(k, v, where) if self._strict_keeps(k, v, where) else "…")
                        for k, v in obj.items()}
            return {k: self.field(k, v, where) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.walk(x, where) for x in obj]
        if isinstance(obj, str):
            return self.text(obj, where)
        return obj

    @staticmethod
    def _strict_keeps(key: str, value, where: str) -> bool:
        if key in STRICT_KEYS:
            return True
        if key in STRICT_CONTAINERS:
            return isinstance(value, (list, dict))
        return key in STRICT_ERROR_KEYS and where.endswith(":error")

    # ---- URLs, headers, bodies ----
    def url(self, url: str, where: str = "url") -> str:
        parts = urlsplit(url)
        if not parts.query and not parts.fragment:
            return url
        def encode(qs, at):
            pairs = [(k, self.field(k, v, at)) for k, v in parse_qsl(qs, keep_blank_values=True)]
            # Placeholders stay readable: they are display text, not a URL anyone will fetch.
            return urlencode(pairs, safe="/:<>*() ", quote_via=quote).replace("%E2%80%A6", "…")

        query = encode(parts.query, where)
        frag = encode(parts.fragment, f"{where}#") if "=" in parts.fragment else parts.fragment
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, frag))

    def headers(self, pairs, where: str) -> dict:
        """[(name, value), …] → {lowercased name: value, or a list when a name repeats}."""
        out: dict = {}
        for name, value in pairs:
            name = name.lower()
            v = self.header(name, value, f"{where}.headers:{name}")
            if name in out:
                out[name] = (out[name] if isinstance(out[name], list) else [out[name]]) + [v]
            else:
                out[name] = v
        return out

    def header(self, name: str, value: str, where: str):
        if name in ("authorization", "proxy-authorization"):
            scheme, _, cred = value.partition(" ")
            if scheme.lower() in ("bearer", "dpop"):
                return f"{scheme} {self.token(cred.strip(), where, 'bearer')}"
            if scheme.lower() == "basic":
                return f"Basic {self.basic(cred.strip())}"
            return f"{scheme} {self.masked(cred)}"
        if name == "cookie":
            return "; ".join(self.cookie(part.strip(), where) for part in value.split(";") if part.strip())
        if name == "set-cookie":
            first, _, attrs = value.partition(";")
            return self.cookie(first.strip(), where) + (f";{attrs}" if attrs else "")
        if name in URL_HEADERS:
            return self.url(value, where)
        if SENSITIVE_HEADER.search(name) and name not in PUBLIC_HEADERS:
            return self.token(value, where, name) if decode_jwt(value) else self.masked(value)
        return self.text(value, where)

    def basic(self, cred: str) -> str:
        """`client_secret_basic`: keep the client id (public), never the secret."""
        try:
            user, _, _ = base64.b64decode(cred).decode().partition(":")
        except (ValueError, binascii.Error, UnicodeDecodeError):
            return self.masked(cred)
        return cred if self.raw else f"{unquote(user)}:***"

    def cookie(self, pair: str, where: str) -> str:
        name, _, value = pair.partition("=")
        if not value or self.raw:
            return pair
        if decode_jwt(value):
            return f"{name}={self.token(value, where, 'cookie')}"
        # EXTEND: decode known session-cookie formats (Starlette's signed JSON holds the PKCE
        # verifier; see reference/browser-flow/tracecommon.py) for PKCE-VERIFIER-IN-COOKIE.
        return f"{name}=<cookie {fingerprint(value)}>"

    def body(self, raw, content_type: str, where: str):
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = bytes(raw).decode()
            except UnicodeDecodeError:
                return {"binary": len(raw)}
        if not raw:
            return None
        ctype = (content_type or "").split(";")[0].strip().lower()
        if "json" in ctype or (not ctype and raw.lstrip()[:1] in ("{", "[")):
            try:
                return self.walk(json.loads(raw), where)
            except ValueError:
                pass
        if ctype == "application/x-www-form-urlencoded":
            return self.walk(dict(parse_qsl(raw, keep_blank_values=True)), where)
        if ctype == "text/event-stream":
            return self.events(raw, where)
        if self.raw:
            return raw
        self.text(raw, where)  # still record tokens hiding in pages and scripts
        return {"len": len(raw), "type": ctype or "unknown"}

    def events(self, raw: str, where: str) -> list:
        """Server-sent events (MCP's streamable HTTP answers this way): each data line, decoded."""
        events = []
        for block in re.split(r"\r?\n\r?\n", raw.strip()):
            event = {}
            for line in block.splitlines():
                key, _, val = line.partition(":")
                if key in ("event", "id"):
                    event[key] = val.strip()
                elif key == "data":
                    try:
                        event["data"] = self.walk(json.loads(val), f"{where}:event")
                    except ValueError:
                        event["data"] = self.text(val.strip(), f"{where}:event")
            if event:
                events.append(event)
        return events
