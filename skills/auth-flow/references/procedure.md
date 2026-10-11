# The /visualizer:auth-flow procedure

This is the procedure that produced the two memes-agent artifacts on 2026-10-03, generalized. Each step is a requirement. Source: [PLAN.md §1 and §12](../../../docs/authtrace/PLAN.md#12-claude-code-plugin-design).

Commands marked *(Mx)* don't exist yet. Until they do, the skill stops after `doctor`.

1. **Discover.** Read CLAUDE.md, the README's architecture section, decision records and learning notes, then manifests, entry points and IdP config (a realm JSON, for example). Pull out:
   - the components (reuse a visualizer `architecture.json` if there is one);
   - the **invariants**, which become checks and callouts;
   - the **learning goals**, which become the step filters. Without any, use the defaults: G1 OAuth roles, G2 tokens and claims, G3 resource-server protection, G4 front vs back channel, G5 OIDC identity layer, G6 sessions, G7 SAML.
2. **Ask once, with defaults:**
   - which flow to run: a command (such as `uv run pytest -m live`) or a browser flow;
   - the output directory: `.authtrace/` in the target, gitignored (default), or a path outside the repo;
   - the redaction level: `dev` (default), or `strict` for sharing;
   - whether to publish a private artifact;
   - whether active probes are allowed (default no).
3. **Preflight.** Health-check what the flow needs: the discovery URL returns 200, the resource server returns 401 with no token, and required env vars are set (by name only). Services that are already running aren't captured. Offer to restart them under capture, and warn when a registered `redirect_uri` pins their port.
4. **Capture** *(M2, M5).* `authtrace.py run -- <cmd>` writes redacted exchanges to `.authtrace/run-<time>/exchanges.jsonl` (with `.authtrace/.gitignore`, so `git status` stays unchanged). Add `import-har <file>` for a browser's front channel, and from M5 the proxy or Playwright.
5. **Build facts** *(M3).* `authtrace.py build` → `trace.json`: exchanges, tokens, nodes by rule, findings.
6. **Investigate.** Every non-2xx, every `inferred` node and every finding. Read the source, including installed libraries under `.venv/…/site-packages`, and add `checks` with file:line evidence. (This is how the memes-agent 400 on the first `POST /mcp` was explained: an unsupported protocol version, after the token had already been validated.)
7. **Narrate.** Write the `narrative` section: steps, what crossed the wire and why, who checks what, invariants, OAuth roles, goal tags and a check-yourself question per step. Mark reconstructed steps as such.
8. **Check** *(M4).* `authtrace.py check`: schema, references, evidence paths that exist, a token and secret lint over the whole file, and confidence on every fact. Fix until it passes.
9. **Render** *(M1).* `authtrace.py render trace.json -o flow.html`. Look at it once if a browser is available.
10. **Deliver.** Share the raw (redacted) logs in chat, then publish the artifact if the user agreed, private by default. Report anything still `unknown`, and what would resolve it. Show that the target's `git status` is unchanged.
