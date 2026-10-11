"""Import a browser HAR (DevTools → Network → Save all as HAR) as exchanges.jsonl.

A HAR holds the front channel: redirects to /authorize, the login form, the callback, cookies.
It's full of raw tokens and cookies, so it's redacted here, record by record, with the same
rules as live capture. The HAR itself is never copied. Static assets are counted, not kept.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .redact import Redactor

ASSET_TYPES = {"image", "font", "stylesheet", "script", "media", "manifest", "texttrack"}
ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".css", ".js", ".mjs", ".map",
                  ".woff", ".woff2", ".ttf", ".mp4", ".webm")


def _when(entry) -> float:
    return datetime.fromisoformat(entry["startedDateTime"].replace("Z", "+00:00")).timestamp()


def _is_asset(entry) -> bool:
    kind = entry.get("_resourceType")
    if kind:
        return kind in ASSET_TYPES
    return urlsplit(entry["request"]["url"]).path.lower().endswith(ASSET_SUFFIXES)


def _pairs(headers) -> list:
    # HTTP/2 pseudo-headers (:authority, :path) aren't headers on the wire.
    return [(h["name"], h["value"]) for h in headers or [] if not h["name"].startswith(":")]


def records(har: dict, level: str = "dev") -> list[dict]:
    entries = sorted(har.get("log", {}).get("entries", []), key=_when)
    if not entries:
        return []
    t0, redactor = _when(entries[0]), Redactor(level)
    out, skipped, hosts = [], 0, set()
    for i, e in enumerate(entries, 1):
        if _is_asset(e):
            skipped += 1
            hosts.add(urlsplit(e["request"]["url"]).netloc)
            continue
        req, res = e["request"], e["response"]
        post = req.get("postData") or {}
        content = res.get("content") or {}
        body = content.get("text")
        if body and content.get("encoding") == "base64":
            body = base64.b64decode(body)
        scan = redactor.scan()
        start = round(_when(e) - t0, 4)
        record = {
            "kind": "exchange", "id": f"har-{i}", "t0": start, "t1": round(start + max(e.get("time", 0), 0) / 1000, 4),
            "proc": "browser", "lib": "har", "method": req["method"], "url": scan.url(req["url"]),
            "status": res.get("status") or None,
            "req": {"headers": scan.headers(_pairs(req.get("headers")), "req"),
                    "body": scan.body(post.get("text"), post.get("mimeType", ""), "req.body")},
            "res": {"headers": scan.headers(_pairs(res.get("headers")), "res"),
                    "body": scan.body(body, content.get("mimeType", ""), "res.body")},
            "tokens": scan.refs, "seen_by": "har", "confidence": "observed",
        }
        if e.get("_resourceType"):
            record["resource_type"] = e["_resourceType"]
        out.append(record)
    if skipped:
        out.append({"kind": "har-skipped", "assets": skipped, "hosts": sorted(hosts)})
    return out


def import_har(src: Path, out: Path, level: str = "dev") -> Path:
    har = json.loads(Path(src).read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "exchanges.jsonl"
    from .run import write_private

    write_private(dest, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records(har, level)))
    return dest
