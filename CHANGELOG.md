# Changelog

## Unreleased

- Plugin skeleton: manifests, a single-plugin marketplace, the `/visualizer:architecture` skill and `scripts/viz.py`.
- Model schema v1 (`references/model-schema.md`): meta, system, boundaries, components, edges, flows and views.
- `viz.py check`: schema, ids, references, size limits, evidence paths and doc anchors that must exist, and a secret lint (including sample values from `.env.example`).
- `viz.py render`: `ARCHITECTURE.md` (diagram, legend, component table, where state lives, flows, assumptions, notes, and a hash footer), plus a README block between markers. Hand edits and mismatched markers stop the render.
- `viz.py preview`: an HTML page that renders every diagram in light and dark with a pinned Mermaid (11.17.2).
- Style guide (`references/style-guide.md`): the GitHub-safe Mermaid subset, translucent theme-neutral colours, and rank-based link lengths that stack trust zones so diagrams fit GitHub's column.
- Worked example: arcade-test at `cc1197d`, which doubles as the golden test.
