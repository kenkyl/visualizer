# Confidence and provenance

Every fact in a trace carries one of these levels. It's the tool's signature feature: a reconstructed narrative gets facts wrong (the first memes-agent artifact said the browser never held the PKCE verifier; capture showed it does), so the trace says how each fact is known. Source: [PLAN.md §7](../../../docs/authtrace/PLAN.md#7-confidence-and-provenance-model).

| Level | Meaning | Example | Drawn as |
|---|---|---|---|
| `observed` | Seen on the wire or in a log | `POST /mcp → 400`, body text | Solid |
| `decoded` | Derived deterministically, not trusted | JWT claims, base64-decoded | Solid, "decoded" tag |
| `verified` | Cryptographically checked by authtrace | Signature checked against the fetched JWKS | Solid + check mark |
| `declared` | Stated by config, metadata or code, with an evidence path | `expected_audiences=[CANONICAL_URL]` in `server.py` | Solid, evidence link |
| `inferred` | Heuristic or LLM reasoning; show the reasoning | "JWKS fetched once, then cached" (from `cache_ttl=3600`, not seen) | Dashed + "inferred" |
| `unknown` | An explicit gap | A third-party API accepted the token; how it validated isn't visible | Hatched + "?" |

Rules:

- An inference says *why* ("returned 401 with `WWW-Authenticate: Bearer`, so it acts as a resource server") and *what would confirm it* ("start process X under capture", "point at its source").
- Roles of third-party hosts are assigned by rule first (status codes, headers, discovery metadata), then refined by you. Both stay `inferred` until evidence upgrades them.
- A narrative step inherits the lowest confidence of the exchanges it cites, unless you cite code evidence that raises it to `declared`. Confidence never rises without evidence.
- Expected traffic that wasn't seen (a JWKS fetch by an already-running server) appears as `inferred` or `declared`. It is never dropped.
