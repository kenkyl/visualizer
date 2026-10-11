# visualizer

A Claude Code plugin with two families of skills, both built as "Claude reasons, a stdlib script does the deterministic parts":

- **Architecture** (`/visualizer:architecture`): a JSON model of a repo rendered as GitHub-native Mermaid. Script: `scripts/viz.py`.
- **Auth flows** (`/visualizer:auth-flow`, `/visualizer:explain-token`, preview): traces a running system's OAuth, OIDC and MCP-auth traffic and renders an interactive step-through. Package: `lib/authtrace/`, launched by `scripts/authtrace.py`. Plan: `docs/authtrace/PLAN.md`. Also a learning project.

Decisions are in `docs/decisions.md`.

## Commands
- `python3 -m unittest discover -s tests`
- `python3 scripts/authtrace.py doctor`
- `uv run --no-project --with httpx --with httpx2 python -m unittest discover -s tests` also runs the httpx capture tests.
- `claude --plugin-dir .` loads the plugin for one session.

## Invariants
- `scripts/viz.py` and `lib/authtrace/` are stdlib-only, Python 3.9+. Optional capture modes (browser, proxy, live) run their dependencies through `uvx --with …` at the call site; nothing in `lib/` imports them.
- `viz.py` never imports authtrace, and the architecture skill never needs more than `python3`.
- Don't import `authtrace` with `scripts/` ahead of `lib/` on `sys.path`: `scripts/authtrace.py` is a launcher and would shadow the package.
- authtrace never modifies the target system. Capture from outside (wrapper, proxy, HAR, IdP APIs).
- Never persist raw token, secret or cookie values. Fingerprints and masks only, unless raw mode is explicitly on.
- Every authtrace fact carries a confidence level: observed, decoded, verified, declared, inferred, unknown.
- The core produces facts. Claude writes only the trace's `narrative` section and never edits facts.
- Claude reads redacted traces, never raw captures.
- Active probes are opt-in and only against allowlisted hosts the user owns.
- authtrace outputs go to `.authtrace/` (gitignored) in the target, or outside it. The architecture skill writes tracked docs, and only after asking.
- Nothing in `docs/authtrace/` or `tests/fixtures/authtrace/` may contain a raw JWT, secret or one-time claim value; `tests/test_reference_lint.py` enforces it.

## Conventions
- unittest, no third-party test dependencies. Mark add-on points with `# EXTEND:` comments.
- Golden fixtures: `VIZ_UPDATE_GOLDEN=1` (and `AUTHTRACE_UPDATE_GOLDEN=1` for authtrace) rewrites them after intended changes.
- Record decisions in `docs/decisions.md`. Keep `CHANGELOG.md` current.

## Pairing (authtrace)
Claude scaffolds the plumbing. The user writes the core pieces (decoders, rules, the skill's investigation step) during lessons. Explain the why, and keep modules small enough to read in one sitting.
