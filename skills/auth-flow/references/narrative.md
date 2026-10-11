# The narrative you write

`build` writes the facts. You write one JSON object, the trace's `narrative` section, to `narrative.json` next to `trace.json`. Then `authtrace.py narrate trace.json narrative.json` puts it in the trace and lays the diagram out again. Re-running `narrate` replaces the previous narrative. Never edit `trace.json` by hand: `check` compares a hash of the facts and fails if any changed.

Get the ids from `authtrace.py show trace.json`, and the wire text for a step from `authtrace.py show trace.json x3`.

**Worked example:** `tests/fixtures/authtrace/memes-agent-live/narrative.json` in the plugin is the narrative of a real memes-agent capture, written by following this skill. It uses names, captions, additions inside fact nodes, three tours and an inspector. `tests/fixtures/authtrace/memes-agent/trace.json` is the older, hand-made M1 trace; its steps are good models, but its internals are facts rather than additions.

## Fields

| Key | Shape | What it's for |
|---|---|---|
| `header` | `{page_title, eyebrow, title, intro, chip, diagram_alt}` | Top of the page. `page_title` is the tab title: two to four words. `intro` says what each tour shows and when the capture ran. `chip` is the run's result. Take it from what was observed: `show` prints the exit code and duration ("4 tests · exit 0 · 11.7 s"), and the command's own "4 passed" line isn't captured. |
| `goals` | `{"1": [name, description], …}` | The target's learning goals, or the defaults (procedure step 1). The keys are numbers as strings. |
| `flows` | `[{id, name, short, steps, dim_nodes, dim_edges, banner}]` | Tours. `name` labels the tour's button: keep it to three or four words, since three long names wrap. `short` heads each card. `steps` lists step ids in order. `dim_*` greys out the parts a tour doesn't use. `banner` is required on a reconstructed tour: say that it wasn't captured, and what it was rebuilt from. |
| `steps` | see below | One card per step. |
| `names` | `{node id: display name}` | Gives a fact node a real name ("Keycloak" for `localhost-8080`). Claim a name only if config or code shows it. |
| `captions` | `{nodes: {id: [lines]}, edges: {id: [lines]}}` | One or two short lines under a node or an edge label, such as "authorization server · realm memes". |
| `nodes` | `[{id, name, kind, parent, boundary, holds_tokens, evidence, why, confidence}]` | Parts the capture can't see: modules inside a process, middleware inside a server, endpoints, a browser, a datastore. See [Additions](#additions). |
| `edges` | `[{id, from, to, label, channel, dashed, evidence, why, confidence}]` | Hops the capture can't see, such as an in-process call or a front-channel redirect. |
| `boundaries` | `[{id, name, token_zone, parent}]` | Extra containers, such as "Agent · same process" inside "Your machine". |
| `inspector` | `{token, eyebrow, title, intro, claims: [{claim, checked_by, why}]}` | One token's claims table. Use `header.kid` for header fields. The values come from the token. You write who checks each claim and why it matters. |
| `logs_note` | string | One line under the run logs, such as how the capture relates to the run. |

### A step

```json
{
  "id": "live-3",
  "title": "Open an MCP session: the client tries the 2026 protocol",
  "time": "+1.863 s",
  "exchanges": ["x2"],
  "tokens": ["t1"],
  "nodes": ["pytest", "gateway", "127-0-0-1-8001", "mw", "sess"],
  "segs": [["pytest--127-0-0-1-8001", 1, "discover + Bearer", "tok"], ["pytest--127-0-0-1-8001", -1, "400", "err"]],
  "what": "Markdown subset. What happened and why, from the code.",
  "wire": "Literal text, copied from `show trace.json x2`.",
  "checks": [["Middleware", "token valid → pass through"], ["Session manager", "protocol unknown → 400"]],
  "inv": "The invariant this step demonstrates, if any.",
  "note": "An aside: a misreading to avoid, or how this connects to a log line.",
  "roles": "Which OAuth role each party plays here.",
  "goals": [3],
  "code": ["src/memes_agent/tools.py:38", ".venv/lib/python3.13/site-packages/arcade_mcp_server/transports/http_session_manager.py:212"],
  "quiz": ["A check-yourself question", "Its answer"]
}
```

