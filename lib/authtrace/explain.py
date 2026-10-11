"""`authtrace explain`: decode one pasted token without ever echoing it back.

The token arrives on stdin (never as an argument, which would land in shell history). Output is
its fingerprint, a mask, the decoded header and claims (one-time claims masked), its lifetime and
token-level findings. Decoded is not verified: there's no JWKS here to check a signature against.
"""

from __future__ import annotations

import time

from .redact import Redactor, decode_jwt, fingerprint, mask
from .rules import MAX_ACCESS_LIFETIME

TYP = {"bearer": "access", "at+jwt": "access", "id": "id", "refresh": "refresh", "offline": "refresh (offline)",
       "logout+jwt": "logout"}


def explain(raw: str, now: float | None = None) -> dict:
    token = raw.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    out = {"fingerprint": fingerprint(token), "mask": mask(token), "length": len(token)}
    decoded = decode_jwt(token)
    if not decoded:
        out.update(format="opaque", type="unknown",
                   note="Not a JWT, so there's nothing to decode. Only the issuer can say what it is (introspection, RFC 7662). "
                        "If it's a refresh token, client secret or API key, treat it as exposed and revoke it.")
        return out
    header, _ = decoded
    scan = Redactor("dev").scan()
    claims = scan.claims(decoded[1])
    typ = str(header.get("typ") if header.get("typ") not in (None, "JWT") else claims.get("typ", "")).lower()
    looks_id = "nonce" in claims or (claims.get("aud") is not None and claims.get("aud") == claims.get("azp"))
    kind = TYP.get(typ, "id" if looks_id else "access" if "scope" in claims else "unknown")
    out.update(format="jwt", type=kind, header=header, claims=claims, verified=False)
    now = time.time() if now is None else now
    findings = []
    if isinstance(claims.get("exp"), (int, float)):
        out["expires_in"] = round(claims["exp"] - now)
        if isinstance(claims.get("iat"), (int, float)):
            out["lifetime"] = claims["exp"] - claims["iat"]
            if kind == "access" and out["lifetime"] > MAX_ACCESS_LIFETIME:
                findings.append(("AT-LIFETIME", "warn", f"lives {out['lifetime'] // 60} minutes; over {MAX_ACCESS_LIFETIME // 60} widens the window for a leaked token"))
    else:
        findings.append(("NO-EXP", "warn", "no `exp`: it never expires on its own"))
    alg = str(header.get("alg", "")).lower()
    if alg == "none":
        findings.append(("JWT-ALG", "critical", "`alg: none`: unsigned, so anyone can mint it"))
    elif alg.startswith("hs") and kind in ("access", "id"):
        findings.append(("JWT-ALG", "warn", f"`{header['alg']}` is a shared secret: anyone holding the key can mint tokens"))
    out["findings"] = [{"rule": r, "severity": s, "detail": d} for r, s, d in findings]
    return out


def format_text(e: dict) -> str:
    lines = [f"token {e['fingerprint']}  {e['mask']}  ({e['format']}, {e['type']})"]
    if e["format"] != "jwt":
        return "\n".join(lines + [f"  {e['note']}"])
    lines.append(f"  header  {e['header']}")
    for k, v in e["claims"].items():
        lines.append(f"  {k:<20} {v}")
    if "expires_in" in e:
        state = f"expires in {e['expires_in']} s" if e["expires_in"] > 0 else f"expired {-e['expires_in']} s ago"
        lines.append(f"  {state}" + (f"; lifetime {e['lifetime']} s" if "lifetime" in e else ""))
    lines.append("  decoded, not verified: no JWKS was available to check the signature")
    for f in e["findings"]:
        lines.append(f"  {f['severity']:<8} {f['rule']}: {f['detail']}")
    return "\n".join(lines)
