"""Phone conversation geometry and controls in real Chromium."""

import pytest

from tests.test_app_chat_cloud_browser import _box, _enter_chat
from tests.test_app_chat_cloud_browser import app_url as _app_url
from tests.test_app_chat_cloud_browser import browser as _browser

app_url = _app_url
browser = _browser

pytestmark = pytest.mark.real_browser


def test_phone_conversation(app_url, browser, tmp_path):
    context = browser.new_context(viewport={"width": 390, "height": 844},
                                  is_mobile=True, has_touch=True)
    page = context.new_page()
    _enter_chat(page, app_url)
    page.evaluate("""() => {
        for (const id of ['btn-ui-switch', 'btn-plan', 'btn-voice', 'voice-output-select'])
            document.getElementById(id).hidden = false;
        document.getElementById('model-next').textContent =
            'Next message: a very long saved model name';
        cloudState.open = {x:20,y:30,w:320,h:360};
        cloudState.userSet = true; refreshChatCloud();
        document.getElementById('request-rail').hidden = false;
        renderRail([]);
        InlineApprovals.history([
            {title:'First request', status:'approved'},
            {title:'Second request', status:'skipped'},
            {title:'Third request', status:'approved'}]);
    }""")
    page.wait_for_function(
        "() => document.getElementById('rail-head').textContent === 'Request history'"
    )
    assert page.locator('#view-chat .chat-header').count() == 0
    header = _box(page, '#chat-cloud-bar')
    thread = _box(page, '#thread')
    field = _box(page, '#composer-input')
    assert header['height'] <= 56
    assert thread['height'] >= 844 * .5
    assert field['width'] >= 390 * .6
    assert page.locator('#btn-send').is_visible()
    send = _box(page, '#btn-send')
    assert 0 <= send['x'] and send['x'] + send['width'] <= 390
    assert 0 <= send['y'] and send['y'] + send['height'] <= 844
    assert _box(page, '#request-rail')['height'] <= 44
    assert page.locator('#request-history').is_hidden()
    assert page.locator('#request-history p').count() == 3
    assert page.locator('#pending-requests > #rail-items').count() == 1
    assert page.locator('#thread #btn-rail-add').is_visible()
    assert page.locator('#chat-cloud-resize').is_hidden()
    stage, cloud = _box(page, '#chat-stage'), _box(page, '#chat-cloud')
    assert cloud == pytest.approx(stage, abs=1)
    page.locator('#rail-head').tap()
    assert page.locator('#request-history').is_visible()
    assert page.locator('#request-history button').count() == 0
    page.locator('#rail-head').tap()
    # New history arrives without changing the folded chip into a pending queue.
    page.evaluate("""() => InlineApprovals.history([
        {title:'Another answered request', status:'skipped'}])""")
    page.wait_for_function(
        "() => document.getElementById('rail-head').textContent === 'Request history'"
    )
    page.locator('#btn-cloud-menu').tap()
    assert page.locator('#btn-account').is_visible()
    assert page.locator('#btn-signout').is_visible()
    page.keyboard.press('Escape')
    assert page.locator('#btn-account').is_hidden()
    page.locator('#btn-cloud-menu').tap()
    page.locator('#thread').tap()
    assert page.locator('#btn-account').is_hidden()
    page.locator('#btn-cloud-menu').tap()
    page.locator('#btn-account').tap()
    assert page.locator('#btn-cloud-menu').get_attribute('aria-expanded') == 'false'
    page.evaluate("showView('chat'); refreshChatCloud();")
    page.screenshot(path=tmp_path / 'phone-p0.png')
    (tmp_path / 'phone-p0-measurements.txt').write_text(
        f"cloud_bar={header['height']}, thread={thread['height']}, input={field['width']}")
    page.set_viewport_size({'width': 390, 'height': 500})
    page.wait_for_function(
        "() => document.documentElement.style.getPropertyValue('--app-h') === '500px'"
    )
    page.wait_for_function(
        "() => document.getElementById('composer-input').getBoundingClientRect().bottom <= 500"
    )
    composer = _box(page, '#composer')
    send = _box(page, '#btn-send')
    assert composer['y'] >= 0 and composer['y'] + composer['height'] <= 500
    assert send['y'] + send['height'] <= 500
    with (tmp_path / 'phone-p0-measurements.txt').open('a') as output:
        output.write(f", composer_at_500={composer}, send_at_500={send}")
    # Empty history keeps its chip; adding a key remains in the conversation.
    page.evaluate("""() => { renderRail([]); InlineApprovals.history([]); }""")
    page.wait_for_function(
        "() => document.getElementById('rail-head').textContent === 'Request history'"
    )
    assert page.locator('#request-rail').is_visible()
    page.locator('#rail-head').tap()
    assert page.locator('#rail-head').get_attribute('aria-expanded') == 'true'
    assert page.locator('#request-history').evaluate(
        "node => getComputedStyle(node).display") != 'none'
    assert page.locator('#request-history p').count() == 0
    assert page.locator('#thread #btn-rail-add').is_visible()
    context.close()


