"""The recovery bar can always sign out, even when the app never booted."""
import json
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.real_browser,
    pytest.mark.verifies("onboarding-web-app:The recovery bar can always sign out"),
]


@pytest.mark.parametrize("reachable", [True, False])
def test_log_out_from_the_recovery_bar_without_the_app(reachable):
    from playwright.sync_api import sync_playwright

    recovery = Path("tinyassets/onboarding/app_recovery.js").read_text("utf-8").replace(
        "__TA_ONBOARDING_CONFIG__", "{}")
    page_html = (
        '<nav><a href="/app" data-app-recovery="logout">Log out</a></nav>'
        '<span id="app-recovery-failed" hidden></span>'
        "<script>" + recovery + "</script>"
    )
    sent = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        context.route("**/*", lambda route: route.abort())
        context.route("https://app.example/app", lambda route: route.fulfill(
            body=page_html, content_type="text/html"))

        def token(route):
            sent.append(route.request.post_data_json)
            if reachable:
                route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
            else:
                route.abort()
        context.route("https://app.example/app/token", token)
        page = context.new_page()
        page.goto("https://app.example/app")
        page.evaluate("""() => {
            localStorage.setItem('ta_sref', 'handle');
            sessionStorage.setItem('ta_access_token', 'token');
        }""")
        page.get_by_role("link", name="Log out").click()
        page.wait_for_function("sessionStorage.getItem('ta_access_token') === null")
        page.wait_for_load_state()
        assert sent == [{"grant_type": "logout", "session_ref": "handle"}]
        stored = page.evaluate("""() => [localStorage.getItem('ta_sref'),
                                         localStorage.getItem('ta_logout_pending')]""")
        # Reached: the session is gone. Unreachable: the next start finishes it.
        assert stored == ([None, None] if reachable else ["handle", "1"])
        assert page.url == "https://app.example/app"
        context.close()
        browser.close()
