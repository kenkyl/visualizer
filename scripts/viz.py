#!/usr/bin/env python3
"""visualizer: the deterministic half of the /visualizer:architecture skill.

The skill writes architecture.json, a small model of the repo's components,
flows and state. This script validates that model and renders GitHub-safe
Mermaid from it, so ids, styling and emit order stay stable between runs.

  viz.py check MODEL     validate the model against the schema and the repo
  viz.py render MODEL    write ARCHITECTURE.md and update the README block
  viz.py preview FILE    write an HTML page that renders every diagram in a
                         model (or a Markdown file) in light and dark, using a
                         pinned Mermaid, for a visual check in a browser

Stdlib only, Python 3.9+.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

__version__ = "0.1.0"
GENERATOR = "visualizer"
SCHEMA_VERSION = 1
MERMAID_VERSION = "11.17.2"  # pinned for `preview`; see references/style-guide.md

# ---------------------------------------------------------------------------
# Schema

BOUNDARY_KINDS = ("local", "container", "cloud", "saas")
COMPONENT_KINDS = ("entrypoint", "client", "service", "module", "datastore", "queue", "infra", "external")
EDGE_KINDS = ("sync", "async", "auth", "deploy")
STEP_KINDS = ("call", "return", "note")
DETAILS = ("container", "component")
DIRECTIONS = ("LR", "RL", "TB", "BT")
HOLDS_KINDS = ("datastore", "queue")

LIMITS = {
    "nodes_warn": 15,
    "nodes_max": 25,
    "edges_warn": 25,
    "edges_max": 40,
    "boundaries": 6,
    "flows": 3,
    "steps": 12,
    "highlights": 5,
    "summary_sentences": 5,
    "responsibility": 120,
    "edge_label": 40,
    "edge_label_warn": 28,
    "name_warn": 32,
    "tech_warn": 28,
    "msg_warn": 60,
    "main_path": 20,
}

ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PLACEMENT_RE = re.compile(r"^(?:after-intro|end|(?:before|after):.+)$")

NULLABLE_STR = (str, type(None))

# field: (allowed types, required)
SPECS = {
    "model": {
        "meta": (dict, True),
        "system": (dict, True),
        "boundaries": (list, False),
        "components": (list, True),
        "edges": (list, False),
        "flows": (list, False),
        "views": (list, False),
    },
    "meta": {
        "generator": (str, True),
        "schema_version": (int, True),
        "model_sha": (NULLABLE_STR, False),
        "model_date": (NULLABLE_STR, False),
        "reviewed_sha": (NULLABLE_STR, False),
        "dirty": (bool, False),
        "scope": (str, False),
        "detail": (str, False),
        "readme": (dict, False),
        "notes": (list, False),
    },
    "readme": {"embed": (bool, True), "path": (str, False), "placement": (str, False)},
    "note": {"text": (str, True), "added": (str, True)},
    "system": {"name": (str, True), "summary": (str, True), "highlights": (list, False)},
    "boundary": {"id": (str, True), "name": (str, True), "kind": (str, True), "parent": (str, False)},
    "component": {
        "id": (str, True),
        "name": (str, True),
        "kind": (str, True),
        "boundary": (str, False),
        "responsibility": (str, True),
        "tech": (str, False),
        "evidence": (list, True),
        "members": (list, False),
        "holds": (list, False),
        "inferred": (bool, False),
    },
    "hold": {"data": (str, True), "lifetime": (str, True)},
    "edge": {
        "from": (str, True),
        "to": (str, True),
        "label": (str, True),
        "kind": (str, True),
        "step": (int, False),
    },
    "flow": {"id": (str, True), "name": (str, True), "description": (str, False), "steps": (list, True)},
    "step": {"from": (str, True), "to": (str, True), "msg": (str, True), "kind": (str, True)},
    "view": {"id": (str, True), "title": (str, True), "include": (list, False), "direction": (str, False)},
}

SECRET_PATTERNS = [
    ("an Anthropic API key", re.compile(r"\bsk-ant-\S*")),
    ("an API key (sk-...)", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("an Arcade API key", re.compile(r"\barc_\S*")),
    ("an AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("credentials inside a URL", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:[^/\s@]+@")),
]
SECRET_ENV_NAME_RE = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|PASSWD|PWD|CREDENTIAL|PRIVATE|AUTH", re.I)
ENV_EXAMPLE_RE = re.compile(r"(?:^|/)(?:[^/]*\.)?env\.(?:example|sample|template)$|(?:^|/)[^/]*\.env\.example$")
FORBIDDEN_EVIDENCE_RE = re.compile(
    r"(?:^|/)(?:\.env(?:\.(?!example$|sample$|template$)[^/]*)?|[^/]*\.(?:pem|key|p12|pfx)|id_(?:rsa|dsa|ecdsa|ed25519)[^/]*)$"
)


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_type(value, types) -> bool:
    types = types if isinstance(types, tuple) else (types,)
    if isinstance(value, bool) and bool not in types:
        return False  # bool is an int subclass; don't let true pass as a step number
    return isinstance(value, types)


def _describe(types) -> str:
    names = {str: "a string", int: "an integer", bool: "true or false", list: "a list", dict: "an object",
             type(None): "null"}
    types = types if isinstance(types, tuple) else (types,)
    return " or ".join(names[t] for t in types)


def _fields(obj, spec: str, where: str, rep: Report) -> bool:
    """Check one object's fields against SPECS[spec]. Returns False if it can't be used further."""
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
        elif not _is_type(obj[key], types):
            rep.error(f"{where}.{key}", f"must be {_describe(types)}")
            ok = False
    return ok


def _text(value: str, where: str, rep: Report, limit: int | None = None, warn_at: int | None = None) -> None:
    if not value.strip():
        rep.error(where, "must not be empty")
    if "\n" in value or "\r" in value:
        rep.error(where, "must be a single line")
    if limit is not None and len(value) > limit:
        rep.error(where, f"is {len(value)} characters; the limit is {limit}")
    elif warn_at is not None and len(value) > warn_at:
        rep.warn(where, f"is {len(value)} characters; aim for {warn_at} or fewer")


def _label(item: dict, index: int, key: str = "id") -> str:
    value = item.get(key) if isinstance(item, dict) else None
    return value if isinstance(value, str) and value else str(index)


def validate(model, rep: Report) -> None:
    """Structural checks that need only the model: schema, ids, references and limits."""
    if not _fields(model, "model", "model", rep):
        return

    meta = model["meta"]
    if _fields(meta, "meta", "meta", rep):
        if meta["generator"] != GENERATOR:
            rep.error("meta.generator", f"must be \"{GENERATOR}\"")
        if meta["schema_version"] != SCHEMA_VERSION:
            rep.error("meta.schema_version", f"must be {SCHEMA_VERSION}")
        for key in ("model_sha", "reviewed_sha"):
            if isinstance(meta.get(key), str) and not SHA_RE.match(meta[key]):
                rep.error(f"meta.{key}", "must be a lowercase hex commit SHA or null")
        if isinstance(meta.get("model_date"), str) and not DATE_RE.match(meta["model_date"]):
            rep.error("meta.model_date", "must be YYYY-MM-DD or null")
        if "detail" in meta and meta["detail"] not in DETAILS:
            rep.error("meta.detail", f"must be one of {', '.join(DETAILS)}")
        scope = meta.get("scope")
        if isinstance(scope, str) and (scope.startswith("/") or ".." in scope.split("/")):
            rep.error("meta.scope", "must be a path inside the repo, like \".\" or \"services/api\"")
        readme = meta.get("readme")
        if readme is not None and _fields(readme, "readme", "meta.readme", rep):
            if "placement" in readme and not PLACEMENT_RE.match(readme["placement"]):
                rep.error("meta.readme.placement", "must be after-intro, end, before:<heading> or after:<heading>")
            if "path" in readme and _bad_path(readme["path"]):
                rep.error("meta.readme.path", "must be a relative path inside the repo")
        for i, note in enumerate(meta.get("notes") or []):
            if _fields(note, "note", f"meta.notes[{i}]", rep):
                if not note["text"].strip():
                    rep.error(f"meta.notes[{i}].text", "must not be empty")
                if not DATE_RE.match(note["added"]):
                    rep.error(f"meta.notes[{i}].added", "must be YYYY-MM-DD")

    system = model["system"]
    if _fields(system, "system", "system", rep):
        _text(system["name"], "system.name", rep)
        if not system["summary"].strip():
            rep.error("system.summary", "must not be empty")
        sentences = len(re.findall(r"[.!?](?:\s|$)", system["summary"].strip()))
        if sentences > LIMITS["summary_sentences"]:
            rep.warn("system.summary", f"looks like {sentences} sentences; keep it to {LIMITS['summary_sentences']}")
        highlights = system.get("highlights") or []
        if len(highlights) > LIMITS["highlights"]:
            rep.error("system.highlights", f"has {len(highlights)} entries; the limit is {LIMITS['highlights']}")
        for i, h in enumerate(highlights):
            if not isinstance(h, str):
                rep.error(f"system.highlights[{i}]", "must be a string")
            else:
                _text(h, f"system.highlights[{i}]", rep)

    boundaries = {}
    raw_boundaries = model.get("boundaries") or []
    if len(raw_boundaries) > LIMITS["boundaries"]:
        rep.error("boundaries", f"has {len(raw_boundaries)} entries; the limit is {LIMITS['boundaries']}")
    for i, b in enumerate(raw_boundaries):
        where = f"boundaries[{_label(b, i)}]"
        if not _fields(b, "boundary", where, rep):
            continue
        _check_id(b["id"], f"{where}.id", rep)
        if b["id"] in boundaries:
            rep.error(f"{where}.id", "duplicate boundary id")
        boundaries[b["id"]] = b
        _text(b["name"], f"{where}.name", rep, warn_at=LIMITS["name_warn"])
        if b["kind"] not in BOUNDARY_KINDS:
            rep.error(f"{where}.kind", f"must be one of {', '.join(BOUNDARY_KINDS)}")
    for bid, b in boundaries.items():
        parent = b.get("parent")
        if parent is None:
            continue
        if parent not in boundaries:
            rep.error(f"boundaries[{bid}].parent", f"unknown boundary '{parent}'")
            continue
        seen, cur = {bid}, parent
        while cur is not None and cur in boundaries:
            if cur in seen:
                rep.error(f"boundaries[{bid}].parent", "boundaries form a cycle")
                break
            seen.add(cur)
            cur = boundaries[cur].get("parent")

    components = {}
    for i, c in enumerate(model["components"]):
        where = f"components[{_label(c, i)}]"
        if not _fields(c, "component", where, rep):
            continue
        _check_id(c["id"], f"{where}.id", rep)
        if c["id"] in components:
            rep.error(f"{where}.id", "duplicate component id")
        components[c["id"]] = c
        _text(c["name"], f"{where}.name", rep, warn_at=LIMITS["name_warn"])
        _text(c["responsibility"], f"{where}.responsibility", rep, limit=LIMITS["responsibility"])
        if "tech" in c:
            _text(c["tech"], f"{where}.tech", rep, warn_at=LIMITS["tech_warn"])
        if c["kind"] not in COMPONENT_KINDS:
            rep.error(f"{where}.kind", f"must be one of {', '.join(COMPONENT_KINDS)}")
        if "boundary" in c and c["boundary"] not in boundaries:
            rep.error(f"{where}.boundary", f"unknown boundary '{c['boundary']}'")
        if not c["evidence"] and not c.get("inferred"):
            rep.error(f"{where}.evidence", "cite at least one repo path or doc:FILE#anchor, or mark the component inferred")
        elif not c["evidence"]:
            rep.warn(f"{where}.evidence", "inferred component with no evidence; say why it is assumed")
        for j, ev in enumerate(c["evidence"]):
            if not isinstance(ev, str) or not ev.strip():
                rep.error(f"{where}.evidence[{j}]", "must be a non-empty string")
            elif _bad_path(_evidence_file(ev)):
                rep.error(f"{where}.evidence[{j}]", "must be a relative path inside the repo, without '..'")
        for j, m in enumerate(c.get("members") or []):
            if not isinstance(m, str) or not m.strip():
                rep.error(f"{where}.members[{j}]", "must be a non-empty string")
        holds = c.get("holds") or []
        if holds and c["kind"] not in HOLDS_KINDS:
            rep.error(f"{where}.holds", f"only {' and '.join(HOLDS_KINDS)} components hold state; "
                                        "model a store inside an external system as its own datastore")
        for j, h in enumerate(holds):
            if _fields(h, "hold", f"{where}.holds[{j}]", rep):
                _text(h["data"], f"{where}.holds[{j}].data", rep)
                _text(h["lifetime"], f"{where}.holds[{j}].lifetime", rep)
        if c["kind"] == "datastore" and not holds:
            rep.warn(f"{where}.holds", "a datastore should say what it holds and for how long")

    used_boundaries = {c["boundary"] for c in components.values() if c.get("boundary")}
    for bid in boundaries:
        if bid not in used_boundaries and not any(b.get("parent") == bid for b in boundaries.values()):
            rep.warn(f"boundaries[{bid}]", "contains no components")

    edges = []
    steps_seen: dict[int, int] = {}
    pairs = set()
    connected = set()
    for i, e in enumerate(model.get("edges") or []):
        where = f"edges[{i}]"
        if not _fields(e, "edge", where, rep):
            continue
        edges.append(e)
        for end in ("from", "to"):
            if e[end] not in components:
                rep.error(f"{where}.{end}", f"unknown component '{e[end]}'")
        if e["from"] == e["to"]:
            rep.error(where, "an edge must join two different components")
        connected.update((e["from"], e["to"]))
        _text(e["label"], f"{where}.label", rep, limit=LIMITS["edge_label"], warn_at=LIMITS["edge_label_warn"])
        if e["kind"] not in EDGE_KINDS:
            rep.error(f"{where}.kind", f"must be one of {', '.join(EDGE_KINDS)}")
        key = (e["from"], e["to"], e["kind"])
        if key in pairs:
            rep.warn(where, f"duplicate {e['kind']} edge {e['from']} -> {e['to']}; merge the labels")
        pairs.add(key)
        if "step" in e:
            n = e["step"]
            if not 1 <= n <= LIMITS["main_path"]:
                rep.error(f"{where}.step", f"must be between 1 and {LIMITS['main_path']}")
            elif n in steps_seen:
                rep.error(f"{where}.step", f"step {n} is already used by edges[{steps_seen[n]}]")
            else:
                steps_seen[n] = i
    if steps_seen and sorted(steps_seen) != list(range(1, len(steps_seen) + 1)):
        rep.warn("edges", "main-path steps should run 1, 2, 3, ... without gaps")
    for cid in components:
        if cid not in connected:
            rep.warn(f"components[{cid}]", "has no edges")

    flows = model.get("flows") or []
    if len(flows) > LIMITS["flows"]:
        rep.error("flows", f"has {len(flows)} entries; the limit is {LIMITS['flows']}")
    flow_ids = set()
    for i, f in enumerate(flows):
        where = f"flows[{_label(f, i)}]"
        if not _fields(f, "flow", where, rep):
            continue
        _check_id(f["id"], f"{where}.id", rep)
        if f["id"] in flow_ids:
            rep.error(f"{where}.id", "duplicate flow id")
        flow_ids.add(f["id"])
        _text(f["name"], f"{where}.name", rep)
        if not f["steps"]:
            rep.error(f"{where}.steps", "must have at least one step")
        if len(f["steps"]) > LIMITS["steps"]:
            rep.error(f"{where}.steps", f"has {len(f['steps'])} steps; the limit is {LIMITS['steps']}")
        for j, s in enumerate(f["steps"]):
            sw = f"{where}.steps[{j}]"
            if not _fields(s, "step", sw, rep):
                continue
            for end in ("from", "to"):
                if s[end] not in components:
                    rep.error(f"{sw}.{end}", f"unknown component '{s[end]}'")
            if s["kind"] not in STEP_KINDS:
                rep.error(f"{sw}.kind", f"must be one of {', '.join(STEP_KINDS)}")
            elif s["kind"] != "note" and s["from"] == s["to"]:
                rep.error(sw, "a call or return must go between two different components; use a note")
            _text(s["msg"], f"{sw}.msg", rep, warn_at=LIMITS["msg_warn"])

    view_ids = set()
    for i, v in enumerate(model.get("views") or []):
        where = f"views[{_label(v, i)}]"
        if not _fields(v, "view", where, rep):
            continue
        _check_id(v["id"], f"{where}.id", rep)
        if v["id"] in view_ids:
            rep.error(f"{where}.id", "duplicate view id")
        view_ids.add(v["id"])
        _text(v["title"], f"{where}.title", rep)
        if "direction" in v and v["direction"] not in DIRECTIONS:
            rep.error(f"{where}.direction", f"must be one of {', '.join(DIRECTIONS)}")
        for j, cid in enumerate(v.get("include") or []):
            if cid not in components:
                rep.error(f"{where}.include[{j}]", f"unknown component '{cid}'")

    if rep.ok:
        for view in resolve_views(model):
            n_nodes, n_edges = len(view["components"]), len(view["edges"])
            where = f"views[{view['id']}]"
            if n_nodes > LIMITS["nodes_max"]:
                rep.error(where, f"{n_nodes} nodes; the limit is {LIMITS['nodes_max']}. Collapse siblings or split views")
            elif n_nodes > LIMITS["nodes_warn"]:
                rep.warn(where, f"{n_nodes} nodes; aim for {LIMITS['nodes_warn']} or fewer")
            if n_edges > LIMITS["edges_max"]:
                rep.error(where, f"{n_edges} edges; the limit is {LIMITS['edges_max']}")
            elif n_edges > LIMITS["edges_warn"]:
                rep.warn(where, f"{n_edges} edges; aim for {LIMITS['edges_warn']} or fewer")

    for where, value in _strings(model):
        for what, pattern in SECRET_PATTERNS:
            if pattern.search(value):
                rep.error(where, f"looks like it contains {what}; never put secrets or key placeholders in the model")


def _check_id(value: str, where: str, rep: Report) -> None:
    if not ID_RE.match(value) or len(value) > 40:
        rep.error(where, f"'{value}' must be kebab-case (a-z, 0-9, single hyphens), starting with a letter")


def _bad_path(path: str) -> bool:
    return not path or path.startswith("/") or "\\" in path or ".." in path.split("/")


def _evidence_file(ev: str) -> str:
    """The repo path an evidence string points at: 'doc:README.md#setup' -> 'README.md'."""
    if ev.startswith("doc:"):
        ev = ev[4:]
    return ev.split("#", 1)[0]


def _strings(value, where: str = "model"):
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, f"{where}[{i}]")
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, f"{where}.{k}")


# ---------------------------------------------------------------------------
# Repo checks: evidence exists, and no sample secrets leak into the model


def git_files(root: Path) -> list[str] | None:
    """Tracked files under root, or None if root isn't in a git work tree."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]


