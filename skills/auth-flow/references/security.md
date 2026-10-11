# Security and privacy

The tool captures credentials in order to teach about them, so it must not leak them. Source: [PLAN.md §13](../../../docs/authtrace/PLAN.md#13-security-and-privacy-the-tool-captures-credentials).

- **Redaction by default (`dev`).** Token values become a fingerprint (a hash prefix) plus a mask like `eyJhbGciOiJS…(redacted)`. `client_secret`, `password`, `code`, `code_verifier`, `refresh_token`, cookies and API keys are masked to `first4…(len)`. Claims stay readable.
- **`strict`, for sharing,** also hashes `sub`, emails and names, and drops bodies except the classified fields. A publish re-checks for `strict`, or asks.
- **`raw`** is opt-in and local-only, with files at mode 0600 and a loud banner.
- **Mask one-time values inside decoded tokens too:** `nonce`, `at_hash`, Keycloak's `state_checker`, and in `strict` also `session_state` and `sid`. The 2026-10-03 browser run missed these at first.
- **Leak detection uses fingerprints in memory.** To catch a token reaching an LLM API, keep the known tokens' fingerprints and scan outgoing bodies. Never persist raw values to do it.
- **Proxy and CA hygiene.** Listen on 127.0.0.1, generate a per-install CA, trust it per process through env vars, and never install it system-wide by default.
- **Active probes** are opt-in, limited to an allowlist of hosts the user owns, and never send real users' tokens anywhere new.
- **Outputs are gitignored** (`.authtrace/`). `check` fails if a trace sits in a tracked path that isn't ignored.
