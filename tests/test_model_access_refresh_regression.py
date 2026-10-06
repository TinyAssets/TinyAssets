"""Refresh diagnostics cannot undo accepted access in the desktop picker."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_native_discovery_integration import install_discovery
from tests.test_native_model_authority import native  # noqa: F401
from tinyassets.agent_turn_coordinator import AgentTurnCoordinator
from tinyassets.exceptions import ProviderError
from tinyassets.providers.model_options import model_options_document
from tinyassets.providers.model_policy import ModelRef
from tinyassets.providers.native_catalogue import NativeCatalogue, NativeModel
from tinyassets.providers.served_model_plan import prepare_owned_model_plan
from tinyassets.providers.shortlist_refresh import SHORTLIST_CACHE
from tinyassets.storage.learned_models import OwnModelHistory

pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
@pytest.mark.parametrize("history", ["verified", "last_catalogue"])
@pytest.mark.parametrize("failed", [False, True])
def test_known_model_stays_pickable_during_refresh(native, monkeypatch, history, failed):  # noqa: F811
    model_id = "gpt-6-astra"
    where = dict(base=native.base, owner="owner-1", universe_id=native.universe.name,
                 provider="codex")

    async def discover():
        return NativeCatalogue((NativeModel(model_id, frozenset({"text"})),), model_id,
                               datetime.now(timezone.utc))

    install_discovery(native, monkeypatch, discover)
    SHORTLIST_CACHE.forget(**where)
    if history == "verified":
        OwnModelHistory(native.base).record(
            source_kind="subscription", model_id=model_id, owner_user_id="owner-1")
    snapshot = SHORTLIST_CACHE.refresh_now(**where)
    # Even verified history needs this provider's snapshot. Display retention
    # has no upper age limit; execution still requires fresh discovery.
    # Age the read clock, not a replacement snapshot: the durable cache correctly
    # refuses to overwrite a newer observation with an older refresh result.
    monkeypatch.setattr(SHORTLIST_CACHE, "_now",
                        lambda: snapshot.observed_at + timedelta(days=365))
    if failed:
        def unavailable(**kwargs):
            raise ProviderError("offline")
        monkeypatch.setattr("tinyassets.providers.native_discovery.discover_native_models_sync",
                            unavailable)
        SHORTLIST_CACHE.refresh_now(**where)
    monkeypatch.setattr(SHORTLIST_CACHE, "schedule", lambda **kwargs: False)
    prepared = prepare_owned_model_plan(
        base=native.base, universe=native.universe, owner="owner-1", agent=native.agent,
        allow_empty=True)
    document = model_options_document(prepared.catalog, prepared.plan, prepared.ineligible)
    row = next(row for row in document["options"] if row["reference"]["model_id"] == model_id)
    assert row["in_candidate_catalog"] is True, row
    assert row["reasons"] == [], row
    expected = "native_catalogue_unavailable" if failed else "catalogue_refresh_pending"
    assert expected in {reason["reason"] for source in document["source_failures"]
                        for reason in source["reasons"]}
    # These display facts must never become launch authority.
    with pytest.raises(ValueError, match="display catalogue cannot authorize"):
        prepared.recheck(None, store=None, base=native.base, universe=native.universe,
                         owner="owner-1", agent=native.agent)


@pytest.mark.parametrize("native", ["explicit", "discovered"], indirect=True)
def test_refresh_retention_respects_current_scope_and_owner(native, monkeypatch):  # noqa: F811
    install_discovery(native, monkeypatch)
    monkeypatch.setattr(SHORTLIST_CACHE, "schedule", lambda **kwargs: False)
    history = OwnModelHistory(native.base)
    history.record(source_kind="subscription", model_id="private-other-owner-model",
                   owner_user_id="someone-else")
    history.record(source_kind="subscription", model_id="previously-verified-model",
                   owner_user_id="owner-1")
    SHORTLIST_CACHE.refresh_now(
        base=native.base, owner="owner-1", universe_id=native.universe.name,
        provider="codex")
    prepared = prepare_owned_model_plan(
        base=native.base, universe=native.universe, owner="owner-1", agent=native.agent,
        allow_empty=True)
    document = model_options_document(prepared.catalog, prepared.plan, prepared.ineligible)
    rows = {row["reference"]["model_id"]: row for row in document["options"]}
    assert "private-other-owner-model" not in rows
    discovered = prepared.assignment.candidates[0].access.model_scope == "discovered"
    assert rows["previously-verified-model"]["in_candidate_catalog"] is False
    if not discovered:
        assert rows["future-native-model"]["in_candidate_catalog"] is True
        assert rows["future-native-model"]["reasons"] == []
        assert rows["previously-verified-model"]["reasons"][0]["reason"] == \
            "model_access_optin_required"


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
@pytest.mark.parametrize("mismatch", ["owner_id", "universe", "custody", "provider"])
def test_last_catalogue_cannot_cross_current_source_authority(native, monkeypatch, mismatch):  # noqa: F811
    install_discovery(native, monkeypatch)
    where = dict(base=native.base, owner="owner-1", universe_id=native.universe.name,
                 provider="codex")
    snapshot = SHORTLIST_CACHE.refresh_now(**where)
    changes = {
        "owner_id": "someone-else", "universe": native.base / "other-home",
        "custody": replace(snapshot.custody, reference_digest="old-credential"),
        "provider": "claude-code",
    }
    stale = replace(snapshot, observed_at=snapshot.observed_at - timedelta(minutes=10),
                    **{mismatch: changes[mismatch]})
    monkeypatch.setattr(SHORTLIST_CACHE, "get",
                        lambda **kwargs: (stale, "catalogue_refresh_pending"))
    prepared = prepare_owned_model_plan(
        base=native.base, universe=native.universe, owner="owner-1", agent=native.agent,
        allow_empty=True)
    assert not prepared.plan.catalog.connections
    assert any(item.reason == "discovery_unavailable" for item in prepared.ineligible)


def _display(rig):
    prepared = prepare_owned_model_plan(
        base=rig.base, universe=rig.universe, owner="owner-1", agent=rig.agent,
        allow_empty=True)
    return model_options_document(prepared.catalog, prepared.plan, prepared.ineligible)


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
@pytest.mark.parametrize("failed", [False, True])
def test_verified_on_source_a_is_not_selectable_on_source_b(native, monkeypatch, failed):  # noqa: F811
    install_discovery(native, monkeypatch)
    model_id = "source-a-only-model"
    # Use the same history writer as a successful subscription turn on source A.
    AgentTurnCoordinator._learn_verified_model(SimpleNamespace(
        context=replace(native.context, model_selection=ModelRef("claude-code", model_id)),
        owner="owner-1"), response=None)
    where = dict(base=native.base, owner="owner-1", universe_id=native.universe.name,
                 provider="codex")
    snapshot = SHORTLIST_CACHE.refresh_now(**where)
    aged = replace(snapshot, observed_at=snapshot.observed_at - timedelta(minutes=10))
    monkeypatch.setattr("tinyassets.providers.native_discovery.discover_native_models_sync",
                        lambda **kwargs: aged)
    SHORTLIST_CACHE.refresh_now(**where)
    if failed:
        def unavailable(**kwargs):
            raise ProviderError("offline")
        monkeypatch.setattr("tinyassets.providers.native_discovery.discover_native_models_sync",
                            unavailable)
        SHORTLIST_CACHE.refresh_now(**where)
    monkeypatch.setattr(SHORTLIST_CACHE, "schedule", lambda **kwargs: False)
    rows = {row["reference"]["model_id"]: row for row in _display(native)["options"]}
    assert rows[model_id]["in_candidate_catalog"] is False
    assert rows["new-account-model"]["in_candidate_catalog"] is True


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
def test_fresh_catalogue_that_dropped_verified_model_hides_it(native, monkeypatch):  # noqa: F811
    model_id = "withdrawn-model"
    models = [NativeModel(model_id, frozenset({"text"}))]

    async def discover():
        return NativeCatalogue(tuple(models), None, datetime.now(timezone.utc))

    install_discovery(native, monkeypatch, discover)
    OwnModelHistory(native.base).record(
        source_kind="subscription", model_id=model_id, owner_user_id="owner-1")
    where = dict(base=native.base, owner="owner-1", universe_id=native.universe.name,
                 provider="codex")
    SHORTLIST_CACHE.refresh_now(**where)
    assert any(row["reference"]["model_id"] == model_id and row["in_candidate_catalog"]
               for row in _display(native)["options"])
    models[:] = [NativeModel("replacement-model", frozenset({"text"}))]
    SHORTLIST_CACHE.refresh_now(**where)
    rows = {row["reference"]["model_id"]: row for row in _display(native)["options"]}
    assert rows[model_id]["in_candidate_catalog"] is False
    assert rows["replacement-model"]["in_candidate_catalog"] is True


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
def test_hidden_verified_model_stays_unselectable_with_no_snapshot(native, monkeypatch):  # noqa: F811
    model_id = "hidden-model"

    async def discover():
        return NativeCatalogue((NativeModel(model_id, frozenset({"text"}), hidden=True),),
                               None, datetime.now(timezone.utc))

    install_discovery(native, monkeypatch, discover)
    OwnModelHistory(native.base).record(
        source_kind="subscription", model_id=model_id, owner_user_id="owner-1")
    where = dict(base=native.base, owner="owner-1", universe_id=native.universe.name,
                 provider="codex")
    SHORTLIST_CACHE.refresh_now(**where)
    assert all(row["reference"]["model_id"] != model_id for row in _display(native)["options"])
    SHORTLIST_CACHE.forget(**where)
    # Forgetting a process-local entry now reloads the retained source snapshot.
    # This case explicitly exercises NO snapshot, so remove this fixture's
    # durable entry too, leaving the verified-model history intact.
    from tinyassets.providers import shortlist_store
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    with SQLiteProviderWorkAuthorityStore(native.base).connection() as conn:
        conn.execute(
            "DELETE FROM native_shortlist_cache "
            "WHERE owner_user_id=? AND universe_id=? AND provider=?",
            (where["owner"], where["universe_id"], where["provider"]),
        )
        conn.commit()
    assert shortlist_store.load(**where) is None
    monkeypatch.setattr(SHORTLIST_CACHE, "schedule", lambda **kwargs: False)
    rows = {row["reference"]["model_id"]: row for row in _display(native)["options"]}
    assert rows[model_id]["in_candidate_catalog"] is False
