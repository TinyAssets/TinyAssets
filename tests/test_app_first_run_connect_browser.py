"""Chromium proof of the shipped send/card/popup/resume code, with scripted HTTP."""

import os

import pytest

from tests.test_app_chat_cloud_browser import _enter_chat
from tests.test_app_chat_cloud_browser import app_url as _app_url

app_url = _app_url
pytestmark = pytest.mark.real_browser


@pytest.fixture(params=[390, 1280])
def page(app_url, request):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("TINYASSETS_TEST_CHROMIUM"))
        width, native = (
            request.param if isinstance(request.param, tuple) else (request.param, False)
        )
        context = browser.new_context(viewport={"width": width, "height": 844})
        if native:
            context.add_init_script("""window.nativeOpened=[];
                window.Capacitor={isNativePlatform:()=>true,Plugins:{Browser:{
                    open:async options=>{nativeOpened.push(options);},close:async()=>{}
                }}};""")
        context.route("**/*", lambda route: route.continue_() if
                      route.request.url.startswith(app_url.rsplit("/", 1)[0] + "/")
                      else route.abort())
        page = context.new_page()
        _enter_chat(page, app_url)
        page.evaluate(r"""() => {
            setQueueScope('home-1');MCP.sessionId='test-session';
            sessionStorage.setItem(TOKEN_KEY,'test-token');
            sessionStorage.setItem(EXP_KEY,String(Date.now()+3600000));
            window.wire={sends:[],answers:0,connected:false,flow:'waiting'};
            const original=window.fetch;
            window.fetch=async(url,opts)=>{
                if(url==='/mcp'){
                    const frame=JSON.parse(opts.body);
                    if(frame.method==='tools/call'){
                        let result;
                        if(frame.params.name==='converse'){
                            wire.sends.push(frame.params.arguments);
                            result=wire.connected?{reply:'Your day is planned.'}:{
                                status:'held',reason:'setup_required',history_saved:true,
                                turn_failure:{code:'setup_required',effects:'none'},
                                needs_connection:{action:{type:'connect',setup:{primary:{
                                    preset_id:'test_source',name:wire.provider||'OpenRouter',
                                    label:'Connect '+(wire.provider||'OpenRouter')
                                }}}}
                            };
                        }else{
                            throw new Error('Unexpected bearer answer');
                        }
                        return Response.json({jsonrpc:'2.0',id:frame.id,result:{
                            content:[{type:'text',text:JSON.stringify(result)}]}});
                    }
                    throw new Error('Unexpected handshake');
                }
                if(url==='/app/model-connect/inline_begin'&&wire.needsOwnerLogin)
                    return Response.json({error:'interactive_approval_required'},{status:403});
                if(url==='/app/model-connect/inline_begin')return Response.json({
                    flow:'a'.repeat(43),launch_path:'/app/model-callback/'+'a'.repeat(43)+'?launch=1'});
                if(url==='/app/model-connect/inline_poll'){
                    if(wire.flow!=='ready')return Response.json({status:wire.flow});
                    wire.flow='consumed';wire.answers++;wire.connected=true;
                    return Response.json({status:'confirmation_required',request_id:'free-request',
                        request:{request_id:'free-request',action:{type:'bind_model_access'}},
                        answer:{status:'answered'}});
                }
                if(url==='/app/model-connect/inline_cancel'){
                    if(wire.delayCancel)await new Promise(resolve=>wire.releaseCancel=resolve);
                    wire.flow='cancelled';return Response.json({status:'cancelled'});}
                if(url==='/app/me')return Response.json({
                    setup:wire.connected?'connected':'empty',
                    principal_id:'owner-1',universe_id:'home-1'});
                if(url==='/app/turn/pending')return Response.json({pending:[]});
                if(url==='/app/api/read'||url==='/app/api/status')return Response.json({});
                return original(url,opts);
            };
        }""")
        yield page
        context.close()
        browser.close()


def send(page):
    from playwright.sync_api import expect

    page.locator("#composer-input").fill("Help me plan my day")
    page.locator("#btn-send").click()
    expect(page.get_by_role("region", name="Needs a connection")).to_be_visible()
    assert page.evaluate("readInflight().message") == "Help me plan my day"


