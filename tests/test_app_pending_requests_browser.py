"""Pending requests remain reachable beside the latest messages in Chromium."""

import pytest

from tests.test_app_chat_cloud_browser import _enter_chat
from tests.test_app_chat_cloud_browser import app_url as _app_url
from tests.test_app_chat_cloud_browser import browser as _browser

app_url = _app_url
browser = _browser
pytestmark = pytest.mark.real_browser


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
        renderRail(asks);
        for(let i=0;i<100;i++) appendMessage(i%2?'founder':'agent',
            'History message '+i+' with enough text to fill the conversation.');
    }""")

    def in_view(selector):
        box = page.locator(selector).bounding_box()
        assert box is not None
        assert 0 <= box["x"] and box["x"] + box["width"] <= width
        assert 0 <= box["y"] and box["y"] + box["height"] <= 844
        composer = page.locator('#composer').bounding_box()
        assert box["y"] + box["height"] <= composer["y"] + 1
        pending = page.locator('#pending-requests').bounding_box()
        assert pending['y'] <= box['y'] + 1
        assert box['y'] + box['height'] <= pending['y'] + pending['height'] + 1

    def at_latest():
        assert page.locator('#thread').evaluate(
            'el => el.scrollHeight > el.clientHeight * 5')
        assert page.locator('#thread').evaluate(
            'el => Math.abs(el.scrollHeight - el.clientHeight - el.scrollTop) < 2')

    at_latest()
    in_view('#rail-items .rtab:nth-child(1) .rtab-btn')
    in_view('#rail-items .rtab:nth-child(2) .rtab-btn')
    if width == 390:
        assert page.locator('#rail-items .rtab-btn').first.bounding_box()['height'] < 40
    if expanded:
        page.get_by_role('button', name='API Reconnect LinkedIn').click()
        page.locator('#fb_reconnect').fill('Unsent note')
    page.evaluate("""() => {
        asks.push({request_id:'new',kind:'API',title:'A new request',fields:[]});
        renderRail(asks);
    }""")
    in_view('#rail-items .rtab:nth-child(3) .rtab-btn')
    if expanded:
        assert page.locator('#fb_reconnect').input_value() == 'Unsent note'
    page.get_by_role('button', name='API A new request').click()
    page.locator('#fb_new').fill('Go ahead')
    page.get_by_role('button', name='Accept', exact=True).click()
    page.wait_for_function('answers.length === 1 && relays.length === 1')
    assert page.evaluate('answers') == [
        {'request_id': 'new', 'feedback': 'Go ahead', 'values': {}}
    ]
    assert page.evaluate('relays[0][0]') == 'Approved: "A new request" — Go ahead'
    assert page.locator('#rail-items .rtab').count() == 2
    in_view('#rail-items .rtab:nth-child(1) .rtab-btn')
    in_view('#rail-items .rtab:nth-child(2) .rtab-btn')
    page.close()
