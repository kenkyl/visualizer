# Changelog

## 0.2.0 (unreleased)

- authtrace joins the plugin (scaffold): `/visualizer:auth-flow` and `/visualizer:explain-token` skills, in preview and run only when typed.
- `lib/authtrace/`, a stdlib-only package launched by `scripts/authtrace.py`. `doctor` reports which capture modes this machine supports; the other commands are stubs that name their milestone.
- `docs/authtrace/`: the authtrace plan and the 2026-10-03 memes-agent reference captures, with the dev passwords in the reference scripts moved to environment variables.
- `docs/decisions.md` and `CLAUDE.md`.
- Tests: the authtrace CLI and its stdlib-only rule, and a lint that keeps raw JWTs, secrets and one-time claim values out of `docs/authtrace/`.
- CI: unittest on Python 3.9 and 3.13.

## 0.1.0

- Plugin skeleton: manifests, a single-plugin marketplace, the `/visualizer:architecture` skill and `scripts/viz.py`.
- Model schema v1 (`references/model-schema.md`): meta, system, boundaries, components, edges, flows and views.
- `viz.py check`: schema, ids, references, size limits, evidence paths and doc anchors that must exist, and a secret lint (including sample values from `.env.example`).
- `viz.py render`: `ARCHITECTURE.md` (diagram, legend, component table, where state lives, flows, assumptions, notes, and a hash footer), plus a README block between markers. Hand edits and mismatched markers stop the render.
- `viz.py preview`: an HTML page that renders every diagram in light and dark with a pinned Mermaid (11.17.2).
- Style guide (`references/style-guide.md`): the GitHub-safe Mermaid subset, translucent theme-neutral colours, and rank-based link lengths that stack trust zones so diagrams fit GitHub's column.
- Worked example: arcade-test at `cc1197d`, which doubles as the golden test.
