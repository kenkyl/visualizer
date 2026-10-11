"""Hook for the stdlib's http.client, which urllib, urllib3 and requests all send through.

It records what actually goes on the wire: the request line, every header (including ones an
auth handler added), the body, and the response as the caller reads it. Reading is never forced;
a response that's never read to the end is written at exit, marked incomplete.
"""

from __future__ import annotations

import threading

_depth = threading.local()


def _s(v) -> str:
    return v.decode("latin-1") if isinstance(v, (bytes, bytearray)) else str(v)


def _url(conn, target: str) -> str:
    if target.startswith(("http://", "https://")):  # absolute form, through a proxy
        return target
    # urllib3's HTTPSConnection doesn't subclass http.client's, but both default to 443.
    https = getattr(conn, "default_port", 80) == 443
    host = getattr(conn, "_tunnel_host", None) or conn.host
    port = getattr(conn, "_tunnel_port", None) or conn.port
    default = 443 if https else 80
    netloc = host if port in (None, default) else f"{host}:{port}"
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]" if port in (None, default) else f"[{host}]:{port}"
    return f"{'https' if https else 'http'}://{netloc}{target}"


def install(mod, rec) -> None:
    C, R = mod.HTTPConnection, mod.HTTPResponse
    if getattr(C, "_authtrace", False):
        return
    C._authtrace = True
    putrequest, putheader, endheaders, send, getresponse = (
        C.putrequest, C.putheader, C.endheaders, C.send, C.getresponse)

    def _putrequest(self, method, url, *a, **kw):
        try:
            self._at = {"p": rec.begin("http.client"), "method": method, "url": url, "headers": [],
                        "body": [], "sent": False}
        except Exception:
            rec.hook_error("http.client putrequest")
        return putrequest(self, method, url, *a, **kw)

    def _putheader(self, header, *values):
        st = self.__dict__.get("_at")
        if st is not None:
            st["headers"].append((_s(header), ", ".join(_s(v) for v in values)))
        return putheader(self, header, *values)

    def _endheaders(self, message_body=None, **kw):
        st = self.__dict__.get("_at")
        if st is not None and isinstance(message_body, (bytes, bytearray, str)):
            st["body"].append(message_body)
        result = endheaders(self, message_body, **kw)
        if st is not None:
            st["sent"] = True
        return result

    def _send(self, data):
        st = self.__dict__.get("_at")
        if st is not None and st["sent"] and isinstance(data, (bytes, bytearray, str)):
            st["body"].append(data)  # body chunks sent after the headers (urllib3 does this)
        return send(self, data)

    def _getresponse(self, *a, **kw):
        st = self.__dict__.pop("_at", None)
        p = st and st["p"]
        if p:
            try:
                body = b"".join(b.encode() if isinstance(b, str) else bytes(b) for b in st["body"]) or None
                p.request(st["method"], _url(self, st["url"]), st["headers"], body)
            except Exception:
                rec.hook_error("http.client request")
        try:
            resp = getresponse(self, *a, **kw)
        except BaseException as e:
            if p:
                p.finish(error=e)
            raise
        if p:
            p.response(resp.status, resp.getheaders())
            resp._at = p
            if resp.fp is None:  # no body (HEAD, 204, 304): already complete
                p.finish()
        return resp

    C.putrequest, C.putheader, C.endheaders, C.send, C.getresponse = (
        _putrequest, _putheader, _endheaders, _send, _getresponse)

    # Response side: tee what the caller reads. read() calls readinto() internally, so only the
    # outermost call on a thread records, or bytes would be counted twice.
    def tee(name):
        orig = getattr(R, name)

        def wrapper(self, *a, **kw):
            p = self.__dict__.get("_at")
            if p is None or getattr(_depth, "n", 0):
                return orig(self, *a, **kw)
            _depth.n = 1
            try:
                result = orig(self, *a, **kw)
            finally:
                _depth.n = 0
            try:
                data = a[0][:result] if name == "readinto" and result else result
                if data:
                    p.chunk(data)
                if self.fp is None or not result:
                    p.finish()
            except Exception:
                rec.hook_error(f"http.client {name}")
            return result

        setattr(R, name, wrapper)

    for name in ("read", "read1", "readinto", "readline"):  # not peek: it doesn't consume
        if hasattr(R, name):
            tee(name)

    close = R.close

    def _close(self):
        p = self.__dict__.pop("_at", None)
        close(self)
        if p:
            p.finish()

    R.close = _close
