# The /visualizer:auth-flow procedure

This is the procedure that produced the two memes-agent artifacts on 2026-10-03, generalized. Each step is a requirement. Source: [PLAN.md §1 and §12](../../../docs/authtrace/PLAN.md#12-claude-code-plugin-design). `$AT` means `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/authtrace.py`, run from the target's root.

## 1. Discover

Read CLAUDE.md, the README's architecture section, decision records and learning notes (gitignored private notes too, if the project keeps its goals there; use them for goals and framing, but don't quote or cite them, since the artifact may be shared), then manifests, entry points and IdP config (a realm JSON, for example). Read the code on the request path: the client that gets tokens, whatever attaches them, the resource server's auth setup, and the model client. Pull out:

- **components**, with names from config or code. Reuse a visualizer `architecture.json` if there is one.
- **invariants** ("tokens never reach the LLM", "identity is `sub`"). They become step callouts (`inv`) and checks.
- **learning goals**. They become the goal filters. Without any, use the defaults: G1 OAuth roles, G2 tokens and claims, G3 resource-server protection, G4 front vs back channel, G5 OIDC identity layer, G6 sessions, G7 SAML.

## 2. Ask once, with defaults

- **Flow:** a command (`uv run pytest -m live`), a browser HAR, or a capture already in `.authtrace/`. Reusing one costs nothing. A fresh run may call paid APIs. To reuse one, build into a new folder (`build <capture> -o .authtrace/<name>/trace.json`) and keep the narrative and page there.
- **Servers' side:** `run` captures only the processes it starts. A resource server that's already running shows up as an origin, with its token validation and key fetch `inferred`. Restarting it under capture makes them observed, but needs a yes, and a pinned `redirect_uri` may fix its port.
- **Redaction:** `dev`, or `strict` for anything that will be shared.
- **Publish** a private artifact: yes or no.
- **Active probes** (a request with no token or a wrong `aud`, to see the 401): default no. Only against hosts the user owns and names.

## 3. Preflight

The discovery URL returns 200, the resource server answers, and required env vars are set (checked by name only). A server that's already running won't be captured, and that's fine: `build` draws its unseen traffic as `inferred`. Offer to restart it under capture only if seeing its side matters, and warn when a registered `redirect_uri` pins its port.

## 4–5. Capture and build

`$AT run -- <cmd>`, then `$AT build <capture folder> --target <name>`. Relay `build`'s output: nodes with their inferred roles and reasons, unseen traffic, and findings. `$AT show trace.json` starts with the run's command, exit code and duration, then lists every id you'll cite. For a long trace, skim it for the shape and look up ids as you need them. `$AT show trace.json x3 t1` prints exchanges as wire text, and tokens' header and claims.

## 6. Investigate

This is where an artifact earns its explanations. Work through, in order:

1. **Every non-2xx exchange.** Find the code that produced it. Read the server's framework, not just the app: installed libraries live under the target's `.venv/lib/python3.*/site-packages/`. Establish *what* failed and *what had already passed*. The memes-agent 400 on the first `POST /mcp` was an unsupported protocol version, and reading the middleware proved the token had been validated first.
2. **Every `inferred` or `unknown` part** (`check` lists them). For each one, find the code or config that declares it, such as the JWKS URL and cache TTL in the resource server's setup, or the realm's audience mapper. If nothing does, keep it inferred, and work out what would confirm it.
3. **Every finding.** Is it by design (a dev-only password grant), a real risk, or an artifact of the test? Cite where that's decided.
4. **Who checks what.** For each token-bearing hop: which component checks which claim (signature, `iss`, `aud`, `exp`, scope), and at which file:line. Record what the code shows. Don't assume a check exists because the spec says it should: a granted scope that nothing checks is worth a step.
5. **Reconstructed flows.** If a goal needs a flow that wasn't captured (a browser login when only the test ran), you may narrate it from code as a separate tour. It cites no exchanges, so its steps come out `declared` (with code) or `inferred`. Give it a banner saying so.

Keep notes as you go: (claim, evidence path:line, confidence). Every sentence the narrative states as fact should trace back to one of them, or to an exchange.

## 7. Narrate

Write `narrative.json` beside `trace.json`, following [narrative.md](narrative.md), then run `$AT narrate trace.json narrative.json`.

- **One tour per distinct flow** in the capture. A test suite can hold several (a happy path, a missing scope, an offline grant), and each gets its own tour. When tests repeat a flow, narrate it once, and point out what differs in a note.
- **A step per meaningful hop,** not per exchange: `initialize`, `notifications/initialized` and the event stream can be one "session opens" step that cites all three.
- **Each step answers:** what crossed the wire (`wire`, copied from `show`), who checked what (`checks`), why it matters (`what`, `inv`), which OAuth role each party plays (`roles`), and a check-yourself question with its answer (`quiz`).
- **Name things as the code does.** Use `names` for fact nodes and additions for internals, so the diagram matches the reader's mental model.
- **The inspector** shows the access token that the flow uses, with who checks each claim.

## 8. Check

`$AT check trace.json` must pass before you render. It checks:

- a secret lint over the whole file;
- the schema, and references to ids that exist;
- that the facts are unchanged since `build`;
- that each narrative part claims no more confidence than its evidence;
- that every evidence path and line exists in the target;
- that the trace is git-ignored.

Fix problems in `narrative.json` and re-narrate. If a fact looks wrong, say so to the user and keep it. Never edit `trace.json` to pass.

## 9. Render

`$AT render trace.json` writes `flow.html` next to it. Open it in a browser once if one is available, and step through every tour. Look for clipped labels and long tour names.

## 10. Deliver

1. **The capture log:** the summary `run` printed, or the run log in the page. It's redacted.
2. **What's still uncertain:** the "still inferred or unknown" list from `check`. Fact roles stay `inferred` even when your narrative explains them from code: facts never change. For each item, say what would make it observed (run that server under capture, enable IdP events, allow a probe), and where the narrative already declares it from code.
3. **Publish, if the user agreed.** If an Artifact tool is available, publish `flow.html` as a private artifact. If the trace's redaction isn't `strict`, ask once more first: `dev` keeps identity claims (`sub`, email) readable. Otherwise, give the path to `flow.html`.
4. **The target is unchanged:** run `git status --short` in the target and show it, along with where the outputs are (`.authtrace/run-<time>/`, which is ignored).