def git_toplevel(path: Path) -> Path | None:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                             capture_output=True, check=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(out.stdout.strip())


def github_slugs(markdown: str) -> set[str]:
    """Anchors GitHub generates for a Markdown file's headings."""
    slugs: set[str] = set()
    counts: dict[str, int] = {}
    for level, text in _headings(markdown.splitlines()):
        text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links and images keep their text
        text = re.sub(r"<[^>]+>", "", text).replace("`", "").replace("*", "")
        slug = re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")
        n = counts.get(slug, 0)
        counts[slug] = n + 1
        slugs.add(slug if n == 0 else f"{slug}-{n}")
    return slugs


def _headings(lines):
    """Yield (level, text) for ATX headings outside fenced code blocks."""
    for _, level, text in _heading_positions(lines):
        yield level, text


def _heading_positions(lines):
    fence = None
    for i, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        m = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker[0] * len(marker)
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        m = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$", line)
        if m:
            yield i, len(m.group(1)), (m.group(2) or "").strip()


def check_repo(model: dict, root: Path, rep: Report) -> None:
    """Checks that need the repo: evidence paths and anchors exist, and sample secrets stay out."""
    tracked = git_files(root)
    evidence = [(f"components[{c['id']}].evidence[{j}]", ev)
                for c in model.get("components") or [] for j, ev in enumerate(c.get("evidence") or [])]
    check_evidence(evidence, root, tracked, rep)
    check_sample_secrets(model, root, tracked, rep)


