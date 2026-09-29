# Style guide: what the renderer emits, and why

`viz.py render` turns the model into Mermaid that GitHub renders natively, in light and dark mode. This file records the rules the renderer follows. Read it before changing `scripts/viz.py`, and when judging a render in the visual check.

## Contents

- [Mermaid subset](#mermaid-subset)
- [Ids, labels and escaping](#ids-labels-and-escaping)
- [Shapes and colours](#shapes-and-colours)
- [Edges and layout](#edges-and-layout)
- [Document layout](#document-layout)
- [Visual check](#visual-check)
- [Why it looks like this](#why-it-looks-like-this)

## Mermaid subset

**Diagram types:** only `flowchart` and `sequenceDiagram`.

**Never emitted:**

- `@{shape: ...}` node syntax or `fa:` icons: too new, or not bundled on GitHub.
- `click` or `linkStyle`: `click` is stripped on GitHub, and `linkStyle` addresses edges by index, which breaks silently when edges change.
- `%%{init}%%` directives or frontmatter themes: GitHub picks the light or dark theme itself, and an override breaks one of them.
- `architecture-beta`, `block-beta` or C4 diagram types.
- Markdown strings (labels in backticks): they behave differently across versions.

**Emitted:** `flowchart <dir>`, `subgraph b_x["..."] ... end`, the node shapes below, `-->`/`-.->`/`==>` and their longer forms, `|"label"|`, `classDef`, `class`, `style` on subgraphs and inferred nodes, and `%%` comments on their own line. Sequence diagrams use `autonumber`, `participant x as Name`, `->>`, `-->>` and `Note over`.

## Ids, labels and escaping

- Nodes are `c_<id>` and subgraphs are `b_<id>`, with hyphens turned into underscores (`agent-core` → `c_agent_core`). Model ids are kebab-case, so this never collides, and a prefix means an id can never be a keyword such as `end`, `graph` or `subgraph`.
- Every label is quoted. User text is escaped in one pass with Mermaid entity codes:

| Character | Flowchart | Sequence |
|---|---|---|
| `#` | `#35;` | `#35;` |
| `&` | `#amp;` | `#amp;` |
| `"` | `#quot;` | `#quot;` |
| `<` `>` | `#lt;` `#gt;` | `#lt;` `#gt;` |
| `` ` `` | `#96;` (stops a label turning into a Markdown string) | `#96;` |
| `;` | kept (safe inside quotes) | `#59;` (a statement separator) |

- The renderer's own markup is `<b>name</b><br/>tech` in nodes. Nothing else is raw HTML.
- Main-path edges start with `1.`, `2.`, ... Circled digits were tried and rendered too small to read.

## Shapes and colours

| Kind | Shape | Fill (translucent) | Stroke |
|---|---|---|---|
| entrypoint | `(["..."])` stadium | `#2da44e26` | `#2da44e` |
| client | `(["..."])` stadium | `#8957e526` | `#8957e5` |
| service | `["..."]` box | `#218bff26` | `#218bff` |
| module | `["..."]` box | `#1b998b26` | `#1b998b` |
| datastore | `[("...")]` cylinder | `#d4a72c33` | `#bf8700` |
| queue | `[["..."]]` subroutine | `#fb8f4426` | `#e16f24` |
| infra | `["..."]` box | `#e85aad26` | `#e85aad` |
| external | `["..."]` box, dashed `5 3` | `#8c959f1f` | `#8c959f` |

- **Fills are translucent tints and the text colour is never set.** The tint sits over whatever the page is, and the text uses the theme's own colour: dark on light, light on dark. Opaque pastels with forced dark text were tried first: readable, but they glare as bright boxes on GitHub's dark background.
- Strokes are mid-tone (Primer scale 4 or 5), so they show on both `#ffffff` and `#0d1117`. Stroke width is 1.5px.
- **Boundaries:** `fill:none`, dashed `#8b949e` stroke, and the title in the theme's colour.
- **Inferred components:** a `3 3` dashed stroke plus "(inferred)" in the name, so it doesn't depend on colour.
- `classDef` is emitted only for kinds in use, in a fixed kind order.
- Every diagram has a Markdown legend under it naming shapes and colours in words, so colour is never the only cue.

## Edges and layout

| Edge kind | Link | Meaning |
|---|---|---|
| sync | `-->` | call or request |
| async | `-.->` | message, event or job |
| deploy | `-.->` | build or release path (the label says which) |
| auth | `==>` | sign-in or credential flow |

- **Direction:** `TB` unless a view says otherwise. GitHub's content column is about 880px, and top-to-bottom layouts fit it far better than left-to-right ones.
- **Trust zones stack.** Mermaid places two boundaries side by side whenever their nodes share ranks, which gives wide diagrams shrunk to unreadable text. `edge_lengths()` assigns ranks first: longest path, plus the rule that an edge leaving a top-level boundary lands below that boundary's last rank. It then emits the edge as a longer link (`--->`, `-..->`, `===>`: a minimum rank span in Mermaid), capped at 4. Back edges, and boundaries that call each other in a cycle, keep length 1, so ranking always terminates. On arcade-test this cut the overview from 3100px wide to about 1180px.
- **Emit order** is the model's order: boundaries appear where their first component does, then edges, then `classDef`, `class` and `style` lines. The same model always gives the same bytes.

## Document layout

**ARCHITECTURE.md**, in order:

1. a "generated, don't edit" comment;
2. the title, summary and highlights;
3. each view's diagram, with the legend after the first;
4. the component table: component (with tech and members), kind, where it runs, responsibility, and evidence links relative to the file;
5. "Where state lives", one row per `holds` entry, with the components whose edges reach the store;
6. the flows as sequence diagrams;
7. Assumptions (only if something is inferred);
8. Notes (only if there are any), quoted verbatim;
9. a footer with the model's commit and a content hash of everything above it.

The hash lets `render` tell its own output from hand edits. It refuses to overwrite an edited or foreign `ARCHITECTURE.md` unless given `--force`.

**README block:** between `<!-- visualizer:start path=<model> hash=<hash> -->` and `<!-- visualizer:end -->`. It holds an `## Architecture` heading, the first view's diagram, and a plain link to ARCHITECTURE.md for places that don't render Mermaid, such as PyPI and npm. Only the lines between the markers are ever changed. Markers inside code fences are ignored. The file's line endings and BOM are kept. Mismatched markers or a hand-edited block stop the render.

## Visual check

`viz.py preview FILE` writes an HTML page that renders every diagram in a model, or in any Markdown file, with a pinned Mermaid, in GitHub's light (`default`) and dark themes. After rendering it sets `window.__viz = {results: [{title, theme, ok, error}], done: true}`. The pin is `MERMAID_VERSION` in `viz.py` (11.17.2). Output has also been checked on 10.9.8 and 12.0.0 with `--mermaid`. GitHub doesn't publish which version it runs, so test across versions when the renderer changes.

The Claude desktop browser pane only runs scripts in files inside the project folder, so write the page there (see SKILL.md). The page may be loaded as a `data:` URL, which drops `?theme=`. In that case switch themes with `document.body.className = "only-light"` or `"only-dark"`. To judge readability, limit the page to GitHub's width: `document.querySelectorAll("section").forEach(s => s.style.maxWidth = "880px")`.

What to look for: every result `ok`; overview text readable at 880px (natural width about 1300px or less); no boundary title hidden under an edge; the main path readable top to bottom.

## Why it looks like this

The renderer was A/B tested on arcade-test against Mermaid an LLM wrote directly (2026-09-29). The direct version was more readable at GitHub width, for three reasons: it ran top to bottom, it used short labels, and its translucent fills followed the theme. The renderer adopted all three, and added rank-based link lengths to stack trust zones, which the direct version got by accident. The direct version's `linkStyle` and bidirectional `<--->` links stay out: both are fragile.
