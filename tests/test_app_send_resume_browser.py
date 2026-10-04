"""Hermetic Chromium proof; lifecycle events simulate backgrounding, not Android OS.

Only fetch is scripted. The shipped send, SSE reader, watchdog, recovery UI and
owner read client execute in the rendered page. No live account/provider is used.
"""
import os

import pytest

from tests.test_app_chat_cloud_browser import _enter_chat
from tests.test_app_chat_cloud_browser import app_url as _app_url

app_url = _app_url

pytestmark = pytest.mark.real_browser


@pytest.fixture
def recovery_page(app_url):
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="owner=codex runs-in=real-browser-proof"
    )
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("TINYASSETS_TEST_CHROMIUM"))
        context = browser.new_context(viewport={"width": 390, "height": 844},
                                      is_mobile=True, has_touch=True)
        page = context.new_page()
        # Refuse external traffic, including any accidental production call.
        page.route("**/*", lambda route: route.continue_() if
                   route.request.url.startswith(app_url.rsplit("/", 1)[0] + "/")
                   else route.abort())
        _enter_chat(page, app_url)
        page.evaluate(r"""() => {
            setQueueScope('home-1');
            window.wire = {sends:[], reads:[], turns:[], hidden:false};
            Object.defineProperty(document, 'visibilityState', {
                configurable:true, get:()=>wire.hidden?'hidden':'visible'});
            MCP.sessionId='hermetic-session';
            const original=window.fetch;
            window.fetch=async (url, opts) => {
                if(url==='/mcp') {
                    const frame=JSON.parse(opts.body);
                    if(frame.method==='tools/call' && frame.params.name==='converse') {
                        wire.sends.push(frame);
                        return new Response(new ReadableStream({start(c){wire.stream=c;}}),
                            {headers:{'Content-Type':'text/event-stream'}});
                    }
                    throw new Error('unexpected MCP mutation/handshake');
                }
                if(url==='/app/api/status') {
                    const args=JSON.parse(opts.body); wire.reads.push(args);
                    if(wire.readError) throw new Error('offline');
                    if(wire.delayRead) await new Promise(r=>wire.releaseRead=r);
                    return Response.json({recent_conversation:{turns:wire.turns}});
                }
                if(url==='/app/turn/pending') return Response.json({pending:[]});
                if(url==='/app/api/read') return Response.json({});
                return original(url,opts);
            };
            MCP._pause=async()=>{};
            startSessionKeepAlive();
        }""")
        yield page
        context.close()
        browser.close()


def _send_and_interrupt(page, mode="cut"):
    from playwright.sync_api import expect

    page.locator("#composer-input").fill("Please finish the checklist")
    page.locator("#btn-send").click()
    page.wait_for_function("wire.sends.length===1 && !!wire.stream")
    page.evaluate("""mode => {
        wire.hidden=true; document.dispatchEvent(new Event('visibilitychange'));
        if(mode==='cut') {
            wire.stream.enqueue(new TextEncoder().encode('data: {"jsonrpc":'));
            wire.stream.close();
        } else if(mode==='disconnect') wire.stream.error(new TypeError('network lost'));
    }""", mode)
    expect(page.get_by_text("Delivery could not be confirmed:", exact=False)).to_be_visible()


def _complete_and_resume(page):
    page.evaluate("""() => {
        wire.turns=[{speaker:'founder',text:'Please finish the checklist',ts:Date.now()/1000},
            {speaker:'universe',text:'The checklist is finished.',ts:Date.now()/1000}];
        wire.hidden=false; document.dispatchEvent(new Event('visibilitychange'));
    }""")


@pytest.mark.parametrize("mode", ["cut", "disconnect", "silent"])
def test_resume_observes_saved_reply_without_replaying_or_claiming_delivery(
        recovery_page, mode, tmp_path):
    from playwright.sync_api import expect

    page = recovery_page
    if mode == "silent":
        page.evaluate("MCP.SILENCE_MS=300")
    _send_and_interrupt(page, mode)
    page.locator("#composer-input").fill("my next draft")
    _complete_and_resume(page)
    expect(page.locator("#thread pre")).to_contain_text(
        ["Please finish the checklist", "The checklist is finished."])
    expect(page.get_by_text("cannot tell which saved reply", exact=False)).to_be_visible()
    expect(page.locator("#composer-input")).to_have_value("my next draft")
    assert page.evaluate("wire.sends.length") == 1
    assert page.evaluate("readInflight().message") == "Please finish the checklist"
    assert page.locator(".msg--founder").count() == 1
    page.screenshot(path=tmp_path / (mode + "-resumed.png"))


