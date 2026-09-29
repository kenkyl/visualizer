# visualizer

A Claude Code plugin that draws architecture diagrams, and will keep them up to date. It reads a repository and writes a small JSON model of its components, data flows, trust boundaries and state. It renders that model as Mermaid that GitHub displays natively, in light and dark mode.

- `ARCHITECTURE.md`: overview diagram and legend, a component table with evidence links, where state lives, and sequence diagrams for the key flows.
- `README.md`: the overview, between `<!-- visualizer:start -->` and `<!-- visualizer:end -->` markers, plus a link to the full page.

Claude does the reasoning. [`scripts/viz.py`](scripts/viz.py), stdlib-only Python 3.9+, does the deterministic parts. It validates the model (ids, references, limits, evidence paths that must exist, a secret lint) and renders it the same way every time.

**Status:** early development. First runs work. Updating an existing diagram, with an archive of the old one and a changelog, comes next.

## Install

```bash
claude plugin marketplace add ~/Workspace/claude-code/visualizer
```

```bash
claude plugin install visualizer@visualizer
```

Or try it for one session without installing:

```bash
claude --plugin-dir ~/Workspace/claude-code/visualizer
```

## Use

In the repository you want to diagram:

```
/visualizer:architecture
/visualizer:architecture focus on the auth flow; output to docs/arch
```

It asks where to put the files and where the README block goes. It writes the files and offers to commit; nothing is committed without a yes.

## Example

[`skills/architecture/examples/arcade-test.architecture.json`](skills/architecture/examples/arcade-test.architecture.json) is the model for [arcade-test](https://github.com/kenkyl/arcade-test) at `cc1197d`. Its rendered output is in [`tests/fixtures/golden/arcade-test/`](tests/fixtures/golden/arcade-test/ARCHITECTURE.md).

## Develop

```bash
python3 -m unittest discover -s tests
```

`VIZ_UPDATE_GOLDEN=1` rewrites the golden files after an intended output change. `ARCADE_TEST_REPO=<checkout at cc1197d>` also checks the example's evidence against the real repo. `scripts/viz.py preview MODEL` writes a page that renders every diagram with a pinned Mermaid, for a visual check.

## License

[MIT](LICENSE)
