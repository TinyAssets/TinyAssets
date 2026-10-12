"""Owner request proof and accepted-source renewal guards for served turns."""

from __future__ import annotations

import base64
import json

import pytest

from tests import test_served_model_preferences as prefs
from tests.test_subscription_credential_refresh import (
    ID_TOKEN,
    _endpoint_from_the_credential,
)


def _refresh_before_launch(**kwargs):
    from tinyassets.subscription_refresh import _refresh_document

    return bool(_refresh_document(**kwargs))


_converse = prefs._converse
rig = prefs.rig
reader = prefs.reader
configured = prefs.configured
served = prefs.served
agent = prefs.agent


def _stale_document(refresh: str) -> str:
    return base64.b64encode(json.dumps({
        "tokens": {"id_token": ID_TOKEN, "access_token": "a-1", "refresh_token": refresh},
        "last_refresh": "2020-01-01T00:00:00Z",
    }).encode("utf-8")).decode("ascii")


def _redeposit_stale(agent) -> None:
    """The owner's last sign-in, now older than the refresh window.

    Deposited and accepted through the real reconnect path, so the binding and the
    custody row pin THIS document's digest -- the live state before the turn.
    """
    from tinyassets import daemon_server
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.custom_agents import get_binding
    from tinyassets.onboarding.serving import ensure_founder_serving

    base = agent.served.rig.base
    universe = agent.served.context.universe_dir
    daemon_server.grant_universe_access(
        base, universe_id="u-models", actor_id="owner", permission="admin",
        granted_by="owner",
    )
    write_credential_vault(
        universe, [{
            "credential_type": "llm_subscription", "service": "codex",
            "auth_json_b64": _stale_document("r-1"),
        }], owner_user_id="owner", universe_id="u-models",
    )
    result = ensure_founder_serving(
        base_path=base, universe_dir=universe, owner_user_id="owner",
        universe_id="u-models", service="codex",
    )
    assert result["status"] == "serving", result
    agent.served.agent = get_binding(
        base, universe_id="u-models", binding_id=agent.served.agent["agent_binding_id"],
    )


def _custody_matches_the_vault(agent) -> bool:
    from tinyassets.credential_vault import current_llm_subscription_custody
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    store = SQLiteProviderWorkAuthorityStore(agent.served.rig.base)
    with store.connection() as conn:
        return current_llm_subscription_custody(
            conn, universe_dir=agent.served.context.universe_dir,
            owner_user_id="owner", universe_id="u-models", service="codex",
        ) is not None


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
def test_an_unproven_request_spends_no_refresh_token(agent, monkeypatch):
    """The owner comes from the request carrier the admission proves. A carrier
    that proves nothing must not spend the owner's single-use refresh token."""
    import asyncio

    from tinyassets import provider_assignment, subscription_refresh
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.model_policy import ModelRef

    _redeposit_stale(agent)
    _endpoint_from_the_credential(monkeypatch)
    monkeypatch.setattr(
        subscription_refresh, "refresh_deposited_subscriptions",
        lambda **_k: pytest.fail("an unproven request reached the refresh"),
    )
    # Not a request carrier the auth middleware issued.
    stranger = object()

    async def drive():
        async with provider_assignment.authorize_served_provider_call_async(
            agent.served.rig.base, universe_dir=agent.served.context.universe_dir,
            request_carrier=stranger, role="writer", operation="converse",
            model_selection=ModelRef("codex", ""), agent_turn=True,
        ):
            pass

    with pytest.raises(ProviderAuthorityHeldError):
        asyncio.run(drive())


def test_a_launch_refresh_without_an_owner_refuses_before_spending(tmp_path, monkeypatch):
    """No owner means the rotation could not be renewed: refuse up front."""
    from tinyassets import subscription_refresh

    universe = tmp_path / "u-models"
    universe.mkdir()
    monkeypatch.setattr(
        subscription_refresh, "_spend",
        lambda *_a, **_k: pytest.fail("spent a refresh token it could not renew"),
    )
    for owner in (None, "", "  "):
        with pytest.raises(ValueError):
            subscription_refresh.refresh_deposited_subscriptions(
                base_path=tmp_path, universe_dir=universe, owner_user_id=owner,
                universe_id="u-models", launching="codex",
            )


def test_a_deposit_the_owner_never_accepted_is_not_renewed_by_a_turn(agent, monkeypatch):
    """Renewal carries an ACCEPTED source forward; it never binds a new one."""
    from datetime import datetime, timezone

    from tinyassets import subscription_refresh
    from tinyassets.credential_vault import write_credential_vault

    fresh = base64.b64encode(json.dumps({
        "tokens": {"id_token": ID_TOKEN, "access_token": "a-1", "refresh_token": "r-1"},
        "last_refresh": datetime.now(timezone.utc).isoformat(),
    }).encode("utf-8")).decode("ascii")
    write_credential_vault(
        agent.served.context.universe_dir, [{
            "credential_type": "llm_subscription", "service": "codex",
            "auth_json_b64": fresh,
        }], owner_user_id="owner", universe_id="u-models",
    )
    renewed: list[str] = []
    monkeypatch.setattr(
        subscription_refresh, "renew_accepted_source",
        lambda **kwargs: renewed.append(kwargs["service"]) or {"status": "serving"},
    )

    assert _converse(agent, monkeypatch) == "finished exact answer"
    assert renewed == []


