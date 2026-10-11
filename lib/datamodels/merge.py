"""Fold snapshots into data-models.json, keep every annotation, and say what changed.

merge owns `observed`, `observed_hash`, a store's `snapshot` and `fk` relationships. It never touches
descriptions, declared blocks or the relationships Claude wrote, except to drop ones that point at
an entity the store no longer has. Matching is by (store, entity name), so ids never change.
"""

from __future__ import annotations

import hashlib
import re

from . import model as m
from .sources import SNAPSHOT_VERSION, Scope


class MergeError(Exception):
    pass


def new_model(name: str) -> dict:
    return {
        "meta": {"generator": m.GENERATOR, "model": m.MODEL, "schema_version": m.SCHEMA_VERSION,
                 "model_sha": None, "model_date": None, "detail": "full",
                 "readme": {"embed": True, "path": "README.md", "placement": "after-intro"}, "notes": []},
        "system": {"name": name, "summary": "", "highlights": []},
        "stores": [],
        "entities": [],
        "relationships": [],
    }


def entity_id(name: str, store: dict, taken: set) -> str:
    """A kebab-case id from the entity name, unique in the model: "public.orders" -> "orders"."""
    base = name.split(".", 1)[1] if store["engine"] == "postgres" and name.startswith("public.") else name
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "entity"
    if not slug[0].isalpha():
        slug = "e-" + slug
    candidates = [slug, f"{store['id']}-{slug}"]
    for cand in candidates:
        if len(cand) > 40:
            cand = cand[:33].rstrip("-") + "-" + hashlib.sha1(name.encode()).hexdigest()[:6]
        if cand not in taken:
            return cand
    n = 2
    while f"{candidates[-1][:36]}-{n}" in taken:
        n += 1
    return f"{candidates[-1][:36]}-{n}"


def _describe_field(f: dict) -> str:
    return f"{f['name']} ({f['type']})" if f.get("type") else f["name"]


def observed_diff(old: dict | None, new: dict) -> list[str]:
    """Human-readable differences between two observed blocks. Estimates and presence are left out: they drift."""
    if old is None:
        return ["now found in the store"]
    out = []
    for key in ("type", "ttl"):
        if old.get(key) != new.get(key):
            out.append(f"{key} {old.get(key) or 'none'} -> {new.get(key) or 'none'}")
    before = {f["name"]: f for f in old.get("fields") or []}
    after = {f["name"]: f for f in new.get("fields") or []}
    out += [f"+ field {_describe_field(f)}" for n, f in after.items() if n not in before]
    out += [f"- field {n}" for n in before if n not in after]
    for n in after:
        if n in before:
            for key in ("type", "nullable", "key", "default"):
                if before[n].get(key) != after[n].get(key):
                    out.append(f"~ {n}: {key} {before[n].get(key)} -> {after[n].get(key)}")
    old_ix = {ix["name"] for ix in old.get("indexes") or []}
    new_ix = {ix["name"] for ix in new.get("indexes") or []}
    out += [f"+ index {n}" for n in sorted(new_ix - old_ix)] + [f"- index {n}" for n in sorted(old_ix - new_ix)]
    old_ck = {ck["name"]: ck["definition"] for ck in old.get("checks") or []}
    new_ck = {ck["name"]: ck["definition"] for ck in new.get("checks") or []}
    out += [f"+ check {n}" for n in new_ck if n not in old_ck] + [f"- check {n}" for n in old_ck if n not in new_ck]
    out += [f"~ check {n}" for n in new_ck if n in old_ck and old_ck[n] != new_ck[n]]
    return out


def _covered(name: str, scope: Scope, engine: str) -> bool:
    """Whether the snapshot's scope included this entity, so its absence means it's gone."""
    if engine == "postgres" and scope.schemas and name.split(".", 1)[0] not in scope.schemas:
        return False
    return scope.wants(name)