def check_evidence(evidence: list[tuple[str, str]], root: Path, tracked: list[str] | None, rep: Report) -> None:
    """Each (where, evidence) must exist under root, be tracked, not be a secrets file, and anchor a real heading.

    Also used by lib/datamodels, so keep the signature stable.
    """
    tracked_set = set(tracked or [])
    tracked_dirs = {str(Path(p).parent.as_posix()) + "/" for p in tracked_set}
    for d in list(tracked_dirs):
        parts = d.rstrip("/").split("/")
        for k in range(1, len(parts)):
            tracked_dirs.add("/".join(parts[:k]) + "/")

    for where, ev in evidence:
        path = _evidence_file(ev)
        if FORBIDDEN_EVIDENCE_RE.search(path.rstrip("/")):
            rep.error(where, f"'{path}' is a secrets file; never read or cite it")
            continue
        target = root / path
        if not target.exists():
            rep.error(where, f"'{path}' does not exist in {root}")
            continue
        if tracked is not None:
            is_tracked = path in tracked_set or (target.is_dir() and path.rstrip("/") + "/" in tracked_dirs)
            if not is_tracked:
                rep.warn(where, f"'{path}' isn't tracked by git, so the evidence link won't work for others")
        if "#" in ev:
            anchor = ev.split("#", 1)[1]
            if target.is_dir() or target.suffix.lower() not in (".md", ".markdown"):
                rep.error(where, "anchors are only supported on Markdown files")
            elif anchor not in github_slugs(target.read_text(encoding="utf-8", errors="replace")):
                rep.error(where, f"no heading in {path} has the anchor #{anchor}")


