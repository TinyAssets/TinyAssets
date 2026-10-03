"""The connect screen's sources and the daily-cap card in a real Chromium.

The node harnesses in ``test_connect_free_ai_screen`` drive a synthetic DOM. This
runs the same shipped code against a real one: real ``hidden``, real URL
parsing, real locale time, real layout of the page's own stylesheet, real clicks.
Skipped where Playwright or its Chromium is not installed.
"""

from __future__ import annotations

import json

import pytest

from tinyassets.onboarding import render_app_html

pytestmark = pytest.mark.real_browser

_START = "  // End connect OAuth controller.\n"
_END = "  // End connect screen sources."
SETUP = {
    "sign_in_sources": [{"id": "huggingface", "name": "Hugging Face",
                         "offer": "A small free monthly allowance.",
                         "billing_note": "Paid credits may be billed. Check your billing settings.",
                         "label": "Sign in with Hugging Face"}],
    "subscriptions": [{"service": "codex", "name": "ChatGPT",
                       "label": "Use your ChatGPT subscription", "note": "For more volume."}],
    "daily_caps": [{"host": "openrouter.ai", "name": "OpenRouter", "free_requests_per_day": 50,
                    "credit_requests_per_day": 1000, "credit_amount": "$10",
                    "credit_url": "https://openrouter.ai/settings/credits"}],
}


def _page_html() -> str:
    html, _ = render_app_html()
    style = html[html.index("<style"):html.index("</style>") + len("</style>")]
    source = html[html.index(_START) + len(_START):html.index(_END)]
    return f"""<!doctype html><html><head>{style}</head><body>
<div id="thread"></div>
<div id="connect-sign-in-sources"></div><div id="connect-subscriptions"></div>
<div id="sub-signin" hidden></div>
<script>
const $=id=>document.getElementById(id);
const NATIVE=false, CONNECT_REQUEST_ID="sys_connect_llm";
window.opened=0; function openConnectRequest(){{window.opened++;}}
window.calls=[];
const ConnectOAuth={{
  async post(op,payload){{window.calls.push({{op,payload}});
    return {{request:{{request_id:"req-1",action:{{type:"connect",oauth:{{}}}}}}}};}},
  async begin(req,note,button,opts){{
    window.calls.push({{begin:req.request_id,source:!!(opts&&opts.source)}});}},
}};
const SignInConnect={{ids:{{}},configure(){{}},start(){{}}}};
let railCache=[{{request_id:"sys_connect_llm",
  action:{{type:"connect",setup:{json.dumps(SETUP)}}}}}];
{source}
</script></body></html>"""


@pytest.fixture
def page():
    sync_api = pytest.importorskip(
        "playwright.sync_api",
        reason="Playwright unavailable; owner=codex runs-in=real-browser-proof",
    )
    with sync_api.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - a host without the browser build
            pytest.skip(f"Chromium unavailable; owner=codex runs-in=real-browser-proof: {exc}")
        try:
            tab = browser.new_page()
            tab.set_content(_page_html())
            yield tab
        finally:
            browser.close()


def test_daily_cap_card_renders_and_dismisses_in_a_real_browser(page):
    page.evaluate("""() => document.getElementById('thread').appendChild(
        DailyCapCard.build({provider_detail:'Daily quota exhausted. Reset: 2026-10-02 00:00 UTC. '
          + 'Add credit: https://openrouter.ai/settings/credits'}))""")
    card = page.locator(".daily-cap")
    assert card.is_visible()
    text = card.inner_text()
    assert "You've used today's free OpenRouter requests." in text
    assert "On a free OpenRouter account that is 50/day." in text
    assert ("Adding $10 credit to your own OpenRouter account once raises your OpenRouter daily "
            "limit to 1,000 requests") in text
    assert "the money goes to OpenRouter, not TinyAssets." in text
    assert "It can be used again after the daily reset" in text
    assert "continues" not in text and "Not now" not in text
    credit = page.get_by_role("link", name="Add credit on OpenRouter")
    assert credit.get_attribute("href") == "https://openrouter.ai/settings/credits"
    assert credit.get_attribute("target") == "_blank"
    page.get_by_role("button", name="Connect another AI").click()
    assert page.evaluate("window.opened") == 1
    page.get_by_role("button", name="Wait until tomorrow").click()
    assert not card.is_visible()


def test_sign_in_source_card_is_one_tap_in_a_real_browser(page):
    page.evaluate("() => SignInSourceCards.render(railCache[0].action.setup.sign_in_sources)")
    button = page.get_by_role("button", name="Sign in with Hugging Face")
    assert button.is_visible()
    note = page.get_by_text(SETUP["sign_in_sources"][0]["billing_note"], exact=True)
    assert note.is_visible()
    assert note.get_attribute("class") == "connect-terms"
    assert note.evaluate("el => el.previousElementSibling.textContent") == (
        SETUP["sign_in_sources"][0]["offer"])
    assert note.evaluate("el => el.nextElementSibling.tagName") == "BUTTON"
    assert note.bounding_box()["y"] < button.bounding_box()["y"]
    button.click()
    page.wait_for_function("window.calls.length === 2")
    assert page.evaluate("window.calls") == [
        {"op": "source_sign_in", "payload": {"preset_id": "huggingface"}},
        {"begin": "req-1", "source": True}]
