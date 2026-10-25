"""The canary renews like a signed-in app and never loses its rotated handle."""

from __future__ import annotations

import base64

import pytest

from scripts import canary_session


@pytest.fixture
def chain(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "TINYASSETS_CANARY_SESSION_KEY", base64.urlsafe_b64encode(b"k" * 32).decode()
    )
    monkeypatch.setenv("TINYASSETS_CANARY_SESSION_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("TINYASSETS_CANARY_SESSION_SEED", canary_session.seal("seed-handle"))
    sent = []

    def post(answers):
        def fake(origin, path, body):
            sent.append((path, body))
            return answers.pop(0)

        monkeypatch.setattr(canary_session, "_post", fake)

    return tmp_path / "state", sent, post


@pytest.mark.verifies("canary-sign-in:The canary is an ordinary account")
def test_renewal_stores_the_rotated_handle_before_returning(chain):
    state, sent, post = chain
    post(
        [
            (200, {"access_token": "a1", "session_ref": "h1"}),
            (200, {"access_token": "a2", "session_ref": "h2"}),
        ]
    )
    assert canary_session.access_token("https://x") == "a1"
    assert canary_session.unseal(state.read_text()) == "h1"
    assert canary_session.access_token("https://x") == "a2"
    assert [body["session_ref"] for _, body in sent] == ["seed-handle", "h1"]
    assert all(
        path == "/app/token" and body["grant_type"] == "refresh_token" for path, body in sent
    )


@pytest.mark.verifies("canary-sign-in:Each workflow keeps its own sealed chain")
@pytest.mark.parametrize(
    "status,code", [(401, "canary_signed_out"), (503, "canary_renewal_failed")]
)
def test_failed_renewal_keeps_the_current_handle(chain, status, code):
    state, _, post = chain
    post([(200, {"access_token": "a1", "session_ref": "h1"}), (status, {"error": "x"})])
    canary_session.access_token("https://x")
    with pytest.raises(canary_session.CanarySessionError, match=code):
        canary_session.access_token("https://x")
    assert canary_session.unseal(state.read_text()) == "h1"


@pytest.mark.verifies("canary-sign-in:Each workflow keeps its own sealed chain")
def test_a_sealed_handle_needs_the_key(chain, monkeypatch):
    sealed = canary_session.seal("h")
    monkeypatch.setenv(
        "TINYASSETS_CANARY_SESSION_KEY", base64.urlsafe_b64encode(b"z" * 32).decode()
    )
    with pytest.raises(canary_session.CanarySessionError, match="unreadable"):
        canary_session.unseal(sealed)
