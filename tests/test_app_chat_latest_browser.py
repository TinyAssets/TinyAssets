"""Chromium scroll contracts for the SPA shared by web and native shells."""
import pytest

from tests.test_app_chat_cloud_browser import _enter_chat
from tests.test_app_chat_cloud_browser import app_url as _app_url
from tests.test_app_two_surfaces_browser import browser as _browser

app_url = _app_url
browser = _browser
pytestmark = pytest.mark.real_browser


@pytest.fixture(params=[390, 1280], ids=["phone", "desktop"])
def chat_page(app_url, request, browser):
    page = browser.new_page(viewport={"width": request.param, "height": 844})
    page.route("**/*", lambda route: route.continue_() if
               route.request.url.startswith(app_url.rsplit("/", 1)[0] + "/")
               else route.abort())
    _enter_chat(page, app_url)
    page.evaluate("""() => {
        setQueueScope('home-1');
        sessionStorage.setItem(TOKEN_KEY,'hermetic-token');
        window.turns=Array.from({length:80},(_,i)=>({speaker:i%2?'universe':'founder',
            text:'Message '+i+' '+('Long conversation line. '.repeat(12)),ts:i+100}));
        Owner.getConversation=async()=>({universe_id:'home-1',recent_conversation:{
            turns,has_more:true,next_before:100}});
        window.hiddenForTest=false;
        Object.defineProperty(document,'visibilityState',{
            configurable:true,get:()=>hiddenForTest?'hidden':'visible'});
    }""")
    yield page
    page.close()


def _bottom(page):
    page.wait_for_function("""() => {
        const t=document.getElementById('thread');
        return t.clientHeight>0 && t.scrollHeight>t.clientHeight &&
            t.scrollHeight-t.clientHeight-t.scrollTop<=2;
    }""")


def _settle(page):
    page.evaluate("() => new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")


def _scroll_up(page):
    page.locator("#thread").hover()
    page.mouse.wheel(0, -600)
    page.wait_for_function("""() => {
        const t=document.getElementById('thread');
        return t.scrollHeight-t.clientHeight-t.scrollTop>100;
    }""")
    _settle(page)


@pytest.mark.parametrize("hidden", [False, True], ids=["visible-load", "hidden-load"])
def test_initial_long_history_opens_at_latest(chat_page, hidden):
    page = chat_page
    if hidden:
        page.click("#btn-cloud-shrink")
    page.evaluate("loadHistory()")
    if hidden:
        page.click("#chat-cloud-bubble")
    _bottom(page)


def test_late_history_and_layout_growth_follow_latest(chat_page):
    page = chat_page
    page.evaluate("""() => {
        const read=Owner.getConversation;
        Owner.getConversation=async()=>{
            await new Promise(r=>window.releaseHistory=r);return read();};
        loadHistory();
    }""")
    page.wait_for_function("() => !!window.releaseHistory")
    page.evaluate("releaseHistory()")
    _bottom(page)
    page.evaluate("""() => {
        const card=document.createElement('div');card.id='late-card';
        card.textContent='Loading card';
        document.querySelector('#thread .msg:last-child').appendChild(card);
    }""")
    _settle(page)
    page.evaluate("document.getElementById('late-card').style.height='900px'")
    _bottom(page)


def test_keyboard_reader_can_scroll_back_to_latest_and_follow_again(chat_page):
    page = chat_page
    page.evaluate("loadHistory()")
    _bottom(page)
    page.locator("#thread").focus()
    page.keyboard.press("Home")
    page.wait_for_function("() => document.getElementById('thread').scrollTop===0")
    page.evaluate("appendMessage('universe','Arrived while reading')")
    _settle(page)
    assert page.locator("#thread").evaluate("t=>t.scrollTop") == 0
    page.keyboard.press("End")
    _bottom(page)
    page.evaluate("appendMessage('universe','Following again '.repeat(100))")
    _bottom(page)


@pytest.mark.parametrize("event", ["visibility", "focus", "resume", "pageshow"])
def test_return_to_app_resets_reader_to_latest(chat_page, event):
    page = chat_page
    page.evaluate("loadHistory()")
    _bottom(page)
    _scroll_up(page)
    page.evaluate("""async event => {
        if(event==='visibility') {
            hiddenForTest=true;document.dispatchEvent(new Event('visibilitychange'));
            hiddenForTest=false;document.dispatchEvent(new Event('visibilitychange'));
        } else {
            if(event==='focus') {
                window.dispatchEvent(new Event('blur'));
                await new Promise(r=>setTimeout(r,0));
            }
            (event==='resume'?document:window).dispatchEvent(new Event(event));
        }
    }""", event)
    _bottom(page)


