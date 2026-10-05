"""Chromium scroll contracts for the SPA shared by web and native shells."""
import os

import pytest

from tests.test_app_chat_cloud_browser import _enter_chat
from tests.test_app_chat_cloud_browser import app_url as _app_url

app_url = _app_url
pytestmark = pytest.mark.real_browser


@pytest.fixture(params=[390, 1280], ids=["phone", "desktop"])
def chat_page(app_url, request):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("TINYASSETS_TEST_CHROMIUM"))
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
        browser.close()


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
    page.evaluate("""event => {
        if(event==='visibility') {
            hiddenForTest=true;document.dispatchEvent(new Event('visibilitychange'));
            hiddenForTest=false;document.dispatchEvent(new Event('visibilitychange'));
        } else (event==='resume'?document:window).dispatchEvent(new Event(event));
    }""", event)
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
        page.evaluate("window.dispatchEvent(new Event('focus'))")
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
