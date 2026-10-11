"""datamodels.py: doctor, introspect, merge, check and render.

  datamodels.py doctor                  what this machine can introspect
  datamodels.py introspect ENGINE ...   read one store's metadata into a snapshot (read-only)
  datamodels.py merge MODEL --snapshot  fold snapshots into the model and print what changed
  datamodels.py check MODEL             validate the model against the schema and the repo
  datamodels.py render MODEL            write DATA_MODELS.md and update the README block
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__, merge as merging, model as m, render as rendering, sources
from ._viz import viz

LAUNCHER = Path(__file__).resolve().parents[2] / "scripts" / "datamodels.py"
IN_UV = "DATAMODELS_IN_UV"


# ---------------------------------------------------------------------------
# Connection targets: read by name, never printed


_REF_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?::?-([^}]*))?\}|([A-Za-z_][A-Za-z0-9_]*))")


def read_env_file(path: Path, name: str) -> str | None:
    """One variable's value from a dotenv file, with ${VAR}, ${VAR:-default} and $VAR expanded the way
    docker compose does (earlier lines in the file, then the environment). Single-quoted values are literal.
    Only the requested value is returned; nothing in the file is printed.
    """
    seen: dict[str, str] = {}

    def expand(value: str) -> str:
        def sub(m):
            ref, default = m.group(1) or m.group(3), m.group(2)
            found = seen.get(ref, os.environ.get(ref))
            return found if found else (default or "")
        return _REF_RE.sub(sub, value)

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not match:
            continue
        key, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] == "'":
            value = value[1:-1]
        else:
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            elif " #" in value:
                value = value.split(" #", 1)[0].rstrip()
            value = expand(value)
        seen[key] = value
        if key == name:
            return value
    return None


class UsageError(Exception):
    pass


def resolve_url(args) -> str:
    if not args.url_env:
        raise UsageError("introspect: name the connection's environment variable with --url-env")
    if args.env_file:
        path = Path(args.env_file)
        if not path.is_file():
            raise UsageError(f"introspect: {args.env_file} not found")
        value = read_env_file(path, args.url_env)
        where = f"{args.env_file}"
    else:
        value, where = os.environ.get(args.url_env), "the environment"
    if not value:
        raise UsageError(f"introspect: {args.url_env} is not set in {where}")
    return value


def redact(text: str, secret: str | None) -> str:
    """Errors from drivers can echo the URL; mask it, its password, and any credentials in URLs."""
    if secret:
        text = text.replace(secret, "<connection url>")
        password = urlsplit(secret).password if "://" in secret else None
        if password:
            text = text.replace(password, "***")
    return re.sub(r"(://[^/\s:@]+):[^/\s@]+@", r"\1:***@", text)


# ---------------------------------------------------------------------------
# Commands


def cmd_doctor(args) -> int:
    uv = shutil.which("uv")
    rows = []
    for engine in sources.SOURCES:
        src = sources.load(engine)
        ready = not src.REQUIRES or bool(uv)
        rows.append({"engine": engine, "ready": ready, "requires": list(src.REQUIRES),
                     "missing": [] if ready else ["uv (https://docs.astral.sh/uv/)"]})
    result = {"version": __version__, "python": platform.python_version(), "uv": uv, "sources": rows}
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"datamodels {__version__} doctor\n")
    print(f"  python  {result['python']}")
    print(f"  uv      {uv or 'not found'}\n")
    for r in rows:
        needs = "stdlib" if not r["requires"] else "uv run --with " + " ".join(r["requires"])
        state = "ready" if r["ready"] else "missing: " + "; ".join(r["missing"])
        print(f"  {r['engine']:<9} {state:<10}  {needs}")
    return 0


def _reexec_under_uv(src, argv: list[str]) -> int:
    uv = shutil.which("uv")
    if not uv:
        print(f"introspect: {src.ENGINE} needs uv to fetch its driver ({', '.join(src.REQUIRES)}). "
              "Install uv (https://docs.astral.sh/uv/), or run `datamodels.py doctor`", file=sys.stderr)
        return 2
    cmd = [uv, "run", "--no-project", "--quiet", "--python", sys.executable]
    for req in src.REQUIRES:
        cmd += ["--with", req]
    cmd += ["python", str(LAUNCHER), *argv]
    return subprocess.run(cmd, env={**os.environ, IN_UV: "1"}).returncode


def cmd_introspect(args, argv: list[str]) -> int:
    src = sources.load(args.engine)
    viz_rep = viz.Report()
    viz._check_id(args.store, "--store", viz_rep)
    if not viz_rep.ok:
        print(f"introspect: {viz_rep.errors[0]}", file=sys.stderr)
        return 2
    if args.engine == "sqlite":
        if not args.path:
            print("introspect: sqlite needs --path FILE", file=sys.stderr)
            return 2
        target, secret = args.path, None
    else:
        try:
            target = secret = resolve_url(args)
        except UsageError as e:
            print(e, file=sys.stderr)
            return 2
        if src.REQUIRES and os.environ.get(IN_UV) != "1":
            return _reexec_under_uv(src, argv)

    exclude = list(args.exclude or [])
    if args.engine in ("postgres", "sqlite") and not args.all_tables:
        exclude += [t for t in sources.BOOKKEEPING if t not in exclude]
    scope = sources.Scope(args.schema or [], args.include or [], exclude, args.sample, args.max_keys)
    try:
        conn = src.connect(target)
    except Exception as e:
        print(f"introspect: couldn't connect to {args.store}: {redact(str(e), secret)}", file=sys.stderr)
        return 2
    try:
        body = src.introspect(conn, scope)
    except Exception as e:
        print(f"introspect: reading {args.store} failed: {redact(str(e), secret)}", file=sys.stderr)
        return 2
    finally:
        try:
            src.close(conn)
        except Exception:
            pass

    snapshot = {"generator": m.GENERATOR, "snapshot_version": sources.SNAPSHOT_VERSION, "engine": args.engine,
                "store": args.store, "taken": datetime.date.today().isoformat(),
                "server_version": body.get("server_version"), "sampled": src.SAMPLED,
                "scope": scope.as_dict(), "entities": body["entities"], "relationships": body["relationships"]}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{out}: {len(snapshot['entities'])} entities from {args.store} "
          f"({args.engine} {snapshot['server_version'] or 'unknown version'})")
    return 0


def load_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"error: {what} {path} not found")
    except json.JSONDecodeError as e:
        raise SystemExit(f"error: {path} is not valid JSON: {e}")


def write_model(path: Path, model: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(model, indent=2, ensure_ascii=False) + "\n")


def cmd_merge(args) -> int:
    path = Path(args.model)
    if path.exists():
        if args.init:
            print(f"merge: {path} already exists; drop --init to refresh it", file=sys.stderr)
            return 2
        model = load_json(path, "model")
    elif args.init:
        if not args.name:
            print("merge: --init needs --name (the system's name, usually the repo's)", file=sys.stderr)
            return 2
        model = merging.new_model(args.name)
    else:
        print(f"merge: {path} doesn't exist; pass --init --name NAME to create it", file=sys.stderr)
        return 2

    all_changes = []
    for snap_path in args.snapshot:
        try:
            all_changes.append(merging.merge(model, load_json(Path(snap_path), "snapshot")))
        except (merging.MergeError, KeyError) as e:
            print(f"merge: {snap_path}: {e}", file=sys.stderr)
            return 1
    write_model(path, model)
    if args.json:
        print(json.dumps(all_changes, indent=2))
    else:
        for changes in all_changes:
            print("\n".join(merging.format_changes(changes)))
        print(f"{path}: {'created' if args.init else 'updated'}")
    return 0


def repo_root(model_path: Path, given: str | None) -> Path:
    if given:
        return Path(given).resolve()
    return viz.git_toplevel(model_path.resolve().parent) or Path.cwd().resolve()


def cmd_check(args) -> int:
    path = Path(args.model)
    model = load_json(path, "model")
    rep = viz.Report()
    m.validate(model, rep)
    if rep.ok and not args.no_files:
        m.check_repo(model, repo_root(path, args.root), rep)
    findings = merging.drift(model) if rep.ok else []
    if args.json:
        print(json.dumps({"ok": rep.ok, "errors": rep.errors, "warnings": rep.warnings,
                          "drift": [{"entity": e["id"], "field": f, "finding": d} for e, f, d in findings]},
                         indent=2))
        return 0 if rep.ok else 1
    for e in rep.errors:
        print(f"error: {e}")
    for w in rep.warnings:
        print(f"warning: {w}")
    for e, f, d in findings:
        print(f"drift: {e['id']}{'.' + f if f else ''}: {d}")
    print(f"{'ok' if rep.ok else 'failed'}: {len(rep.errors)} errors, {len(rep.warnings)} warnings, "
          f"{len(findings)} drift findings")
    return 0 if rep.ok else 1


def cmd_render(args) -> int:
    path = Path(args.model)
    model = load_json(path, "model")
    rep = viz.Report()
    m.validate(model, rep)
    if not rep.ok:
        for e in rep.errors:
            print(f"error: {e}")
        print("render: fix the model first (datamodels.py check)", file=sys.stderr)
        return 1

    root = repo_root(path, args.root)
    model_abs = path.resolve()
    out_dir = Path(args.out).resolve() if args.out else model_abs.parent
    try:
        model_rel = model_abs.relative_to(root).as_posix()
        out_rel = out_dir.relative_to(root).as_posix()
    except ValueError:
        print(f"render: the model and output directory must be inside the repo root {root}", file=sys.stderr)
        return 2
    out_rel = "" if out_rel == "." else out_rel
    doc_rel = f"{out_rel}/DATA_MODELS.md" if out_rel else "DATA_MODELS.md"
    writes: list[tuple[Path, str, str]] = []

    doc_path = root / doc_rel
    doc = rendering.data_models_md(model, model_rel, out_rel)
    if doc_path.exists():
        existing = doc_path.read_text(encoding="utf-8")
        state = viz.generated_state(existing)
        if existing == doc:
            print(f"{doc_rel}: unchanged")
        elif state != "clean" and not args.force:
            why = "was edited by hand" if state == "edited" else "wasn't generated by visualizer"
            print(f"render: {doc_rel} {why}. Fold the edits into the model, or use --force to overwrite",
                  file=sys.stderr)
            return 3
        else:
            writes.append((doc_path, doc, "updated"))
    else:
        writes.append((doc_path, doc, "created"))

    readme = model["meta"].get("readme") or {"embed": False}
    if readme.get("embed"):
        readme_rel = readme.get("path", "README.md")
        readme_path = root / readme_rel
        inner = rendering.readme_block_inner(model, readme_rel, doc_rel)
        old = readme_path.read_bytes().decode("utf-8") if readme_path.exists() else None
        try:
            new, status = viz.upsert_readme(
                old, inner, model_rel, readme.get("placement", "after-intro"), insert=args.readme_insert,
                create_title=model["system"]["name"] if args.readme_create else None, force=args.force,
                marker=rendering.MARKER)
        except viz.ReadmeError as e:
            print(f"render: {readme_rel}: {e}", file=sys.stderr)
            return 3
        if status == "missing":
            print(f"{readme_rel}: no data-models markers; not changed (use --readme-insert to add the block)")
        elif status == "no-readme":
            print(f"{readme_rel}: doesn't exist; not created (use --readme-create)")
        elif status == "unchanged":
            print(f"{readme_rel}: unchanged")
        else:
            writes.append((readme_path, new, f"{status} block"))

    for target, text, status in writes:
        rel = target.relative_to(root).as_posix()
        if args.dry_run:
            print(f"{rel}: would be {status}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        print(f"{rel}: {status}")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="datamodels.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"datamodels {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("doctor", help="say which sources this machine can introspect")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("introspect", help="read one store's metadata into a snapshot, read-only")
    p.add_argument("engine", choices=sources.SOURCES)
    p.add_argument("--store", required=True, help="the store's id in the model (kebab-case)")
    p.add_argument("--url-env", help="environment variable holding the connection URL (never the URL itself)")
    p.add_argument("--env-file", help="read --url-env from this dotenv file instead of the environment")
    p.add_argument("--path", help="the database file (sqlite)")
    p.add_argument("--schema", action="append", help="schema (Postgres) or database (MongoDB); repeatable")
    p.add_argument("--include", action="append", help="glob over entity names; repeatable")
    p.add_argument("--exclude", action="append", help="glob over entity names; repeatable")
    p.add_argument("--sample", type=int, help="documents per collection, or keys per Redis pattern")
    p.add_argument("--max-keys", type=int, help="Redis: stop scanning after this many keys (default 10000)")
    p.add_argument("--all-tables", action="store_true",
                   help="keep migration bookkeeping tables (alembic_version, django_migrations, ...)")
    p.add_argument("--out", required=True, help="snapshot path; keep it outside the repo or untracked")

    p = sub.add_parser("merge", help="fold snapshots into the model and print what changed")
    p.add_argument("model")
    p.add_argument("--snapshot", action="append", required=True)
    p.add_argument("--init", action="store_true", help="create the model")
    p.add_argument("--name", help="system name, with --init")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("check", help="validate a model")
    p.add_argument("model")
    p.add_argument("--root", help="repo root that evidence paths are relative to (default: git top level)")
    p.add_argument("--no-files", action="store_true", help="skip checks that read the repo")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("render", help="write DATA_MODELS.md and update the README block")
    p.add_argument("model")
    p.add_argument("--root", help="repo root (default: git top level)")
    p.add_argument("--out", help="directory for DATA_MODELS.md (default: next to the model)")
    p.add_argument("--readme-insert", action="store_true",
                   help="add the README block at meta.readme.placement if the markers are missing")
    p.add_argument("--readme-create", action="store_true", help="create the README if it doesn't exist")
    p.add_argument("--force", action="store_true", help="overwrite hand edits in generated output")
    p.add_argument("--dry-run", action="store_true", help="say what would change without writing")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "introspect":
        return cmd_introspect(args, argv)
    return {"doctor": cmd_doctor, "merge": cmd_merge, "check": cmd_check, "render": cmd_render}[args.command](args)