def check_sample_secrets(model: dict, root: Path, tracked: list[str] | None, rep: Report) -> None:
    """No string in the model may contain a secret-named sample value from a tracked .env.example."""
    samples = _sample_secret_values(root, tracked)
    for where, value in _strings(model):
        for source, name, sample in samples:
            if sample in value:
                rep.error(where, f"contains the sample value of {name} from {source}")


def _sample_secret_values(root: Path, tracked: list[str] | None):
    """(file, name, value) for secret-looking entries in tracked .env.example-style files."""
    if tracked is None:
        tracked = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", ".venv", "venv", "__pycache__")]
            for name in filenames:
                tracked.append(Path(dirpath, name).relative_to(root).as_posix())
    found = []
    for rel in sorted(p for p in tracked if ENV_EXAMPLE_RE.search(p)):
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if not m or not SECRET_ENV_NAME_RE.search(m.group(1)):
                continue
            value = m.group(2)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if len(value) >= 6:
                found.append((rel, m.group(1), value))
    return found


# ---------------------------------------------------------------------------
# Views and emit order


def resolve_views(model: dict) -> list[dict]:
    """Each view with its components (model order) and the edges between them."""
    comps = model["components"]
    edges = model.get("edges") or []
    views = model.get("views") or [{"id": "overview", "title": "Overview"}]
    out = []
    for v in views:
        include = v.get("include")
        wanted = set(include) if include else {c["id"] for c in comps}
        vcomps = [c for c in comps if c["id"] in wanted]
        vedges = [e for e in edges if e["from"] in wanted and e["to"] in wanted]
        out.append({"id": v["id"], "title": v["title"], "direction": v.get("direction", "TB"),
                    "components": vcomps, "edges": vedges})
    return out


def _chains(model: dict, comps: list[dict]) -> dict[str, list[str]]:
    """Component id -> boundary ids from the outermost down to its own."""
    parents = {b["id"]: b.get("parent") for b in model.get("boundaries") or []}
    chains = {}
    for c in comps:
        chain, cur = [], c.get("boundary")
        while cur is not None:
            chain.insert(0, cur)
            cur = parents.get(cur)
        chains[c["id"]] = chain
    return chains


def layout(model: dict, comps: list[dict]):
    """Nested emit order: a list of ("component", comp) and ("boundary", boundary, children).

    Order follows the model: each boundary appears where its first component does,
    so authors control layout by ordering components.
    """
    boundaries = {b["id"]: b for b in model.get("boundaries") or []}
    chains = _chains(model, comps)

    def scope(depth: int, members: list[dict]):
        items, seen = [], set()
        for c in members:
            chain = chains[c["id"]]
            if len(chain) > depth:
                child = chain[depth]
                if child in seen:
                    continue
                seen.add(child)
                inner = [x for x in members if len(chains[x["id"]]) > depth and chains[x["id"]][depth] == child]
                items.append(("boundary", boundaries[child], scope(depth + 1, inner)))
            else:
                items.append(("component", c))
        return items

    return scope(0, comps)


def _flatten(items, trail=()):
    """(component, boundary names from outer to inner) in emit order."""
    for item in items:
        if item[0] == "component":
            yield item[1], trail
        else:
            yield from _flatten(item[2], trail + (item[1]["name"],))


MAX_EDGE_LENGTH = 4


def edge_lengths(model: dict, view: dict) -> list[int]:
    """Minimum rank span for each edge in the view, so trust zones stack instead of sitting side by side.

    Mermaid's layout puts two boundaries next to each other whenever their nodes share
    ranks, which makes wide, unreadable diagrams. So ranks are assigned here: longest
    path, plus the rule that an edge leaving a top-level boundary lands below that
    boundary's last rank. The result is emitted as longer links (--->), which the
    layout treats as minimum lengths. Back edges, and boundaries that call each other
    in a cycle, are left alone, so the ranking always terminates.
    """
    ids = [c["id"] for c in view["components"]]
    chains = _chains(model, view["components"])
    group = {i: chains[i][0] if chains[i] else f"_{i}" for i in ids}
    edges = [(e["from"], e["to"]) for e in view["edges"]]

    adj: dict[str, list[str]] = {i: [] for i in ids}
    for u, v in edges:
        adj[u].append(v)
    state: dict[str, int] = {}
    forward: set[tuple[str, str]] = set()

    def dfs(u: str) -> None:  # recursion is fine: views are capped at 25 nodes
        state[u] = 1
        for v in adj[u]:
            if state.get(v) == 1:
                continue  # back edge: part of a cycle, not ranked
            forward.add((u, v))
            if v not in state:
                dfs(v)
        state[u] = 2

    for i in ids:
        if i not in state:
            dfs(i)
    fwd = [(u, v) for u, v in edges if (u, v) in forward]

    gadj: dict[str, set[str]] = {}
    for u, v in fwd:
        if group[u] != group[v]:
            gadj.setdefault(group[u], set()).add(group[v])

    def reaches(a: str, b: str) -> bool:
        seen, todo = set(), [a]
        while todo:
            g = todo.pop()
            if g == b:
                return True
            if g not in seen:
                seen.add(g)
                todo.extend(gadj.get(g, ()))
        return False

    stacked = {(group[u], group[v]) for u, v in fwd
               if group[u] != group[v] and not reaches(group[v], group[u])}

    rank = {i: 0 for i in ids}
    for _ in range(4 * len(ids) + 4):
        changed = False
        for u, v in fwd:
            if rank[v] < rank[u] + 1:
                rank[v], changed = rank[u] + 1, True
        bottom: dict[str, int] = {}
        for i in ids:
            bottom[group[i]] = max(bottom.get(group[i], 0), rank[i])
        for u, v in fwd:
            if (group[u], group[v]) in stacked and rank[v] < bottom[group[u]] + 1:
                rank[v], changed = bottom[group[u]] + 1, True
        if not changed:
            break
    else:
        return [1] * len(edges)  # didn't settle: plain links are always safe

    return [min(MAX_EDGE_LENGTH, max(1, rank[v] - rank[u])) if (u, v) in forward else 1 for u, v in edges]


# ---------------------------------------------------------------------------
# Mermaid

