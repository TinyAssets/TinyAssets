"""Real app rendering/control proof; the production-image oracle proves custody."""
import json

import pytest

from tests.test_app_chat_cloud_browser import _enter_chat
from tests.test_app_chat_cloud_browser import app_url as _app_url

app_url = _app_url
pytestmark = pytest.mark.real_browser


@pytest.mark.parametrize('width', [390, 1280])
def test_one_signin_tap_enters_takeover_and_completion_needs_no_tap(app_url, width):
    from playwright.sync_api import expect, sync_playwright

    calls = []
    connected = False

    def transport(route):
        nonlocal connected
        data = route.request.post_data_json
        calls.append(data['action'])
        if data['action'] == 'input':
            connected = True
        if data['action'] == 'frame' and connected:
            value = {'status': 'connected'}
        elif data['action'] == 'frame':
            value = {'origin': 'https://site.example', 'image':
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5V8AAAAASUVORK5CYII=',
                'width': 1024, 'height': 768}
        else:
            value = {'status': 'login'}
        route.fulfill(content_type='application/json', body=json.dumps(value))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 844})
        page.route('**/app/browser-login', transport)
        _enter_chat(page, app_url)
        page.evaluate("""() => {
          token=()=> 'test-session';queueScope='u-1';
          window.completions=[];window.refreshes=0;
          ApprovalHandoff.completed=payload=>completions.push(payload);
          refreshRail=async()=>{refreshes++;renderRail([]);};
          renderRail([{request_id:'login-1',kind:'Browser',title:'Sign in · https://site.example',
            action:{type:'connect_browser',connection_id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'},
            fields:[],items:[]}]);
        }""")
        page.locator('#browser-signins').get_by_role('button', name='Sign in', exact=False).click()
        view = page.get_by_alt_text('Live website sign-in')
        expect(view).to_be_visible()
        assert calls.count('begin') == 1
        assert page.get_by_role('button', name='I’m signed in').count() == 0
        view.click()  # A site-login gesture; no TinyAssets finish/continue gesture.
        page.wait_for_function('() => completions.length === 1 && refreshes === 1')
        assert 'finish' not in calls
        assert calls.count('begin') == 1
        assert page.locator('#browser-signins button').count() == 0
        browser.close()