@pytest.mark.parametrize("target", ["body", "thread"])
def test_window_return_with_command_center_lands_on_latest(chat_page, target):
    page = chat_page
    page.evaluate("loadHistory()")
    _bottom(page)
    _scroll_up(page)
    # Keep the production listeners and their boot order, including the focus
    # handler that really focuses the iframe and emits a nested window blur.
    page.evaluate("""target => {
        const frame=document.createElement('iframe');frame.id='ui-frame';
        const host=document.getElementById('ui-frame-host');
        host.hidden=false;host.replaceChildren(frame);
        const active=target==='body'?document.body:document.getElementById('thread');
        active.tabIndex=-1;active.focus();
        window.returnEvents=[];
        window.addEventListener('blur',()=>returnEvents.push(document.activeElement.tagName));
    }""", target)
    expected_tag = "BODY" if target == "body" else "DIV"
    assert page.evaluate("document.activeElement.tagName") == expected_tag
    page.evaluate("""async () => {
        window.dispatchEvent(new Event('blur'));
        await new Promise(r=>setTimeout(r,0));
        window.dispatchEvent(new Event('focus'));
    }""")
    page.wait_for_function("() => document.activeElement.id==='ui-frame'")
    assert "IFRAME" in page.evaluate("returnEvents")
    _bottom(page)


@pytest.mark.parametrize("event", ["visibility", "resume"])
def test_native_picker_return_preserves_reader_position(chat_page, event):
    page = chat_page
    page.evaluate("loadHistory()")
    _bottom(page)
    _scroll_up(page)
    before = page.locator("#thread").evaluate("t=>t.scrollTop")
    with page.expect_file_chooser():
        page.click("#btn-attach")
    page.evaluate("""event => {
        hiddenForTest=true;document.dispatchEvent(new Event('visibilitychange'));
        hiddenForTest=false;
        document.dispatchEvent(new Event(event==='visibility'?'visibilitychange':'resume'));
        document.getElementById('file-input').dispatchEvent(new Event('cancel'));
    }""", event)
    _settle(page)
    page.evaluate("appendMessage('universe','Arrived after native picker')")
    _settle(page)
    assert page.locator("#thread").evaluate("t=>t.scrollTop") == pytest.approx(before, abs=2)
    page.evaluate("document.dispatchEvent(new Event('resume'))")
    _bottom(page)


@pytest.mark.parametrize("return_while_loading", [False, True])
def test_show_earlier_preserves_visible_message_position(chat_page, return_while_loading):
    page = chat_page
    page.evaluate("loadHistory()")
    _bottom(page)
    page.locator("#thread").hover()
    page.mouse.wheel(0, -100000)
    page.wait_for_function("() => document.getElementById('thread').scrollTop===0")
    page.evaluate("""() => {
        window.anchor=document.querySelector('#thread .msg');
        window.anchorTop=anchor.getBoundingClientRect().top;
        Owner.getConversation=async()=>{
            await new Promise(r=>window.releaseEarlier=r);
            return {recent_conversation:{turns:turns.map(t=>({...t,ts:t.ts-100,
                text:'Earlier '+t.text})),has_more:false}};};
    }""")
    page.get_by_role("button", name="Show earlier messages", exact=True).click()
    page.wait_for_function("() => !!window.releaseEarlier")
    if return_while_loading:
        page.evaluate("""async () => {
            window.dispatchEvent(new Event('blur'));
            await new Promise(r=>setTimeout(r,0));
            window.dispatchEvent(new Event('focus'));
        }""")
        _bottom(page)
    page.evaluate("releaseEarlier()")
    page.wait_for_function("() => document.querySelectorAll('#thread .msg').length===160")
    _settle(page)
    if return_while_loading:
        _bottom(page)
    else:
        offset = page.evaluate("anchor.getBoundingClientRect().top-anchorTop")
        assert offset == pytest.approx(0, abs=2)
        assert page.evaluate("document.getElementById('thread').scrollTop") > 100


def test_reload_with_saved_collapsed_cloud_loads_latest(chat_page, app_url):
    page = chat_page
    page.evaluate("loadHistory()")
    _bottom(page)
    _scroll_up(page)
    page.click("#btn-cloud-shrink")
    # Clear only this hermetic token so boot does not attempt a real sign-in.
    page.evaluate("sessionStorage.removeItem(TOKEN_KEY)")
    _enter_chat(page, app_url)
    assert page.locator("#chat-cloud-bubble").is_visible()
    page.evaluate("""() => {
        drawHistoryTurns(Array.from({length:80},(_,i)=>({speaker:'universe',
            text:'Reloaded history '+i+' '+('Long line. '.repeat(30)),ts:i})));
    }""")
    page.click("#chat-cloud-bubble")
    _bottom(page)


