"""Real browser address-to-CLI handoff, including same-tab return and native sheet."""
import json
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.real_browser,
    pytest.mark.verifies(
        'box-subscription-sources:Definition-generated connect controls',
    ),
]


@pytest.mark.parametrize("native", [False, True])
def test_native_subscription_connect_controls(native):
    from playwright.sync_api import expect, sync_playwright

    source = Path("tinyassets/onboarding/app/main.js").read_text("utf-8")
    cli = source[source.index("  const SubscriptionSources={"):
                 source.index("\n  };", source.index("  const SubscriptionSources={")) + 6]
    cli = source[source.index("  function copyButton("):
                 source.index("  function focusCommandCenter(")] + cli
    address = source[source.index("  const ModelDiscovery={"):
                     source.index("  // ---- End unified address connection ----")]
    calls, approved = [], []
    script = """
      const $=id=>document.getElementById(id),NATIVE=NATIVE_VALUE,DESKTOP_SHELL=false;
      const ConnectedAI={load(){}};
      const ownerProofUnavailable=()=>'Use a regular browser.';
      const CFG={model_source_address:'https://fixture.example'},MCP={_loginEpoch:1};
      const pkceStore=sessionStorage,queueOwner='owner',queueScope='center';
      const token=()=>true,ensureFreshToken=async()=>{},authHeaders=()=>({});
      const refreshRail=async()=>{},closeExternal=async()=>{};
      const appendMessage=()=>{};
      const closeConnectRequest=()=>{document.body.dataset.connected='yes';};
      const openSignIn=async url=>{
        if(NATIVE)window.externalUrl=url;else window.location.assign(url);};
      const sleep=ms=>new Promise(resolve=>setTimeout(resolve,25));
      const randToken=()=> 'v'.repeat(43),challengeFor=async()=> 'c'.repeat(43);
      const ConnectOAuth={post:async()=>{throw Error('CLI must resolve before discovery');}};
    """.replace("NATIVE_VALUE", json.dumps(native)) + cli + address + """
      ModelDiscovery.render($('connect-panel'),true);
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        def endpoint(route):
            action = route.request.url.rsplit('/', 1)[1]
            body = route.request.post_data_json
            calls.append((action, body))
            if action == 'resolve':
                state = {'source_id': 'fixture-cli'}
            else:
                if action == 'code':
                    approved.append(body['code'])
                state = {'flow': 'fixture-flow', 'source_id': 'fixture-cli',
                    'status': 'connected' if approved else 'waiting',
                    'authorize_url': 'https://login.example.test/authorize',
                    'code': 'ABCD-1234', 'accepts_code': True}
                if action == 'finish':
                    state = {'status': 'connected'}
            route.fulfill(status=200, content_type='application/json', body=json.dumps(state))
        context.route('**/*', lambda route: route.abort())
        context.route('https://app.example/app', lambda route: route.fulfill(
            body='<div id="connect-panel"></div><script>'+script+'</script>',
            content_type='text/html'))
        context.route('https://login.example.test/**', lambda route: route.fulfill(
            body='<a href="https://app.example/app">Return to app</a>', content_type='text/html'))
        context.route('**/app/subscription-connect/*', endpoint)
        page = context.new_page()
        page.goto('https://app.example/app')
        expect(page.get_by_role('button', name='Connect', exact=True)).to_be_visible()
        assert page.locator('button:visible').count() == 1
        page.get_by_role('button', name='Connect', exact=True).click()
        if native:
            expect(page.get_by_role('textbox', name='Code returned after sign-in')).to_be_visible()
            assert page.evaluate('window.externalUrl') == 'https://login.example.test/authorize'
        else:
            page.wait_for_url('https://login.example.test/authorize')
            assert len(context.pages) == 1
            page.get_by_role('link', name='Return to app').click()
        expect(page.get_by_role('textbox', name='Code returned after sign-in')).to_be_visible()
        page.get_by_role('textbox', name='Code returned after sign-in').fill('approved')
        page.get_by_role('button', name='Finish sign-in', exact=True).click()
        expect(page.locator('body')).to_have_attribute('data-connected', 'yes')
        assert approved == ['approved']
        assert ('begin', {'source_id': 'fixture-cli'}) in calls
        assert ('finish', {'flow': 'fixture-flow'}) in calls
        assert page.evaluate("sessionStorage.getItem('ta_cli_login')") is None
        context.close()
        browser.close()


@pytest.mark.verifies("onboarding-web-app:Sign-in completion uses flow-bound owner consent")
def test_a_lapsed_owner_session_goes_straight_to_sign_in():
    from playwright.sync_api import expect, sync_playwright

    source = Path("tinyassets/onboarding/app/main.js").read_text("utf-8")
    cli = source[source.index("  const SubscriptionSources={"):
                 source.index("\n  };", source.index("  const SubscriptionSources={")) + 6]
    cli = source[source.index("  function copyButton("):
                 source.index("  function focusCommandCenter(")] + cli
    address = source[source.index("  const ModelDiscovery={"):
                     source.index("  // ---- End unified address connection ----")]
    script = """
      const $=id=>document.getElementById(id),NATIVE=false,DESKTOP_SHELL=false;
      const ConnectedAI={load(){}};
      const ownerProofUnavailable=()=>'Use a regular browser.';
      const CFG={model_source_address:'https://fixture.example'},MCP={_loginEpoch:1};
      const pkceStore=sessionStorage,queueOwner='owner',queueScope='center';
      const token=()=>true,ensureFreshToken=async()=>{},authHeaders=()=>({});
      const refreshRail=async()=>{},closeExternal=async()=>{},appendMessage=()=>{};
      const closeConnectRequest=()=>{};
      const openSignIn=async url=>{window.location.assign(url);};
      const sleep=ms=>new Promise(resolve=>setTimeout(resolve,25));
      const randToken=()=> 'v'.repeat(43),challengeFor=async()=> 'c'.repeat(43);
      const ConnectOAuth={post:async()=>{throw Error('no discovery after a refusal');}};
    """ + cli + address + """
      ModelDiscovery.render($('connect-panel'),true);
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        context.route('**/*', lambda route: route.abort())
        context.route('https://app.example/app', lambda route: route.fulfill(
            body='<div id="connect-panel"></div><script>'+script+'</script>',
            content_type='text/html'))
        context.route('https://app.example/app/owner-sign-in?app=1', lambda route: route.fulfill(
            body='signing in', content_type='text/html'))
        context.route('**/app/subscription-connect/*', lambda route: route.fulfill(
            status=403, content_type='application/json',
            body=json.dumps({'error': 'interactive_approval_required'})))
        page = context.new_page()
        page.goto('https://app.example/app')
        page.get_by_role('button', name='Connect', exact=True).click()
        page.wait_for_url('https://app.example/app/owner-sign-in?app=1')
        # Back in the app and still refused: say so instead of looping.
        page.goto('https://app.example/app')
        page.get_by_role('button', name='Connect', exact=True).click()
        expect(page.get_by_text('Signing in again did not restore access')).to_be_visible()
        context.close()
        browser.close()
