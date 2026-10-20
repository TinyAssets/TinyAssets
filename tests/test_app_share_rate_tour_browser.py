"""Chromium proof of the Google Play closed-test feedback in /app.

The four tester suggestions were store-listing keywords, a first-run
walkthrough, a Rate option and a Share option. This covers the three that are
app code: the ``⋯`` menu's Rate (Android only) and Share entries, and the
once-per-device walkthrough.

Same idiom as ``test_app_chat_cloud_browser``: the real page with its real CSP
is served locally and every gesture is a real click or key press.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_app_chat_cloud_browser import _enter_chat
from tests.test_app_chat_cloud_browser import app_url as _app_url
from tests.test_app_chat_cloud_browser import browser as _browser

app_url = _app_url
browser = _browser
pytestmark = pytest.mark.real_browser

PLAY_URL = "https://play.google.com/store/apps/details?id=io.tinyassets.app"
SITE_URL = "https://tinyassets.io"

# A Capacitor shell, faked the way test_app_first_run_connect_browser fakes it,
# plus getPlatform, which is what decides whether Rate exists at all.
_NATIVE = """window.nativeOpened=[];
    window.Capacitor={isNativePlatform:()=>true,getPlatform:()=>%r,Plugins:{Browser:{
        open:async options=>{nativeOpened.push(options);},close:async()=>{}
    }}};"""

# navigator.share is absent in headless Chromium, so both branches are installed
# explicitly rather than inferred from the host.
_SHARE_OK = """window.shared=[];
    Object.defineProperty(navigator,'share',
        {configurable:true,value:async payload=>{shared.push(payload);}});"""
_SHARE_FAILS = """window.shared=[];
    Object.defineProperty(navigator,'share',{configurable:true,value:async payload=>{
        shared.push(payload);
        const error=new Error(window.shareFailure||'share failed');
        error.name=window.shareFailureName||'NotAllowedError';
        throw error;}});"""
_NO_SHARE = "Object.defineProperty(navigator,'share',{configurable:true,value:undefined});"


def _chat(browser, app_url, *, init="", width=390, permissions=()):
    context = browser.new_context(viewport={"width": width, "height": 844},
                                  permissions=list(permissions))
    if init:
        context.add_init_script(init)
    page = context.new_page()
    _enter_chat(page, app_url)
    _new_owner(page)
    return context, page


def _new_owner(page):
    """Signed in, and the history read succeeds with an empty thread: that
    successful empty read is what marks a first run."""
    page.evaluate("""() => {
        sessionStorage.setItem('ta_access_token','test');
        Owner.getConversation = async () => ({recent_conversation: {turns: []}});
    }""")


def _open_menu(page):
    page.click("#btn-cloud-menu")


def _menu_labels(page):
    """What the ⋯ menu actually offers, in order, ignoring its hidden entries."""
    return page.eval_on_selector_all(
        "#cloud-menu button:not([hidden])", "els => els.map(e => e.textContent.trim())")


def _tour_button(page, name):
    """Scoped on purpose: Back and Done are also other screens' button labels."""
    return page.locator("#first-run-tour").get_by_role("button", name=name, exact=True)


# --------------------------------------------------------------------------- #
# Rate: the Android store listing, and nowhere else
# --------------------------------------------------------------------------- #


def test_rate_is_offered_on_android_and_opens_the_play_listing(app_url, browser):
    from playwright.sync_api import expect

    context, page = _chat(browser, app_url, init=_NATIVE % "android")
    try:
        _open_menu(page)
        assert _menu_labels(page) == [
            "Apps", "Account", "Share TinyAssets", "Rate TinyAssets", "Sign out"]
        rate = page.locator("#btn-rate-app")
        expect(rate).to_be_visible()
        rate.click()
        page.wait_for_function("nativeOpened.length===1")
        assert page.evaluate("nativeOpened[0].url") == PLAY_URL
        # One in-app tab, never a WebView navigation away from the chat.
        assert len(page.context.pages) == 1
    finally:
        context.close()


@pytest.mark.parametrize("init,label", [("", "web"), (_NATIVE % "ios", "an iOS shell")])
def test_rate_is_hidden_where_there_is_no_store_target(app_url, browser, init, label):
    context, page = _chat(browser, app_url, init=init)
    try:
        _open_menu(page)
        assert page.locator("#cloud-menu").is_visible()
        assert page.locator("#btn-rate-app").is_hidden(), label
        assert _menu_labels(page) == ["Apps", "Account", "Share TinyAssets", "Sign out"], label
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# Share: the system sheet, else the clipboard, and never a silent no-op
# --------------------------------------------------------------------------- #


def test_share_uses_the_system_share_sheet_when_there_is_one(app_url, browser):
    context, page = _chat(browser, app_url, init=_SHARE_OK)
    try:
        _open_menu(page)
        page.locator("#btn-share-app").click()
        page.wait_for_function("shared.length===1")
        assert page.evaluate("shared[0].url") == SITE_URL
        assert page.evaluate("shared[0].title") == "TinyAssets"
        assert page.evaluate("shared[0].text")
        # The sheet itself is the feedback; nothing is written over the chat.
        assert page.text_content("#status-line") == ""
    finally:
        context.close()