KIND_STYLE = {  # translucent tint + mid-tone stroke, text left to the theme: reads on light and dark pages
    "entrypoint": "fill:#2da44e26,stroke:#2da44e",
    "client": "fill:#8957e526,stroke:#8957e5",
    "service": "fill:#218bff26,stroke:#218bff",
    "module": "fill:#1b998b26,stroke:#1b998b",
    "datastore": "fill:#d4a72c33,stroke:#bf8700",
    "queue": "fill:#fb8f4426,stroke:#e16f24",
    "infra": "fill:#e85aad26,stroke:#e85aad",
    "external": "fill:#8c959f1f,stroke:#8c959f,stroke-dasharray:5 3",
}
BOUNDARY_STYLE = "fill:none,stroke:#8b949e,stroke-width:1px,stroke-dasharray:6 4"
INFERRED_STYLE = "stroke-dasharray:3 3"
SHAPES = {"datastore": ("[(", ")]"), "queue": ("[[", "]]"), "entrypoint": ("([", "])"), "client": ("([", "])")}


def arrow(kind: str, length: int = 1) -> str:
    """Link syntax for an edge kind; longer links set a minimum rank span (see edge_lengths)."""
    if kind == "auth":
        return "=" * (length + 1) + ">"
    if kind in ("async", "deploy"):
        return "-" + "." * length + "->"
    return "-" * (length + 1) + ">"

_FLOW_ESC = {"#": "#35;", "&": "#amp;", '"': "#quot;", "<": "#lt;", ">": "#gt;", "`": "#96;"}
_SEQ_ESC = dict(_FLOW_ESC, **{";": "#59;"})


def esc_flow(text: str) -> str:
    """Escape text for a quoted flowchart label."""
    return re.sub(r'[#&"<>`]', lambda m: _FLOW_ESC[m.group()], text)


def esc_seq(text: str) -> str:
    """Escape text for a sequence diagram message, note or participant alias."""
    return re.sub(r'[#&"<>`;]', lambda m: _SEQ_ESC[m.group()], text)


def mid(prefix: str, ident: str) -> str:
    """Mermaid id for a model id: never a reserved word, never ambiguous with arrows."""
    return f"{prefix}_{ident.replace('-', '_')}"


def step_mark(n: int) -> str:
    return f"{n}."


def node_label(c: dict) -> str:
    name = esc_flow(c["name"]) + (" (inferred)" if c.get("inferred") else "")
    label = f"<b>{name}</b>"
    if c.get("tech"):
        label += f"<br/>{esc_flow(c['tech'])}"
    return label


def flowchart(model: dict, view: dict) -> str:
    lines = [f"flowchart {view['direction']}",
             "  %% Generated by visualizer from architecture.json. Edit the model, then re-render."]
    used_kinds, boundaries_emitted, inferred = [], [], []

    def emit(items, indent):
        for item in items:
            if item[0] == "component":
                c = item[1]
                open_, close = SHAPES.get(c["kind"], ("[", "]"))
                lines.append(f'{indent}{mid("c", c["id"])}{open_}"{node_label(c)}"{close}')
                if c["kind"] not in used_kinds:
                    used_kinds.append(c["kind"])
                if c.get("inferred"):
                    inferred.append(c["id"])
            else:
                b = item[1]
                boundaries_emitted.append(b["id"])
                lines.append(f'{indent}subgraph {mid("b", b["id"])}["{esc_flow(b["name"])}"]')
                emit(item[2], indent + "  ")
                lines.append(f"{indent}end")

    emit(layout(model, view["components"]), "  ")
    for e, length in zip(view["edges"], edge_lengths(model, view)):
        label = esc_flow(e["label"])
        if "step" in e:
            label = f"{step_mark(e['step'])} {label}"
        lines.append(f'  {mid("c", e["from"])} {arrow(e["kind"], length)}|"{label}"| {mid("c", e["to"])}')
    for kind in COMPONENT_KINDS:
        if kind in used_kinds:
            lines.append(f"  classDef {kind} {KIND_STYLE[kind]},stroke-width:1.5px")
    for kind in COMPONENT_KINDS:
        ids = [mid("c", c["id"]) for c in view["components"] if c["kind"] == kind]
        if ids:
            lines.append(f"  class {','.join(ids)} {kind}")
    for bid in boundaries_emitted:
        lines.append(f"  style {mid('b', bid)} {BOUNDARY_STYLE}")
    for cid in inferred:
        lines.append(f"  style {mid('c', cid)} {INFERRED_STYLE}")
    return "\n".join(lines)


def sequence(model: dict, flow: dict) -> str:
    comps = {c["id"]: c for c in model["components"]}
    lines = ["sequenceDiagram", "  autonumber"]
    seen = []
    for s in flow["steps"]:
        for cid in (s["from"], s["to"]):
            if cid not in seen:
                seen.append(cid)
    for cid in seen:
        lines.append(f"  participant {mid('c', cid)} as {esc_seq(comps[cid]['name'])}")
    for s in flow["steps"]:
        a, b, msg = mid("c", s["from"]), mid("c", s["to"]), esc_seq(s["msg"])
        if s["kind"] == "note":
            over = a if a == b else f"{a},{b}"
            lines.append(f"  Note over {over}: {msg}")
        else:
            arrow = "->>" if s["kind"] == "call" else "-->>"
            lines.append(f"  {a}{arrow}{b}: {msg}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown

LEGEND_KINDS = {
    "entrypoint": ("Rounded box", "green", "Entry point: something you run"),
    "client": ("Rounded box", "purple", "Client: a person or app that uses the system"),
    "service": ("Box", "blue", "Service you run"),
    "module": ("Box", "teal", "Module inside a program"),
    "datastore": ("Cylinder", "yellow", "Datastore: where state lives"),
    "queue": ("Box with side bars", "orange", "Queue or stream"),
    "infra": ("Box", "pink", "Infrastructure"),
    "external": ("Dashed box", "grey", "External system you don't run"),
}
LEGEND_EDGES = {
    "sync": ("Solid arrow", "Call or request"),
    "async": ("Dotted arrow", "Asynchronous message"),
    "deploy": ("Dotted arrow", "Deployment"),
    "auth": ("Thick arrow", "Sign-in or credential flow"),
}


def md_text(text: str) -> str:
    """Escape raw HTML outside code spans, so text shows as written."""
    parts = re.split(r"(`+[^`]*`+)", text)
    return "".join(p if p.startswith("`") else p.replace("<", "&lt;").replace(">", "&gt;") for p in parts)


def md_cell(text: str) -> str:
    return md_text(text).replace("|", "\\|")


def _url(path: str) -> str:
    return quote(path, safe="/#-._~")


def _rel(target: str, from_dir: str) -> str:
    """Relative POSIX link from from_dir (repo-relative) to target (repo-relative)."""
    rel = os.path.relpath(target or ".", from_dir or ".")
    rel = Path(rel).as_posix()
    if target.endswith("/") and not rel.endswith("/"):
        rel += "/"
    return rel


def evidence_link(ev: str, from_dir: str) -> str:
    path = _evidence_file(ev)
    anchor = ev.split("#", 1)[1] if "#" in ev else ""
    shown = path
    if anchor:
        words = anchor.split("-")
        shown += "#" + ("-".join(words[:4]) + "…" if len(words) > 4 else anchor)
    href = _rel(path, from_dir) + (f"#{anchor}" if anchor else "")
    return f"[`{shown}`]({_url(href)})"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:12]


