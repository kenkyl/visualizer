"""Drive headless Chrome through the full memes-agent v1 flow and log browser-side traffic.

usage: TRACE_OUT=browser.jsonl TRACE_PROC=browser uvx --with playwright --with httpx python drive.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import httpx  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from tracecommon import (  # noqa: E402
    body_view, cookie_header_view, emit, jwt_view, redact_url, set_cookie_view,
)

KC = "http://localhost:8080"
# Dev-only credentials from the memes-agent README; read from the environment so none are committed.
KC_ADMIN_PASSWORD = os.environ["KC_ADMIN_PASSWORD"]
MEMES_TEST_PASSWORD = os.environ["MEMES_TEST_PASSWORD"]
BASE = "http://localhost:8000"
SKIP_TYPES = {"image", "stylesheet", "script", "font", "media", "other"}
skipped = {"count": 0, "hosts": set()}


def kc_snapshot(label):
    """Observer only (not part of the flow): Keycloak's view of dummyuser1's sessions."""
    try:
        tok = httpx.post(f"{KC}/realms/master/protocol/openid-connect/token", data={
            "grant_type": "password", "client_id": "admin-cli", "username": "admin",
            "password": KC_ADMIN_PASSWORD}).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        uid = httpx.get(f"{KC}/admin/realms/memes/users", params={"username": "dummyuser1",
                        "exact": "true"}, headers=h).json()[0]["id"]
        sessions = httpx.get(f"{KC}/admin/realms/memes/users/{uid}/sessions", headers=h).json()
        realm = httpx.get(f"{KC}/admin/realms/memes", headers=h).json()
        emit("kc.sessions", label=label, user_id=uid, now_ms=int(time.time() * 1000), sessions=[
            {"sid": s["id"][:8], "start": s["start"], "lastAccess": s["lastAccess"],
             "clients": list(s.get("clients", {}).values()), "ip": s.get("ipAddress")}
            for s in sessions],
             realm={k: realm.get(k) for k in ("ssoSessionIdleTimeout", "ssoSessionMaxLifespan",
                                               "accessTokenLifespan", "clientSessionIdleTimeout")})
    except Exception as e:
        emit("kc.sessions", label=label, error=repr(e))


def cookies_snapshot(ctx, label):
    view = []
    for c in ctx.cookies():
        item = {k: c.get(k) for k in ("name", "domain", "path", "httpOnly", "secure", "sameSite",
                                      "expires")}
        v = c["value"]
        if c["name"] == "session":
            item["decoded"] = set_cookie_view(f"session={v}").get("session")
        elif v.startswith("ey") and v.count(".") == 2:
            item["jwt"] = jwt_view(v)
        view.append(item)
    emit("browser.cookies", label=label, cookies=view)


def on_response(resp):
    req = resp.request
    if req.resource_type in SKIP_TYPES:
        skipped["count"] += 1
        skipped["hosts"].add(req.url.split("/")[2])
        return
    try:
        rh = resp.headers_array()
    except Exception:
        rh = []
    set_cookies = [set_cookie_view(h["value"]) for h in rh if h["name"].lower() == "set-cookie"]
    loc = next((h["value"] for h in rh if h["name"].lower() == "location"), None)
    ctype = next((h["value"] for h in rh if h["name"].lower() == "content-type"), "")
    body = None
    if req.resource_type in {"fetch", "xhr"} and 200 <= resp.status < 300 or resp.status >= 400:
        try:
            body = body_view(ctype, resp.body())
        except Exception:
            body = None
    rqh = req.headers
    try:
        rqh = req.all_headers()
    except Exception:
        pass
    emit("browser.http", type=req.resource_type, method=req.method, url=redact_url(req.url),
         status=resp.status, location=redact_url(loc) if loc else None,
         req_cookies=cookie_header_view(rqh.get("cookie")),
         post=body_view(rqh.get("content-type", ""), req.post_data) if req.post_data else None,
         set_cookies=set_cookies, ctype=ctype.split(";")[0], body=body,
         timing_ms=round(req.timing.get("responseEnd", -1), 1) if req.timing else None)


def mark(label, page):
    emit("browser.mark", label=label, url=redact_url(page.url), title=page.title())


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.on("response", on_response)

    kc_snapshot("before")
    page.goto(f"{BASE}/")
    mark("home", page)

    page.click("text=Log in")
    page.wait_for_url(f"{KC}/**")
    page.wait_for_selector("#username")
    mark("keycloak-login-page", page)
    cookies_snapshot(ctx, "at-keycloak-login")

    page.fill("#username", "dummyuser1")
    page.fill("#password", MEMES_TEST_PASSWORD)
    page.click("#kc-login")
    page.wait_for_url(f"{BASE}/**")
    page.wait_for_load_state("networkidle")
    mark("after-login", page)
    cookies_snapshot(ctx, "after-login")
    kc_snapshot("after-login")

    if "/onboarding" in page.url:
        for i, w in enumerate(["chaotic", "nerdy", "sleepy"], 1):
            page.fill(f"#word{i}", w)
        page.click("button[type=submit]")
        page.wait_for_url(f"{BASE}/meme")
        mark("after-onboarding", page)

    page.click("#go")
    page.wait_for_selector("#out h2, #out .error", timeout=90_000)
    mark("meme-shown", page)
    emit("browser.meme", title=page.inner_text("#out h2") if page.locator("#out h2").count() else None,
         said=page.locator("#out p").all_inner_texts(),
         img=page.get_attribute("#out img", "src") if page.locator("#out img").count() else None)
    page.screenshot(path=os.path.join(os.path.dirname(__file__), "meme.png"), full_page=True)
    kc_snapshot("after-meme")

    page.click("text=Log out")
    try:
        page.wait_for_url(f"{BASE}/", timeout=10_000)
    except Exception:
        # Keycloak shows a confirmation page when it can't skip it; confirm and continue.
        mark("keycloak-logout-confirm", page)
        page.click("#kc-logout, button[type=submit], input[type=submit]")
        page.wait_for_url(f"{BASE}/", timeout=20_000)
    page.wait_for_load_state("networkidle")
    mark("after-logout", page)
    cookies_snapshot(ctx, "after-logout")
    kc_snapshot("after-logout")

    emit("browser.skipped_assets", count=skipped["count"], hosts=sorted(skipped["hosts"]))
    browser.close()
