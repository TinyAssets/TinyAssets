"""Pipeline unit tests for the inbound webhook receiver (Floor 1, hardened).

These assert the receiver's decision pipeline against the REAL token/admission/dedupe store
(no mocked ownership). End-to-end ownership + real-enqueue coverage lives in
``test_webhook_inbound_hardened.py``; here we pin the pipeline ordering and the pure header
allowlist. The enqueue callable is injected only as a "did dispatch run" signal — never as a
stand-in for ownership.
"""

from __future__ import annotations

import json

import pytest

import tinyassets.webhook_inbound as wh
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets.storage import webhook_hooks


@pytest.fixture(autouse=True)
def _inbound_on(monkeypatch):
    """Most pipeline tests need the master flag ON; individual tests can override."""
    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "1")
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    webhook_hooks._initialized.clear()


def _spy_enqueue():
    calls = []

    def _enqueue(base, *, universe_id, branch_def_id, inputs, principal_id=""):
        # principal_id: the hook's recorded OWNER, carried from the hook row to
        # the run. The delivery thread has no request identity and there is no
        # anonymous one to fall back to (no-anonymous-principal D2).
        calls.append({
            "universe_id": universe_id, "branch_def_id": branch_def_id,
            "inputs": inputs, "principal_id": principal_id,
        })
        return "run-x"

    return _enqueue, calls


def test_dark_flag_makes_the_path_refuse_without_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "0")
    token = webhook_hooks.mint(
        tmp_path, universe_id="u-a", branch_def_id="b-1", owner_principal_id="owner-a",
    )
    enqueue, calls = _spy_enqueue()
    status, payload = wh.handle_hook(
        token=token, body=b"{}", headers={}, base_path=tmp_path, enqueue=enqueue,
    )
    assert status == 404 and payload == {"error": "not_found"} and calls == []


def test_oversized_body_is_refused_before_anything(tmp_path):
    enqueue, calls = _spy_enqueue()
    status, _ = wh.handle_hook(
        token="whatever", body=b"x" * (wh.MAX_BODY_BYTES + 1), headers={},
        base_path=tmp_path, enqueue=enqueue,
    )
    assert status == 413 and calls == []


def test_unknown_revoked_and_malformed_all_answer_uniform_404(tmp_path):
    enqueue, calls = _spy_enqueue()
    for tok in ("nope", "", "   "):
        status, payload = wh.handle_hook(
            token=tok, body=b"{}", headers={}, base_path=tmp_path, enqueue=enqueue,
        )
        assert status == 404 and payload == {"error": "not_found"}
    token = webhook_hooks.mint(
        tmp_path, universe_id="u-a", branch_def_id="b-1", owner_principal_id="owner-a",
    )
    webhook_hooks.revoke(tmp_path, token=token)
    status, payload = wh.handle_hook(
        token=token, body=b"{}", headers={}, base_path=tmp_path, enqueue=enqueue,
    )
    assert status == 404 and payload == {"error": "not_found"} and calls == []


def test_a_valid_plain_token_reaches_dispatch_as_its_universe(tmp_path):
    token = webhook_hooks.mint(
        tmp_path, universe_id="u-a", branch_def_id="b-1", owner_principal_id="owner-a",
    )
    enqueue, calls = _spy_enqueue()
    status, payload = wh.handle_hook(
        token=token, body=b'{"e":1}', headers={"X-GitHub-Event": "push"},
        base_path=tmp_path, enqueue=enqueue,
    )
    assert status == 202
    assert calls[0]["principal_id"] == "owner-a"   # the run acts for the hook's owner
    assert calls[0]["universe_id"] == "u-a" and calls[0]["branch_def_id"] == "b-1"
    assert calls[0]["inputs"]["webhook"]["payload"] == {"e": 1}
    # exact signed bytes preserved so a branch can verify a signature
    import base64
    assert base64.b64decode(calls[0]["inputs"]["webhook"]["raw_base64"]) == b'{"e":1}'


def test_the_request_cannot_redirect_identity(tmp_path):
    token = webhook_hooks.mint(
        tmp_path, universe_id="u-a", branch_def_id="b-1", owner_principal_id="owner-a",
    )
    enqueue, calls = _spy_enqueue()
    wh.handle_hook(
        token=token,
        body=b'{"universe_id":"u-evil","branch_def_id":"b-evil"}',
        headers={"X-Universe-Id": "u-evil"},
        base_path=tmp_path, enqueue=enqueue,
    )
    assert calls[0]["universe_id"] == "u-a" and calls[0]["branch_def_id"] == "b-1"


def test_only_allowlisted_headers_are_forwarded(tmp_path):
    token = webhook_hooks.mint(
        tmp_path, universe_id="u-a", branch_def_id="b-1", owner_principal_id="owner-a",
    )
    enqueue, calls = _spy_enqueue()
    wh.handle_hook(
        token=token, body=b"{}", base_path=tmp_path, enqueue=enqueue,
        headers={"Authorization": "Bearer x", "Cookie": "s=1", "X-Api-Key": "k",
                 "CF-Access-Client-Secret": "s", "X-Hub-Signature-256": "sha=y",
                 "X-GitHub-Event": "push"},
    )
    fwd = calls[0]["inputs"]["webhook"]["headers"]
    assert fwd == {"X-Hub-Signature-256": "sha=y", "X-GitHub-Event": "push"}