def legend(model: dict, views: list[dict]) -> list[str]:
    comps = [c for v in views for c in v["components"]]
    edges = [e for v in views for e in v["edges"]]
    kinds = {c["kind"] for c in comps}
    rows = ["| Shape | Colour | Meaning |", "|---|---|---|"]
    for kind in COMPONENT_KINDS:
        if kind in kinds:
            shape, colour, meaning = LEGEND_KINDS[kind]
            rows.append(f"| {shape} | {colour} | {meaning} |")
    if any(c.get("boundary") for c in comps):
        rows.append("| Dashed outline | — | Trust or deployment boundary |")
    edge_kinds = {e["kind"] for e in edges}
    for kind in EDGE_KINDS:
        if kind in edge_kinds:
            shape, meaning = LEGEND_EDGES[kind]
            rows.append(f"| {shape} | — | {meaning} |")
    if any("step" in e for e in edges):
        rows.append("| 1. 2. 3. before a label | — | The main path, in order |")
    if any(c.get("inferred") for c in comps):
        rows.append("| \"(inferred)\" after a name | — | Not confirmed in the code; see Assumptions |")
    return rows


def architecture_md(model: dict, model_rel: str, out_rel: str) -> str:
    """The full ARCHITECTURE.md. model_rel and out_rel are repo-relative POSIX paths."""
    meta, system = model["meta"], model["system"]
    views = resolve_views(model)
    boundaries = {b["id"]: b for b in model.get("boundaries") or []}
    comps = {c["id"]: c for c in model["components"]}
    model_link = _url(_rel(model_rel, out_rel))
    model_name = Path(model_rel).name

    out = [
        f"<!-- Generated by visualizer from {model_name}. Don't edit this file: edit the model, "
        f"or rerun /visualizer:architecture. -->",
        "",
        f"# {md_text(system['name'])} architecture",
        "",
        md_text(system["summary"].strip()),
        "",
    ]
    if system.get("highlights"):
        out += ["**Highlights**", ""] + [f"- {md_text(h)}" for h in system["highlights"]] + [""]

    for i, view in enumerate(views):
        out += [f"## {md_text(view['title'])}", "", "```mermaid", flowchart(model, view), "```", ""]
        if i == 0:
            out += legend(model, views) + [""]

    out += ["## Components", "",
            "| Component | Kind | Runs in | Responsibility | Evidence |",
            "|---|---|---|---|---|"]
    all_comps = [c for c, _ in _flatten(layout(model, model["components"]))]
    for c in all_comps:
        cell = f"**{md_cell(c['name'])}**"
        if c.get("inferred"):
            cell += " (inferred)"
        if c.get("tech"):
            cell += f"<br><sub>{md_cell(c['tech'])}</sub>"
        if c.get("members"):
            cell += f"<br><sub>Includes: {md_cell(', '.join(c['members']))}</sub>"
        where = _where(c, boundaries)
        evidence = ", ".join(evidence_link(ev, out_rel) for ev in c["evidence"]) or "none"
        out.append(f"| {cell} | {c['kind']} | {md_cell(where)} | {md_cell(c['responsibility'])} | {evidence} |")
    out.append("")

    stores = [c for c in all_comps if c.get("holds")]
    if stores:
        out += ["## Where state lives", "",
                "| Store | Runs in | Holds | Lifetime | Reached by |",
                "|---|---|---|---|---|"]
        for c in stores:
            reached = []
            for e in model.get("edges") or []:
                if e["to"] == c["id"] and comps[e["from"]]["name"] not in reached:
                    reached.append(comps[e["from"]]["name"])
            for h in c["holds"]:
                out.append(f"| **{md_cell(c['name'])}** | {md_cell(_where(c, boundaries))} | {md_cell(h['data'])} | "
                           f"{md_cell(h['lifetime'])} | {md_cell(', '.join(reached)) or 'none'} |")
        out.append("")

    flows = model.get("flows") or []
    if flows:
        out += ["## Flows", ""]
        for f in flows:
            out += [f"### {md_text(f['name'])}", ""]
            if f.get("description"):
                out += [md_text(f["description"].strip()), ""]
            out += ["```mermaid", sequence(model, f), "```", ""]

    inferred = [c for c in all_comps if c.get("inferred")]
    if inferred:
        out += ["## Assumptions", "", "These parts aren't confirmed by the code and were inferred:", ""]
        for c in inferred:
            why = ", ".join(evidence_link(ev, out_rel) for ev in c["evidence"])
            out.append(f"- **{md_text(c['name'])}**: {md_text(c['responsibility'])}" + (f" (from {why})" if why else ""))
        out.append("")

    notes = meta.get("notes") or []
    if notes:
        out += ["## Notes", "", "Kept exactly as written. They steer what the diagram includes.", ""]
        for n in notes:
            out += [f"> {line}" if line else ">" for line in n["text"].splitlines()]
            out += [">", f"> <sub>Added {n['added']}</sub>", ""]

    body = "\n".join(out)
    sha = meta.get("model_sha")
    as_of = f"Model as of commit `{sha[:7]}`" if sha else "Model not tied to a commit"
    if meta.get("model_date"):
        as_of += f" ({meta['model_date']})"
    footer = (f"\n---\n\n<sub>Generated by visualizer from [`{model_name}`]({model_link}). "
              f"{as_of}. Content hash `{content_hash(body)}`.</sub>\n")
    return body + footer


def _where(c: dict, boundaries: dict) -> str:
    names, cur = [], c.get("boundary")
    while cur is not None:
        names.insert(0, boundaries[cur]["name"])
        cur = boundaries[cur].get("parent")
    return " › ".join(names) if names else "—"


FOOTER_HASH_RE = re.compile(r"\n\n---\n\n<sub>Generated by visualizer from .*? Content hash `([0-9a-f]{12})`\.</sub>\n?$",
                            re.S)


def generated_state(text: str) -> str:
    """'clean', 'edited' (hash mismatch) or 'foreign' (no visualizer footer) for an existing ARCHITECTURE.md."""
    text = text.replace("\r\n", "\n")
    m = FOOTER_HASH_RE.search(text)
    if not m:
        return "foreign"
    return "clean" if content_hash(text[:m.start() + 1]) == m.group(1) else "edited"


# ---------------------------------------------------------------------------
# README block

MARKER = "visualizer"  # the architecture block; lib/datamodels uses "visualizer:data-models"
ANY_START_RE = re.compile(r"^<!-- visualizer(?::[a-z][a-z-]*)?:start(?: [a-z]+=\S*)* -->[ \t]*$")
ANY_END_RE = re.compile(r"^<!-- visualizer(?::[a-z][a-z-]*)?:end -->[ \t]*$")


def marker_res(marker: str = MARKER):
    """(start, end) regexes for one block's markers. Other visualizer blocks never match them."""
    m = re.escape(marker)
    return (re.compile(rf"^<!-- {m}:start(?P<attrs>(?: [a-z]+=\S*)*) -->[ \t]*$"),
            re.compile(rf"^<!-- {m}:end -->[ \t]*$"))


