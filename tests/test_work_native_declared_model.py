"""Workflow nodes admit an owner-declared native alias the way chat does.

Live command center ``u-01kxm1vszd8hwp7em418asq8h9``, run
``24feb395567b47bc`` (Morning focus note) and branch ``a2f1b4e0e639`` (node
judge): the owner's serving choice is claude-code / ``opus``, an explicit
alias. Converse admits that alias from the accepted member. Workflow
admission required the alias to occur in the fresh native catalogue, which
lists concrete model ids, and failed the node with ``native catalogue does
not match current model authority`` (``permission_denied:provider_not_bound``).
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tinyassets.credential_vault import LLMCredentialCustodyReference
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.providers.native_catalogue import NativeCatalogue, NativeModel
from tinyassets.providers.native_discovery import NativeDiscoverySnapshot
from tinyassets.providers.work_model_selection import (
    native_work_model,
    prepare_work_model_snapshot,
)


def _custody():
    return LLMCredentialCustodyReference(
        "ref", "owner-1", "home", "claude", 1, "digest", "record-digest",
    )


def _catalogue_without_alias():
    now = datetime.now(timezone.utc)
    universe = Path.cwd().resolve()
    custody = _custody()
    snapshot = NativeDiscoverySnapshot(
        "claude-code", "owner-1", universe, custody, now, now,
        NativeCatalogue(
            (NativeModel("claude-opus-4-7", frozenset({"text"})),),
            "claude-opus-4-7", now,
        ),
    )
    return snapshot, universe, custody


def test_a_declared_alias_is_admitted_when_the_catalogue_does_not_list_it():
    snapshot, universe, custody = _catalogue_without_alias()
    access = ModelAccess(model_scope="explicit", model_ids=("opus",))
    with pytest.raises(PermissionError, match="does not match current model authority"):
        snapshot.select(
            provider="claude-code", owner="owner-1", universe=universe,
            custody=custody, model_id="opus", access=access,
        )
    selected = native_work_model(
        snapshot, provider="claude-code", owner="owner-1", universe=universe,
        custody=custody, model_id="opus", access=access,
    )
    assert selected.requested_model_id == "opus"
    assert selected.basis == "owner_declared"
    # No catalogue is required for that admission.
    again = native_work_model(
        None, provider="claude-code", owner="owner-1", universe=universe,
        custody=custody, model_id="opus", access=access,
    )
    assert again == selected


def test_a_discovered_id_still_has_to_be_in_the_fresh_catalogue():
    snapshot, universe, custody = _catalogue_without_alias()
    access = ModelAccess(model_scope="discovered", model_ids=())
    with pytest.raises(PermissionError, match="does not match current model authority"):
        native_work_model(
            snapshot, provider="claude-code", owner="owner-1", universe=universe,
            custody=custody, model_id="opus", access=access,
        )
    selected = native_work_model(
        snapshot, provider="claude-code", owner="owner-1", universe=universe,
        custody=custody, model_id="claude-opus-4-7", access=access,
    )
    assert selected.basis == "executor_enumerated"
    assert selected.requested_model_id == "claude-opus-4-7"


def test_preparation_does_not_enumerate_an_owner_declared_alias(monkeypatch):
    import sys
    import types

    access = ModelAccess(model_scope="explicit", model_ids=("opus",))
    plan = types.ModuleType("tinyassets.providers.served_model_plan")
    plan.connected_sources = lambda *_a, **_k: {"claude-code"}
    monkeypatch.setitem(sys.modules, "tinyassets.providers.served_model_plan", plan)
    monkeypatch.setattr(
        "tinyassets.provider_assignment.load_provider_assignment",
        lambda *_a, **_k: object(),
    )
    monkeypatch.setattr(
        "tinyassets.provider_assignment.accepted_connected_model_access",
        lambda *_a, **_k: access,
    )

    def explode(**_k):
        raise AssertionError("declared alias must not enumerate")

    monkeypatch.setattr(
        "tinyassets.providers.native_discovery.discover_native_models_sync", explode,
    )
    assert prepare_work_model_snapshot(
        base_path=Path("."), universe_id="home", owner_user_id="owner-1",
        provider="claude-code", model_id="opus",
    ) is None
