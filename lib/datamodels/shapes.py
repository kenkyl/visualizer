"""Shapes from samples: document fields for MongoDB, key patterns and TTLs for Redis.

Pure functions over values the sources already hold in memory. They return names, types and
ratios only; no value from a document or key survives them. Stdlib only.
"""

from __future__ import annotations

import re

MAX_DEPTH = 4
MAX_FIELDS = 200
COLLAPSE_AT = 10  # a key segment with more distinct literal values than this becomes {*}

# Python and BSON class names -> the type names shown in the tables.
TYPE_NAMES = {
    "str": "string", "int": "int", "Int64": "long", "float": "double", "bool": "bool",
    "datetime": "date", "date": "date", "ObjectId": "objectId", "Decimal128": "decimal", "Decimal": "decimal",
    "bytes": "binary", "Binary": "binary", "NoneType": "null", "dict": "object", "SON": "object",
    "list": "array", "UUID": "uuid", "Timestamp": "timestamp", "Regex": "regex", "DBRef": "dbref",
}


def type_name(value) -> str:
    name = type(value).__name__
    return TYPE_NAMES.get(name, name.lower())


def infer_shape(docs: list, max_depth: int = MAX_DEPTH, max_fields: int = MAX_FIELDS):
    """Field paths across sampled documents: [{"name", "type", "presence"}], plus whether paths were capped.

    Nested objects become "a.b", array elements "a[]" and "a[].b". presence is the share of
    documents in which the path appears at least once. type lists every type seen, e.g. "string | null".
    """
    order: list[str] = []
    types: dict[str, set[str]] = {}
    seen_in: dict[str, int] = {}
    truncated = False

    def walk(value, path: str, depth: int, here: set[str]) -> None:
        nonlocal truncated
        if path:
            if path not in types:
                if len(order) >= max_fields:
                    truncated = True
                    return
                order.append(path)
                types[path] = set()
            types[path].add(type_name(value))
            here.add(path)
        if depth >= max_depth:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{path}.{key}" if path else str(key), depth + 1, here)
        elif isinstance(value, list) and path:
            for child in value:
                walk(child, f"{path}[]", depth + 1, here)

    for doc in docs:
        here: set[str] = set()
        walk(doc, "", 0, here)
        for path in here:
            seen_in[path] = seen_in.get(path, 0) + 1

    total = len(docs) or 1
    fields = [{"name": p, "type": " | ".join(sorted(types[p])), "presence": round(seen_in.get(p, 0) / total, 2)}
              for p in order]
    return fields, truncated


_SEGMENT_RULES = [
    ("{uuid}", re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)),
    ("{email}", re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")),
    ("{date}", re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+Z?)?$")),
    ("{id}", re.compile(r"^-?\d+$")),
    ("{hash}", re.compile(r"^[0-9a-f]{8,}$", re.I)),
]
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-.=+/]{6,}$")
PLACEHOLDERS = {p for p, _ in _SEGMENT_RULES} | {"{*}"}


def normalize_segment(segment: str) -> str:
    """A placeholder for segments that look like ids, hashes, emails or dates; other segments as written."""
    for placeholder, pattern in _SEGMENT_RULES:
        if pattern.match(segment):
            return placeholder
    if _TOKEN_RE.match(segment) and re.search(r"\d", segment) and re.search(r"[A-Za-z]", segment):
        return "{id}"
    return segment


def cluster_keys(names, sep: str = ":", collapse_at: int = COLLAPSE_AT) -> dict[str, list]:
    """Group key names into patterns like "session:{id}" -> [the names in that group].

    Ids, UUIDs, hashes, emails and dates become placeholders. A segment that still has more than
    collapse_at distinct values among otherwise-identical keys becomes {*}. Segments with fewer
    distinct values are kept as written, because they're usually part of the schema ("profile",
    "cart"); the skill reviews the patterns before they're committed.
    """
    groups: dict[tuple, list] = {}
    for name in names:
        text = name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name)
        key = tuple(normalize_segment(s) for s in text.split(sep))
        groups.setdefault(key, []).append(name)

    width = max((len(k) for k in groups), default=0)
    for pos in range(width):
        siblings: dict[tuple, set] = {}
        for key in groups:
            if pos < len(key) and key[pos] not in PLACEHOLDERS:
                siblings.setdefault(key[:pos] + (None,) + key[pos + 1:], set()).add(key[pos])
        wide = {shape for shape, values in siblings.items() if len(values) > collapse_at}
        if not wide:
            continue
        merged: dict[tuple, list] = {}
        for key, members in groups.items():
            if pos < len(key) and key[:pos] + (None,) + key[pos + 1:] in wide and key[pos] not in PLACEHOLDERS:
                key = key[:pos] + ("{*}",) + key[pos + 1:]
            merged.setdefault(key, []).extend(members)
        groups = merged

    return {sep.join(k): v for k, v in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))}


def duration(ms: int) -> str:
    """Milliseconds as a short duration: 45s, 30m, 2h, 3d."""
    seconds = max(ms // 1000, 1)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{round(seconds / size)}{unit}"
    return f"{seconds}s"


def summarize_ttl(pttls: list) -> str:
    """One line for a pattern's TTLs, from PTTL replies (-1 means none): "none", "up to 30m", or a mix."""
    live = [t for t in pttls if isinstance(t, int) and t >= 0]
    if not pttls or not live:
        return "none"
    longest = f"up to {duration(max(live))}"
    if len(live) == len(pttls):
        return longest
    return f"{longest} on {round(100 * len(live) / len(pttls))}% of keys"
