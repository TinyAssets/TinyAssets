"""Connect requests follow current universe authority, not a surviving binding row."""


import pytest

from tests.test_open_serving_bind import _CONN_ID, _GRANT_ID, _bound_and_serving
from tinyassets.api.pending_requests import _serving_llm_bound


def test_current_open_connection_satisfies_the_connect_request(tmp_path, monkeypatch):
    _bound_and_serving(tmp_path, monkeypatch)
    assert _serving_llm_bound(tmp_path, "u-owner", "owner-1") is True
    assert _serving_llm_bound(tmp_path, "u-owner", "someone-else") is False


@pytest.mark.parametrize("change", ["revoke_grant", "revoke_connection"])
def test_a_surviving_binding_does_not_hide_a_broken_connection_request(
    tmp_path, monkeypatch, change
):
    from tinyassets.provider_serving_binding import resolve_serving_agent_binding
    from tinyassets.storage.outbound_connections import ConnectionLedger

    _bound_and_serving(tmp_path, monkeypatch)
    assert _serving_llm_bound(tmp_path, "u-owner", "owner-1") is True
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "owner-1"
    )
    if change == "revoke_grant":
        ledger.revoke_grant(_GRANT_ID)
    else:
        ledger.revoke_connection(_CONN_ID)
    # Reproduce the exact old premise: the serving row still exists.
    assert resolve_serving_agent_binding(
        tmp_path, universe_id="u-owner", owner_user_id="owner-1"
    )
    assert _serving_llm_bound(tmp_path, "u-owner", "owner-1") is False


def test_connect_detection_uses_explicit_universe_authority(tmp_path, monkeypatch):
    calls = []

    def resolve(base, **kwargs):
        calls.append((base, kwargs))
        return True

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.serving_connection_is_current",
        resolve,
    )
    assert _serving_llm_bound(tmp_path, "u-owner", "owner-1") is True
    assert calls == [(tmp_path, {
        "universe_dir": tmp_path / "u-owner",
        "universe_id": "u-owner",
        "owner_user_id": "owner-1",
    })]


