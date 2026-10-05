"""Hermetic Chromium proof; lifecycle events simulate backgrounding, not Android OS.

Only fetch is scripted. The shipped send, SSE reader, watchdog, recovery UI and
owner read client execute in the rendered page. No live account/provider is used.
"""
import pytest

from tests.test_app_chat_cloud_browser import _enter_chat
from tests.test_app_chat_cloud_browser import app_url as _app_url
from tests.test_app_two_surfaces_browser import browser as _browser

app_url = _app_url
browser = _browser

pytestmark = pytest.mark.real_browser


@pytest.fixture
def recovery_page(app_url, browser):
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
        window.wire = {sends:[], stops:[], reads:[], turns:[], hidden:false};
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
            if(url==='/app/turn/interrupt'){
                wire.stops.push(opts);return Response.json({interrupted:1});
            }
            if(url==='/app/turn/pending') return Response.json({pending:[],active:wire.active});
            if(url==='/app/api/read') return Response.json({});
            return original(url,opts);
        };
        MCP._pause=async()=>{};
        startSessionKeepAlive();
    }""")
    yield page
    context.close()


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
        wire.turns=[{speaker:'founder',text:'Please finish the checklist',ts:Date.now()/1000,
            client_send_id:readInflight()?.client_send_id},
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
    expect(page.locator("#thread .msg--universe")).to_contain_text("The checklist is finished.")
    expect(page.get_by_role("button", name="Send it again", exact=True)).to_have_count(0)
    assert page.evaluate("wire.stops.length") == 0
    expect(page.locator("#composer-input")).to_have_value("my next draft")
    assert page.evaluate("wire.sends.length") == 1
    assert page.evaluate("readInflight()") is None
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
    expect(page.get_by_text("Not confirmed yet", exact=False)).to_be_visible()
    _complete_and_resume(page)
    expect(page.locator("#thread .msg--universe")).to_have_count(1)
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.evaluate("() => new Promise(r=>setTimeout(r,0))")
    assert page.evaluate("wire.reads.length") == 2
    expect(page.locator("#thread .msg--universe")).to_have_count(1)
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
    expect(page.locator("#thread .msg--universe")).to_contain_text("The checklist is finished.")
    assert page.evaluate("wire.sends.length") == 1
    assert page.evaluate("readInflight()") is None


def test_restored_send_observes_reply_on_resume_without_replay(recovery_page):
    from playwright.sync_api import expect

    page = recovery_page
    page.evaluate("""async () => {
        wire.hidden=true;
        rememberInflight('Please finish the checklist','Please finish the checklist',Date.now(),
            'typed',null,null,'main',crypto.randomUUID());
        liveInflight=null; inflightRestored=false;
        await restoreInflight([]);
    }""")
    expect(page.get_by_text("This message was never confirmed", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Check saved conversation", exact=True)).to_be_visible()
    _complete_and_resume(page)
    expect(page.locator("#thread .msg--universe")).to_contain_text("The checklist is finished.")
    assert page.evaluate("wire.sends.length") == 0
    assert page.evaluate("readInflight()") is None


@pytest.mark.parametrize("bad", ["older", "truncated", "unstamped", "not_newest"])
def test_resume_requires_newest_complete_recent_founder_turn(recovery_page, bad):
    from playwright.sync_api import expect

    page = recovery_page
    _send_and_interrupt(page)
    page.evaluate("""bad => {
        const t={speaker:'founder',text:'Please finish the checklist',ts:Date.now()/1000};
        if(bad==='older') t.ts-=600;
        if(bad==='truncated') t.truncated=true;
        if(bad==='unstamped') delete t.ts;
        wire.turns=[t,{speaker:'universe',text:'An unrelated reply',ts:Date.now()/1000}];
        if(bad==='not_newest') wire.turns.push(
            {speaker:'founder',text:'something else',ts:Date.now()/1000});
        wire.hidden=false;document.dispatchEvent(new Event('visibilitychange'));
    }""", bad)
    expect(page.get_by_text("Not confirmed yet", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Send it again", exact=True)).to_have_count(0)
    assert page.evaluate("!!readInflight()")
    assert page.evaluate("wire.sends.length") == 1
    assert page.evaluate("wire.stops.length") == 0
    expect(page.locator("#thread pre")).to_have_count(0)


@pytest.mark.parametrize("restored", [False, True])
def test_resume_shows_a_matching_active_turn_without_resending(recovery_page, restored):
    from playwright.sync_api import expect

    page = recovery_page
    if restored:
        page.evaluate("""async () => {
            wire.hidden=true;
            rememberInflight('Please finish the checklist',
                'Please finish the checklist',Date.now(),'typed',null,null,'main',
                crypto.randomUUID());
            liveInflight=null;inflightRestored=false;
            await restoreInflight([]);
        }""")
        expect(page.get_by_role('button', name='Send it again', exact=True)).to_have_count(0)
    else:
        _send_and_interrupt(page)
    page.evaluate("""() => {
        wire.active={text:'Please finish the checklist',started_at:Date.now()/1000,
            client_send_id:readInflight().client_send_id};
        wire.hidden=false;document.dispatchEvent(new Event('visibilitychange'));
    }""")
    expect(page.get_by_text("Your agent is working on this.", exact=True)).to_be_visible()
    expect(page.locator(".msg--founder")).to_have_count(1)
    expect(page.get_by_role("button", name="Send it again", exact=True)).to_have_count(0)
    assert page.evaluate("wire.sends.length") == (0 if restored else 1)
    assert page.evaluate("wire.stops.length") == 0
    assert page.evaluate("!!readInflight()")  # retained until the watched turn finishes


@pytest.mark.parametrize("text", ["Yes", "No!", "okay", "Please explain the changes first"])
def test_request_chat_keeps_history_open_and_nudges_decisions(recovery_page, text):
    from playwright.sync_api import expect

    page = recovery_page
    page.evaluate("""() => {
        wire.answers=[];wire.chat=[];
        const req={request_id:'publish-1',title:'Publish the page',fields:[],status:'pending',
            action:{type:'publish'}};
        window.testRequest=req;
        MCP.answerRequest=async payload=>{wire.answers.push(payload);return {status:'answered'};};
        sendTurn=async line=>{wire.chat.push(line);};
        refreshRail=async()=>{};
        document.getElementById('thread').appendChild(railBody(req));
        InlineApprovals.history([{request_id:'publish-1',title:req.title,status:'answered'}],[req]);
    }""")
    page.locator('#fb_publish-1').fill(text)
    page.get_by_role('button', name='Send chat (keeps open)', exact=True).click()
    assert page.evaluate('wire.answers') == []
    expect(page.locator('#request-history')).to_contain_text('Publish the page \u00b7 Open')
    expect(page.locator('#request-history')).not_to_contain_text('answered')
    if text != 'Please explain the changes first':
        expect(page.locator('#note_publish-1')).to_contain_text('use Accept or Deny')
        assert page.evaluate('wire.chat') == []
        expect(page.locator('#fb_publish-1')).to_have_value(text)
        page.get_by_role('button', name='Accept', exact=True).click()
        assert len(page.evaluate('wire.answers')) == 1
        assert page.evaluate('wire.answers[0].decision || "accept"') == 'accept'
    else:
        assert page.evaluate('wire.chat') == ['About "Publish the page": ' + text]
        expect(page.locator('#note_publish-1')).to_contain_text('ask stays open')



def test_confirming_delivery_keeps_queued_approval_offered(recovery_page):
    from playwright.sync_api import expect

    page = recovery_page
    page.locator("#composer-input").fill("Please finish the checklist")
    page.locator("#btn-send").click()
    page.wait_for_function("wire.sends.length===1 && !!wire.stream")
    page.evaluate("""() => {
        sendTurn('Approved: "Publish page"',undefined,{relay:true,keepComposer:true});
        wire.hidden=true;
        wire.stream.error(new TypeError('network lost'));
    }""")
    expect(page.get_by_text("Delivery could not be confirmed:", exact=False)).to_be_visible()
    _complete_and_resume(page)
    expect(page.get_by_role("button", name="Send it again", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Send queued messages", exact=True)).to_be_visible()
    assert page.evaluate("sendQueueHeld")
    assert page.evaluate("sendQueue.length") == 1
    assert page.evaluate("wire.sends.length") == 1
    assert page.evaluate("readInflight()") is None

@pytest.mark.parametrize("restored", [False, True])
@pytest.mark.parametrize("identity", ["different", "missing_row", "missing_record", "match"])
def test_recent_old_identical_prompt_does_not_confirm_new_send(recovery_page, restored, identity):
    """Port of queue_recent_identical_prompt_probe.py, plus restore and causal controls."""
    from playwright.sync_api import expect

    page = recovery_page
    _send_and_interrupt(page)
    sent = page.evaluate("readInflight()")
    assert sent["client_send_id"] == page.evaluate(
        "wire.sends[0].params.arguments.client_send_id")
    page.evaluate("""async ({restored,identity}) => {
        const sent=readInflight(), before=(sent.ts-10000)/1000;
        const old={speaker:'founder',text:sent.message,ts:before,client_send_id:'previous-send'};
        const row={speaker:'founder',text:sent.message,ts:before,
            client_send_id:identity==='match'?sent.client_send_id:'other-device-send'};
        if(identity==='missing_row') delete row.client_send_id;
        if(identity==='missing_record'){
            delete sent.client_send_id;
            localStorage.setItem(INFLIGHT_KEY,JSON.stringify(sent));
        }
        wire.turns=[old,{speaker:'universe',text:'OLD REPLY FROM THE PREVIOUS SEND',ts:before+1},
            row,{speaker:'universe',text:'Only the matching reply',ts:before+2},
            {speaker:'founder',text:'later send',client_send_id:'later-send'},
            {speaker:'universe',text:'UNRELATED LATER REPLY'}];
        // Also exercise the active-turn matcher against identical text with no causal ID.
        wire.active={text:sent.message,started_at:before};
        if(restored){
            document.getElementById('thread').replaceChildren();
            liveInflight=null; inflightRestored=false;
            await restoreInflight(wire.turns);
        }else if(identity==='missing_record'){
            for(const note of document.getElementById('thread').children)
                if(note.unconfirmed) delete note.unconfirmed.client_send_id;
        }
        wire.hidden=false; window.dispatchEvent(new Event('focus'));
    }""", {"restored": restored, "identity": identity})
    if identity == "match":
        page.wait_for_function("readInflight()===null")
        expect(page.get_by_role("button", name="Send it again", exact=True)).to_have_count(0)
        if not restored:
            expect(page.locator("#thread .msg--universe .msg-body")).to_have_text(
                ["OLD REPLY FROM THE PREVIOUS SEND", "Only the matching reply",
                 "UNRELATED LATER REPLY"])
    else:
        expect(page.get_by_text("Not confirmed yet", exact=False)).to_be_visible()
        assert page.evaluate("readInflight().ts") == sent["ts"]
        expect(page.get_by_role("button", name="Send it again", exact=True)).to_have_count(0)
        expect(page.locator("#thread .msg--universe")).to_have_count(3)
    if not restored or identity != "match":
        expect(page.locator("#thread")).to_contain_text("OLD REPLY FROM THE PREVIOUS SEND")
        expect(page.locator("#thread")).to_contain_text("UNRELATED LATER REPLY")
    assert page.evaluate("wire.sends.length") == 1


@pytest.mark.parametrize("restored", [False, True])
def test_running_identity_precedes_saved_history(recovery_page, restored):
    from playwright.sync_api import expect

    page = recovery_page
    _send_and_interrupt(page)
    page.evaluate("""async restored => {
        const sent=readInflight();
        wire.active={text:sent.message,started_at:Date.now()/1000,client_send_id:sent.client_send_id};
        wire.turns=[{speaker:'founder',text:sent.message,client_send_id:sent.client_send_id},
            {speaker:'universe',text:'Do not draw while still running'}];
        if(restored){
            document.getElementById('thread').replaceChildren();
            liveInflight=null; inflightRestored=false;
            await restoreInflight(wire.turns);
        }else{
            wire.hidden=false;window.dispatchEvent(new Event('focus'));
        }
    }""", restored)
    expect(page.get_by_text("Your agent is working on this.", exact=True)).to_be_visible()
    assert page.evaluate("!!readInflight()")
    expect(page.locator("#thread .msg--universe")).to_have_count(0)


def test_desktop_focus_rechecks_after_initial_gap(recovery_page):
    from playwright.sync_api import expect

    page = recovery_page
    _send_and_interrupt(page)
    page.evaluate("wire.hidden=false;window.dispatchEvent(new Event('focus'))")
    expect(page.get_by_text("Not confirmed yet", exact=False)).to_be_visible()
    page.evaluate("""() => {
        const sent=readInflight();
        wire.turns=[{speaker:'founder',text:sent.message,client_send_id:sent.client_send_id},
            {speaker:'universe',text:'Finished after the gap'}];
        window.dispatchEvent(new Event('focus'));
    }""")
    expect(page.locator("#thread .msg--universe .msg-body")).to_have_text("Finished after the gap")
    assert page.evaluate("readInflight()") is None
    assert page.evaluate("wire.sends.length") == 1


def test_optional_connection_suggestion_is_not_open_history(recovery_page):
    from playwright.sync_api import expect

    page = recovery_page
    page.evaluate("""() => InlineApprovals.history([], [
        {request_id:'optional',title:'Connect another LLM',status:'optional'},
        {request_id:'real',title:'Publish page',status:'pending'}])""")
    expect(page.locator('#request-history')).to_contain_text('Publish page · Open')
    expect(page.locator('#request-history')).not_to_contain_text('Connect another LLM')


def test_watched_send_keeps_recovery_when_only_an_earlier_reply_is_saved(recovery_page):
    from playwright.sync_api import expect

    page = recovery_page
    _send_and_interrupt(page)
    page.evaluate("""() => {
        const sent=readInflight();
        wire.active={text:sent.message,client_send_id:sent.client_send_id};
        wire.hidden=false;window.dispatchEvent(new Event('focus'));
    }""")
    expect(page.get_by_text("Your agent is working on this.", exact=True)).to_be_visible()
    page.evaluate("""async () => {
        const sent=readInflight(); wire.active=null;
        wire.turns=[{speaker:'founder',text:sent.message,client_send_id:'previous-send',
            ts:(sent.ts-10000)/1000},{speaker:'universe',text:'Wrong previous reply'}];
        await finishActiveTurn();
    }""")
    expect(page.get_by_text("Not confirmed yet", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Send it again", exact=True)).to_have_count(0)
    expect(page.locator("#thread .msg--universe")).to_have_count(1)
    assert page.evaluate("!!readInflight()")
    _complete_and_resume(page)
    expect(page.locator("#thread .msg--universe .msg-body")).to_have_text(
        ["Wrong previous reply", "The checklist is finished."])
    assert page.evaluate("readInflight()") is None
    assert page.evaluate("wire.sends.length") == 1


@pytest.mark.parametrize("dismiss", [False, True])
def test_outage_recovers_automatically_without_focus_or_resend(recovery_page, dismiss):
    from playwright.sync_api import expect

    page = recovery_page
    _send_and_interrupt(page)
    page.evaluate("wire.readError=true;wire.hidden=false;window.dispatchEvent(new Event('focus'))")
    expect(page.get_by_text("Retrying automatically", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Send it again", exact=True)).to_have_count(0)
    expect(page.locator("#btn-send")).to_be_enabled()
    page.locator("#composer-input").fill("my next draft")
    if dismiss:
        page.get_by_role("button", name="Dismiss", exact=True).click()
        notice = page.get_by_text("Delivery could not be confirmed:", exact=False)
        expect(notice).not_to_be_visible()
    page.evaluate("""() => {
        wire.readError=false;
        const sent=readInflight();
        wire.turns=[{id:'101',speaker:'founder',text:sent.message,ts:100,
            client_send_id:sent.client_send_id},
            {id:'102',speaker:'universe',text:'Recovered automatically',ts:101}];
    }""")
    expect(page.locator("#thread .msg--universe .msg-body")).to_have_text("Recovered automatically")
    expect(page.get_by_role("button", name="Check saved conversation", exact=True)).to_have_count(0)
    expect(page.locator("#composer-input")).to_have_value("my next draft")
    assert page.evaluate("readInflight()") is None
    assert page.evaluate("wire.sends.length") == 1
    assert page.evaluate("wire.stops.length") == 0


def test_late_history_inserts_before_newer_notice_without_duplicates(recovery_page):
    page = recovery_page
    result = page.evaluate("""() => {
        appendMessage('system','Newer notice',null,200);
        const turns=[{id:'2',speaker:'universe',text:'Late answer',ts:100},
            {id:'1',speaker:'founder',text:'Earlier question',ts:90}];
        drawHistoryTurns(turns);drawHistoryTurns(turns);
        return Array.from(document.querySelectorAll('#thread .msg-body'),n=>n.textContent);
    }""")
    assert result == ['Earlier question', 'Late answer', 'Newer notice']
    assert page.evaluate("wire.sends.length") == 0


def test_recovery_deduplicates_previously_delivered_live_exchange(recovery_page):
    from playwright.sync_api import expect

    page = recovery_page
    page.evaluate("""async () => {
        const original=MCP.converse;
        MCP.converse=async()=>({reply:'First live answer'});
        await sendTurn('First live question');
        MCP.converse=original;
        wire.firstId=document.querySelector('#thread .msg--founder').clientSendId;
    }""")
    _send_and_interrupt(page)
    page.evaluate("""() => {
        const sent=readInflight();
        wire.turns=[{id:'1',speaker:'founder',text:'First live question',ts:1,
            client_send_id:wire.firstId},
            {id:'2',speaker:'universe',text:'First live answer',ts:1},
            {id:'3',speaker:'founder',text:sent.message,ts:2,client_send_id:sent.client_send_id},
            {id:'4',speaker:'universe',text:'Second saved answer',ts:2}];
        wire.hidden=false;window.dispatchEvent(new Event('focus'));
    }""")
    expect(page.locator('#thread .msg--universe .msg-body')).to_have_text(
        ['First live answer', 'Second saved answer'])
    expect(page.locator('#thread .msg--founder')).to_have_count(2)
    assert page.evaluate('readInflight()') is None
    assert page.evaluate('wire.sends.length') == 1


def test_history_uses_server_timestamps_before_row_ids_and_live_appends(recovery_page):
    page = recovery_page
    result = page.evaluate("""() => {
        drawHistoryTurns([{id:'20',speaker:'universe',text:'Later stored',ts:200},
            {id:'40',speaker:'founder',text:'Earlier but committed later',ts:100},
            {id:'21',speaker:'platform',text:'Same time next row',ts:200}]);
        appendMessage('founder','New live send on a slow clock',null,50);
        return Array.from(document.querySelectorAll('#thread .msg-body'),n=>n.textContent);
    }""")
    assert result[0:2] == ['Earlier but committed later', 'Later stored']
    assert result[2].startswith('Same time next row')
    assert result[3] == 'New live send on a slow clock'
    assert page.evaluate('wire.sends.length') == 0
