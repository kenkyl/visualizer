"""Redis: key patterns from a capped SCAN, their types, TTLs and hash fields, and Search index schemas.
Driver: redis-py.

Redis has no read-only session either, so every command goes through ReadOnlyRedis, which refuses
anything outside ALLOWED. Key names are clustered into patterns in memory (shapes.cluster_keys) and
then dropped; hash values that HSCAN returns are dropped unread. The snapshot holds patterns only.

EXTEND: streams (XINFO), RedisJSON paths (JSON.OBJKEYS), memory stats, and Redis Cluster.
"""

from __future__ import annotations

from ..shapes import cluster_keys, summarize_ttl

ENGINE = "redis"
REQUIRES = ("redis>=5,<7",)
SAMPLED = True
DEFAULT_SAMPLE = 20  # keys per pattern
MAX_KEYS = 10000

ALLOWED = {"SCAN", "TYPE", "PTTL", "HSCAN", "DBSIZE", "INFO", "FT._LIST", "FT.INFO"}
TYPE_NAMES = {"ReJSON-RL": "json", "TSDB-TYPE": "timeseries", "MBbloom--": "bloom"}


class ReadOnlyRedis:
    """Sends only allowlisted commands; anything else raises PermissionError before reaching Redis."""

    def __init__(self, client):
        self._client = client

    def call(self, *args):
        command = str(args[0]).upper()
        if command not in ALLOWED:
            raise PermissionError(f"'{command}' is not on the read-only allowlist")
        return self._client.execute_command(*args)

    def close(self) -> None:
        self._client.close()


def connect(target: str):
    import redis

    client = redis.Redis.from_url(target, socket_timeout=10, socket_connect_timeout=10,
                                  client_name="visualizer-datamodels")
    return ReadOnlyRedis(client)


def close(conn) -> None:
    conn.close()


def _s(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _pairs(value) -> dict:
    """A reply that's either a dict already (RESP3, or a client callback) or a flat [k, v, k, v] list."""
    if isinstance(value, dict):
        return {_s(k): v for k, v in value.items()}
    items = list(value or [])
    return {_s(items[i]): items[i + 1] for i in range(0, len(items) - 1, 2)}


def _scan(r, cursor):
    reply = r.call("SCAN", cursor, "COUNT", 1000)
    return int(_s(reply[0])), list(reply[1])


def _hash_fields(r, key) -> list[str]:
    reply = r.call("HSCAN", key, 0, "COUNT", 100)
    data = reply[1]
    names = list(data.keys()) if isinstance(data, dict) else list(data)[0::2]
    return [_s(n) for n in names]  # values are never read


def _server_version(r) -> str | None:
    info = r.call("INFO", "server")
    if isinstance(info, (bytes, str)):
        info = dict(line.split(":", 1) for line in _s(info).splitlines() if ":" in line)
    return _s(info.get("redis_version")) if isinstance(info, dict) and info.get("redis_version") else None


def introspect(r, scope) -> dict:
    max_keys = scope.max_keys or MAX_KEYS
    per_pattern = scope.sample or DEFAULT_SAMPLE

    names, cursor, complete = [], 0, False
    while len(names) < max_keys:
        cursor, batch = _scan(r, cursor)
        names.extend(batch)
        if cursor == 0:
            complete = True
            break
    names = names[:max_keys]
    total = int(r.call("DBSIZE"))
    scanned = len(names)

    entities = []
    for pattern, keys in cluster_keys(names).items():
        if not scope.wants(pattern):
            continue
        sample = keys[:per_pattern]
        types, ttls, field_counts = [], [], {}
        for key in sample:
            kind = _s(r.call("TYPE", key))
            types.append(TYPE_NAMES.get(kind, kind))
            ttls.append(int(r.call("PTTL", key)))
            if kind == "hash":
                for f in set(_hash_fields(r, key)):
                    field_counts[f] = field_counts.get(f, 0) + 1
        distinct = sorted(set(types))
        hashes = types.count("hash") or 1
        observed = {
            "type": distinct[0] if len(distinct) == 1 else "mixed: " + ", ".join(distinct),
            "ttl": summarize_ttl(ttls),
            "estimate": len(keys) if complete else round(len(keys) / max(scanned, 1) * total),
            "sample": len(sample),
            "fields": [{"name": f, "type": "string", "presence": round(n / hashes, 2)}
                       for f, n in sorted(field_counts.items())],
        }
        entities.append({"name": pattern, "kind": "key-pattern", "observed": observed})
    del names

    entities += _search_indexes(r, scope)
    return {"server_version": _server_version(r), "entities": entities, "relationships": []}


def _search_indexes(r, scope) -> list:
    try:
        index_names = [_s(n) for n in r.call("FT._LIST")]
    except Exception:  # no Search module: an "unknown command" error
        return []
    out = []
    for name in index_names:
        if not scope.wants(name):
            continue
        info = _pairs(r.call("FT.INFO", name))
        definition = _pairs(info.get("index_definition"))
        fields = []
        for attr in info.get("attributes") or []:
            a = _pairs(attr)
            field = {"name": _s(a.get("attribute") or a.get("identifier")), "type": _s(a.get("type", "")).lower()}
            identifier = _s(a.get("identifier", ""))
            if identifier and identifier != field["name"]:
                field["comment"] = f"indexes {identifier}"
            fields.append(field)
        details = {"on": _s(definition.get("key_type", "HASH")).lower()}
        prefixes = [_s(p) for p in definition.get("prefixes") or []]
        if prefixes:
            details["prefixes"] = ", ".join(prefixes)
        out.append({"name": name, "kind": "search-index",
                    "observed": {"estimate": int(float(_s(info.get("num_docs", 0)))), "details": details,
                                 "fields": fields}})
    return out