def test_the_per_token_flood_limit_refuses_a_storm_then_recovers(tmp_path):
    # The per-token flood limit is the only load gate left on this path; there is no
    # in-flight ceiling to isolate it from any more.
    token = webhook_hooks.mint(
        tmp_path, universe_id="u-a", branch_def_id="b-1", owner_principal_id="owner-a",
    )
    enqueue, _ = _spy_enqueue()
    now = 1000.0
    admitted = 0
    for i in range(wh._RATE_MAX + 5):
        # unique body each time so the dedupe layer never masks the rate test
        status, _ = wh.handle_hook(
            token=token, body=f'{{"i":{i}}}'.encode(), headers={},
            base_path=tmp_path, enqueue=enqueue, now=now,
        )
        if status == 202:
            admitted += 1
    assert admitted == wh._RATE_MAX
    status, _ = wh.handle_hook(
        token=token, body=b'{"late":1}', headers={},
        base_path=tmp_path, enqueue=enqueue, now=now + wh._RATE_WINDOW_S + 1,
    )
    assert status == 202


def test_a_replay_is_deduped_without_consuming_rate_budget(tmp_path):
    token = webhook_hooks.mint(
        tmp_path, universe_id="u-a", branch_def_id="b-1", owner_principal_id="owner-a",
    )
    enqueue, calls = _spy_enqueue()
    now = 5000.0
    body = b'{"same":1}'
    # 100 identical deliveries: dispatch runs ONCE, and rate budget is not exhausted.
    for _ in range(100):
        status, _ = wh.handle_hook(
            token=token, body=body, headers={}, base_path=tmp_path, enqueue=enqueue, now=now,
        )
        assert status == 202
    assert len(calls) == 1


def test_the_delivery_key_is_server_side_not_a_caller_header():
    # Same (token, body) -> same key regardless of caller headers (Codex #4).
    k1 = wh._delivery_key("tok", b"body")
    k2 = wh._delivery_key("tok", b"body")
    k3 = wh._delivery_key("tok", b"other")
    assert k1 == k2 and k1 != k3 and k1.startswith("sha256:")


def test_a_non_json_body_is_passed_as_text(tmp_path):
    token = webhook_hooks.mint(
        tmp_path, universe_id="u-a", branch_def_id="b-1", owner_principal_id="owner-a",
    )
    enqueue, calls = _spy_enqueue()
    wh.handle_hook(
        token=token, body=b"not json at all", headers={}, base_path=tmp_path, enqueue=enqueue,
    )
    assert calls[0]["inputs"]["webhook"]["payload"] == "not json at all"


def test_a_dispatch_failure_answers_uniform_404_without_leaking(tmp_path):
    token = webhook_hooks.mint(
        tmp_path, universe_id="u-a", branch_def_id="b-1", owner_principal_id="owner-a",
    )

    def _boom(base, *, universe_id, branch_def_id, inputs):
        raise RuntimeError("internal branch resolution detail")

    status, payload = wh.handle_hook(
        token=token, body=b"{}", headers={}, base_path=tmp_path, enqueue=_boom,
    )
    assert status == 404 and payload == {"error": "not_found"}
    assert "internal" not in json.dumps(payload)


@pytest.mark.usefixtures("cloud_runtime")
def test_the_hooks_route_wires_token_body_and_headers_when_enabled(monkeypatch):
    from starlette.testclient import TestClient

    from tests.owner_app_fixture import create_owner_app as create_streamable_http_app

    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "1")
    seen: dict = {}

    def _spy(*, token, body, headers):
        seen.update({"token": token, "body": body, "headers": headers})
        return 202, {"queued": True}

    monkeypatch.setattr("tinyassets.webhook_inbound.handle_hook", _spy)
    client = TestClient(create_streamable_http_app())   # no `with` -> skip lifespan
    resp = client.post("/hooks/inbound/abc123", content=b'{"x":1}', headers={"X-Test": "y"})
    assert resp.status_code == 202
    assert seen["token"] == "abc123" and seen["body"] == b'{"x":1}'
    assert seen["headers"]["x-test"] == "y"


@pytest.mark.usefixtures("cloud_runtime")
def test_the_route_is_absent_when_disabled(monkeypatch):
    from starlette.testclient import TestClient

    from tests.owner_app_fixture import create_owner_app as create_streamable_http_app

    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "0")
    client = TestClient(create_streamable_http_app())
    resp = client.post("/hooks/inbound/abc123", content=b"{}")
    # This path is outside /mcp; with inbound off there is no route to deliver it.
    assert resp.status_code == 404
    resp_authenticated = client.post(
        "/hooks/inbound/abc123", content=b"{}", headers={"Authorization": "Bearer any"},
    )
    assert resp_authenticated.status_code == 404   # route not mounted at all


@pytest.mark.usefixtures("cloud_runtime")
def test_the_route_rejects_an_oversized_content_length_before_reading(monkeypatch):
    from starlette.testclient import TestClient

    from tests.owner_app_fixture import create_owner_app as create_streamable_http_app

    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "1")
    called = {"n": 0}

    def _spy(*, token, body, headers):
        called["n"] += 1
        return 202, {"queued": True}

    monkeypatch.setattr("tinyassets.webhook_inbound.handle_hook", _spy)
    client = TestClient(create_streamable_http_app())
    resp = client.post(
        "/hooks/inbound/abc", content=b"x" * 10,
        headers={"Content-Length": str(wh.MAX_BODY_BYTES + 1)},
    )
    assert resp.status_code == 413 and called["n"] == 0
