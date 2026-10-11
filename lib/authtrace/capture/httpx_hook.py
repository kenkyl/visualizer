"""Hook for httpx and httpx2 (same API), at the transport: below the client's auth flow.

Patching `AsyncClient.send` sees a request before `auth=` runs, which is how the 2026-10-03
tracer missed authlib's `Authorization: Basic` (reference/browser-flow/README.md, gap 1). The
transport sees the final wire request. Response bodies are teed as the caller streams them, so
an MCP event stream is recorded without the hook ever blocking on it.
"""

from __future__ import annotations


def _request(p, request) -> None:
    try:
        body = request.content
    except Exception:  # a streaming request body that hasn't been read
        body = None
    p.request(request.method, str(request.url), request.headers.multi_items(), body)


def install(mod, rec, lib: str) -> None:
    T, AT = mod.HTTPTransport, mod.AsyncHTTPTransport
    if getattr(T, "_authtrace", False):
        return
    T._authtrace = AT._authtrace = True

    class SyncTee(mod.SyncByteStream):
        def __init__(self, inner, p):
            self.inner, self.p = inner, p

        def __iter__(self):
            for chunk in self.inner:
                self.p.chunk(chunk)
                yield chunk
            self.p.finish()

        def close(self):
            try:
                self.inner.close()
            finally:
                self.p.finish()

    class AsyncTee(mod.AsyncByteStream):
        def __init__(self, inner, p):
            self.inner, self.p = inner, p

        async def __aiter__(self):
            async for chunk in self.inner:
                self.p.chunk(chunk)
                yield chunk
            self.p.finish()

        async def aclose(self):
            try:
                await self.inner.aclose()
            finally:
                self.p.finish()

    handle, ahandle = T.handle_request, AT.handle_async_request

    def handle_request(self, request):
        p = rec.begin(lib)
        _request(p, request)
        try:
            resp = handle(self, request)
        except BaseException as e:
            p.finish(error=e)
            raise
        p.response(resp.status_code, resp.headers.multi_items())
        resp.stream = SyncTee(resp.stream, p)
        return resp

    async def handle_async_request(self, request):
        p = rec.begin(lib)
        _request(p, request)
        try:
            resp = await ahandle(self, request)
        except BaseException as e:
            p.finish(error=e)
            raise
        p.response(resp.status_code, resp.headers.multi_items())
        resp.stream = AsyncTee(resp.stream, p)
        return resp

    T.handle_request, AT.handle_async_request = handle_request, handle_async_request
