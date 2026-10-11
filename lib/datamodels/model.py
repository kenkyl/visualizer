"""data-models.json: the schema, validation, and the hash that keeps observed facts script-owned.

Claude writes the annotations (descriptions, declared fields, relationships it infers). The script
writes `observed` (what a source read from the store) and its `observed_hash`; `check` fails if the
two disagree, so facts are never edited by hand. See skills/data-models/references/model-schema.md.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ._viz import viz

GENERATOR = "visualizer"
MODEL = "data-models"
SCHEMA_VERSION = 1

# EXTEND: a new source adds its engine here and a module in sources/.
ENGINES = ("postgres", "sqlite", "mongodb", "redis", "code")
SAMPLED_ENGINES = ("mongodb", "redis")  # their observations come from samples, not a catalog
KINDS = ("table", "view", "collection", "key-pattern", "search-index", "model")
RELATIONSHIP_KINDS = ("fk", "reference", "inferred", "embedded")
ON_DELETE = ("cascade", "set null", "set default", "restrict")
DETAILS = ("full", "summary")
KEYS = ("pk", "unique")

LIMITS = {"entities_warn": 80, "highlights": 5, "summary_sentences": 5, "role": 160, "description": 300,
          "durability": 120}

ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REF_RE = re.compile(r"^(?P<entity>[a-z][a-z0-9]*(?:-[a-z0-9]+)*)\.(?P<field>.+)$")
ENTITY_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
# Includes SQLAlchemy-style schemes such as postgresql+psycopg:// and mongodb+srv://.
CONNECTION_URL_RE = re.compile(r"\b(?:postgres(?:ql)?|rediss?|mongodb|mysql|mariadb|sqlite)(?:\+[a-z0-9]+)?://", re.I)

OPT_STR = (str, type(None))

# field: (allowed types, required)
SPECS = {
    "model": {"meta": (dict, True), "system": (dict, True), "stores": (list, True), "entities": (list, True),
              "relationships": (list, False)},
    "meta": {"generator": (str, True), "model": (str, True), "schema_version": (int, True),
             "model_sha": (OPT_STR, False), "model_date": (OPT_STR, False), "dirty": (bool, False),
             "detail": (str, False), "readme": (dict, False), "notes": (list, False)},
    "readme": viz.SPECS["readme"],
    "note": viz.SPECS["note"],
    "system": {"name": (str, True), "summary": (str, True), "highlights": (list, False)},
    "store": {"id": (str, True), "name": (str, True), "engine": (str, True), "role": (str, True),
              "durability": (str, False), "evidence": (list, False), "connection": (dict, False),
              "scope": (dict, False), "snapshot": (dict, False)},
    "connection": {"env": (str, False), "env_file": (str, False), "path": (str, False)},
    "scope": {"schemas": (list, False), "include": (list, False), "exclude": (list, False), "sample": (int, False)},
    "snapshot": {"taken": (str, True), "server_version": (OPT_STR, False), "sampled": (bool, False)},
    "entity": {"id": (str, True), "store": (str, True), "name": (str, True), "kind": (str, True),
               "description": (str, False), "field_descriptions": (dict, False), "declared": (dict, False),
               "observed": (dict, False), "observed_hash": (str, False), "inferred": (bool, False),
               "evidence": (list, False)},
    "declared": {"evidence": (list, True), "fields": (list, False), "type": (str, False), "ttl": (str, False)},
    "declared_field": {"name": (str, True), "type": (str, False)},
    "observed": {"type": (str, False), "ttl": (str, False), "estimate": ((int, type(None)), False),
                 "sample": (int, False), "comment": (OPT_STR, False), "details": (dict, False),
                 "fields": (list, True), "indexes": (list, False), "checks": (list, False),
                 "truncated": (bool, False)},
    "observed_field": {"name": (str, True), "type": (str, True), "nullable": (bool, False),
                       "default": (OPT_STR, False), "key": (OPT_STR, False), "presence": ((int, float), False),
                       "comment": (OPT_STR, False)},
    "index": {"name": (str, True), "fields": (list, True), "unique": (bool, False), "method": (str, False)},
    "check": {"name": (str, True), "definition": (str, True)},
    "relationship": {"from": (str, True), "to": (str, True), "kind": (str, True), "on_delete": (str, False),
                     "note": (str, False), "evidence": (list, False)},
}


def _fields(obj, spec: str, where: str, rep) -> bool:
    """Check one object's fields against SPECS[spec], as viz._fields does. False if it can't be used further."""
    if not isinstance(obj, dict):
        rep.error(where, "must be an object")
        return False
    fields = SPECS[spec]
    ok = True
    for key in obj:
        if key not in fields:
            rep.error(f"{where}.{key}", f"unknown field (allowed: {', '.join(fields)})")
    for key, (types, required) in fields.items():
        if key not in obj:
            if required:
                rep.error(where, f"missing required field '{key}'")
                ok = False
        elif not viz._is_type(obj[key], types):
            rep.error(f"{where}.{key}", f"must be {viz._describe(types)}")
            ok = False
    return ok


def observed_hash(observed: dict) -> str:
    return viz.content_hash(json.dumps(observed, sort_keys=True, ensure_ascii=False))


def parse_ref(ref: str):
    """'orders.user_id' -> ('orders', 'user_id'), or None. Entity ids never contain dots; fields may."""
    m = REF_RE.match(ref)
    return (m.group("entity"), m.group("field")) if m else None


def relationship_ends(r: dict):
    """(from, to) as (entity, field) pairs. An embedded relationship's `to` is a whole entity: field None."""
    to = (r["to"], None) if r.get("kind") == "embedded" and ENTITY_RE.match(r["to"]) else parse_ref(r["to"])
    return parse_ref(r["from"]), to


