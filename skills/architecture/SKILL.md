---
name: architecture
description: Draw an architecture diagram of the current repository, showing its components, data flows, trust boundaries, credential flow and where state lives, as GitHub-native Mermaid in ARCHITECTURE.md with an overview embedded in the README. Use when the user asks to diagram, visualize, map or document a repo's architecture, wants an architecture diagram created or refreshed, asks how the pieces of a codebase fit together, or runs /visualizer:architecture. Optional free-text notes steer what to include, e.g. "focus on auth; output to docs/arch".
argument-hint: "[notes, e.g. focus on auth; output to docs/arch]"
---

# Architecture diagram

You work out the architecture; `viz.py` does the mechanical parts. Write `architecture.json`, a small model of the repo, and let the script validate it and render the Mermaid. Never write Mermaid by hand: the renderer keeps ids, styling and layout stable between runs, and only emits syntax GitHub renders.

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/viz.py check  MODEL
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/viz.py render MODEL [--readme-insert] [--readme-create]
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/viz.py preview MODEL --out PAGE.html
```

It needs Python 3.9 or later and nothing else. Read [references/model-schema.md](references/model-schema.md) before writing a model. [references/style-guide.md](references/style-guide.md) explains what the output looks like and why. [examples/arcade-test.architecture.json](examples/arcade-test.architecture.json) is a complete worked example.

Notes from the user, if any: `$ARGUMENTS`

## 1. Find the target and the mode

Run `git ls-files -co --exclude-standard '*architecture.json'` and keep the files whose `meta.generator` is `"visualizer"`. A README containing `<!-- visualizer:start path=... -->` also names its model.

- **No model: first run.** Continue below.
- **A model exists:** this version can re-render it but can't update it yet. Updating the diagram to match new code, with archiving and a changelog, comes in a later version. Say so, and offer to re-render as it stands (step 7). Don't edit the model: an update without archiving would lose the old version.

Treat the notes as free text. Pull out an output directory if one is given ("output to docs/arch"); everything else steers content. Save the steering notes in `meta.notes` as `{"text": <verbatim>, "added": <today>}`.

## 2. First run: ask what's needed

Ask all of these at once, with the defaults marked as recommended:

- **Output directory** for `architecture.json` and `ARCHITECTURE.md`. Default `docs/architecture/`.
- **README placement.** Show a short preview of where the `## Architecture` block would go, by quoting the headings around it. Default: before the first `##` heading after the intro (`after-intro`). Other options: `end`, `before:<heading>`, `after:<heading>`, or don't embed (`embed: false`).
- **Only if the repo has no README:** create one with just the title and the diagram, or skip the README.

## 3. Discover

Read enough to be sure, not everything:

- `git ls-files` for the layout, languages and size;
- the README and docs, which often name components and flows directly;
- manifests (`pyproject.toml`, `package.json`, `go.mod`, ...) for dependencies that point at outside services;
- entry points, meaning mains, CLIs and servers, and follow what they call;
- infra and deploy files: Dockerfiles, compose, Kubernetes, Terraform, CI workflows;
- where state is kept, meaning databases, caches, files and vaults, and how long it's kept.

**Never open** `.env`, `*.pem`, `*.key` or other credential files. `.env.example` is fine for variable names; never copy its values.

Above about 400 tracked files, or with more than 5 source roots, split discovery across parallel Explore subagents, one per area. Have each return components, edges and evidence paths.

## 4. Decide the level of detail

- **Match the repo.** A library or CLI gets no invented deployment view. A service shows its runtime and datastores. A tutorial shows its steps collapsed into one node, using `members`.
- **Match the audience.** `container` detail (runnable units, datastores, outside systems) suits architects and SAs, and is the default. `component` detail (modules inside a program) suits developers. Save the choice in `meta.detail`.
- **Always show:** outside systems, datastores, trust and deployment boundaries, how credentials flow, and the entry points.
- **Leave out:** utilities, tests, config files, and anything a reader doesn't need to follow the main path.
- **Notes win.** "Focus on auth" means the auth path gets full detail, even at the cost of other parts.

## 5. Write the model

Follow the schema, and keep these in mind:

- Every component cites evidence that exists: repo paths, or `doc:FILE#anchor` for things that live only in docs. If you can't point at evidence, mark the component `inferred`.
- Number one main path with `step`: the route a new reader should follow first.
- Keep labels short (edge labels 28 characters or fewer, tech 28 or fewer). Width is what makes GitHub shrink a diagram until it's unreadable.
- At most 3 flows, for the paths that matter most, such as a first sign-in or a request that touches state.
- `meta.model_sha` and `meta.reviewed_sha` are the output of `git rev-parse HEAD`, and `model_date` is that commit's date. Set `dirty` if `git status --porcelain` shows changes. Outside git, use null and false.

## 6. Check

Run `check` and fix every error, then rerun until it passes. Warnings are there for a reason: fix them unless you can say why not, for example a boundary that intentionally holds one component.

## 7. Render

First run: `render MODEL --readme-insert`, adding `--readme-create` if the user agreed to a new README. Re-render only: `render MODEL`. Exit codes:

- `0`: done. It prints what it created, updated or left unchanged.
- `1`: the model is invalid. Go back to step 6.
- `3`: stopped without writing, because a generated file was edited by hand or the README markers don't match. Show the user what's there and ask: fold the edits into the model, or overwrite with `--force`. For mismatched markers, the user must fix the README by hand. Never pass `--force` without asking.

If the README markers are missing on a re-render, ask once whether to add the block back. If not, set `meta.readme.embed` to false. Never add it back silently.

## 8. Visual check (if a browser is available)

1. Write the preview inside the project folder, because the browser pane only runs scripts there. Use `.git/visualizer-preview.html` if `.git` is a directory. Otherwise, as in a git worktree, use `<output dir>/.visualizer-preview.html`, and delete it afterwards.
2. Open it and wait for `window.__viz.done`. Every result must be `ok`. A failure means a renderer bug, not a model problem: report it.
3. Look at the overview at GitHub's width, in both themes; the style guide's visual-check section has the snippets. If the overview is wide or tangled, fix it in the model. Shorten labels, collapse siblings, drop edges that add width but little meaning, or reorder components. Then render again.

## 9. Report and offer to commit

Summarize the architecture in a few lines: the main path, where state lives, the trust boundaries, and anything marked inferred. Show `git status` and a short diff summary. Offer to commit the model, `ARCHITECTURE.md` and the README change together. **Never commit without a yes.**
