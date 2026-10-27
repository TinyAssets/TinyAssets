"""Canary setup failures name the HTTP path and status, never a secret."""

from __future__ import annotations

import json

import pytest

from scripts import core_capability_live as live
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


def test_the_canary_branch_spec_builds(tmp_path, monkeypatch, authenticate_request):
    """The spec the canary sends is one the real validator accepts."""
    from scripts.core_canary_provision import BRANCH_SPEC
    from tinyassets import universe_server
    from tinyassets.daemon_server import grant_universe_access, initialize_author_server

    base = tmp_path / "data"
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    initialize_author_server(base)
    grant_universe_access(base, universe_id="u-canary", actor_id="canary", permission="admin")
    authenticate_request("canary")
    created = json.loads(universe_server.write_graph(
        target="branch", operation="create", graph_id="u-canary",
        payload_json=json.dumps(BRANCH_SPEC),
    ))
    assert created.get("status") == "built", created
    assert created.get("branch_def_id")


def test_a_rejected_spec_is_a_refusal_not_a_success():
    """A ``status: rejected`` envelope raises with bounded labels, never the server's words."""
    words = "State field name 'echo' collides with a graph node ID. " + TOKEN
    result = type("Result", (), {"is_error": False,
                                 "data": {"status": "rejected", "errors": [words]}})()
    with pytest.raises(live.ProbeFailure) as raised:
        live.answer("write_graph", {"target": "branch", "operation": "create"}, result)
    assert str(raised.value).startswith("write_graph branch create refused: ")
    assert TOKEN not in str(raised.value) and "echo" not in str(raised.value)