def field_names(entity: dict) -> list[str]:
    """Observed fields in order, then declared-only ones. A key pattern's placeholders ({id}) count too."""
    names: list[str] = []
    if entity.get("kind") == "key-pattern" and isinstance(entity.get("name"), str):
        names += [p for p in re.findall(r"\{[^{}]+\}", entity["name"]) if p not in names]
    for block in ("observed", "declared"):
        for f in (entity.get(block) or {}).get("fields") or []:
            if isinstance(f, dict) and isinstance(f.get("name"), str) and f["name"] not in names:
                names.append(f["name"])
    return names


def _evidence_list(value, where: str, rep) -> None:
    for j, ev in enumerate(value or []):
        if not isinstance(ev, str) or not ev.strip():
            rep.error(f"{where}[{j}]", "must be a non-empty string")
        elif viz._bad_path(viz._evidence_file(ev)):
            rep.error(f"{where}[{j}]", "must be a relative path inside the repo, without '..'")


def _str_list(value, where: str, rep) -> None:
    for j, v in enumerate(value or []):
        if not isinstance(v, str) or not v.strip():
            rep.error(f"{where}[{j}]", "must be a non-empty string")


def validate(model, rep) -> None:
    """Checks that need only the model: schema, ids, references, ownership and the secret lint."""
    if not _fields(model, "model", "model", rep):
        return

    meta = model["meta"]
    if _fields(meta, "meta", "meta", rep):
        if meta["generator"] != GENERATOR:
            rep.error("meta.generator", f"must be \"{GENERATOR}\"")
        if meta["model"] != MODEL:
            rep.error("meta.model", f"must be \"{MODEL}\"")
        if meta["schema_version"] != SCHEMA_VERSION:
            rep.error("meta.schema_version", f"must be {SCHEMA_VERSION}")
        if isinstance(meta.get("model_sha"), str) and not viz.SHA_RE.match(meta["model_sha"]):
            rep.error("meta.model_sha", "must be a lowercase hex commit SHA or null")
        if isinstance(meta.get("model_date"), str) and not viz.DATE_RE.match(meta["model_date"]):
            rep.error("meta.model_date", "must be YYYY-MM-DD or null")
        if "detail" in meta and meta["detail"] not in DETAILS:
            rep.error("meta.detail", f"must be one of {', '.join(DETAILS)}")
        readme = meta.get("readme")
        if readme is not None and _fields(readme, "readme", "meta.readme", rep):
            if "placement" in readme and not viz.PLACEMENT_RE.match(readme["placement"]):
                rep.error("meta.readme.placement", "must be after-intro, end, before:<heading> or after:<heading>")
            if "path" in readme and viz._bad_path(readme["path"]):
                rep.error("meta.readme.path", "must be a relative path inside the repo")
        for i, note in enumerate(meta.get("notes") or []):
            if _fields(note, "note", f"meta.notes[{i}]", rep):
                if not note["text"].strip():
                    rep.error(f"meta.notes[{i}].text", "must not be empty")
                if not viz.DATE_RE.match(note["added"]):
                    rep.error(f"meta.notes[{i}].added", "must be YYYY-MM-DD")

    system = model["system"]
    if _fields(system, "system", "system", rep):
        if not system["name"].strip():
            rep.error("system.name", "must not be empty")
        if not system["summary"].strip():
            rep.error("system.summary", "must not be empty: say what data the system keeps, and where")
        sentences = len(re.findall(r"[.!?](?:\s|$)", system["summary"].strip()))
        if sentences > LIMITS["summary_sentences"]:
            rep.warn("system.summary", f"looks like {sentences} sentences; keep it to {LIMITS['summary_sentences']}")
        highlights = system.get("highlights") or []
        if len(highlights) > LIMITS["highlights"]:
            rep.error("system.highlights", f"has {len(highlights)} entries; the limit is {LIMITS['highlights']}")
        _str_list(highlights, "system.highlights", rep)

    stores = {}
    for i, s in enumerate(model["stores"]):
        where = f"stores[{viz._label(s, i)}]"
        if not _fields(s, "store", where, rep):
            continue
        viz._check_id(s["id"], f"{where}.id", rep)
        if s["id"] in stores:
            rep.error(f"{where}.id", "duplicate store id")
        stores[s["id"]] = s
        if not s["name"].strip():
            rep.error(f"{where}.name", "must not be empty")
        if s["engine"] not in ENGINES:
            rep.error(f"{where}.engine", f"must be one of {', '.join(ENGINES)}")
        if not s["role"].strip():
            rep.error(f"{where}.role", "must not be empty: say what this store is for")
        elif len(s["role"]) > LIMITS["role"]:
            rep.error(f"{where}.role", f"is {len(s['role'])} characters; the limit is {LIMITS['role']}")
        if "durability" in s:
            if not s["durability"].strip() or "\n" in s["durability"]:
                rep.error(f"{where}.durability", "must be one non-empty line")
            elif len(s["durability"]) > LIMITS["durability"]:
                rep.error(f"{where}.durability", f"is {len(s['durability'])} characters; "
                                                 f"the limit is {LIMITS['durability']}")
        _evidence_list(s.get("evidence"), f"{where}.evidence", rep)
        conn = s.get("connection")
        if conn is not None and _fields(conn, "connection", f"{where}.connection", rep):
            if "env" in conn and not ENV_NAME_RE.match(conn["env"]):
                rep.error(f"{where}.connection.env", "must be the name of an environment variable, never its value")
            for key in ("env_file", "path"):
                if key in conn and viz._bad_path(conn[key]):
                    rep.error(f"{where}.connection.{key}", "must be a relative path inside the repo")
        scope = s.get("scope")
        if scope is not None and _fields(scope, "scope", f"{where}.scope", rep):
            for key in ("schemas", "include", "exclude"):
                _str_list(scope.get(key), f"{where}.scope.{key}", rep)
            if "sample" in scope and scope["sample"] < 1:
                rep.error(f"{where}.scope.sample", "must be at least 1")
        snap = s.get("snapshot")
        if snap is not None and _fields(snap, "snapshot", f"{where}.snapshot", rep):
            if not viz.DATE_RE.match(snap["taken"]):
                rep.error(f"{where}.snapshot.taken", "must be YYYY-MM-DD")
        if s["engine"] == "code" and (snap is not None or conn is not None):
            rep.error(where, "a code store has no connection or snapshot; its entities are declared")

    entities = {}
    undescribed = []
    for i, e in enumerate(model["entities"]):
        where = f"entities[{viz._label(e, i)}]"
        if not _fields(e, "entity", where, rep):
            continue
        viz._check_id(e["id"], f"{where}.id", rep)
        if e["id"] in entities:
            rep.error(f"{where}.id", "duplicate entity id")
        entities[e["id"]] = e
        store = stores.get(e["store"])
        if store is None:
            rep.error(f"{where}.store", f"unknown store '{e['store']}'")
        if not e["name"].strip():
            rep.error(f"{where}.name", "must not be empty")
        if e["kind"] not in KINDS:
            rep.error(f"{where}.kind", f"must be one of {', '.join(KINDS)}")
        if "description" in e:
            if not e["description"].strip() or "\n" in e["description"]:
                rep.error(f"{where}.description", "must be one non-empty line")
            elif len(e["description"]) > LIMITS["description"]:
                rep.error(f"{where}.description", f"is {len(e['description'])} characters; "
                                                  f"the limit is {LIMITS['description']}")
        _evidence_list(e.get("evidence"), f"{where}.evidence", rep)

        declared, observed = e.get("declared"), e.get("observed")
        if declared is None and observed is None:
            rep.error(where, "needs observed facts (from merge) or a declared block (from code or docs)")
        if declared is not None and _fields(declared, "declared", f"{where}.declared", rep):
            if not declared["evidence"]:
                rep.error(f"{where}.declared.evidence", "cite the code or docs that declare this entity")
            _evidence_list(declared["evidence"], f"{where}.declared.evidence", rep)
            for j, f in enumerate(declared.get("fields") or []):
                _fields(f, "declared_field", f"{where}.declared.fields[{j}]", rep)
        if observed is not None:
            if store is not None and store["engine"] == "code":
                rep.error(f"{where}.observed", "entities in a code store are declared, not observed")
            if e.get("observed_hash") != observed_hash(observed):
                rep.error(f"{where}.observed", "doesn't match observed_hash: observed facts come from `merge` "
                                               "and are never edited by hand. Re-run introspect and merge")
            elif _fields(observed, "observed", f"{where}.observed", rep):
                for j, f in enumerate(observed["fields"]):
                    _fields(f, "observed_field", f"{where}.observed.fields[{j}]", rep)
                for j, ix in enumerate(observed.get("indexes") or []):
                    _fields(ix, "index", f"{where}.observed.indexes[{j}]", rep)
                for j, ck in enumerate(observed.get("checks") or []):
                    _fields(ck, "check", f"{where}.observed.checks[{j}]", rep)
        elif "observed_hash" in e:
            rep.error(f"{where}.observed_hash", "set without observed facts")

        names = set(field_names(e))
        for name, text in (e.get("field_descriptions") or {}).items():
            if not isinstance(text, str) or not text.strip() or "\n" in text:
                rep.error(f"{where}.field_descriptions.{name}", "must be one non-empty line")
            elif names and name not in names:
                rep.warn(f"{where}.field_descriptions.{name}", "no observed or declared field has this name")
        if not e.get("description") and not (observed or {}).get("comment"):
            undescribed.append(e["id"])

    if undescribed:
        shown = ", ".join(undescribed[:8]) + (f", and {len(undescribed) - 8} more" if len(undescribed) > 8 else "")
        rep.warn("entities", f"{len(undescribed)} without a description: {shown}")
    if len(entities) > LIMITS["entities_warn"]:
        rep.warn("entities", f"{len(entities)} entities; above {LIMITS['entities_warn']}, scope the model "
                             "(schemas, include, exclude) or use detail \"summary\"")

    for i, r in enumerate(model.get("relationships") or []):
        where = f"relationships[{i}]"
        if not _fields(r, "relationship", where, rep):
            continue
        if r["kind"] not in RELATIONSHIP_KINDS:
            rep.error(f"{where}.kind", f"must be one of {', '.join(RELATIONSHIP_KINDS)}")
        if "on_delete" in r:
            if r["kind"] != "fk":
                rep.error(f"{where}.on_delete", "only foreign keys have a delete action")
            elif r["on_delete"] not in ON_DELETE:
                rep.error(f"{where}.on_delete", f"must be one of {', '.join(ON_DELETE)}")
        for end, ref in zip(("from", "to"), relationship_ends(r)):
            if ref is None:
                shape = "\"<entity id>\" or \"<entity id>.<field>\"" if end == "to" and r["kind"] == "embedded" \
                    else "\"<entity id>.<field>\""
                rep.error(f"{where}.{end}", f"must be {shape}")
            elif ref[0] not in entities:
                rep.error(f"{where}.{end}", f"unknown entity '{ref[0]}'")
            elif ref[1] is not None and ref[1] not in field_names(entities[ref[0]]):
                rep.warn(f"{where}.{end}", f"entity '{ref[0]}' has no field '{ref[1]}'")
        _evidence_list(r.get("evidence"), f"{where}.evidence", rep)

    for where, value in viz._strings(model):
        for what, pattern in viz.SECRET_PATTERNS:
            if pattern.search(value):
                rep.error(where, f"looks like it contains {what}; never put secrets in the model")
        if CONNECTION_URL_RE.search(value):
            rep.error(where, "looks like a connection string; name the environment variable instead")


def evidence_items(model: dict) -> list[tuple[str, str]]:
    """Every (where, evidence) in the model, for viz.check_evidence."""
    items = []
    for s in model.get("stores") or []:
        items += [(f"stores[{s['id']}].evidence[{j}]", ev) for j, ev in enumerate(s.get("evidence") or [])]
    for e in model.get("entities") or []:
        items += [(f"entities[{e['id']}].evidence[{j}]", ev) for j, ev in enumerate(e.get("evidence") or [])]
        declared = e.get("declared") or {}
        items += [(f"entities[{e['id']}].declared.evidence[{j}]", ev)
                  for j, ev in enumerate(declared.get("evidence") or [])]
    for i, r in enumerate(model.get("relationships") or []):
        items += [(f"relationships[{i}].evidence[{j}]", ev) for j, ev in enumerate(r.get("evidence") or [])]
    return items


def check_repo(model: dict, root: Path, rep) -> None:
    """Checks that need the repo: evidence exists and is tracked, and sample secrets stay out."""
    tracked = viz.git_files(root)
    viz.check_evidence(evidence_items(model), root, tracked, rep)
    viz.check_sample_secrets(model, root, tracked, rep)
