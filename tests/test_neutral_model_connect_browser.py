"""Local real-page proof: registry payload, stale setup, visible choices, OAuth.

TA_CONNECT_SCREENSHOTS selects the artifact directory (e.g. /out in the oracle).
All non-local requests are intercepted; no production service is contacted.
"""
import json
import os
from pathlib import Path

import pytest

from tests.test_app_chat_cloud_browser import app_url, browser  # noqa: F401
from tinyassets.api.pending_requests import _model_connect_request

pytestmark = pytest.mark.real_browser


@pytest.mark.parametrize("width,legacy", [(390, False), (1366, False), (1920, False),
                                         (2400, False), (1366, True)])
def test_visible_choices_and_one_tap_sign_in(
        browser, app_url, width, legacy, tmp_path):  # noqa: F811
    page = browser.new_page(viewport={"width": width, "height": 900 if width > 390 else 844})
    page.set_default_timeout(7000)
    payload = _model_connect_request()
    preset = payload["action"]["setup"]["hosted_sources"][0]["preset_id"]
    if legacy:
        setup = payload["action"]["setup"]
        setup["primary"] = setup.pop("hosted_sources")[0]
    calls = []

    def route(request):
        url = request.request.url
        if url == app_url:
            request.continue_()
        elif url.endswith("/fixture/setup"):
            request.fulfill(json=payload)
        elif url.endswith("/app/model-connect/begin"):
            calls.append(request.request.post_data_json)
            request.fulfill(json={"flow": "f" * 43, "expires_in": 300,
                                  "authorize_url": "https://provider.invalid/authorize"})
        elif url.endswith("/app/model-connect/inline_cancel"):
            request.fulfill(status=410, json={"error": "model_connection_expired"})
        elif url.endswith("/app/model-connect/exchange"):
            request.fulfill(json={"status": "confirmation_required", "request_id": "fresh",
                                  "request": {"request_id": "fresh",
                                              "action": {"type": "bind_model_access"}}})
        elif url.endswith("/fixture/answer"):
            request.fulfill(json={"status": "answered"})
        elif url.endswith("/app/me"):
            request.fulfill(json={"setup": "connected"})
        elif url == "https://provider.invalid/authorize":
            request.fulfill(body="<h1>Local provider authorize page</h1>", content_type="text/html")
        else:
            request.fulfill(json={})

    page.route("**/*", route)
    page.goto(app_url)
    page.wait_for_selector("#view-signin", state="visible")
    page.evaluate("""async () => {
        setQueueOwner('local-owner'); setQueueScope('local-center');
        showView('chat'); refreshChatCloud();
        const request=await (await fetch('/fixture/setup')).json();
        HostedModelConnect.setup='recovery';
        renderRail([request]);
        $('request-rail').close();
        Owner.listRequests=async()=>({pending:[request]});
        const record={owner:'local-owner',scope:'local-center',needsConnection:true,
                      connectionFlow:'expired-flow'};
        InlineConnection.current=r=>r===record;
        InlineConnection.save=()=>{};
        InlineConnection.schedule=()=>{};
        ownsSavedRow=()=>true;
        InlineConnection.hold(request,record);
        ensureFreshToken=async()=>{}; token=()=> 'local-test-token';
    }""")
    page.get_by_role("button", name="Connect", exact=True).click()
    dialog = page.locator("#request-rail")
    dialog.wait_for(state="visible")
    sign_in = dialog.get_by_role("button", name="Continue with OpenRouter", exact=True)
    assert sign_in.is_visible()
    assert dialog.get_by_text("Use an API key instead", exact=True).is_visible()
    for source in payload["action"]["setup"]["sources"]:
        choice = dialog.locator("#free-source-cards summary").filter(has_text=source["name"])
        assert choice.is_visible()
    endpoint = dialog.get_by_text("Connect any OpenAI-compatible endpoint", exact=True)
    assert endpoint.is_visible()
    assert not dialog.get_by_role("button", name="Finish connecting", exact=True).count()
    assert not page.locator("#connect-other").evaluate("e=>e.open")
    bounds = dialog.bounding_box()
    assert 0 <= bounds["x"] and bounds["x"] + bounds["width"] <= width + 1
    assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= page.viewport_size["height"] + 1
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if width > 390:
        assert abs(bounds["x"] + bounds["width"] / 2 - width / 2) <= 1
        cloud = page.locator("#chat-cloud").bounding_box()
        assert abs(cloud["x"] + cloud["width"] / 2 - width / 2) <= 2
    out = Path(os.environ.get("TA_CONNECT_SCREENSHOTS", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    suffix = f"{width}-legacy" if legacy else str(width)
    page.screenshot(path=str(out / f"connect-dialog-{suffix}.png"), full_page=True)
    page.locator("#request-rail").evaluate("e=>e.close()")
    page.screenshot(path=str(out / f"connect-app-{suffix}.png"), full_page=True)
    page.get_by_role("button", name="Connect", exact=True).click()
    endpoint.click()
    assert page.locator("#endpoint-url").is_visible()
    assert page.evaluate("InlineConnection.record.connectionFlow") is None
    endpoint.click()
    dialog.locator("#free-source-cards summary").filter(has_text="Groq").click()
    assert dialog.locator("#free-source-cards input[type=password]").first.is_visible()
    # Exercise the actual callback controller and answer path with local API
    # responses. Only the account bootstrap and MCP transport are test doubles.
    page.evaluate("""async () => {
        MCP.answerRequest=payload=>fetch('/fixture/answer',{
          method:'POST',body:JSON.stringify(payload)}).then(r=>r.json());
        enterSignedIn=async()=>{
          HostedModelConnect.setup=(await (await fetch('/app/me')).json()).setup;
        };
        await HostedModelConnect.complete({flow:'local-return'});
    }""")
    assert not dialog.is_visible()
    page.get_by_role("button", name="Connect", exact=True).click()
    sign_in.click()
    page.wait_for_url("https://provider.invalid/authorize")
    assert calls == [{"preset_id": preset,
                      "code_challenge": calls[0]["code_challenge"]}]
    (out / f"connect-proof-{suffix}.json").write_text(json.dumps({"width": width, "dialog": bounds,
                                                               "one_tap_authorize": True}))
    page.close()
