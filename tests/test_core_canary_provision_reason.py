"""Canary setup failures name the HTTP path and status, never a secret."""

from __future__ import annotations

import pytest

from scripts import verify_core_canary_provision as provision

pytestmark = pytest.mark.verifies("owner-daemon-in-box:C12")

TOKEN = "LEAK-TOKEN-0123456789abcdef"


class HTTPStatusError(Exception):
    def __init__(self, path, status, body):
        super().__init__(body)
        self.request = type("Request", (), {"url": type("URL", (), {"path": path})()})()
        self.response = type("Response", (), {"status_code": status, "text": body})()


def test_http_failure_names_path_and_status_only():
    exc = HTTPStatusError("/app/me", 503, TOKEN + " principal=user_secret query=token")
    detail = provision.http_failure_detail(exc)
    assert detail == " path=/app/me status=503"
    assert TOKEN not in detail


def test_http_failure_drops_query_and_unknown_paths():
    exc = HTTPStatusError("/hooks/inbound/" + TOKEN, 404, "no")
    assert provision.http_failure_detail(exc) == " path=redacted status=404"


def test_classify_prints_the_http_reason_and_not_the_body(monkeypatch):
    async def boom(_config):
        raise HTTPStatusError(
            "/app/models/preferences", 503, "owner_box_unavailable " + TOKEN)

    monkeypatch.setattr(provision, "credentials", lambda: {"universe_id": "u-test"})
    monkeypatch.setattr(provision, "_provision", boom)
    ok, reason = provision.classify()
    assert ok is False
    assert reason == "canary setup failed: HTTPStatusError path=/app/models/preferences status=503"
    assert TOKEN not in reason