- `segs` animate packets: `[edge id, 1 (along the edge) or -1 (back), label, "tok" | "dat" | "err"]`. Use `tok` when an access token rides the hop. The edge can be a fact edge or one you added.
- `nodes` lights up boxes. Include the children involved (`mw`), not just their parent.
- `exchanges`, `tokens` and `code` are the step's evidence. Its confidence tag is **derived** from them: the weakest cited exchange or token, lifted to `declared` by code if it was weaker. A step that cites nothing is `inferred`. Never write a `confidence` on a step; `check` rejects it. The order, strongest first, is verified, observed, decoded, declared, inferred, unknown. So a step citing a decoded token and code shows `decoded`, because what a token claims outranks what code says should happen.
- Only `title` is required. Leave out what you have nothing true to say about.

### Text rules

- **Prose** (`what`, `inv`, `note`, `roles`, `intro`, `banner`, claims, quiz) is a Markdown subset: `` `code` ``, `**bold**`, `*italic*`, and `[text](#anchor)` links within the page. The anchors are `#inspector`, `#findings`, `#goalmap`, `#logs`, and a step as `#<flow id>-<step number>` (`#live-4`). The viewer escapes everything else. Never write HTML.
- **`wire`** is literal text. Copy it from `show`, which is already redacted. Shorten it if you like, but never fill in a value the trace doesn't hold.
- **No values the trace doesn't have.** Not from logs, not from `.env`, not from memory. `check` lints the whole file, the narrative included.
- **Names from code are fine; people aren't.** Config names, client ids, URLs, scopes and class names belong in the narrative. Usernames, emails and real names don't, even a dev user's from a test file: `strict` hashed them in the trace for a reason. Write "the dev test user".

## Additions

The capture sees processes and origins. The reference diagram also shows what's inside them: the gateway class that adds the Bearer header, the middleware that checks `aud`, the `/token` endpoint. You add those as narrative `nodes`, `edges` and `boundaries`. They're drawn with the facts, and they never change a fact.

- **Ids must be new.** To label a fact node, use `names` and `captions`, not an addition with the same id.
- **`parent`** puts a node inside another, such as an endpoint inside the authorization server's node. The layout stacks children inside the parent's box.
- **Required:** `id`, `name`, `kind` and `confidence` on a node; `id`, `from`, `to`, `label` and `confidence` on an edge.
- **`kind`**: `user-agent`, `client`, `agent-module`, `authorization-server`, `resource-server`, `llm-api`, `datastore`, `gateway`, `component` (a part inside a service), `endpoint` (one URL, drawn as a single line) or `unknown`. The kind picks the column of a top-level node. A child sits inside its parent whatever its kind, but `endpoint` draws it as one line, so give an endpoint a short name (`/token`, not the full path).
- **`channel`** on an edge: `front` (through the browser), `back` (server to server) or `in-process` (a call inside one process).
- **`holds_tokens: false`** on a node outside the token zone draws it dashed, with a "no tokens" badge, as with a browser or a model API. Children inherit their parent's zone.
- **Labels and captions are short.** Captions fit about 30 characters a line. An edge between two children of the same parent is drawn down the parent's edge, with its label outside the box, so give it one or two words ("calls", "asks").
- **Confidence never exceeds the evidence**, and `check` enforces it:
  - `declared` needs `evidence`: file paths, with `:line` or `:a-b`, relative to the target's root.
  - `observed` needs `exchanges` that are themselves observed. It's rare for an addition.
  - `inferred` and `unknown` need a `why`: the reasoning, and what would confirm it.
- **Evidence paths** are relative to the target repo, and `check` verifies that each file and line exists. Cite installed libraries through the target's environment (`.venv/lib/python3.13/site-packages/…`). Never cite `.env` or key files.
- **Don't duplicate a fact edge.** If the capture has `pytest → 127.0.0.1:8001`, animate that edge. Don't add `gateway → mw` alongside it. Add edges only for hops the capture can't see.
- **Describe a fact edge; don't redraw it.** `build`'s dashed `inferred` edges (an RS's JWKS fetch that wasn't captured) stay as they are. Caption them ("JWKS · cached 1 h"), and let the step that explains them cite the code. That step is then `declared`, and the edge stays honestly unobserved.
