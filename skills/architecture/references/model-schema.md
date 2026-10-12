# Model schema: `architecture.json` (schema_version 1)

You write this file. `viz.py` validates it (`check`) and renders it (`render`); you never write Mermaid yourself. Every rule below is enforced by `check` unless it says "guidance".

A full worked example is [`../examples/arcade-test.architecture.json`](../examples/arcade-test.architecture.json).

## Contents

- [Top level](#top-level)
- [meta](#meta)
- [system](#system)
- [boundaries](#boundaries)
- [components](#components)
- [edges](#edges)
- [flows](#flows)
- [views](#views)
- [Checks that read the repo](#checks-that-read-the-repo)
- [Writing good entries](#writing-good-entries)

## Top level

```json
{"meta": {}, "system": {}, "boundaries": [], "components": [], "edges": [], "flows": [], "views": []}
```

`meta`, `system` and `components` are required; the rest default to empty. Unknown fields are errors, so a typo like `"respons"` fails loudly instead of vanishing.

**Order matters.** The renderer emits boundaries and components in model order, and each boundary appears where its first component does. Put entry points and the people or clients who use the system first, then what they call, then outside services. When updating, keep the existing order and add new entries next to related ones; `viz.py update` reports a reorder in the changelog.

## meta

| Field | Type | Rules |
|---|---|---|
| `generator` | string | Always `"visualizer"`. It's how the skill finds its own models. |
| `schema_version` | integer | Always `1`. |
| `model_sha` | string or null | Full commit SHA the diagram content was last written against. Shown in the footer. |
| `model_date` | string or null | `YYYY-MM-DD` of that commit. |
| `reviewed_sha` | string or null | Last commit a run confirmed the model against. Not rendered, so bumping it alone never changes the output. |
| `dirty` | boolean | True if there were uncommitted changes when the model was written. |
| `scope` | string | Subtree the model covers, `"."` for the whole repo. Evidence paths are still relative to the repo root. |
| `detail` | string | `container` or `component`. Saved so later runs keep the same level of detail. |
| `readme` | object | `{"embed": bool, "path": "README.md", "placement": "..."}`. See below. |
| `notes` | list | `[{"text": "...", "added": "YYYY-MM-DD"}]`. The user's steering notes, kept verbatim. |

`readme.placement` says where to insert the block the first time (after that, the markers decide): `after-intro` (before the first `##` heading, the default), `end`, `before:<heading text>` or `after:<heading text>` (after that whole section). Heading text matches case-insensitively and must be unique.

## system

| Field | Rules |
|---|---|
| `name` | The system's name, usually the repo name. |
| `summary` | What the system does and how, in at most 5 sentences (guidance; `check` warns above 5). |
| `highlights` | At most 5 one-line facts an architect would want flagged: trust, credentials, data residency, single points of failure. Concrete, never marketing. Example: "OAuth tokens never leave Arcade: neither this code nor Claude ever sees one." |

## boundaries

Trust or deployment zones: who runs it, and where. At most 6.

| Field | Rules |
|---|---|
| `id` | kebab-case, unique among boundaries. |
| `name` | Short, like "Your machine", "AWS us-east-1", "GitHub". |
| `kind` | `local` (a developer's machine), `container` (Docker, a pod), `cloud` (infrastructure you run in a cloud account), `saas` (a service someone else runs). |
| `parent` | Optional id of the enclosing boundary, e.g. Docker inside "Your machine". No cycles. |

A boundary with one component is fine when the trust line matters, such as a third-party LLM.

## components

| Field | Rules |
|---|---|
| `id` | kebab-case (`a-z`, `0-9`, single hyphens), starting with a letter, at most 40 characters, unique. **Never rename an id once written**: `update` matches components, boundaries, flows and views by id (edges by from, to and kind), so a stable id is what makes the changelog say "changed" instead of "removed and added". |
| `name` | Display name, at most about 32 characters (guidance). |
| `kind` | See the table below. |
| `boundary` | Optional boundary id. |
| `responsibility` | One sentence, at most 120 characters: what it does, not how it's built. |
| `tech` | Optional, at most about 28 characters (guidance): the one detail a reader needs, like "Postgres 16" or "localhost:6379 only". It's the second line of the node. |
| `evidence` | Required unless `inferred`. Repo paths (`src/api/main.py`, a directory as `src/api/`) or doc anchors (`doc:README.md#deployment`). |
| `members` | Optional names of collapsed siblings, e.g. the six step scripts behind one "Step scripts 01–08" node. |
| `holds` | Datastores and queues only: `[{"data": "...", "lifetime": "..."}]`. Who reads and writes it comes from the edges, so don't repeat that here. If an outside service holds state that matters (a token vault, an S3 bucket), model the store as its own `datastore` inside that service's boundary. |
| `inferred` | True if the code doesn't confirm it. Drawn dashed, named "(inferred)", and listed under Assumptions. Say why in `responsibility` or `evidence`. |

| Kind | Use for | Drawn as |
|---|---|---|
| `entrypoint` | Something you run: a CLI, a script, a `main` | green rounded box |
| `client` | A person, browser or app that uses the system | purple rounded box |
| `service` | A long-running process you deploy | blue box |
| `module` | A part of a program worth showing on its own | teal box |
| `datastore` | Anywhere state lives: databases, caches, vaults, buckets | yellow cylinder |
| `queue` | Queues, streams, topics | orange box with side bars |
| `infra` | Infrastructure you operate: a load balancer, a gateway you run | pink box |
| `external` | A system someone else runs | grey dashed box |

## edges

| Field | Rules |
|---|---|
| `from`, `to` | Component ids; different components. Point from the caller to the callee. |
| `label` | Verb plus data or protocol, at most 40 characters (hard) and ideally 28 or fewer (`check` warns). Examples: "execute as user_id", "reads and writes rows", "MCP over HTTP". |
| `kind` | `sync` (call or request), `async` (message, event, job), `auth` (sign-in or credential flow), `deploy` (build or release path). |
| `step` | Optional position on the main path: 1, 2, 3, ... with no gaps or repeats. Number one path only, the one a new reader should follow first. |

One edge per pair and kind; merge labels instead of drawing two. Leave out edges that make the overview wide without adding meaning, like a person to a far-away system when a flow already shows it.

## flows

At most 3 sequence diagrams for the paths that matter most, such as a first sign-in or a request that touches state.

| Field | Rules |
|---|---|
| `id`, `name` | kebab-case id; the name is the section heading. |
| `description` | Optional; one or two sentences on when this happens. |
| `steps` | 1 to 12 of `{"from", "to", "msg", "kind"}`. `kind` is `call` (solid arrow), `return` (dashed arrow) or `note` (a note over `from`, or over `from` and `to`). `msg` ideally 60 characters or fewer. |

Steps are numbered automatically. Participants appear in the order they first take part.

## views

Optional. With no views there is one overview of every component. A view is `{"id", "title", "include": [component ids], "direction": "TB"}`. `include` omitted means everything; `direction` is `TB` (default), `LR`, `RL` or `BT`. The first view is the one embedded in the README. Use extra views only when one diagram can't stay under the limits.

**Limits per view:** `check` warns above 15 nodes or 25 edges, and fails above 25 nodes or 40 edges. Collapse siblings with `members`, or split into views.

## Updates, archives and the changelog

`viz.py update MODEL NEXT --summary "..."` replaces a model with a candidate and keeps the history next to it, in the output directory:

| Path | Holds |
|---|---|
| `architecture.changelog.md` | One entry per update that changed the diagram, newest first: date, `old → new` commit, the summary, and what was added, removed or changed. |
| `archive/<model_date>-<sha7>/` | The version each update replaced: its `architecture.json`, and its `ARCHITECTURE.md` rendered with links that work from there. `undated` and `nosha` stand in when `meta` has no date or SHA, and `-2`, `-3` ... mark repeats. |

An update compares content only: `meta.model_sha`, `model_date`, `reviewed_sha` and `dirty` never count, so bumping `reviewed_sha` alone writes the model and creates no archive or entry. To go back to an archived version, copy its `architecture.json` over the model and render.

## Checks that read the repo

`check` (without `--no-files`) also verifies, against the repo root:

- every evidence path exists, and `doc:FILE#anchor` matches a heading's GitHub anchor in that Markdown file;
- evidence is tracked by git (a warning otherwise, since the link won't work for anyone else);
- no evidence points at a secrets file (`.env`, `*.pem`, `*.key`, `id_rsa`, ...);
- no string contains a secret or key placeholder (`sk-ant-...`, `ghp_...`, `arc_...`, AWS key ids, credentials inside URLs), or the sample value of a secret-named variable from a tracked `.env.example`.

## Writing good entries

- Name things the way the repo's own docs do.
- Say what a component does for the system, not what file it's in; the evidence column covers that.
- Prefer one clear node over three thin ones. A helper module earns a node only if a reader needs it to follow the main path.
- Put people and clients inside the boundary they act from.
- Mark anything you guessed `inferred`. A diagram that's honest about its gaps is more useful than a confident wrong one.