def test_share_falls_back_to_the_clipboard_with_a_visible_confirmation(app_url, browser):
    from playwright.sync_api import expect

    context, page = _chat(browser, app_url, init=_NO_SHARE,
                          permissions=("clipboard-read", "clipboard-write"))
    try:
        _open_menu(page)
        page.locator("#btn-share-app").click()
        expect(page.locator("#status-line")).to_have_text("Link copied: " + SITE_URL)
        assert page.evaluate("navigator.clipboard.readText()") == SITE_URL
    finally:
        context.close()


def test_cancelling_the_share_sheet_says_nothing(app_url, browser):
    context, page = _chat(browser, app_url, init=_SHARE_FAILS)
    try:
        page.evaluate("() => { window.shareFailureName = 'AbortError'; }")
        _open_menu(page)
        page.locator("#btn-share-app").click()
        page.wait_for_function("shared.length===1")
        assert page.text_content("#status-line") == ""
    finally:
        context.close()


def test_a_share_that_fails_for_any_other_reason_is_said_out_loud(app_url, browser):
    from playwright.sync_api import expect

    context, page = _chat(browser, app_url, init=_SHARE_FAILS)
    try:
        page.evaluate("() => { window.shareFailure = 'no sharing on this device'; }")
        _open_menu(page)
        page.locator("#btn-share-app").click()
        line = page.locator("#status-line")
        expect(line).to_contain_text("Could not share: no sharing on this device")
        # The link is still given, so the owner can do it by hand.
        expect(line).to_contain_text(SITE_URL)
    finally:
        context.close()


def test_a_browser_with_neither_a_sheet_nor_a_clipboard_says_so(app_url, browser):
    from playwright.sync_api import expect

    context, page = _chat(
        browser, app_url,
        init=_NO_SHARE + "Object.defineProperty(navigator,'clipboard',"
                         "{configurable:true,value:undefined});")
    try:
        _open_menu(page)
        page.locator("#btn-share-app").click()
        expect(page.locator("#status-line")).to_contain_text(
            "no share sheet and no clipboard")
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# The first-run walkthrough
# --------------------------------------------------------------------------- #


def _tour(page):
    from playwright.sync_api import expect

    # The shipped entry point, not a copy of its glue: enterSignedIn calls
    # exactly this, and it is what chains the tour onto the history peek.
    assert page.evaluate("offerFirstRunWalkthrough()") is True
    dialog = page.get_by_role("dialog", name="Getting started")
    expect(dialog).to_be_visible()
    return dialog


def test_the_walkthrough_steps_through_the_real_ui_and_done_persists(app_url, browser):
    from playwright.sync_api import expect

    context, page = _chat(browser, app_url)
    try:
        dialog = _tour(page)
        total = page.evaluate("tourStepList.length")
        assert 3 <= total <= 4
        # Focus is inside the dialog, on the step the owner is reading.
        assert page.evaluate("document.activeElement.id") == "tour-step-head"
        expect(page.locator("#tour-progress")).to_have_text(f"Step 1 of {total}")
        assert page.locator("#btn-tour-back").is_disabled()
        # Step 1 names the composer, which is really there and really says this.
        expect(page.locator("#tour-step-text")).to_contain_text("Message your agent")
        assert page.get_attribute("#composer-input", "placeholder") == "Message your agent"

        _tour_button(page, "Next").click()
        expect(page.locator("#tour-progress")).to_have_text(f"Step 2 of {total}")
        assert not page.locator("#btn-tour-back").is_disabled()
        # Step 2 names the connect panel's own heading.
        expect(page.locator("#tour-step-text")).to_contain_text("Connect free AI")
        assert page.text_content(".connect-heading").strip() == "Connect free AI"

        _tour_button(page, "Back").click()
        expect(page.locator("#tour-progress")).to_have_text(f"Step 1 of {total}")
        _tour_button(page, "Next").click()

        for _ in range(total - 2):
            _tour_button(page, "Next").click()
        expect(page.locator("#tour-progress")).to_have_text(f"Step {total} of {total}")
        # The last step names the ⋯ menu's own entries.
        expect(page.locator("#tour-step-text")).to_contain_text("Share TinyAssets")
        done = _tour_button(page, "Done")
        expect(done).to_be_visible()
        done.click()

        expect(dialog).to_be_hidden()
        assert page.evaluate("localStorage.getItem('app.tour.seen')") == "1"
        assert page.evaluate("offerFirstRunWalkthrough()") is False
    finally:
        context.close()


