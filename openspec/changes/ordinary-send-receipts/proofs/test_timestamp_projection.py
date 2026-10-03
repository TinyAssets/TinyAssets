"""CHARACTERIZATION of current timestamp drift, not fixed behavior or live timing.

Actual conversation store and app JS; synthetic local data and transport only.
"""
from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from playwright.sync_api import sync_playwright

from tinyassets import conversation_store as cs
from tinyassets.onboarding import render_app_html

SENT = 1791064920.0
TERMINAL = SENT + 900


@pytest.fixture
def page():
    html, csp = render_app_html()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path != '/app':
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Security-Policy', csp)
            self.end_headers()
            self.wfile.write(html.encode())

        def do_POST(self):
            self.send_error(404)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=os.environ.get('TINYASSETS_TEST_CHROMIUM', '/usr/bin/chromium')
            )
            context = browser.new_context(timezone_id='America/Los_Angeles')
            context.route('**/*', lambda route: route.continue_()
                          if route.request.url.startswith(origin + '/') else route.abort())
            page = context.new_page()
            page.goto(origin + '/app')
            page.wait_for_selector('#view-signin', state='visible')
            page.wait_for_load_state('networkidle')
            page.evaluate("""() => {
                setQueueOwner('synthetic-owner'); setQueueScope('synthetic-home');
                showView('chat'); refreshChatCloud();
            }""")
            yield page
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize('failed', [False, True])
def test_CHARACTERIZATION_terminal_projection_replaces_original_send_time(page, tmp_path, failed):
    # Both current server terminal call sites omit ts; no provider is called.
    with patch.object(cs.time, 'time', return_value=TERMINAL):
        if failed:
            assert cs.record_failure(
                tmp_path, 'synthetic-session', 'synthetic hi', 'provider_error'
            )
        else:
            assert cs.record_exchange(
                tmp_path, 'synthetic-session', 'synthetic hi', 'synthetic reply'
            )
    turns = [{'speaker': row.speaker, 'text': row.text, 'ts': row.ts}
             for row in cs.load_recent(tmp_path, 'synthetic-session')]
    assert [row['ts'] for row in turns] == [TERMINAL, TERMINAL]
    # Exercise the real sendTurn echo/catch/render logic, replacing only transport.
    page.evaluate("""async ({sent, terminal, failed}) => {
        Date.now = () => terminal * 1000;
        sendConversationRequest = async () => {
            if (failed) throw Object.assign(new Error('Synthetic failure'),
                {served:true, historySaved:true});
            return {reply:'synthetic reply'};
        };
        await sendTurn('synthetic hi', null, {sentAt:sent * 1000});
    }""", {'sent': SENT, 'terminal': TERMINAL, 'failed': failed})
    first = page.locator('.msg--founder .msg-time').first.get_attribute('datetime')
    assert first == '2026-10-03T22:02:00.000Z'
    # A fresh page draws persisted history, exactly as navigation/reload does.
    page.reload()
    page.wait_for_selector('#view-signin', state='visible')
    page.wait_for_load_state('networkidle')
    page.evaluate('(turns) => drawHistoryTurns(turns)', turns)
    after = page.locator('.msg--founder .msg-time').first.get_attribute('datetime')
    assert after == '2026-10-03T22:17:00.000Z'
    assert after != first


def test_CHARACTERIZATION_steering_requeue_overwrites_send_time(page):
    result = page.evaluate("""({sent,terminal}) => {
        const bubble=appendMessage('founder','synthetic steer',null,sent);
        steeredLines=[{id:'synthetic-steer',message:'synthetic steer',bubble,
            owner:queueOwner,scope:queueScope,opts:{sentAt:sent*1000}}];
        Date.now=()=>terminal*1000;
        settleSteered({delivered:[],undelivered:[{id:'synthetic-steer',text:'synthetic steer'}]});
        return {queued:sendQueue[0].opts.sentAt, persisted:sendQueue[0].ts,
            visible:bubble.querySelector('.msg-time').dateTime};
    }""", {'sent': SENT, 'terminal': TERMINAL})
    assert result == {'queued': TERMINAL * 1000, 'persisted': TERMINAL * 1000,
                      'visible': '2026-10-03T22:02:00.000Z'}
