"""MongoDB: collections, validators and indexes, and field shapes from a $sample. Driver: pymongo.

MongoDB has no read-only session flag, so every driver object is wrapped in ReadOnly, which only
exposes the methods listed in ALLOWED. The only aggregation ever sent is a single $sample stage.
Sampled documents stay in memory just long enough for shapes.infer_shape; values never leave it.
"""

from __future__ import annotations

from ..shapes import infer_shape

ENGINE = "mongodb"
REQUIRES = ("pymongo>=4.6,<5",)
SAMPLED = True
DEFAULT_SAMPLE = 200

TIMEOUT_MS = 15000

# Each object's allowed methods; a nested dict means the method returns another object to wrap.
COLLECTION = {"list_indexes": None, "estimated_document_count": None, "aggregate": None}
DATABASE = {"list_collections": None, "get_collection": COLLECTION}
CLIENT = {"server_info": None, "get_database": DATABASE, "get_default_database": DATABASE, "close": None}


class ReadOnly:
    """Exposes only the allowlisted methods of a driver object; anything else raises PermissionError."""

    def __init__(self, target, allowed: dict):
        self._target, self._allowed = target, allowed

    def __getattr__(self, name):
        if name.startswith("_") or name not in self._allowed:
            raise PermissionError(f"'{name}' is not on the read-only allowlist")
        attr = getattr(self._target, name)
        child = self._allowed[name]
        if child is None:
            return attr
        return lambda *a, **k: ReadOnly(attr(*a, **k), child)

    def __getitem__(self, name):
        raise PermissionError("use get_collection(), which is wrapped")


def connect(target: str):
    import pymongo

    client = pymongo.MongoClient(target, serverSelectionTimeoutMS=10000, appname="visualizer-datamodels",
                                 readPreference="secondaryPreferred")
    return ReadOnly(client, CLIENT)


def close(conn) -> None:
    conn.close()


def _database(client, scope):
    if len(scope.schemas) > 1:
        raise ValueError("introspect one MongoDB database at a time")
    if scope.schemas:
        return client.get_database(scope.schemas[0])
    try:
        return client.get_default_database()
    except Exception as e:  # pymongo.errors.ConfigurationError when the URL names no database
        raise ValueError("name the database in the URL path or with --schema") from e


def _sample(coll, size: int) -> list:
    pipeline = [{"$sample": {"size": size}}]
    assert list(pipeline[0]) == ["$sample"]  # the only stage this source ever sends
    return list(coll.aggregate(pipeline, maxTimeMS=TIMEOUT_MS))


def _validator(options: dict) -> str | None:
    schema = ((options or {}).get("validator") or {}).get("$jsonSchema")
    if not isinstance(schema, dict):
        return None
    props, required = schema.get("properties") or {}, schema.get("required") or []
    text = f"$jsonSchema with {len(props)} properties"
    if required:
        text += ", required: " + ", ".join(str(r) for r in required)
    return text


def introspect(client, scope) -> dict:
    db = _database(client, scope)
    size = scope.sample or DEFAULT_SAMPLE
    entities = []
    for info in db.list_collections():
        name = info["name"]
        if name.startswith("system.") or not scope.wants(name):
            continue
        coll = db.get_collection(name)
        is_view = info.get("type") == "view"
        docs = _sample(coll, size)
        sampled = len(docs)
        fields, truncated = infer_shape(docs)
        del docs
        for f in fields:
            if f["name"] == "_id":
                f["key"] = "pk"
        observed = {"sample": sampled, "fields": fields, "indexes": []}
        if not is_view:
            observed["estimate"] = coll.estimated_document_count(maxTimeMS=TIMEOUT_MS)
            for ix in coll.list_indexes():
                if ix["name"] == "_id_":
                    continue
                keys = list(ix["key"].items())
                names = [k for k, _ in keys]
                if "_fts" in names:  # a text index stores its fields as weights
                    names = [n for n in names if n not in ("_fts", "_ftsx")] + list(ix.get("weights") or {})
                entry = {"name": ix["name"], "fields": names, "unique": bool(ix.get("unique"))}
                kinds = {v for _, v in keys if isinstance(v, str)}
                if kinds:
                    entry["method"] = ", ".join(sorted(kinds))
                observed["indexes"].append(entry)
        details = {}
        if info.get("type") == "timeseries":
            details["type"] = "time series"
        validator = _validator(info.get("options"))
        if validator:
            details["validator"] = validator
        if details:
            observed["details"] = details
        if truncated:
            observed["truncated"] = True
        entities.append({"name": name, "kind": "view" if is_view else "collection", "observed": observed})
    version = client.server_info().get("version")
    return {"server_version": version, "entities": entities, "relationships": []}