def merge(model: dict, snap: dict) -> dict:
    """Apply one snapshot to the model in place. Returns the change set for that store."""
    if snap.get("snapshot_version") != SNAPSHOT_VERSION or snap.get("generator") != m.GENERATOR:
        raise MergeError("not a visualizer data-models snapshot (or from a newer version)")
    engine, store_id = snap["engine"], snap["store"]
    stores = {s["id"]: s for s in model["stores"]}
    store = stores.get(store_id)
    if store is None:
        store = {"id": store_id, "name": store_id, "engine": engine, "role": ""}
        model["stores"].append(store)
    elif store["engine"] != engine:
        raise MergeError(f"store '{store_id}' is {store['engine']}, but the snapshot is {engine}")
    refresh = bool(store.get("snapshot"))  # on a first merge every fk is new; listing them is noise
    store["snapshot"] = {"taken": snap["taken"], "server_version": snap.get("server_version"),
                         "sampled": bool(snap.get("sampled"))}

    entities = model["entities"]
    taken = {e["id"] for e in entities}
    existing = {e["name"]: e for e in entities if e["store"] == store_id}
    changes = {"store": store_id, "engine": engine, "server_version": snap.get("server_version"),
               "entities": len(snap["entities"]), "added": [], "removed": [], "changed": {}, "dropped": [], "relationships": []}

    added = []
    for se in snap["entities"]:
        observed = se["observed"]
        e = existing.get(se["name"])
        if e is None:
            e = {"id": entity_id(se["name"], store, taken), "store": store_id, "name": se["name"],
                 "kind": se["kind"], "observed": observed, "observed_hash": m.observed_hash(observed)}
            taken.add(e["id"])
            added.append(e)
            changes["added"].append(se["name"])
            continue
        diff = observed_diff(e.get("observed"), observed)
        if e["kind"] != se["kind"]:
            diff.insert(0, f"kind {e['kind']} -> {se['kind']}")
            e["kind"] = se["kind"]
        e["observed"], e["observed_hash"] = observed, m.observed_hash(observed)
        if diff:
            changes["changed"][se["name"]] = diff

    seen = {se["name"] for se in snap["entities"]}
    scope = Scope.from_dict(snap.get("scope"))
    removed_ids = set()
    for name, e in existing.items():
        if name in seen or "observed" not in e or not _covered(name, scope, engine):
            continue
        if e.get("declared"):
            del e["observed"], e["observed_hash"]
            changes["changed"][name] = ["no longer in the store; kept because code declares it (see Drift)"]
        else:
            removed_ids.add(e["id"])
            changes["removed"].append(name)
    if removed_ids:
        entities[:] = [e for e in entities if e["id"] not in removed_ids]

    # New entities go after the store's last entity, sorted by name, so each store stays together.
    if added:
        added.sort(key=lambda e: e["name"])
        last = max((i for i, e in enumerate(entities) if e["store"] == store_id), default=len(entities) - 1)
        entities[last + 1:last + 1] = added

    # This store's fk relationships follow the snapshot: unchanged ones stay where they are, so a refresh
    # doesn't reorder the file. Others are kept unless they now point at a removed entity.
    ids = {e["name"]: e["id"] for e in entities if e["store"] == store_id}
    store_ids = set(ids.values())
    fks: dict[tuple, str | None] = {}
    for sr in snap.get("relationships") or []:
        src, dst = ids.get(sr["from"][0]), ids.get(sr["to"][0])
        if src and dst:
            fks[(f"{src}.{sr['from'][1]}", f"{dst}.{sr['to'][1]}")] = sr.get("on_delete")
    rels = model.setdefault("relationships", [])
    kept, present = [], set()
    for r in rels:
        ends = m.relationship_ends(r)
        pair = (r["from"], r["to"])
        if r["kind"] == "fk" and ends[0] and ends[0][0] in store_ids:
            if pair in fks:
                present.add(pair)
                if r.get("on_delete") != fks[pair]:
                    if refresh:
                        changes["relationships"].append(f"~ fk {r['from']} -> {r['to']}: on delete "
                                                        f"{r.get('on_delete') or 'no action'} -> "
                                                        f"{fks[pair] or 'no action'}")
                    r.pop("on_delete", None)
                    if fks[pair]:
                        r["on_delete"] = fks[pair]
                kept.append(r)
            elif refresh:
                changes["relationships"].append(f"- fk {r['from']} -> {r['to']}")
            continue
        if any(end and end[0] in removed_ids for end in ends):
            changes["dropped"].append(f"{r['from']} -> {r['to']}")
            continue
        kept.append(r)
    for (a, b), on_delete in fks.items():
        if (a, b) not in present:
            kept.append(dict({"from": a, "to": b, "kind": "fk"}, **({"on_delete": on_delete} if on_delete else {})))
            if refresh:
                changes["relationships"].append(f"+ fk {a} -> {b}")
    rels[:] = kept
    return changes


def format_changes(changes: dict) -> list[str]:
    version = f" {changes['server_version']}" if changes.get("server_version") else ""
    counts = (f"{len(changes['added'])} added, {len(changes['removed'])} removed, "
              f"{len(changes['changed'])} changed")
    lines = [f"{changes['store']} ({changes['engine']}{version}): {changes['entities']} entities; {counts}"]
    lines += [f"  + {n}" for n in changes["added"]]
    lines += [f"  - {n}" for n in changes["removed"]]
    lines += [f"  ~ {n}: " + "; ".join(diff) for n, diff in changes["changed"].items()]
    lines += [f"  {r}" for r in changes.get("relationships", [])]
    lines += [f"  dropped relationship {r} (its entity is gone)" for r in changes["dropped"]]
    return lines


def drift(model: dict) -> list[tuple[dict, str | None, str]]:
    """(entity, field or None, finding) where the declared schema and the store disagree on which fields exist.

    EXTEND: compare types too, once there's a mapping from ORM types to each engine's types.
    """
    stores = {s["id"]: s for s in model.get("stores") or []}
    out = []
    for e in model.get("entities") or []:
        store, declared, observed = stores.get(e["store"]), e.get("declared"), e.get("observed")
        if store is None or declared is None or store["engine"] == "code":
            continue
        sampled = store["engine"] in m.SAMPLED_ENGINES
        if observed is None:
            if store.get("snapshot"):
                out.append((e, None, "declared in code, not seen in the scan" if sampled
                            else "declared in code, not found in the store"))
            continue
        if not declared.get("fields"):
            continue
        if sampled and not observed.get("fields"):
            continue  # nothing was read (a JSON or string value), which isn't the same as nothing there
        dnames = {f["name"].lower() for f in declared["fields"]}
        onames = {f["name"].lower() for f in observed.get("fields") or []}
        for f in declared["fields"]:
            if f["name"].lower() not in onames:
                out.append((e, f["name"], "declared in code, not seen in the sample" if sampled
                            else "declared in code, not in the store"))
        for f in observed.get("fields") or []:
            nested = "." in f["name"] or "[]" in f["name"]
            if f["name"].lower() not in dnames and not (sampled and nested):
                out.append((e, f["name"], "in the store, not declared in code"))
    return out
