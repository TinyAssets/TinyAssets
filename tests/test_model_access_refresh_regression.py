"""Refresh diagnostics cannot undo accepted access in the desktop picker."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_native_discovery_integration import install_discovery
from tests.test_native_model_authority import native  # noqa: F401
from tinyassets.exceptions import ProviderError
from tinyassets.providers.model_options import model_options_document
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
    else:
        snapshot = SHORTLIST_CACHE.refresh_now(**where)
        # Expire both the cache and snapshot clocks, not merely the refresh TTL.
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
    prepared = prepare_owned_model_plan(
        base=native.base, universe=native.universe, owner="owner-1", agent=native.agent,
        allow_empty=True)
    document = model_options_document(prepared.catalog, prepared.plan, prepared.ineligible)
    rows = {row["reference"]["model_id"]: row for row in document["options"]}
    assert "private-other-owner-model" not in rows
    discovered = prepared.assignment.candidates[0].access.model_scope == "discovered"
    assert rows["previously-verified-model"]["in_candidate_catalog"] is discovered
    if not discovered:
        assert rows["future-native-model"]["in_candidate_catalog"] is True
        assert rows["future-native-model"]["reasons"] == []
        assert rows["previously-verified-model"]["reasons"][0]["reason"] == \
            "model_access_optin_required"


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
@pytest.mark.parametrize("mismatch", ["owner_id", "universe", "custody"])
def test_last_catalogue_cannot_cross_current_source_authority(native, monkeypatch, mismatch):  # noqa: F811
    install_discovery(native, monkeypatch)
    where = dict(base=native.base, owner="owner-1", universe_id=native.universe.name,
                 provider="codex")
    snapshot = SHORTLIST_CACHE.refresh_now(**where)
    changes = {
        "owner_id": "someone-else", "universe": native.base / "other-home",
        "custody": replace(snapshot.custody, reference_digest="old-credential"),
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