@pytest.mark.parametrize('width', [601, 768, 1280])
def test_wide_cloud_menu(app_url, browser, width):
    page = browser.new_page(viewport={'width': width, 'height': 800})
    _enter_chat(page, app_url)
    assert page.locator('#view-chat .chat-header').count() == 0
    assert page.locator('#btn-cloud-menu').is_visible()
    assert page.locator('#btn-account').is_hidden()
    page.click('#btn-cloud-menu')
    assert page.locator('#btn-account').is_visible()
    assert page.locator('#btn-signout').is_visible()
    assert page.locator('#rail-head').text_content() == 'Request history'
    # Desktop and the Electron app keep 100dvh: no visual-viewport height.
    assert page.evaluate("document.documentElement.style.getPropertyValue('--app-h')") == ''
    page.close()


@pytest.mark.parametrize('width', [390, 1280])
@pytest.mark.parametrize('changed', ['epoch', 'owner', 'home'])
@pytest.mark.parametrize('late_error', [False, True])
def test_profile_response_cannot_cross_account_context(app_url, browser, width,
                                                     changed, late_error):
    """Actual Account navigation and DOM; only the profile transport is delayed."""
    from playwright.sync_api import expect

    context = browser.new_context(viewport={'width': width, 'height': 844},
                                  is_mobile=width == 390, has_touch=width == 390)
    page = context.new_page()
    _enter_chat(page, app_url)
    page.evaluate("""() => {
        setQueueScope('home-a');
        const original = window.fetch;
        window.profileRequests = [];
        window.fetch = (url, options) => {
            if(url !== '/app/profile') return original(url, options);
            return new Promise((resolve, reject) => {
                profileRequests.push({resolve, reject, options});
            });
        };
    }""")

    def account():
        page.locator('#btn-cloud-menu').click()
        page.locator('#btn-account').click()
        expect(page.locator('#view-account')).to_be_visible()

    account()
    page.wait_for_function('profileRequests.length === 1')
    assert page.evaluate('profileRequests[0].options.credentials') == 'same-origin'
    # Show A, then begin a second A read which remains pending across the switch.
    page.evaluate("""() => profileRequests[0].resolve({ok:true, json:async()=>({
        name:'Agent A', responsibility:'Private A', status:'working'})})""")
    expect(page.locator('#profile-name')).to_have_text('Agent A')
    page.evaluate("showView('chat'); refreshChatCloud();")
    account()
    page.wait_for_function('profileRequests.length === 2')
    # Each independent fence must reject A; a combined switch could hide a missing guard.
    page.evaluate("""changed => {
        if(changed === 'epoch') MCP._loginEpoch++;
        if(changed === 'owner') setQueueOwner('owner-b');
        if(changed === 'home') setQueueScope('home-b');
    }""", changed)
    page.evaluate("showView('chat'); refreshChatCloud();")
    account()
    page.wait_for_function('profileRequests.length === 3')
    page.evaluate("""() => profileRequests[2].resolve({ok:true, json:async()=>({
        name:'<b>Agent B</b>', responsibility:'Private B', status:'waiting_on_you'})})""")
    expect(page.locator('#profile-name')).to_have_text('<b>Agent B</b>')
    assert page.locator('#profile-name b').count() == 0
    page.evaluate("""async lateError => {
        if(lateError) profileRequests[1].reject(new Error('late A failure'));
        else profileRequests[1].resolve({ok:true, json:async()=>({
            name:'Agent A', responsibility:'Private A', status:'working'})});
        await new Promise(resolve => setTimeout(resolve, 0));
    }""", late_error)
    expect(page.locator('#profile-name')).to_have_text('<b>Agent B</b>')
    expect(page.locator('#profile-responsibility')).to_have_text('Private B')
    expect(page.locator('#profile-status')).to_have_text('Waiting on you')
    # Exercise the exact merged reset function, including both parents' hooks.
    page.evaluate("""() => {
        addressedAgent={agent_id:'private-agent',name:'Private agent'};
        clearAccountScopedState();
    }""")
    for field in ('name', 'responsibility', 'status'):
        expect(page.locator('#profile-' + field)).to_have_text('')
    assert page.evaluate('addressedAgentId()') == 'main'
    context.close()