def test_connect_answers_original_once_at_phone_and_desktop_width(page, tmp_path):
    from playwright.sync_api import expect

    send(page)
    card = page.get_by_role("region", name="Needs a connection")
    assert card.evaluate("e=>e.scrollWidth<=e.clientWidth")
    page.locator("#composer-input").fill("an unsent draft")
    with page.expect_popup():
        card.get_by_role("button", name="Connect OpenRouter", exact=True).click()
    page.evaluate("wire.flow='ready'")
    expect(card.get_by_role("status")).to_have_text("Connected", timeout=10000)
    expect(page.get_by_text("Your day is planned.", exact=True)).to_be_visible()
    assert page.evaluate("wire.sends.map(s=>s.message)") == ["Help me plan my day"] * 2
    assert page.evaluate("wire.answers") == 1
    page.evaluate("InlineConnection.check()")
    assert page.evaluate("wire.sends.length") == 2
    expect(page.locator("#composer-input")).to_have_value("an unsent draft")
    assert page.locator(".msg--founder").count() == 1
    page.screenshot(path=tmp_path / "connected.png")


def test_cancel_keeps_original_and_offers_retry(page):
    from playwright.sync_api import expect

    send(page)
    with page.expect_popup():
        page.get_by_role("button", name="Connect OpenRouter", exact=True).click()
    page.get_by_role("button", name="Cancel sign-in", exact=True).click()
    expect(page.get_by_role("button", name="Retry", exact=True)).to_be_visible()
    assert page.evaluate("readInflight().message") == "Help me plan my day"
    assert page.evaluate("wire.sends.length") == 1
    assert page.evaluate("wire.answers") == 0


def test_owner_change_does_not_resume_or_render_foreign_message(page):
    send(page)
    page.evaluate("""() => {
        clearAccountScopedState();setQueueOwner('other-owner');setQueueScope('other-home');
        wire.connected=true;
        InlineConnection.check();
    }""")
    assert page.evaluate("wire.sends.length") == 1
    assert page.locator(".needs-connection").count() == 0


def test_reload_restores_waiting_message_and_continues_once(page):
    from playwright.sync_api import expect

    send(page)
    # Re-enter the shipped restore path with its durable record, as on a reload.
    page.evaluate("""async()=>{
        clearThread();InlineConnection.card=null;InlineConnection.record=null;
        inflightRestored=false;liveInflight=null;await restoreInflight([]);
        wire.connected=true;await InlineConnection.check();
    }""")
    expect(page.get_by_text("Your day is planned.", exact=True)).to_be_visible()
    assert page.evaluate("wire.sends.length") == 2


def test_primary_provider_name_comes_from_setup(page):
    from playwright.sync_api import expect

    page.evaluate("wire.provider='Example AI'")
    send(page)
    expect(page.get_by_role("button", name="Connect Example AI", exact=True)).to_be_visible()
    assert page.get_by_role("button", name="Connect OpenRouter", exact=True).count() == 0


def test_cancel_then_retry_connects_and_resumes_once(page):
    from playwright.sync_api import expect

    send(page)
    with page.expect_popup():
        page.get_by_role("button", name="Connect OpenRouter", exact=True).click()
    page.get_by_role("button", name="Cancel sign-in", exact=True).click()
    expect(page.get_by_role("button", name="Retry", exact=True)).to_be_visible()
    page.evaluate("wire.flow='waiting'")
    with page.expect_popup():
        page.get_by_role("button", name="Retry", exact=True).click()
    page.evaluate("wire.flow='ready'")
    expect(page.get_by_text("Your day is planned.", exact=True)).to_be_visible(timeout=10000)
    assert page.evaluate("wire.sends.map(s=>s.message)") == ["Help me plan my day"] * 2
    assert page.evaluate("wire.answers") == 1


