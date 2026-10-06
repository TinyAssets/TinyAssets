"""Pending requests remain reachable beside the latest messages in Chromium."""

import pytest

from tests.test_app_chat_cloud_browser import _enter_chat
from tests.test_app_chat_cloud_browser import app_url as _app_url
from tests.test_app_chat_cloud_browser import browser as _browser

app_url = _app_url
browser = _browser
pytestmark = pytest.mark.real_browser


def test_notification_is_informational_and_opens_source_chat(app_url, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _enter_chat(page, app_url)
    page.evaluate("""() => {
        window.openedAgents=[]; window.dismissals=[]; window.scrolledItems=[];
        HTMLElement.prototype.scrollIntoView=function(){
            if(this.dataset.itemId)scrolledItems.push(this.dataset.itemId);
        };
        drawHistoryTurns([{speaker:'universe',text:'Report details',ts:10,
            consumer_turn_id:'turn_report'}]);
        addressAgent=async agent=>openedAgents.push(agent.agent_id);
        MCP.callTool=async (name,args)=>{dismissals.push(args);return {status:'withdrawn'};};
        renderRail([{request_id:'notice',title:'Report ready',body:'All tests passed',
            agent:'social-manager',informational:true,requires_answer:false,
            action:{type:'notify',attachment_ref:'file_report',item_id:'turn_report'},fields:[],items:[]}]);
    }""")
    page.locator('#needs-you-open').click()
    page.get_by_role('button', name='Report ready', exact=False).click()
    sheet = page.locator('#request-rail')
    assert sheet.get_by_text('Notification · No answer needed').is_visible()
    assert sheet.get_by_text('Attachment: file_report').is_visible()
    assert sheet.locator('input:visible,textarea:visible').count() == 1
    assert sheet.get_by_role('textbox', name='Reply to notification').is_visible()
    assert sheet.get_by_role('button', name='Accept', exact=True).count() == 0
    sheet.get_by_role('button', name='Open chat', exact=True).click()
    assert page.evaluate('openedAgents') == ['social-manager']
    assert page.evaluate('scrolledItems') == ['turn_report']
    page.locator('#needs-you-open').click()
    page.get_by_role('button', name='Report ready', exact=False).click()
    sheet.get_by_role('button', name='Dismiss', exact=True).click()
    page.wait_for_function('dismissals.length === 1 && railCache.length === 0')
    assert page.evaluate('dismissals[0].operation') == 'withdraw_request'
    page.close()


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("expanded", [False, True])
def test_pending_requests_at_latest_and_new_arrival_answer(app_url, browser, width, expanded):
    page = browser.new_page(viewport={"width": width, "height": 844})
    _enter_chat(page, app_url)
    page.evaluate("""() => {
        window.asks = [
            {request_id:'reconnect',kind:'API',title:'Reconnect LinkedIn',fields:[]},
            {request_id:'github',kind:'API',title:'Let me file GitHub issues',fields:[]}
        ];
        window.answers = []; window.relays = [];
        token = () => 'test-session';
        Owner.listRequests = async () => ({pending:asks,recently_answered:[]});
        MCP.answerRequest = async payload => {
            answers.push(payload);
            asks = asks.filter(row => row.request_id !== payload.request_id);
            return {receipt:'Accepted.'};
        };
        sendTurn = async (...args) => { relays.push(args); };
        for(let i=0;i<100;i++) appendMessage(i%2?'founder':'agent',
            'History message '+i+' with enough text to fill the conversation.');
    }""")

    def in_view(selector):
        box = page.locator(selector).bounding_box()
        assert box is not None
        assert 0 <= box["x"] and box["x"] + box["width"] <= width
        assert 0 <= box["y"] and box["y"] + box["height"] <= 844
        assert page.locator('#needs-you').evaluate('el => el.matches(":modal")')
        pending = page.locator('#needs-you-items').bounding_box()
        assert pending['y'] <= box['y'] + 1
        assert box['y'] + box['height'] <= pending['y'] + pending['height'] + 1

    def at_latest():
        assert page.locator('#thread').evaluate(
            'el => el.scrollHeight > el.clientHeight * 5')
        assert page.locator('#thread').evaluate(
            'el => Math.abs(el.scrollHeight - el.clientHeight - el.scrollTop) < 2')
        thread = page.locator('#thread').bounding_box()
        last = page.locator('#thread .msg').last.bounding_box()
        assert last is not None
        # A tall message may exceed the compact cloud; its ending stays visible.
        assert thread['y'] < last['y'] + last['height']
        assert last['y'] + last['height'] <= thread['y'] + thread['height'] + 1

    page.wait_for_function("""() => {
        const el=document.getElementById('thread');
        return Math.abs(el.scrollHeight-el.clientHeight-el.scrollTop)<2;
    }""")
    at_latest()
    page.evaluate('renderRail(asks)')
    at_latest()
    assert page.locator('#pending-requests').count() == 0
    assert not page.locator('#request-rail').is_visible()
    page.locator('#needs-you-open').click()
    in_view('#needs-you-items button:nth-child(1)')
    in_view('#needs-you-items button:nth-child(2)')
    if width == 390:
        assert page.locator('#needs-you-items button').first.bounding_box()['width'] < 390
    if expanded:
        page.get_by_role('button', name='Reconnect LinkedIn', exact=False).click()
        assert page.locator('#request-rail').evaluate('el => el.matches(":modal")')
        page.locator('#fb_reconnect').fill('Unsent note')
        page.locator('#request-sheet-close').click()
    at_latest()
    page.evaluate("""() => {
        asks.push({request_id:'new',kind:'API',title:'A new request',fields:[]});
        renderRail(asks);
    }""")
    if not page.locator('#needs-you').is_visible():
        page.locator('#needs-you-open').click()
    in_view('#needs-you-items button:nth-child(3)')
    at_latest()
    if expanded:
        assert page.locator('#fb_reconnect').input_value() == 'Unsent note'
    page.get_by_role('button', name='A new request', exact=False).click()
    page.locator('#fb_new').fill('Go ahead')
    page.get_by_role('button', name='Accept', exact=True).click()
    page.wait_for_function(
        '() => answers.length === 1 && !document.querySelector("#request-rail").open')
    assert page.evaluate('answers') == [
        {'request_id': 'new', 'feedback': 'Go ahead', 'values': {}}
    ]
    assert page.evaluate('relays') == []  # Routing is committed with the answer on the server.
    assert not page.locator('#request-rail').is_visible()
    page.locator('#needs-you-open').click()
    assert page.locator('#needs-you-items button').count() == 2
    in_view('#needs-you-items button:nth-child(1)')
    in_view('#needs-you-items button:nth-child(2)')
    page.locator('#needs-you-close').click()
    # A poll must also leave someone reading older messages where they were.
    old_scroll = page.locator('#thread').evaluate('el => (el.scrollTop = 200)')
    page.evaluate("""() => {
        asks.push({request_id:'while-reading',kind:'API',title:'Later request',fields:[]});
        renderRail(asks);
    }""")
    assert page.locator('#thread').evaluate('el => el.scrollTop') == old_scroll
    page.close()
