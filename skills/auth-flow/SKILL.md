---
name: auth-flow
description: (Preview) Trace a running system's identity and access traffic, meaning OAuth 2.x, OIDC and MCP authorization, and render it as an interactive step-through diagram that shows which tokens exist, where they travel, who checks which claim, and how sure each fact is. Use when the user runs /visualizer:auth-flow, or asks to trace, capture or visualize a login or token flow in a system they run locally. Optional notes pick the flow, e.g. "web login through logout" or "the live pytest run"; "doctor" only reports what this machine can capture.
argument-hint: "[notes, e.g. web login through logout | doctor]"
disable-model-invocation: true
---

# Auth flow trace

**Preview.** This version checks the machine, captures Python processes (M2), and builds and renders a facts-only trace (M3). The narrate, check and deliver steps (M4) aren't built yet; see [the plan](../../docs/authtrace/PLAN.md#15-milestones-each-one-is-also-a-lesson). The procedure below is the contract those milestones build toward. Don't improvise the missing steps by hand in the meantime.

You explain the flow; `authtrace.py` does the mechanical parts. The core records *facts* (exchanges, decoded tokens, findings). You write only the `narrative` section, and you never edit a fact.

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/authtrace.py doctor [--json]
```

It needs Python 3.9 or later. Browser, proxy and live capture also need `uv`, and run their dependencies through `uvx` on demand. `doctor` says what's missing.

Notes from the user, if any: `$ARGUMENTS`

## Status (this version)

1. Run `doctor` and show its output. If the notes are just `doctor`, that's the whole job.
2. If the notes name a flow to capture, read the target's README and test setup to find the command (for example `uv run pytest -m live`). Ask once before running it: it may call paid APIs, and services it needs that aren't running stay down until the user starts them.
3. Capture from the target's root:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/authtrace.py run -- <cmd>
   ```
   Use `--redaction strict` if the user will share the output. Never use `raw` unless they ask for it. For a browser flow, ask them to save a HAR (DevTools → Network → Save all as HAR), then run `authtrace.py import-har <file>`. Proxy and Playwright capture come in M5.
4. Show the summary it prints (one line per exchange). Point out processes that made no HTTP calls, such as servers that were already running and so weren't captured. Never read a capture made with `--redaction raw`: you work from redacted output only.
5. Build and render, using the capture folder the summary named:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/authtrace.py build .authtrace/run-<time> --target <name>
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/authtrace.py render .authtrace/run-<time>/trace.json
   ```
   `build` prints the nodes and their inferred roles (each with its reason), any traffic that must exist but wasn't captured, and the findings. Relay those, and give the path to `flow.html`: a one-step-per-exchange tour of the facts. Say that the narrated tour (what each step means, who checks what, from the code) comes in M4, and stop.

## Invariants

These hold for every version, including this one:

- **Never modify the target system.** Capture from outside: a process wrapper, a proxy, a HAR file, IdP admin APIs. `git status` in the target must be unchanged at the end.
- **Never persist raw token, secret or cookie values.** Fingerprints and masks only. Raw mode is a separate opt-in. See [references/security.md](references/security.md).
- **Never open** `.env`, `*.pem`, `*.key` or other credential files. Checking that a variable is set, by name, is fine.
- **Every fact carries a confidence level:** observed, decoded, verified, declared, inferred or unknown. Draw gaps; don't hide them. See [references/confidence.md](references/confidence.md).
- **Work from redacted traces only,** never raw captures.
- **Active probes are opt-in,** and only against hosts the user owns and lists.
- **Outputs go to `.authtrace/`** (gitignored) in the target, or outside the repo. Never write tracked files.
- **Stopping or restarting the user's services needs a yes,** every time.

## Full procedure (from M4)

[references/procedure.md](references/procedure.md) has the ten steps: discover, ask once, preflight, capture, build, investigate, narrate, check, render and deliver. In short, read the project's own context and the code on the request path, run the flow under capture, explain every anomaly from source (including installed libraries), write the narrative, validate, render, and publish a private artifact if the user agrees.

If the target has a visualizer `architecture.json`, reuse its component names and boundaries for the trace's nodes, so the two diagrams line up.
