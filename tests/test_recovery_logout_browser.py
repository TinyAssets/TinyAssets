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
            localStorage.setItem('ta_pkce', 'pending-native-flow');
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
        assert page.evaluate("localStorage.getItem('ta_pkce')") is None
        assert page.url == "https://app.example/app"
        context.close()
        browser.close()



def test_native_log_out_disarms_and_unregisters_notifications():
    from playwright.sync_api import sync_playwright

    recovery = Path("tinyassets/onboarding/app_recovery.js").read_text("utf-8").replace(
        "__TA_ONBOARDING_CONFIG__", "{}")
    native = """
      const record=name=>async arg=>{
        const calls=JSON.parse(sessionStorage.getItem('calls')||'[]');
        calls.push([name,arg===undefined?null:arg]);sessionStorage.setItem('calls',JSON.stringify(calls));};
      window.Capacitor={isNativePlatform:()=>true,Plugins:{
        NotificationReply:{setActive:record('setActive'),consume:record('consume')},
        PushNotifications:{unregister:record('unregister'),
          removeAllDeliveredNotifications:record('removeAll')}}};
    """
    page_html = (
        '<nav><a href="/app" data-app-recovery="logout">Log out</a></nav>'
        '<span id="app-recovery-failed" hidden></span>'
        "<script>" + native + recovery + "</script>"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        context.route("**/*", lambda route: route.abort())
        context.route("https://app.example/app", lambda route: route.fulfill(
            body=page_html, content_type="text/html"))
        context.route("https://app.example/app/token", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"ok":true}'))
        page = context.new_page()
        page.goto("https://app.example/app")
        page.evaluate("localStorage.setItem('app.push.owner', 'previous-owner')")
        with page.expect_navigation():
            page.get_by_role("link", name="Log out").click()
        assert page.evaluate("localStorage.getItem('app.push.owner')") is None
        calls = json.loads(page.evaluate("sessionStorage.getItem('calls')"))
        assert calls == [["setActive", {"active": False, "recipient": ""}], ["consume", None],
                         ["unregister", None], ["removeAll", None]]
        context.close()
        browser.close()


def test_a_hung_notification_plugin_cannot_strand_log_out():
    from playwright.sync_api import sync_playwright

    recovery = Path("tinyassets/onboarding/app_recovery.js").read_text("utf-8").replace(
        "__TA_ONBOARDING_CONFIG__", "{}")
    hung = """
      const never=()=>new Promise(()=>{});
      window.Capacitor={isNativePlatform:()=>true,Plugins:{
        NotificationReply:{setActive:never,consume:never},
        PushNotifications:{unregister:never,removeAllDeliveredNotifications:never}}};
    """
    page_html = (
        '<nav><a href="/app" data-app-recovery="logout">Log out</a></nav>'
        '<span id="app-recovery-failed" hidden></span>'
        "<script>" + hung + recovery + "</script>"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        context.route("**/*", lambda route: route.abort())
        context.route("https://app.example/app", lambda route: route.fulfill(
            body=page_html, content_type="text/html"))
        context.route("https://app.example/app/token", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"ok":true}'))
        page = context.new_page()
        page.goto("https://app.example/app")
        page.evaluate("sessionStorage.setItem('marker', 'before')")
        with page.expect_navigation(timeout=8000):
            page.get_by_role("link", name="Log out").click()
        context.close()
        browser.close()