def test_second_message_waits_until_original_has_resumed(page):
    from playwright.sync_api import expect

    send(page)
    page.locator("#composer-input").fill("Then plan tomorrow")
    page.locator("#btn-send").click()
    assert page.evaluate("wire.sends.length") == 1
    assert page.evaluate("readInflight().message") == "Help me plan my day"
    with page.expect_popup():
        page.get_by_role("button", name="Connect OpenRouter", exact=True).click()
    page.evaluate("wire.flow='ready'")
    expect(page.get_by_text("Your day is planned.", exact=True)).to_have_count(2, timeout=10000)
    assert page.evaluate("wire.sends.map(s=>s.message)") == [
        "Help me plan my day", "Help me plan my day", "Then plan tomorrow"
    ]
    assert page.evaluate("wire.answers") == 1


@pytest.mark.parametrize("page", [(390, True)], indirect=True)
def test_native_browser_handoff_returns_to_original_message(page):
    from playwright.sync_api import expect

    send(page)
    page.get_by_role("button", name="Connect OpenRouter", exact=True).click()
    expect(page.get_by_role("button", name="Cancel sign-in", exact=True)).to_be_visible()
    opened = page.evaluate("nativeOpened")
    assert len(opened) == 1
    assert opened[0]["url"].endswith("/app/model-callback/" + "a" * 43 + "?launch=1")
    assert opened[0]["presentationStyle"] == "popover"
    assert len(page.context.pages) == 1
    page.evaluate("wire.flow='ready'")
    expect(page.get_by_text("Your day is planned.", exact=True)).to_be_visible(timeout=10000)
    assert page.evaluate("wire.sends.map(s=>s.message)") == ["Help me plan my day"] * 2
    assert page.evaluate("wire.answers") == 1


def test_late_cancel_cannot_replace_connected_card(page):
    from playwright.sync_api import expect

    send(page)
    with page.expect_popup():
        page.get_by_role("button", name="Connect OpenRouter", exact=True).click()
    page.evaluate("wire.delayCancel=true")
    page.get_by_role("button", name="Cancel sign-in", exact=True).click()
    # Another connection finishes while the cancellation response is in flight.
    page.evaluate("wire.connected=true")
    expect(page.get_by_text("Your day is planned.", exact=True)).to_be_visible(timeout=10000)
    page.evaluate("wire.releaseCancel()")
    card = page.get_by_role("region", name="Needs a connection")
    expect(card.get_by_role("status")).to_have_text("Connected")
    assert page.evaluate("wire.sends.length") == 2


def test_missing_owner_session_offers_protected_sign_in_and_keeps_message(page):
    from playwright.sync_api import expect

    send(page)
    page.evaluate("wire.needsOwnerLogin=true")
    card = page.get_by_role("region", name="Needs a connection")
    card.get_by_role("button", name="Connect OpenRouter", exact=True).click()
    link = card.get_by_role("link", name="Sign in to approve")
    expect(link).to_be_visible()
    expect(link).to_have_attribute("href", "/app/owner-sign-in")
    assert page.evaluate("readInflight().message") == "Help me plan my day"
    assert page.evaluate("wire.answers") == 0
    assert page.evaluate("wire.sends.length") == 1


def test_generic_sign_in_without_owner_session_offers_protected_login(page):
    from playwright.sync_api import expect

    page.evaluate("""async()=>{
        const original=window.fetch;
        window.fetch=async(url,opts)=>url==='/app/model-connect/oauth_begin'
            ? Response.json({error:'interactive_approval_required'},{status:403})
            : original(url,opts);
        const note=document.createElement('div');note.id='oauth-start-status';
        const button=document.createElement('button');button.id='oauth-start-button';
        document.body.append(note,button);
        await ConnectOAuth.begin({request_id:'owner-request',title:'Connect'},note,button);
    }""")
    link = page.locator("#oauth-start-status").get_by_role("link", name="Sign in to approve")
    expect(link).to_be_visible()
    expect(link).to_have_attribute("href", "/app/owner-sign-in")
    expect(page.locator("#oauth-start-button")).to_be_enabled()
    assert page.evaluate("sessionStorage.getItem(ConnectOAuth.storageKey)") is None