def test_the_walkthrough_is_shown_once_per_device(app_url, browser):
    context, page = _chat(browser, app_url)
    try:
        _tour(page)
        _tour_button(page, "Skip").click()
        assert page.locator("#first-run-tour").is_hidden()
        # A real reload, so the decision is read back out of localStorage.
        page.evaluate("sessionStorage.removeItem('ta_access_token')")
        page.reload()
        _enter_chat(page, app_url)
        _new_owner(page)
        assert page.evaluate("offerFirstRunWalkthrough()") is False
        assert page.locator("#first-run-tour").is_hidden()
    finally:
        context.close()


def test_skip_is_on_every_step_and_escape_skips_too(app_url, browser):
    context, page = _chat(browser, app_url)
    try:
        _tour(page)
        total = page.evaluate("tourStepList.length")
        for step in range(total):
            assert _tour_button(page, "Skip").is_visible(), step
            if step < total - 1:
                _tour_button(page, "Next").click()
        page.keyboard.press("Escape")
        assert page.locator("#first-run-tour").is_hidden()
        assert page.evaluate("localStorage.getItem('app.tour.seen')") == "1"
    finally:
        context.close()


def test_every_step_fits_a_phone_with_all_three_controls_reachable(app_url, browser):
    """A tour nobody can finish on a phone is worse than no tour at all."""
    context, page = _chat(browser, app_url, width=390)
    try:
        _tour(page)
        total = page.evaluate("tourStepList.length")
        for step in range(total):
            box = page.locator("#first-run-tour").bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= 390, step
            assert box["y"] >= 0 and box["y"] + box["height"] <= 844, step
            # No sideways scroll inside it, and no step taller than the phone.
            assert page.evaluate(
                "() => { const d = document.getElementById('first-run-tour');"
                " return [d.scrollWidth <= d.clientWidth, d.scrollHeight <= d.clientHeight]; }"
            ) == [True, True], step
            for name in ("Skip", "Back", "Done" if step == total - 1 else "Next"):
                control = _tour_button(page, name).bounding_box()
                assert control["y"] + control["height"] <= box["y"] + box["height"], (step, name)
                assert control["height"] >= 44, (step, name)
            if step < total - 1:
                _tour_button(page, "Next").click()
    finally:
        context.close()


@pytest.mark.parametrize("returning", ["historyLoaded", "hasMessages"])
def test_a_returning_owner_is_never_stopped_by_the_walkthrough(app_url, browser, returning):
    context, page = _chat(browser, app_url)
    try:
        page.evaluate(f"() => {{ {returning} = true; }}")
        assert page.evaluate("offerFirstRunWalkthrough()") is False
        assert page.locator("#first-run-tour").is_hidden()
    finally:
        context.close()


def test_a_history_read_that_fails_never_opens_the_walkthrough(app_url, browser):
    """An outage is not a new owner: the modal must not cover recovery controls."""
    context, page = _chat(browser, app_url)
    try:
        page.evaluate("""() => {
            historyLoaded = false;
            Owner.getConversation = async () => { throw new Error('history is down'); };
        }""")
        assert page.evaluate("offerFirstRunWalkthrough()") is False
        assert page.locator("#first-run-tour").is_hidden()
    finally:
        context.close()


def test_storage_that_throws_shows_the_walkthrough_again(app_url, browser):
    """A locked-down WebView must not turn the tour into a crash or a trap."""
    context, page = _chat(browser, app_url)
    try:
        page.evaluate("""() => {
            const real=Storage.prototype.setItem;
            Storage.prototype.setItem=function(key){
                if(key==='app.tour.seen') throw new Error('storage is off');
                return real.apply(this,arguments);
            };
        }""")
        _tour(page)
        _tour_button(page, "Skip").click()
        assert page.locator("#first-run-tour").is_hidden()
        assert page.evaluate("localStorage.getItem('app.tour.seen')") is None
        assert page.evaluate("offerFirstRunWalkthrough()") is True
    finally:
        context.close()


def test_the_walkthrough_mentions_rate_only_where_rate_exists(app_url, browser):
    native, native_page = _chat(browser, app_url, init=_NATIVE % "android")
    web, web_page = _chat(browser, app_url)
    try:
        _tour(native_page)
        on_android = native_page.evaluate("tourStepList.map(s=>s.text).join(' ')")
        _tour(web_page)
        on_web = web_page.evaluate("tourStepList.map(s=>s.text).join(' ')")
        assert "Rate TinyAssets" in on_android
        assert "Rate TinyAssets" not in on_web
        assert "Share TinyAssets" in on_web
    finally:
        native.close()
        web.close()


def test_the_signed_in_chat_entry_point_offers_the_walkthrough():
    """enterSignedIn is the only caller: the tour must ride on the real sign-in,
    not only on a test calling the helper. Source-level, because driving the
    whole sign-in is a different file's job."""
    source = (Path(__file__).resolve().parents[1]
              / "tinyassets" / "onboarding" / "app" / "main.js").read_text(encoding="utf-8")
    body = source.split("async function enterSignedIn(){", 1)[1].split("\n  }\n", 1)[0]
    assert "offerFirstRunWalkthrough();" in body
