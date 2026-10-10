"""The served app posts only a completion handle and its existing bearer."""

import pytest

from tests.test_app_chat_cloud_browser import app_url as _app_url
from tests.test_app_chat_cloud_browser import browser as _browser

app_url = _app_url
browser = _browser
pytestmark = pytest.mark.real_browser


@pytest.mark.parametrize("outcome", ["success", "mismatch", "no_session", "renewal", "logout"])
def test_owner_completion_bootstrap_in_rendered_app(app_url, browser, outcome):
    context = browser.new_context()
    page = context.new_page()
    handle = "h" * 43
    sent = []
    renewals = []
    if outcome != "no_session":
        page.add_init_script("sessionStorage.setItem('ta_access_token','existing-access')")
    if outcome == "logout":
        page.add_init_script("localStorage.setItem('ta_logout_pending','1')")

    def renew(route):
        renewals.append(route.request.post_data_json)
        route.fulfill(status=401 if outcome == "no_session" else 200,
                      json={"error": "no_session"} if outcome == "no_session" else
                      {"access_token": "renewed-access", "expires_in": 300})

    def complete(route):
        sent.append((route.request.post_data_json, route.request.headers))
        if outcome == "renewal" and len(sent) == 1:
            route.fulfill(status=401, json={"error": "authentication_required"})
        elif outcome == "mismatch":
            route.fulfill(status=403, json={
                "error": "identity_mismatch",
                "message": "Sign-in could not finish. Start sign-in again.",
            })
        else:
            route.fulfill(json={"redirect": "/app?completed=1"})

    page.route("**/app/token", renew)
    page.route("**/app/owner-sign-in/complete", complete)
    page.goto(app_url + "#owner_completion=" + handle)
    if outcome in {"success", "renewal"}:
        page.wait_for_url("**/app?completed=1")
        assert sent[-1][0] == {"completion": handle}
        expected = "renewed-access" if outcome == "renewal" else "existing-access"
        assert sent[-1][1]["authorization"] == "Bearer " + expected
        assert len(sent) == (2 if outcome == "renewal" else 1)
    else:
        message = {"no_session": "blocked the sign-in cookie",
                   "logout": "Sign in to this browser"}.get(outcome, "Start sign-in again")
        page.wait_for_function("text=>document.getElementById('signin-notice').textContent.includes(text)",
                               arg=message)
        assert page.locator("#signin-notice").is_visible()
        assert len(sent) == (0 if outcome in {"no_session", "logout"} else 1)
    assert "owner_completion" not in page.url
    if outcome == "renewal":
        assert len(renewals) == 1
    if outcome == "logout":
        assert renewals[0]["grant_type"] == "logout"
    context.close()