@pytest.mark.parametrize("draw", ["append", "history", "finish"])
def test_new_messages_respect_user_scroll_until_reopen(chat_page, draw):
    page = chat_page
    page.evaluate("loadHistory()")
    _bottom(page)
    _scroll_up(page)
    before = page.locator("#thread").evaluate("t=>t.scrollTop")
    page.evaluate("""async draw => {
        if(draw==='append') appendMessage('universe','A new arriving answer');
        if(draw==='history') drawHistoryTurns([
            {speaker:'universe',text:'Recovered answer',ts:999}]);
        if(draw==='finish') {
            watchedActive={text:turns[78].text};
            Owner.getConversation=async()=>({recent_conversation:{turns:[turns[78],
                {speaker:'universe',text:'Finished answer',ts:999}]}});
            await finishActiveTurn();
        }
    }""", draw)
    _settle(page)
    assert page.locator("#thread").evaluate("t=>t.scrollTop") == pytest.approx(before, abs=2)
    page.click("#btn-cloud-shrink")
    page.click("#chat-cloud-bubble")
    _bottom(page)


@pytest.mark.parametrize("change", ["agent", "home", "view", "same-agent"])
def test_switching_chat_context_starts_at_latest(chat_page, change):
    page = chat_page
    page.evaluate("loadHistory()")
    _bottom(page)
    _scroll_up(page)
    page.evaluate("""async change => {
        if(change==='agent') await addressAgent({agent_id:'villager',name:'Villager'});
        if(change==='same-agent') await addressAgent({agent_id:'main',name:'Your agent'});
        if(change==='home') setQueueScope('home-2');
        if(change==='view'){showView('signin');showView('chat');}
    }""", change)
    _bottom(page)


@pytest.mark.parametrize("surface", ["iframe", "picker"])
def test_in_app_focus_handoff_preserves_reader_position(chat_page, surface):
    page = chat_page
    page.evaluate("loadHistory()")
    _bottom(page)
    _scroll_up(page)
    before = page.locator("#thread").evaluate("t=>t.scrollTop")
    if surface == "iframe":
        page.evaluate("""() => {
            const frame=document.createElement('iframe');frame.id='ui-frame';
            const host=document.getElementById('ui-frame-host');
            host.hidden=false;host.replaceChildren(frame);
            frame.contentWindow.focus();
        }""")
        page.wait_for_function("() => document.activeElement.id==='ui-frame'")
        _settle(page)
        page.locator("#composer-input").focus()
    else:
        with page.expect_file_chooser():
            page.click("#btn-attach")
        # Playwright intercepts the native dialog. Replay its OS focus lifecycle
        # after the real attach button has opened the chooser, then cancel it.
        page.evaluate("window.dispatchEvent(new Event('blur'))")
        _settle(page)
        page.evaluate("""() => {
            window.dispatchEvent(new Event('focus'));
            document.getElementById('file-input').dispatchEvent(new Event('cancel'));
        }""")
    _settle(page)
    page.evaluate("appendMessage('universe','Arrived after surface handoff')")
    _settle(page)
    assert page.locator("#thread").evaluate("t=>t.scrollTop") == pytest.approx(before, abs=2)
    # The exclusion must not suppress the next actual departure and return.
    page.locator("#composer-input").focus()
    _settle(page)
    page.evaluate("window.dispatchEvent(new Event('blur'))")
    _settle(page)
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    _bottom(page)


@pytest.mark.parametrize("gesture", ["nested-wheel", "scrollbar"])
def test_gesture_without_thread_movement_keeps_following(chat_page, gesture):
    page = chat_page
    page.evaluate("loadHistory()")
    _bottom(page)
    if gesture == "nested-wheel":
        page.evaluate("""() => {
            const nested=document.createElement('div');nested.id='nested-scroll';
            nested.style.cssText='height:100px;overflow:auto;overscroll-behavior:contain';
            const content=document.createElement('div');content.style.height='1000px';
            content.textContent='Scrollable card';nested.append(content);
            document.querySelector('#thread .msg:last-child').append(nested);
            nested.scrollTop=500;
        }""")
        _bottom(page)
        page.locator("#nested-scroll").hover()
        page.mouse.wheel(0, -100)
        page.wait_for_function("() => document.getElementById('nested-scroll').scrollTop<500")
    else:
        # Overlay scrollbars have no clickable gutter in headless Chromium.
        page.locator("#thread").evaluate("""t=>{
            t.dispatchEvent(new PointerEvent('pointerdown',{
                clientX:t.getBoundingClientRect().left+t.clientWidth,bubbles:true}));
            window.dispatchEvent(new PointerEvent('pointerup'));
        }""")
    page.wait_for_function("() => document.getElementById('thread').chatScroll.following")
    page.evaluate("appendMessage('universe','Following after stationary gesture '.repeat(100))")
    _bottom(page)


def test_earlier_page_keeps_following_when_reader_is_at_bottom(chat_page):
    page = chat_page
    page.evaluate("loadHistory()")
    _bottom(page)
    page.evaluate("""async () => {
        Owner.getConversation=async()=>({recent_conversation:{
            turns:[{speaker:'universe',text:'Earlier message',ts:1}],has_more:false}});
        await loadEarlier(document.getElementById('thread').firstElementChild,100,()=>true);
    }""")
    _bottom(page)
    page.evaluate("appendMessage('universe','Following after earlier page '.repeat(100))")
    _bottom(page)