START_RE, END_RE = marker_res()


def readme_block_inner(model: dict, view: dict, readme_rel: str, arch_rel: str) -> str:
    link = _rel(arch_rel, str(Path(readme_rel).parent.as_posix()))
    return "\n".join([
        "## Architecture",
        "",
        "```mermaid",
        flowchart(model, view),
        "```",
        "",
        f"Components, data flows and where state lives: [{arch_rel}]({_url(link)})",
    ])


class ReadmeError(Exception):
    pass


def _marker_lines(lines: list[str], start_re=START_RE, end_re=END_RE):
    """Indexes of start and end marker lines, ignoring any inside fenced code blocks."""
    starts, ends, fence = [], [], None
    for i, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        m = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker[0] * len(marker)
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        if start_re.match(line):
            starts.append(i)
        elif end_re.match(line):
            ends.append(i)
    return starts, ends


def upsert_readme(text: str | None, inner: str, model_rel: str, placement: str = "after-intro",
                  insert: bool = False, create_title: str | None = None, force: bool = False,
                  marker: str = MARKER):
    """Return (new_text, status). Only the block between the markers is ever changed.

    marker names the block ("visualizer" for architecture), so blocks from different skills can share a README.

    status: unchanged | updated | inserted | created | missing (markers absent and insert=False)
            | no-readme (file absent and create_title is None).
    Raises ReadmeError for mismatched markers, a hand-edited block (unless force),
    or a placement heading that can't be found.
    """
    if text is None:
        if create_title is None:
            return None, "no-readme"
        text, status = f"# {create_title}\n", "created"
        insert = True
    else:
        status = None

    bom = "﻿" if text.startswith("﻿") else ""
    text = text[len(bom):]
    lines = text.splitlines(keepends=True)
    eol = "\r\n" if sum(l.endswith("\r\n") for l in lines) > len(lines) / 2 else "\n"
    attrs = f" path={quote(model_rel, safe='/-._~')} hash={content_hash(inner)}"
    block = [f"<!-- {marker}:start{attrs} -->"] + inner.split("\n") + [f"<!-- {marker}:end -->"]
    block = [line + eol for line in block]

    starts, ends = _marker_lines(lines, *marker_res(marker))
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
            raise ReadmeError(f"found {len(starts)} start and {len(ends)} end markers; expected one of each, "
                              "start first. Fix the README by hand, then re-render")
        s, e = starts[0], ends[0]
        old_inner = "".join(lines[s + 1:e]).replace("\r\n", "\n").rstrip("\n")
        m = re.search(r"\bhash=([0-9a-f]{12})\b", lines[s])
        if not force and old_inner != inner and (not m or content_hash(old_inner) != m.group(1)):
            raise ReadmeError("the README block was edited by hand since the last render. Fold the edits into "
                              "the model, or re-render with --force to drop them")
        new_lines = lines[:s] + block + lines[e + 1:]
        new_text = bom + "".join(new_lines)
        return new_text, ("unchanged" if new_text == bom + text else "updated")

    if not insert:
        return bom + text, "missing"

    at = _insert_at(lines, placement)
    before, after = lines[:at], lines[at:]
    if before and not before[-1].endswith(("\n", "\r")):
        before[-1] += eol
    pad_before = [eol] if before and before[-1].strip() else []
    pad_after = [eol] if after and after[0].strip() else []
    new_text = bom + "".join(before + pad_before + block + pad_after + after)
    return new_text, status or "inserted"


def _insert_at(lines: list[str], placement: str) -> int:
    at = _placement_line(lines, placement)
    # Never land inside another skill's block: go before it for before:, after it otherwise.
    starts, ends = _marker_lines(lines, ANY_START_RE, ANY_END_RE)
    for s, e in zip(starts, ends):
        if s < at <= e:
            return s if placement.startswith("before:") else e + 1
    return at


def _placement_line(lines: list[str], placement: str) -> int:
    heads = list(_heading_positions(lines))
    if placement == "end":
        return len(lines)
    if placement == "after-intro":
        seen_h1 = False
        for i, level, _ in heads:
            if level == 1 and not seen_h1:
                seen_h1 = True
                continue
            if level >= 2:
                return i
        return len(lines)
    how, _, title = placement.partition(":")
    matches = [(i, level) for i, level, text in heads if text.strip().lower() == title.strip().lower()]
    if not matches:
        raise ReadmeError(f"placement '{placement}': no heading '{title}' in the README")
    if len(matches) > 1:
        raise ReadmeError(f"placement '{placement}': {len(matches)} headings match '{title}'")
    i, level = matches[0]
    if how == "before":
        return i
    for j, lvl, _ in heads:
        if j > i and lvl <= level:
            return j
    return len(lines)


# ---------------------------------------------------------------------------
# Preview page


def mermaid_blocks(markdown: str) -> list[tuple[str, str]]:
    """(title, code) for each ```mermaid block, titled by the nearest heading above it."""
    blocks, title, code, inside = [], "diagram", [], False
    for line in markdown.splitlines():
        if not inside:
            m = re.match(r"^#{1,6}\s+(.*)", line)
            if m:
                title = m.group(1).strip()
            if re.match(r"^```mermaid\s*$", line):
                inside, code = True, []
        elif re.match(r"^```\s*$", line):
            blocks.append((title, "\n".join(code)))
            inside = False
        else:
            code.append(line)
    return blocks


