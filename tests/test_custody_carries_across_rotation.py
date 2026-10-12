"""A deposited credential must match its custody pin at read and launch."""

from __future__ import annotations

import base64
import json

import pytest

from tests import test_served_refresh_renews_binding as served_refresh
from tests.test_subscription_credential_refresh import ID_TOKEN

_redeposit_stale = served_refresh._redeposit_stale
rig = served_refresh.rig
reader = served_refresh.reader
configured = served_refresh.configured
served = served_refresh.served
agent = served_refresh.agent


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
def test_bytes_nobody_pinned_are_still_refused(agent):
    """The reference no longer covers the bytes; the pin still does."""
    from tinyassets.credential_vault import (
        current_llm_subscription_custody,
        write_credential_vault,
    )
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    _redeposit_stale(agent)
    write_credential_vault(
        agent.served.context.universe_dir, [{
            "credential_type": "llm_subscription", "service": "codex",
            "auth_json_b64": base64.b64encode(json.dumps({"tokens": {
                "id_token": ID_TOKEN, "access_token": "x", "refresh_token": "y",
            }}).encode()).decode(),
        }], owner_user_id="owner", universe_id="u-models",
    )
    store = SQLiteProviderWorkAuthorityStore(agent.served.rig.base)
    with store.connection() as conn:
        assert current_llm_subscription_custody(
            conn, universe_dir=agent.served.context.universe_dir, owner_user_id="owner",
            universe_id="u-models", service="codex",
        ) is None


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
def test_a_launch_copy_that_differs_from_the_pin_is_refused(agent, monkeypatch):
    """After the copy, the copied BYTES are compared with the pin. The v2
    reference does not cover the bytes, so a reference check alone would pass."""
    from tinyassets import credential_vault
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    _redeposit_stale(agent)
    with SQLiteProviderWorkAuthorityStore(agent.served.rig.base).connection() as conn:
        custody = credential_vault.current_llm_subscription_custody(
            conn, universe_dir=agent.served.context.universe_dir, owner_user_id="owner",
            universe_id="u-models", service="codex",
        )
    assert custody is not None
    real_write = credential_vault._write_exclusive_snapshot_file

    def corrupt(path, contents):
        real_write(path, contents + b" " if path.name == "auth.json" else contents)

    monkeypatch.setattr(credential_vault, "_write_exclusive_snapshot_file", corrupt)
    with pytest.raises(PermissionError, match="custody digest disagrees"):
        credential_vault.snapshot_llm_subscription_credential(
            universe_dir=agent.served.context.universe_dir, custody=custody,
        )
