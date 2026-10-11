"""`authtrace run -- CMD`: run a command with capture hooks in every Python process it starts.

The command runs unchanged, with three additions to its environment: PYTHONPATH gains
capture/boot (a sitecustomize) and lib/ (this package), and AUTHTRACE_* says where to write.
Child processes inherit all of it, so a test runner's subprocesses are captured too.

Outputs go to a directory that git ignores: `.authtrace/<run>/` by default, with a
`.authtrace/.gitignore` of `*` so the target's `git status` stays unchanged.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from .redact import LEVELS

LIB = Path(__file__).resolve().parent.parent
BOOT = Path(__file__).resolve().parent / "capture" / "boot"
SECRET_ARG = re.compile(r"(passw|secret|token|key|credential)", re.I)


class RunError(Exception):
    pass


def default_out(cwd: Path) -> Path:
    return cwd / ".authtrace" / datetime.now().strftime("run-%Y%m%d-%H%M%S")


def ensure_ignored(out: Path) -> None:
    """Make sure git won't see the outputs; refuse a tracked, non-ignored location."""
    out = out.resolve()
    for parent in [out, *out.parents]:
        if parent.name == ".authtrace":
            gi = parent / ".gitignore"
            if not gi.exists():
                parent.mkdir(parents=True, exist_ok=True)
                gi.write_text("# authtrace outputs: never commit these\n*\n", encoding="utf-8")
            return
    existing = next(p for p in [out, *out.parents] if p.exists())
    try:
        top = subprocess.run(["git", "-C", str(existing), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True)
    except OSError:
        return  # no git: nothing to protect
    if top.returncode != 0:
        return
    probe = out / "exchanges.jsonl"
    ignored = subprocess.run(["git", "-C", top.stdout.strip(), "check-ignore", "-q", str(probe)])
    if ignored.returncode != 0:
        raise RunError(f"{out} is inside the git repo at {top.stdout.strip()} and isn't ignored. "
                       f"Use .authtrace/ (the default) or a directory outside the repo.")


def safe_argv(cmd: list[str]) -> list[str]:
    """The command as recorded: values after secret-looking flags are masked."""
    out, hide = [], False
    for arg in cmd:
        if hide:
            out.append("***")
            hide = False
        elif arg.startswith("-") and "=" in arg and SECRET_ARG.search(arg.split("=", 1)[0]):
            out.append(arg.split("=", 1)[0] + "=***")
        else:
            out.append(arg)
            hide = arg.startswith("-") and bool(SECRET_ARG.search(arg))
    return out


def capture_env(out_file: Path, level: str, t0: float, base: dict | None = None) -> dict:
    env = dict(os.environ if base is None else base)
    path = [str(BOOT), str(LIB)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    env.update(PYTHONPATH=os.pathsep.join(path), AUTHTRACE_OUT=str(out_file), AUTHTRACE_REDACTION=level,
               AUTHTRACE_T0=repr(t0))
    return env


def run(cmd: list[str], out: Path, level: str = "dev") -> tuple[int, Path]:
    if not cmd:
        raise RunError("nothing to run: put the command after --, e.g. authtrace run -- pytest -m live")
    if level not in LEVELS:
        raise RunError(f"--redaction must be one of {', '.join(LEVELS)}")
    ensure_ignored(out)
    out.mkdir(parents=True, exist_ok=True)
    out_file = out / "exchanges.jsonl"
    if level == "raw":
        sys.stderr.write(f"authtrace: RAW capture. Token and secret values will be written to {out_file}. "
                         f"Keep it local and delete it when you're done.\n")
    t0 = time.time()
    meta = {"tool": "authtrace", "version": __version__, "command": safe_argv(cmd), "cwd": os.getcwd(),
            "started": datetime.now().astimezone().isoformat(timespec="seconds"), "t0": t0,
            "mechanisms": ["run-hooks"], "redaction": level}
    try:
        code = subprocess.call(cmd, env=capture_env(out_file, level, t0))
    except FileNotFoundError:
        raise RunError(f"command not found: {cmd[0]}") from None
    except KeyboardInterrupt:
        code = 130
    meta.update(ended=datetime.now().astimezone().isoformat(timespec="seconds"),
                seconds=round(time.time() - t0, 3), exit_code=code)
    write_private(out / "run.json", json.dumps(meta, indent=1) + "\n")
    return code, out_file


def write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarize(path: Path) -> str:
    """The run as a log: one line per exchange in time order, then processes and problems."""
    lines = read_lines(path)
    xs = sorted((r for r in lines if r["kind"] == "exchange"), key=lambda r: r["t0"])
    out = [f"{len(xs)} exchange(s) → {path}"]
    for x in xs:
        status = x.get("status") or x.get("error", "no response")
        toks = f"  tokens {','.join(sorted({t['fp'] for t in x.get('tokens', [])}))}" if x.get("tokens") else ""
        out.append(f"  [{x['t0']:7.3f}s] {x.get('proc', '?')[:12]:<12} {x['method']:<6} {x['url'][:90]} → {status}{toks}")
    procs = [r for r in lines if r["kind"] == "process"]
    silent = [p for p in procs if not p["exchanges"]]
    if silent:
        out.append(f"  {len(silent)} Python process(es) made no HTTP calls: "
                   + ", ".join(sorted({p['proc'] for p in silent})))
    if not procs and not xs:
        out.append("  No Python process reported in. Was the command a Python program, run without -I or -E?")
    errors = [r for r in lines if r["kind"] == "hook-error"]
    if errors:
        out.append(f"  {len(errors)} hook error(s); see kind=hook-error lines in {path.name}")
    return "\n".join(out)
