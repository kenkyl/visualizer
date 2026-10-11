---
name: auth-flow
description: (Preview) Trace a running system's identity and access traffic, meaning OAuth 2.x, OIDC and MCP authorization, and render it as an interactive step-through diagram that shows which tokens exist, where they travel, who checks which claim, and how sure each fact is. Use when the user runs /visualizer:auth-flow, or asks to trace, capture or visualize a login or token flow in a system they run locally. Optional notes pick the flow, e.g. "web login through logout" or "the live pytest run"; "doctor" only reports what this machine can capture.
argument-hint: "[notes, e.g. web login through logout | doctor]"
disable-model-invocation: true
---

# Auth flow trace

**Preview.** This version only checks the machine. Capture, build, check and render land in milestones M1 to M4 of [the plan](../../docs/authtrace/PLAN.md#15-milestones-each-one-is-also-a-lesson). The procedure below is the contract those milestones build toward. Don't improvise a capture by hand in the meantime.

You explain the flow; `authtrace.py` does the mechanical parts. The core records *facts* (exchanges, decoded tokens, findings). You write only the `narrative` section, and you never edit a fact.

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/authtrace.py doctor [--json]
```

It needs Python 3.9 or later. Browser, proxy and live capture also need `uv`, and run their dependencies through `uvx` on demand. `doctor` says what's missing.

Notes from the user, if any: `$ARGUMENTS`

## Status (this version)

1. Run `doctor` and show its output.
2. Say that capture arrives in M2 (Python apps) and M5 (browser and proxy), and which of those this machine is ready for.
3. Stop. If the notes are just `doctor`, step 1 is the whole job.

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