def test_another_principals_deposit_is_never_spent_on_this_owners_turn(agent, monkeypatch):
    """Proving the serving binding is not proving every credential in the vault."""
    from tinyassets import subscription_refresh
    from tinyassets.credential_vault import write_credential_vault

    write_credential_vault(
        agent.served.context.universe_dir, [{
            "credential_type": "llm_subscription", "service": "codex",
            "auth_json_b64": _stale_document("r-other"),
        }], owner_user_id="other-owner", universe_id="u-models",
    )
    _endpoint_from_the_credential(monkeypatch)
    monkeypatch.setattr(
        subscription_refresh, "_spend",
        lambda *_a, **_k: pytest.fail("spent another principal's refresh token"),
    )
    renewed: list[str] = []
    monkeypatch.setattr(
        subscription_refresh, "renew_accepted_source",
        lambda **kwargs: renewed.append(kwargs["service"]) or {"status": "serving"},
    )

    assert _converse(agent, monkeypatch) == "finished exact answer"
    assert renewed == []


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
def test_a_source_no_longer_accepted_is_not_renewed_back_in(agent, monkeypatch):
    """A custody row from an earlier acceptance is history, not consent."""
    from tinyassets import subscription_refresh
    from tinyassets.api.custom_agents import custom_agents
    from tinyassets.custom_agents import get_binding
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_assignment_manifest import ModelAccess

    _redeposit_stale(agent)
    base = agent.served.rig.base
    current = get_binding(
        base, universe_id="u-models", binding_id=agent.served.agent["agent_binding_id"],
    )
    # The owner narrows the accepted setup to the HTTP source alone.
    narrowed = custom_agents(
        action="bind_serving_provider", universe_id="u-models",
        binding_id=current["agent_binding_id"], expected_revision=current["revision"],
        payload={"provider": agent.served.rig.definition.id, "model_access": {
            agent.served.rig.definition.id: ModelAccess("discovered").document(),
        }},
    )
    assert narrowed["status"] == "ready", narrowed
    before = load_provider_assignment(base, universe_id="u-models")
    assert "codex" not in {m.provider for m in before.candidates}

    _endpoint_from_the_credential(monkeypatch)
    monkeypatch.setattr(
        subscription_refresh, "_spend",
        lambda document, **_: subscription_refresh._rebuild(
            document, access_token="a-2", refresh_token="r-2", id_token=""),
    )
    # Fresh bytes behind the old codex custody row, with no renewal.
    assert _refresh_before_launch(
        universe_dir=agent.served.context.universe_dir, service="codex",
        owner_user_id=None, universe_id="u-models",
    )
    renewed: list[str] = []
    monkeypatch.setattr(
        subscription_refresh, "renew_accepted_source",
        lambda **kwargs: renewed.append(kwargs["service"]) or {"status": "serving"},
    )
    subscription_refresh.refresh_deposited_subscriptions(
        base_path=base, universe_dir=agent.served.context.universe_dir,
        owner_user_id="owner", universe_id="u-models",
    )
    assert renewed == []
    assert load_provider_assignment(base, universe_id="u-models") == before


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
def test_an_unreadable_assignment_renews_nothing_and_leaves_the_refusal_to_the_launch(
    agent, monkeypatch,
):
    """The self-heal check must not replace the launch's own refusal with its error."""
    from tinyassets import provider_assignment, subscription_refresh

    _redeposit_stale(agent)
    monkeypatch.setattr(
        subscription_refresh, "_spend",
        lambda *_a, **_k: pytest.fail("nothing is stale; nothing may be spent"),
    )
    def unreadable(*_a, **_k):
        # The loader's own words for a tampered row (provider_assignment.py).
        raise RuntimeError("provider assignment digest is invalid")

    monkeypatch.setattr(
        provider_assignment, "load_provider_assignment_in_transaction", unreadable,
    )
    renewed: list[str] = []
    monkeypatch.setattr(
        subscription_refresh, "renew_accepted_source",
        lambda **kwargs: renewed.append(kwargs["service"]) or {"status": "serving"},
    )
    # Nothing is stale, so only the self-heal check runs.
    monkeypatch.setattr(subscription_refresh, "document_is_stale", lambda *_a: False)

    subscription_refresh.refresh_deposited_subscriptions(
        base_path=agent.served.rig.base, universe_dir=agent.served.context.universe_dir,
        owner_user_id="owner", universe_id="u-models",
    )
    assert renewed == []
