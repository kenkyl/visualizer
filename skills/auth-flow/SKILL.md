---
name: auth-flow
description: (Preview) Trace a running system's identity and access traffic, meaning OAuth 2.x, OIDC and MCP authorization, and render it as an interactive step-through diagram that shows which tokens exist, where they travel, who checks which claim, and how sure each fact is. Use when the user runs /visualizer:auth-flow, or asks to trace, capture or visualize a login or token flow in a system they run locally. Optional notes pick the flow, e.g. "web login through logout" or "the live pytest run"; "doctor" only reports what this machine can capture.
argument-hint: "[notes, e.g. the live pytest run | reuse the last capture | doctor]"
disable-model-invocation: true
---

# Auth flow trace

**Preview.** Captures Python processes and browser HARs. Proxy and Playwright capture (M5) and live mode (M6) aren't built yet; see [the plan](../../docs/authtrace/PLAN.md#15-milestones-each-one-is-also-a-lesson).

You explain the flow; `authtrace.py` does the mechanical parts. The core records *facts*: exchanges, decoded tokens, nodes and roles by rule, findings. You write only the *narrative*: tours, steps, who checks what from the code, goals and questions. You never edit a fact.

Below, `$AT` stands for `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/authtrace.py`; write it out in full in each command, since shell variables don't carry between calls. It needs Python 3.9 or later. Notes from the user, if any: `$ARGUMENTS`

## Steps

Run every command from the target's root. `$OUT` is the capture folder `run` names (`.authtrace/run-<time>/`). When reusing a capture, it's a new folder under `.authtrace/` that you build into, so earlier outputs stay as they were. Each step is a requirement. [references/procedure.md](references/procedure.md) says what each one involves and why.

1. **Doctor.** `$AT doctor`. If the notes are just `doctor`, show its output and stop.
2. **Discover.** Read the project's context and the code on the request path. Pull out components, invariants and learning goals. Never open `.env` or key files.
3. **Ask once,** with defaults, in one question set:
   - the flow: a command, or reuse a capture already in `.authtrace/`;
   - whether to restart already-running servers under capture, so their side (token validation, key fetches) is observed rather than inferred (default no);
   - redaction: `dev`, or `strict` (default if they'll share it);
   - whether to publish a private artifact;
   - whether active probes are allowed (default no).
4. **Preflight.** Check that the dependencies are up. Restarting a service under capture needs a yes, every time.
5. **Capture.** `$AT run [--redaction strict] -- <cmd>`. Show the summary, including processes that made no calls. Reusing a capture skips this step.
6. **Build.** `$AT build <capture folder> --target <name> [-o $OUT/trace.json]`, then `$AT show $OUT/trace.json`, which prints the run's command, exit code and duration, then every id.
7. **Investigate.** Explain every non-2xx, every `inferred` or `unknown` part and every finding from source, with file:line. Keep notes. Don't write the narrative yet.
8. **Narrate.** Write `$OUT/narrative.json` as [references/narrative.md](references/narrative.md) describes, then `$AT narrate $OUT/trace.json $OUT/narrative.json`. It runs the same checks as `check`, except the git one, and writes nothing if they fail.
9. **Check.** `$AT check $OUT/trace.json`. Fix `narrative.json`, re-narrate, and repeat until it passes. Never edit `trace.json` to pass.
10. **Render.** `$AT render $OUT/trace.json` writes `flow.html` beside it.
11. **Deliver.** Show the capture log and the parts still `inferred` or `unknown`, each with what would confirm it. Publish if agreed. Show that the target's `git status` is unchanged.

## Invariants

- **Never modify the target system.** Capture from outside: a process wrapper, a proxy, a HAR file, IdP admin APIs. `git status` in the target must be unchanged at the end.
- **Never persist raw token, secret or cookie values.** Fingerprints and masks only. Raw mode is a separate opt-in. See [references/security.md](references/security.md).
- **Never open** `.env`, `*.pem`, `*.key` or other credential files. Checking that a variable is set, by name, is fine.
- **Every fact carries a confidence level:** observed, decoded, verified, declared, inferred or unknown. Draw gaps; don't hide them. See [references/confidence.md](references/confidence.md).
- **Work from redacted traces only,** never raw captures. Don't read a capture made with `--redaction raw`.
- **Active probes are opt-in,** and only against hosts the user owns and lists.
- **Outputs go to `.authtrace/`** (gitignored) in the target, or outside the repo. Never write tracked files.
- **Stopping or restarting the user's services needs a yes,** every time.

If the target has a visualizer `architecture.json`, reuse its component names and boundaries for the narrative's names and additions, so the two diagrams line up.