PREVIEW_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>visualizer preview: __TITLE__</title>
<style>
  body { margin: 0; font: 14px -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }
  header { padding: 10px 16px; background: #f6f8fa; border-bottom: 1px solid #d0d7de; }
  section { padding: 16px 24px; }
  section.light { background: #ffffff; color: #1f2328; }
  section.dark { background: #0d1117; color: #e6edf3; }
  h2 { font-size: 15px; margin: 18px 0 8px; }
  .err { color: #cf222e; font-family: ui-monospace, monospace; white-space: pre-wrap; }
  body.only-light section.dark, body.only-dark section.light { display: none; }
</style></head>
<body>
<header>visualizer preview · <b>__TITLE__</b> · Mermaid <span id="ver">__MERMAID__</span> ·
  <span id="status">rendering…</span> · add <code>?theme=light</code> or <code>?theme=dark</code> to show one</header>
<section class="light" id="light"></section>
<section class="dark" id="dark"></section>
<script type="application/json" id="diagrams">__DIAGRAMS__</script>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@__MERMAID__/dist/mermaid.esm.min.mjs";
  const theme = new URLSearchParams(location.search).get("theme");
  if (theme) document.body.classList.add("only-" + theme);
  const diagrams = JSON.parse(document.getElementById("diagrams").textContent);
  const results = [];
  let n = 0;
  for (const [section, mermaidTheme] of [["light", "default"], ["dark", "dark"]]) {
    mermaid.initialize({ startOnLoad: false, theme: mermaidTheme, securityLevel: "strict" });
    const root = document.getElementById(section);
    for (const d of diagrams) {
      const h = document.createElement("h2");
      h.textContent = d.title;
      root.appendChild(h);
      const box = document.createElement("div");
      root.appendChild(box);
      try {
        await mermaid.parse(d.code);
        const { svg } = await mermaid.render("viz" + n++, d.code);
        box.innerHTML = svg;
        results.push({ title: d.title, theme: section, ok: true });
      } catch (err) {
        box.className = "err";
        box.textContent = String(err && err.message || err);
        results.push({ title: d.title, theme: section, ok: false, error: box.textContent });
      }
    }
  }
  const bad = results.filter(r => !r.ok).length;
  document.getElementById("status").textContent = bad ? bad + " failed" : "all " + results.length + " rendered";
  window.__viz = { mermaid: "__MERMAID__", results, done: true };
</script>
</body></html>
"""


def preview_html(title: str, diagrams: list[tuple[str, str]], version: str = MERMAID_VERSION) -> str:
    data = json.dumps([{"title": t, "code": c} for t, c in diagrams], ensure_ascii=False)
    data = data.replace("</", "<\\/")
    page = PREVIEW_HTML.replace("__MERMAID__", version).replace("__DIAGRAMS__", data)
    return page.replace("__TITLE__", title.replace("&", "&amp;").replace("<", "&lt;"))


# ---------------------------------------------------------------------------
# CLI


def load_model(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"error: {path} not found")
    except json.JSONDecodeError as e:
        sys.exit(f"error: {path} is not valid JSON: {e}")


def repo_root(model_path: Path, given: str | None) -> Path:
    if given:
        return Path(given).resolve()
    return git_toplevel(model_path.resolve().parent) or Path.cwd().resolve()


def print_report(rep: Report, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"ok": rep.ok, "errors": rep.errors, "warnings": rep.warnings}, indent=2))
        return
    for e in rep.errors:
        print(f"error: {e}")
    for w in rep.warnings:
        print(f"warning: {w}")
    print(f"{'ok' if rep.ok else 'failed'}: {len(rep.errors)} errors, {len(rep.warnings)} warnings")


def cmd_check(args) -> int:
    model_path = Path(args.model)
    model = load_model(model_path)
    rep = Report()
    validate(model, rep)
    if rep.ok and not args.no_files:
        check_repo(model, repo_root(model_path, args.root), rep)
    print_report(rep, args.json)
    return 0 if rep.ok else 1


def cmd_render(args) -> int:
    model_path = Path(args.model)
    model = load_model(model_path)
    rep = Report()
    validate(model, rep)
    if not rep.ok:
        print_report(rep, False)
        print("render: fix the model first (viz.py check)", file=sys.stderr)
        return 1

    root = repo_root(model_path, args.root)
    model_abs = model_path.resolve()
    out_dir = Path(args.out).resolve() if args.out else model_abs.parent
    try:
        model_rel = model_abs.relative_to(root).as_posix()
        out_rel = out_dir.relative_to(root).as_posix()
    except ValueError:
        print(f"render: the model and output directory must be inside the repo root {root}", file=sys.stderr)
        return 2
    out_rel = "" if out_rel == "." else out_rel
    arch_rel = f"{out_rel}/ARCHITECTURE.md" if out_rel else "ARCHITECTURE.md"
    writes: list[tuple[Path, str, str]] = []

    arch_path = root / arch_rel
    arch = architecture_md(model, model_rel, out_rel)
    if arch_path.exists():
        existing = arch_path.read_text(encoding="utf-8")
        state = generated_state(existing)
        if existing == arch:
            print(f"{arch_rel}: unchanged")
        elif state != "clean" and not args.force:
            why = "was edited by hand" if state == "edited" else "wasn't generated by visualizer"
            print(f"render: {arch_rel} {why}. Fold the edits into the model, or use --force to overwrite",
                  file=sys.stderr)
            return 3
        else:
            writes.append((arch_path, arch, "updated"))
    else:
        writes.append((arch_path, arch, "created"))

    readme = model["meta"].get("readme") or {"embed": False}
    if readme.get("embed"):
        readme_rel = readme.get("path", "README.md")
        readme_path = root / readme_rel
        view = resolve_views(model)[0]
        inner = readme_block_inner(model, view, readme_rel, arch_rel)
        old = readme_path.read_bytes().decode("utf-8") if readme_path.exists() else None
        try:
            new, status = upsert_readme(
                old, inner, model_rel, readme.get("placement", "after-intro"), insert=args.readme_insert,
                create_title=model["system"]["name"] if args.readme_create else None, force=args.force)
        except ReadmeError as e:
            print(f"render: {readme_rel}: {e}", file=sys.stderr)
            return 3
        if status == "missing":
            print(f"{readme_rel}: no visualizer markers; not changed (use --readme-insert to add the block)")
        elif status == "no-readme":
            print(f"{readme_rel}: doesn't exist; not created (use --readme-create)")
        elif status == "unchanged":
            print(f"{readme_rel}: unchanged")
        else:
            writes.append((readme_path, new, f"{status} block"))

    for path, text, status in writes:
        rel = path.relative_to(root).as_posix()
        if args.dry_run:
            print(f"{rel}: would be {status}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        print(f"{rel}: {status}")
    return 0


def cmd_preview(args) -> int:
    src = Path(args.file)
    if src.suffix == ".json":
        model = load_model(src)
        rep = Report()
        validate(model, rep)
        if not rep.ok:
            print_report(rep, False)
            return 1
        diagrams = [(v["title"], flowchart(model, v)) for v in resolve_views(model)]
        diagrams += [(f["name"], sequence(model, f)) for f in model.get("flows") or []]
        title = model["system"]["name"]
    else:
        diagrams = mermaid_blocks(src.read_text(encoding="utf-8"))
        title = src.name
    out = Path(args.out) if args.out else src.with_name(src.stem + ".preview.html")
    out.write_text(preview_html(title, diagrams, args.mermaid), encoding="utf-8")
    print(f"{out}: {len(diagrams)} diagrams, Mermaid {args.mermaid}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="viz.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"visualizer {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("check", help="validate a model")
    p.add_argument("model")
    p.add_argument("--root", help="repo root that evidence paths are relative to (default: git top level)")
    p.add_argument("--no-files", action="store_true", help="skip checks that read the repo")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("render", help="write ARCHITECTURE.md and update the README block")
    p.add_argument("model")
    p.add_argument("--root", help="repo root (default: git top level)")
    p.add_argument("--out", help="directory for ARCHITECTURE.md (default: next to the model)")
    p.add_argument("--readme-insert", action="store_true",
                   help="add the README block at meta.readme.placement if the markers are missing")
    p.add_argument("--readme-create", action="store_true", help="create the README if it doesn't exist")
    p.add_argument("--force", action="store_true", help="overwrite hand edits in generated output")
    p.add_argument("--dry-run", action="store_true", help="say what would change without writing")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("preview", help="write an HTML page that renders the diagrams in light and dark")
    p.add_argument("file", help="a model (.json) or a Markdown file with ```mermaid blocks")
    p.add_argument("--out", help="output HTML path (default: next to the input)")
    p.add_argument("--mermaid", default=MERMAID_VERSION, help=f"Mermaid version (default {MERMAID_VERSION})")
    p.set_defaults(func=cmd_preview)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
