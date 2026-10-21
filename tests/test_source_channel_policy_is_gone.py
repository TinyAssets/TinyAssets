"""The source-channel approval policy store is deleted, not quietly kept.

Concern `2026-09-24-source-channel-policy-store-has-no-reader`: the connector's
`write_graph target=source_channel operation=set_policy` wrote
`(universe_id, channel_type, mode)` to `${data_dir}/.source_channel_policy.db`
and answered `policy_set`. The only reader of that table was `get_policy_mode`,
whose only caller was `apply_auto_approval_policy` -- which had no callers at
all, and whose own docstring claimed `run_branch` called it. So the owner could
set a policy, read it back, and change nothing observable. Hard Rule 8 calls that
worse than a crash.

Deleted rather than rewired, because a rewire contradicts an approved principle.
ADR-013 (founder-approved 2026-08-30) settles it: **authorship, not host
approval, decides whose code runs**, and the OS sandbox bounds what it touches.
Since change `sandboxed-code-node` an approval gates no run, so there is no
enforcement point left for a per-channel approval mode to reach. The channel
policy enforcement DOES read is the consent row, which change
`agent-access-controls` (D2/D3) exposes as `approve`/`revoke`.

The owner keeps every setting that was ever real. What they lose is a control
that reported success for nothing.
"""

from __future__ import annotations

import json

import pytest

from tests.test_pending_requests import _login, _logout, _make_universe

_SINK = "authenticated_external_call"
_DEST = "hooks.example"


@pytest.fixture(autouse=True)
def _reset_auth():
    _logout()
    yield
    _logout()


@pytest.fixture
def base(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    _make_universe(root, "u-1", admin="founder")
    return root


def _connector(action, **fields) -> dict:
    """The CONNECTOR path the concern is about, as the authenticated owner."""
    from tinyassets.api.source_channel import source_channel

    _login("founder")
    return json.loads(source_channel(
        action=action, universe_id="u-1", payload=json.dumps(fields),
    ))


@pytest.mark.parametrize("action", ["set_policy", "get_policy"])
def test_the_connector_refuses_the_policy_verbs_loudly(base, action):
    """A loud refusal replaces a fake success: the owner learns it is not a thing."""
    out = _connector(action, channel_type="source_code", mode="auto")
    assert out["error"] == "unknown_source_channel_operation", out
    assert out["operation"] == action
    assert out["allowed_operations"] == ["approve", "revoke"]
    assert "policy_set" not in json.dumps(out)


def test_no_policy_database_is_created_by_the_refusal(base):
    """The refusal must not leave the store it was refusing behind."""
    _connector("set_policy", channel_type="source_code", mode="auto")
    assert not list(base.glob("*source_channel_policy*"))








# --------------------------------------------------------------------------
# The guard: the two operations enforcement ACTUALLY reads still work, and the
# owner gate on them is unchanged.
# --------------------------------------------------------------------------


def _consent_active(base, sink=_SINK, destination=_DEST) -> bool:
    """Read the ENFORCEMENT store, never the write's own reply."""
    from tinyassets.storage.effector_consents import is_consent_active

    return is_consent_active(base / "u-1", sink=sink, destination=destination)


def test_the_consent_row_is_still_the_channel_policy(base):
    """`approve` then `revoke` -- the per-channel setting that is real."""
    approved = _connector("approve", channel_type=_SINK, destination=_DEST)
    assert approved["status"] == "granted", approved
    assert _consent_active(base) is True

    revoked = _connector("revoke", channel_type=_SINK, destination=_DEST)
    assert revoked["status"] == "revoked", revoked
    assert _consent_active(base) is False


def test_a_non_owner_still_cannot_reach_the_channel_surface(base):
    from tinyassets.api.source_channel import source_channel

    _login("mallory")
    out = json.loads(source_channel(
        action="approve", universe_id="u-1",
        payload=json.dumps({"channel_type": _SINK, "destination": _DEST}),
    ))
    assert out["error"] == "auth_failed", out
