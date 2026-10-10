"""Real Chromium windows consume the actual app heartbeat and recovery code."""

import json
from types import SimpleNamespace

import pytest

from tests.test_app_recovery_browser import healthy
from tests.test_app_recovery_browser import page as page
from tests.test_app_recovery_browser import server as server
from tests.test_conversation_failure_readers import _authenticate
from tests.test_conversation_run_admissions import HOME, OWNER, complete, project, reserve
from tests.test_conversation_run_admissions import store as store

pytestmark = pytest.mark.real_browser


def test_two_windows_observe_pending_and_final_without_reload(page, server, store, monkeypatch):
    from playwright.sync_api import expect

    from tinyassets.api.status import get_status

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(store))
    _authenticate(monkeypatch, OWNER)
    monkeypatch.setattr(
        "tinyassets.auth.middleware.current_identity", lambda: SimpleNamespace(user_id=OWNER)
    )
    context = page.context
    admitted = []
    finished = []

    context.route(
        "**/app/me",
        lambda r: r.fulfill(
            json={"principal_id": OWNER, "universe_id": HOME, "setup": "connected"}
        ),
    )
    context.route(
        "**/app/api/status",
        lambda r: r.fulfill(
            body=get_status(universe_id=HOME, include_conversation=True),
            content_type="application/json",
        ),
    )

    def envelope():
        row = admitted[-1]
        return {
            "consumer_turn": {
                "version": 1,
                "turn_id": row["admission_id"],
                "run_id": row["run_id"],
                "state": "completed" if finished else "pending",
                "run_status": "completed" if finished else "queued",
                "projection": "committed" if finished else "pending",
            },
            **({"reply": "Visible finished reply"} if finished else {}),
        }

    def read(route):
        if route.request.post_data_json.get("target") == "conversation_turn":
            route.fulfill(json=envelope())
        elif route.request.post_data_json.get("target") == "app_ui":
            route.fulfill(
                json={
                    "app_ui": {
                        "universe_id": HOME,
                        "revision": 1,
                        "ui_library": [],
                        "ui_selection": None,
                        "platform_default": {
                            "kind": "tinyassets.app-ui.v1",
                            "version": 1,
                            "ui_id": "platform:blank",
                            "name": "Test center",
                            "markup": "<p id='working'>Working command center</p>",
                            "style": "",
                            "script": "",
                        },
                    }
                }
            )
        else:
            route.fallback()

    def mcp(route):
        frame = route.request.post_data_json
        method = frame.get("method")
        if method == "notifications/initialized":
            route.fulfill(status=202)
            return
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "test", "version": "1"},
            }
        elif method == "tools/call":
            assert frame["params"]["name"] == "converse"
            args = frame["params"]["arguments"]
            request = args.get("consumer_request")
            if not request:
                value = {
                    "error": "consumer_request_required",
                    "consumer_selection": {"version": 1, "binding_id": "b", "binding_revision": 1},
                }
            else:
                admitted.append(reserve(store, request["request_key"], message=args["message"]))
                value = envelope()
            result = {"content": [{"type": "text", "text": json.dumps(value)}]}
        else:
            result = {}
        route.fulfill(
            json={"jsonrpc": "2.0", "id": frame.get("id"), "result": result},
            headers={"Mcp-Session-Id": "browser-proof"},
        )

    context.route("**/app/api/read", read)
    context.route("**/mcp", mcp)
    page.reload()
    healthy(page)
    observer = context.new_page()
    observer.goto(server[0])
    healthy(observer)
    for tab in (page, observer):
        tab.locator('[data-app-recovery="chat"]').click()
    pages_before = server[1]["pages"]
    observer.locator("#composer-input").fill("Unsent in the other window")
    page.locator("#composer-input").fill("Message from the sending window")
    page.locator("#btn-send").click()
    expect(observer.locator("#thread .msg--founder")).to_contain_text(
        "Message from the sending window", timeout=15000
    )
    expect(observer.locator(".pending-turn-state")).to_have_text("Queued…")
    assert server[1]["pages"] == pages_before
    assert len(admitted) == 1
    complete(store, admitted[0]["run_id"], reply="Visible finished reply")
    project(store, admitted[0]["admission_id"])
    finished.append(True)
    for tab in (page, observer):
        expect(tab.locator("#thread .msg--universe")).to_contain_text(
            "Visible finished reply", timeout=15000
        )
        expect(tab.locator("#thread .msg--founder")).to_have_count(1)
        expect(tab.locator("#thread .msg--universe")).to_have_count(1)
        expect(tab.locator(".pending-turn-state")).to_have_count(0)
    expect(observer.locator("#composer-input")).to_have_value("Unsent in the other window")
    assert server[1]["pages"] == pages_before
    assert len(admitted) == 1
    observer.close()


def test_open_tab_upgrades_content_version_and_restores_focused_draft(page, server):
    from playwright.sync_api import expect

    page.clock.install()
    page.reload()
    healthy(page)
    page.locator('[data-app-recovery="chat"]').click()
    page.locator("#composer-input").fill("Keep this unsent draft \U0001f98a")
    before = server[1]["pages"]
    server[1]["build"] = "after"
    # Advance actual scheduled checks; the focused draft must survive navigation.
    page.clock.run_for(61000)
    page.wait_for_url("**/*_ta_recover=*")
    healthy(page)
    expect(page.locator("#composer-input")).to_have_value(
        "Keep this unsent draft \U0001f98a", timeout=10000
    )
    assert page.evaluate("CFG.build") == "after"
    assert server[1]["pages"] == before + 1
    assert page.evaluate("readInflight()") is None
