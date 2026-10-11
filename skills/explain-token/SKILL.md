---
name: explain-token
description: (Preview) Decode and explain a pasted JWT access or ID token, covering its header, claims, lifetime, which party checks which claim, and any risks, without ever echoing the token back. Use when the user runs /visualizer:explain-token, or asks what is inside a JWT, bearer token or ID token.
argument-hint: "<token>"
disable-model-invocation: true
---

# Explain a token

**Preview.** Don't decode the token by hand: the decoder does it and masks what must stay masked.

The token is in `$ARGUMENTS`. Pass it on stdin through a quoted heredoc, never as a command-line argument and never through a file:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/authtrace.py explain <<'TOKEN'
<the token>
TOKEN
```

If no token was given, ask for one, or suggest `pbpaste | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/authtrace.py explain` so it never enters the conversation.

Then explain the result: the token type, the issuer, who it's for (`aud`) and who requested it (`azp`), the scopes, the lifetime, which party checks each claim, and any findings (for example `alg: none`, or a lifetime over an hour).

## Rules

- **Never repeat the token.** Refer to it by fingerprint and a mask like `eyJhbGciOiJS…(redacted)`, in chat and in any file.
- **Decoded is not verified.** Say that claims are only decoded unless a JWKS was available to check the signature.
- **Don't store it.** No files, no memory, no artifacts containing the token or its one-time claims (`nonce`, `at_hash`).
- If the input looks like a refresh token, a client secret or an API key rather than a JWT, say so and suggest revoking it if it's a real one.
