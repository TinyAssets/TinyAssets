# ruff: noqa: F811
"""Real Chromium Stop against an orphan journal, then the shipped queue sends."""
import os

import pytest

from tests.test_agent_turn_journal import journal  # noqa: F401
from tests.test_app_chat_cloud_browser import (
    _enter_chat,
    app_url,  # noqa: F401
)
from tests.test_orphan_ready_turn import orphan
from tinyassets.turn_interrupt import request_interrupt


@pytest.mark.real_browser
def test_stop_clears_orphan_and_sends_queued_messages(journal, app_url):
    from playwright.sync_api import expect, sync_playwright

    row = orphan(journal, legacy=True)
    stops = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("TINYASSETS_TEST_CHROMIUM"))
        page = browser.new_page()

        def wire(route):
            url = route.request.url
            if url.endswith("/app/turn/interrupt"):
                body = route.request.post_data_json
                count = request_interrupt("owner", body["universe_id"],
                                          agent_id=body["agent_id"],
                                          base_path=journal._ledger.base_path)
                stops.append(count)
                route.fulfill(json={"interrupted": count, "universe_id": "home"})
            elif url.endswith("/app/turn/steer"):
                route.fulfill(json={"steered": False})
            elif url.startswith(app_url.rsplit("/", 1)[0] + "/"):
                route.continue_()
            else:
                route.abort()

        page.route("**/*", wire)
        _enter_chat(page, app_url)
        page.evaluate("""row => {
            setQueueOwner('owner'); setQueueScope('home');
            window.sent=[];
            MCP.converse=async message=>{sent.push(message);return {reply:'Received'};};
            readServerTurn({active_turn:{turn_id:row,state:'ready',age_s:1400,
                started_at:new Date(Date.now()-1400000).toISOString(),stale:false}});
            renderWorking();
        }""", row.turn_id)
        page.locator("#composer-input").fill("Please continue here")
        page.locator("#composer-input").press("Enter")
        page.wait_for_function("() => sendQueue.length === 1 && pendingSteers === 0")
        assert page.evaluate("sent") == []
        expect(page.locator("#btn-stop")).to_be_visible()
        page.locator("#btn-stop").click()
        page.wait_for_function("() => sent.length === 1 && sendQueue.length === 0")
        assert stops == [1]
        assert page.evaluate("sent") == ["Please continue here"]
        assert journal.get("owner", "home", row.turn_id).state == "abandoned"
        browser.close()
