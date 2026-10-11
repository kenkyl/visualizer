# Model schema: `data-models.json` (schema_version 1)

`merge` creates this file and writes the facts. You write the annotations. `check` validates both, and `render` turns them into tables. Every rule below is enforced by `check` unless it says "guidance".

A full worked example is [`../examples/shop.data-models.json`](../examples/shop.data-models.json).

## Contents

- [Who writes what](#who-writes-what)
- [Top level](#top-level)
- [meta](#meta)
- [system](#system)
- [stores](#stores)
- [entities](#entities)
- [relationships](#relationships)
- [Drift](#drift)
- [Checks that read the repo](#checks-that-read-the-repo)

## Who writes what

| Written by | Fields |
|---|---|
| `merge` (facts) | an entity's `observed` and `observed_hash`; a store's `snapshot`; new entities' `id`, `store`, `name` and `kind`; `fk` relationships |
| You (annotations) | everything else: `system`, store `name`, `role`, `evidence`, `connection`, `scope`; entity `description`, `field_descriptions`, `declared`, `evidence`, `inferred`; `reference` and `inferred` relationships; `meta` |

Unknown fields are errors, so a typo like `"descripton"` fails loudly instead of vanishing. **Never edit `observed`.** `check` recomputes `observed_hash`, and a mismatch is an error: re-run `introspect` and `merge` instead.

## Top level

```json
{"meta": {}, "system": {}, "stores": [], "entities": [], "relationships": []}
```

The renderer keeps model order. `merge` adds new entities after their store's last entity, sorted by name, and never reorders existing ones. So once you've annotated, **group each store's entities the way a reader would**: identity first, then the core domain, then content, then logs. The README's "Main entities" column takes the first four, so put the most important entities first.

## meta

| Field | Type | Rules |
|---|---|---|
| `generator` | string | Always `"visualizer"`. |
| `model` | string | Always `"data-models"`. It's how the skill finds its own files. |
| `schema_version` | integer | Always `1`. |
| `model_sha` | string or null | Full commit SHA the annotations were written against. Shown in the footer. |
| `model_date` | string or null | `YYYY-MM-DD` of that commit. |
| `dirty` | boolean | True if there were uncommitted changes. |
| `detail` | string | `full` (the default: a field table per entity) or `summary` (the entity index only). |
| `readme` | object | `{"embed": bool, "path": "README.md", "placement": "..."}`, with the same placements as architecture: `after-intro`, `end`, `before:<heading>` or `after:<heading>`. A block never lands inside another visualizer block. |
| `notes` | list | `[{"text": "...", "added": "YYYY-MM-DD"}]`. The user's steering notes, kept verbatim. |

## system

| Field | Rules |
|---|---|
| `name` | Usually the repo name. |
| `summary` | Required. What data the system keeps, and where, in at most 5 sentences (`check` warns above 5). |
| `highlights` | At most 5 one-line facts: the system of record, what expires, which links aren't enforced, and where personal data lives. |

## stores

| Field | Rules |
|---|---|
| `id` | kebab-case, unique, permanent. It's the `--store` you pass to `introspect`. |
| `name` | Display name, like "Main database" or "Session cache". |
| `engine` | `postgres`, `sqlite`, `mongodb`, `redis`, or `code` for models that exist only in code or docs (payloads, DTOs, JSON Schemas). |
| `role` | Required, one line, at most 160 characters: what the store is for. |
| `durability` | Optional, one line, at most 120 characters: what losing it costs, e.g. "Durable: Docker volume, migrated with Alembic" or "Disposable: rebuilt from Postgres". The Stores table gains a Durability column when any store sets it. |
| `evidence` | Optional repo paths or `doc:FILE#anchor`: where the store is configured or described. |
| `connection` | Optional: `{"env": "DATABASE_URL", "env_file": ".env"}` or `{"path": "data/app.db"}` for SQLite. **Variable names only**, never a URL. A `code` store has none. |
| `scope` | Optional: `{"schemas": [], "include": [], "exclude": [], "sample": N}`. These are the same values you pass to `introspect`, saved so a refresh repeats them. Globs match an entity's full name or its name without the schema. |
| `snapshot` | Written by `merge`: `{"taken", "server_version", "sampled"}`. |

## entities

| Field | Rules |
|---|---|
| `id` | kebab-case, unique, at most 40 characters. `merge` derives it from the name (`public.orders` → `orders`, `session:{id}` → `session-id`) and never changes it. |
| `store` | A store id. |
| `name` | The real name: `public.orders`, `orders`, `session:{id}`, `idx:products`, `OrderPlaced`. |
| `kind` | `table`, `view`, `collection`, `key-pattern`, `search-index` or `model` (a code-only type). |
| `description` | One line, at most 300 characters: what one row, document or key represents. Without one, a Postgres `COMMENT` is shown, and `check` warns once, listing every entity that has neither. |
| `field_descriptions` | `{"field": "one line"}`, for fields whose meaning isn't obvious: units, states, what null means. Names must match an observed or declared field (`check` warns otherwise). Nested Mongo paths work: `"author.verified"`. |
| `declared` | `{"evidence": [...], "fields": [{"name", "type"}], "type"?, "ttl"?}`, required for code-only entities. `evidence` cites the code or docs, and is required. List every field the code declares, by its stored name. With `fields` omitted, the entity is declared but its fields aren't compared. For a Redis key pattern, `type` (hash, json, string, ...) and `ttl` (as configured, e.g. "8h") are what the code sets. They're shown, marked "(declared)", until a snapshot observes the key. After that, a type that differs from the observed one is shown next to it. |
| `evidence` | Optional extra repo paths. |
| `inferred` | True if neither a store nor the code confirms the entity. It's listed under Assumptions. |
| `observed`, `observed_hash` | Written by `merge`. |

### observed (for reference; never edit it)

| Field | Meaning |
|---|---|
| `fields` | `[{"name", "type", "nullable"?, "default"?, "key"?, "presence"?, "comment"?}]`. `key` is `pk` or `unique`. `presence` (sampled stores) is the share of sampled documents or keys that had the field. Mongo paths are `a.b` for nested fields and `a[]` for array elements. |
| `indexes` | `[{"name", "fields", "unique", "method"?}]`. Primary keys, and the indexes behind one-column UNIQUE constraints, are left out because the Key column already shows them. |
| `checks` | Postgres CHECK constraints: `[{"name", "definition"}]`, the definition as Postgres prints it. |
| `estimate` | Planner row estimate (Postgres), estimated document count (MongoDB), or keys extrapolated from the scan (Redis). SQLite reports none. |
| `type`, `ttl`, `sample` | Redis key patterns: the value type, a TTL summary ("none", "up to 30m", "up to 2h on 40% of keys"), and how many keys were sampled. MongoDB sets `sample`. |
| `details` | Extra facts, such as a Mongo `$jsonSchema` validator, or a Search index's `on` and `prefixes`. |
| `truncated` | True if the sampled documents had more paths than the 200 kept. |

## relationships

| Field | Rules |
|---|---|
| `from`, `to` | `"<entity id>.<field>"`. Entity ids have no dots, so everything after the first dot is the field: `reviews.author.id` is field `author.id` of `reviews`. A key pattern's placeholders count as fields: `cart-id.{id}`. An `embedded` relationship's `to` is a whole entity: `"invoice-payload"`. |
| `kind` | `fk` (from the catalog; written by `merge`), `reference` (the code proves the link: cite `evidence`), `inferred` (naming convention only; listed under Assumptions), or `embedded`. |
| `on_delete` | `fk` only, written by `merge`: `cascade`, `set null`, `set default` or `restrict`. Absent means no action. |
| `note`, `evidence` | Optional. |

**`embedded`** says a column, field or document holds a shape defined elsewhere, usually a code model. A JSONB column validated by a Pydantic model is `courses.config` → `course-config`, and a Mongo subdocument with its own schema works the same way. The column's Key cell shows "shape → CourseConfig", so readers can jump from a `jsonb` type to what's inside it. Model the shape as a `model` entity in a `code` store, with its fields declared.

Cross-store links are the point of `reference` and `inferred`: a Redis session's `user_id` → `users.id`, or a Mongo event's `order_id` → `orders.id`. On a refresh, `merge` replaces only this store's `fk` relationships, keeping unchanged ones in place, and drops any relationship whose entity is gone. It reports each foreign key added or removed, each changed delete action, and each relationship it drops.

## Drift

`check` prints, and `render` lists under "Drift", each mismatch between an entity's `declared` fields and its `observed` ones:

- **declared in code, not in the store**, e.g. a migration that never ran (for MongoDB and Redis: not seen in the sample);
- **in the store, not declared in code**, e.g. a column added by hand. Nested Mongo paths are skipped;
- **declared in code, not found in the store**, for an entity the last snapshot didn't contain.

Names are compared case-insensitively. Types aren't compared in v1.

## Checks that read the repo

`check` (without `--no-files`) also verifies, against the repo root:

- every evidence path exists, and every `doc:FILE#anchor` matches a heading in that file;
- evidence is tracked by git (a warning otherwise);
- no evidence points at a secrets file (`.env`, `*.pem`, `*.key`, ...);
- no string contains a secret, a connection URL (`postgres://`, `redis://`, `mongodb+srv://`, ...), or the sample value of a secret-named variable from a tracked `.env.example`.
