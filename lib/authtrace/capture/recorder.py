"""The traced process's side of a capture: redact each exchange and append it to exchanges.jsonl.

Every hook turns a request into a `Pending`, feeds it the response as it arrives, and calls
`finish()`. Nothing raw leaves this module: redaction runs before the write. A hook failure is
recorded and swallowed, because a tracer must never break the program it watches.

Lines in exchanges.jsonl (schema 0):
  {"kind": "exchange", "id": "<pid>-<seq>", "t0", "t1", "proc", "lib", "method", "url", "status",
   "req": {"headers", "body"}, "res": {"headers", "body"}, "tokens": [...], "seen_by", "confidence"}
  {"kind": "process", "pid", "ppid", "proc", "argv0", "exchanges"}      written at exit
  {"kind": "hook-error", "pid", "where", "error"}
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import threading
import time
import traceback
import zlib

from ..redact import Redactor

BODY_CAP = 256 * 1024
_lock = threading.Lock()


def proc_name() -> str:
    if os.environ.get("AUTHTRACE_PROC"):
        return os.environ["AUTHTRACE_PROC"]
    argv0 = (getattr(sys, "argv", None) or [""])[0]
    base = os.path.basename(argv0)
    if base == "__main__.py":  # python -m pytest → .../pytest/__main__.py
        base = os.path.basename(os.path.dirname(argv0))
    return base or "python"


def _header(headers, name: str) -> str:
    return next((v for k, v in headers if k.lower() == name), "")


def decode_body(body: bytes, encoding: str):
    """Undo gzip/deflate: both hooks sit below the client's decompression. Partial bodies work."""
    enc = encoding.strip().lower()
    if not body or enc in ("", "identity"):
        return body
    if enc in ("gzip", "x-gzip", "deflate"):
        try:
            return zlib.decompressobj(zlib.MAX_WBITS | 32).decompress(body)  # 32: gzip or zlib header
        except zlib.error:
            try:
                return zlib.decompressobj(-zlib.MAX_WBITS).decompress(body)  # raw deflate
            except zlib.error:
                pass
    return {"encoded": enc, "len": len(body)}  # br, zstd: no stdlib decoder


class Recorder:
    def __init__(self, path: str, level: str = "dev", t0: float | None = None):
        self.path = path
        self.redactor = Redactor(level)
        self.t0 = t0 if t0 is not None else time.time()
        self.seq = 0
        self.pending: set = set()
        atexit.register(self.close)

    def now(self) -> float:
        return round(time.time() - self.t0, 4)

    def write(self, record: dict) -> None:
        line = (json.dumps(record, ensure_ascii=False, default=str) + "\n").encode()
        with _lock:
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)

    def hook_error(self, where: str) -> None:
        try:
            self.write({"kind": "hook-error", "pid": os.getpid(), "where": where,
                        "error": traceback.format_exc(limit=3)[-600:]})
        except Exception:
            pass

    def begin(self, lib: str) -> Pending:
        with _lock:
            self.seq += 1
            seq = self.seq
        p = Pending(self, lib, seq)
        self.pending.add(p)
        return p

    def close(self) -> None:
        for p in list(self.pending):
            p.finish(incomplete=True)
        try:
            self.write({"kind": "process", "pid": os.getpid(), "ppid": os.getppid(), "proc": proc_name(),
                        "argv0": os.path.basename((getattr(sys, "argv", None) or [""])[0]), "exchanges": self.seq})
        except Exception:
            pass


class Pending:
    """One request in flight. Hooks call request(), response(), chunk() and finish()."""

    def __init__(self, rec: Recorder, lib: str, seq: int):
        self.rec, self.lib, self.seq = rec, lib, seq
        self.t0 = rec.now()
        self.method = self.url = None
        self.req_headers: list = []
        self.req_body = None
        self.status = None
        self.res_headers: list = []
        self.body = bytearray()
        self.truncated = False
        self.done = False

    def request(self, method: str, url: str, headers, body) -> None:
        self.method, self.url, self.req_headers, self.req_body = method, url, list(headers), body

    def response(self, status: int, headers) -> None:
        self.status, self.res_headers = status, list(headers)

    def chunk(self, data) -> None:
        room = BODY_CAP - len(self.body)
        if room > 0:
            self.body += bytes(data[:room])
        if len(data) > room:
            self.truncated = True

    def finish(self, error: BaseException | None = None, incomplete: bool = False) -> None:
        if self.done:
            return
        self.done = True
        self.rec.pending.discard(self)
        try:
            self.rec.write(self.record(error, incomplete))
        except Exception:
            self.rec.hook_error(f"{self.lib} finish")

    def record(self, error, incomplete) -> dict:
        scan = self.rec.redactor.scan()
        req = {"headers": scan.headers(self.req_headers, "req"),
               "body": scan.body(self.req_body, _header(self.req_headers, "content-type"), "req.body")}
        body = decode_body(bytes(self.body), _header(self.res_headers, "content-encoding")) or None
        res = {"headers": scan.headers(self.res_headers, "res"),
               "body": body if isinstance(body, dict) else scan.body(body, _header(self.res_headers, "content-type"), "res.body")}
        rec = {"kind": "exchange", "id": f"{os.getpid()}-{self.seq}", "t0": self.t0, "t1": self.rec.now(),
               "proc": proc_name(), "pid": os.getpid(), "lib": self.lib, "method": self.method,
               "url": scan.url(self.url or ""), "status": self.status, "req": req, "res": res,
               "tokens": scan.refs, "seen_by": "run-hooks", "confidence": "observed"}
        if self.truncated:
            rec["truncated"] = True
        if incomplete:
            rec["incomplete"] = True  # still streaming when the process exited
        if error is not None:
            rec["error"] = f"{type(error).__name__}: {error}"[:300]
        return rec


_recorder: Recorder | None = None


def get() -> Recorder | None:
    """The process-wide recorder, configured from the environment `authtrace run` set."""
    global _recorder
    if _recorder is None and os.environ.get("AUTHTRACE_OUT"):
        t0 = os.environ.get("AUTHTRACE_T0")
        _recorder = Recorder(os.environ["AUTHTRACE_OUT"], os.environ.get("AUTHTRACE_REDACTION", "dev"),
                             float(t0) if t0 else None)
    return _recorder