def test_offline_resume_waits_for_online_and_coalesces_reads(recovery_page):
    from playwright.sync_api import expect

    page = recovery_page
    _send_and_interrupt(page)
    page.evaluate("""() => {
        wire.offline=true;
        Object.defineProperty(navigator,'onLine',{get:()=>!wire.offline});
        wire.hidden=false; document.dispatchEvent(new Event('visibilitychange'));
    }""")
    assert page.evaluate("wire.reads.length") == 0
    page.evaluate("""() => {
        wire.offline=false; wire.delayRead=true;
        for(let i=0;i<5;i++) window.dispatchEvent(new Event('online'));
    }""")
    page.wait_for_function("!!wire.releaseRead")
    assert page.evaluate("wire.reads.length") == 1
    page.evaluate("wire.delayRead=false;wire.releaseRead()")
    expect(page.get_by_text("nothing recorded yet", exact=False)).to_be_visible()
    _complete_and_resume(page)
    expect(page.locator("#thread pre")).to_have_count(2)
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_function("wire.reads.length===3")
    expect(page.locator("#thread pre")).to_have_count(2)
    assert page.evaluate("wire.sends.length") == 1


@pytest.mark.parametrize("changed", ["owner", "home", "epoch", "agent"])
@pytest.mark.parametrize("late", [False, True])
def test_resume_checks_and_late_results_are_thread_fenced(recovery_page, changed, late):
    from playwright.sync_api import expect

    page = recovery_page
    _send_and_interrupt(page)
    if late:
        page.evaluate("wire.delayRead=true")
        _complete_and_resume(page)
        page.wait_for_function("!!wire.releaseRead")
    page.evaluate("""changed => {
        if(changed==='owner') setQueueOwner('owner-b');
        if(changed==='home') setQueueScope('home-b');
        if(changed==='epoch') MCP._loginEpoch++;
        if(changed==='agent') addressedAgent={agent_id:'agent-b',name:'Agent B'};
    }""", changed)
    if late:
        page.evaluate("wire.releaseRead()")
    _complete_and_resume(page)
    # Await a task turn, not a timing assumption about network completion.
    page.evaluate("() => new Promise(r=>setTimeout(r,0))")
    assert page.evaluate("wire.reads.length") == (1 if late else 0)
    expect(page.locator("#thread pre")).to_have_count(0)
    assert page.evaluate("wire.sends.length") == 1


@pytest.mark.parametrize("kind", ["notSent", "provider"])
def test_confirmed_failure_does_not_become_ambiguous_resume(recovery_page, kind):
    from playwright.sync_api import expect

    page = recovery_page
    page.evaluate("""kind => {
        MCP.converse=async()=>{
            const err=new Error(kind==='provider'?'Provider failed':'Not admitted');
            if(kind==='notSent'){err.transport='offline';err.notSent=true;}
            else {err.served=true;err.historySaved=true;err.failureCode='provider_failed';}
            throw err;
        };
    }""", kind)
    page.locator("#composer-input").fill("Please finish the checklist")
    page.locator("#btn-send").click()
    expect(page.locator("#btn-send")).to_be_enabled()
    _complete_and_resume(page)
    page.evaluate("() => new Promise(r=>setTimeout(r,0))")
    assert page.evaluate("wire.reads.length") == 0
    assert page.get_by_role("button", name="Check saved conversation", exact=True).count() == 0


def test_stream_failure_after_visibility_event_also_checks(recovery_page):
    from playwright.sync_api import expect

    page = recovery_page
    page.locator("#composer-input").fill("Please finish the checklist")
    page.locator("#btn-send").click()
    page.wait_for_function("wire.sends.length===1 && !!wire.stream")
    page.evaluate("wire.hidden=true;document.dispatchEvent(new Event('visibilitychange'))")
    _complete_and_resume(page)
    assert page.evaluate("wire.reads.length") == 0  # the stream still owns the turn
    page.evaluate("wire.stream.close()")
    expect(page.locator("#thread pre")).to_contain_text(
        ["Please finish the checklist", "The checklist is finished."])
    assert page.evaluate("wire.sends.length") == 1
    assert page.evaluate("!!readInflight()")
