"""Real app controls in Chromium; provider authorization is a synthetic page."""
import json

import pytest

from tests.test_app_chat_cloud_browser import app_url, browser  # noqa: F401

pytestmark = pytest.mark.real_browser


@pytest.mark.parametrize("native", [False, True])
def test_native_subscription_connect_controls(app_url, browser, native):  # noqa: F811
    from playwright.sync_api import expect

    context = browser.new_context()
    if native:
        context.add_init_script("""window.Capacitor = {isNativePlatform:()=>true,
            getPlatform:()=> 'android', Plugins:{Browser:{
                open:async ({url})=>{window.fixtureExternalUrl=url;}, close:async()=>{}}}};""")
    page = context.new_page()
    approved, calls = [], []
    def endpoint(route):
        action = route.request.url.rsplit('/', 1)[1]
        body = route.request.post_data_json
        calls.append((action, body))
        if action == 'code':
            approved.append(body['code'])
        state = {'flow': 'fixture-flow', 'source_id': 'claude-code',
            'status': 'connected' if approved else 'waiting',
            'authorize_url': 'https://login.example.test/authorize', 'code': 'ABCD-1234',
            'accepts_code': True}
        if action == 'finish':
            state = {'status': 'connected'}
        route.fulfill(status=200, content_type='application/json', body=json.dumps(state))
    context.route('https://login.example.test/**',
                  lambda route: route.fulfill(status=200, body='Synthetic provider approval'))
    context.route('**/app/subscription-connect/*', endpoint)
    try:
        page.goto(app_url)
        page.wait_for_selector('#view-signin', state='visible')
        page.evaluate('''() => {
            storeAccessToken('fixture-token', 3600);
            const sources = [
                {id:'claude-code',label:'Continue with Claude'},
                {id:'codex',label:'Continue with ChatGPT'},
                {id:'gemini',label:'Continue with Gemini'},
                {id:'kimi',label:'Continue with Kimi'}];
            document.body.appendChild(connectBody({action:{setup:{subscription_sources:sources}}}));
        }''')
        for label in ('Claude', 'ChatGPT', 'Gemini', 'Kimi'):
            expect(page.get_by_role('button', name='Continue with ' + label, exact=True)
                   ).to_be_visible()
        if native:
            page.get_by_role('button', name='Continue with Claude', exact=True).click()
            expect(page.get_by_role('textbox', name='Code returned after sign-in')).to_be_visible()
            assert page.evaluate('window.fixtureExternalUrl') == 'https://login.example.test/authorize'
        else:
            with page.expect_popup() as opening:
                page.get_by_role('button', name='Continue with Claude', exact=True).click()
            popup = opening.value
            popup.wait_for_url('https://login.example.test/authorize')
        expect(page.get_by_text('Approve sign-in using code: ABCD-1234', exact=True)
               ).to_be_visible()
        page.get_by_role('textbox', name='Code returned after sign-in').fill('approved')
        page.get_by_role('button', name='Finish sign-in', exact=True).click()
        expect(page.get_by_text('Connected. Your agent can use this subscription.', exact=True)
               ).to_be_visible(timeout=5000)
        assert approved == ['approved']
        assert ('begin', {'source_id': 'claude-code'}) in calls
        assert ('finish', {'flow': 'fixture-flow'}) in calls
    finally:
        context.close()
