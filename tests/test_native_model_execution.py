"""Native requests are owner selections, not reported answering-model evidence."""

import asyncio
import json
from unittest.mock import patch

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.support.owned_spawn import fake_owned_spawn
from tests.test_run_provider_session import _branch, _run_branch
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.providers.base import ModelConfig
from tinyassets.storage.provider_work_authority import db_path

pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.mark.parametrize("value", [None, 1, True, " spaced ", "line\nbreak", "x" * 201])
def test_native_ids_fail_closed(value):
    from tinyassets.providers.native_model_selection import NativeSelection

    with pytest.raises(ValueError):
        NativeSelection("native", value)


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("kind", "http"),
    ("basis", "executor_enumerated"), ("default_model_id", "invented-default"),
])
def test_native_evidence_does_not_manufacture_discovery(field, value):
    from tinyassets.providers.native_model_selection import NativeSelection

    document = NativeSelection("native", "new-model").to_dict()
    document[field] = value
    with pytest.raises(ValueError):
        NativeSelection.from_dict(document)


def test_native_work_evidence_is_strict_and_roundtrips():
    from dataclasses import replace

    from tests.test_provider_invocation_selection import selection
    from tinyassets.provider_work_authority import ProviderInvocationSelection, _canonical_json
    from tinyassets.providers.native_model_selection import NativeSelection

    native = NativeSelection("native", "new-model")
    chosen = selection(provider="native", executor_id="native", model_id="new-model",
                       model_evidence_json=_canonical_json(native.to_dict()))
    assert ProviderInvocationSelection.from_dict(chosen.to_dict()) == chosen
    for kwargs in ({"model_id": "other"}, {"executor_id": "other"}, {"provider": "other"}):
        with pytest.raises(ValueError, match="does not match"):
            replace(chosen, **kwargs)
    with pytest.raises(ValueError):
        replace(chosen, model_evidence_json=json.dumps(native.to_dict(), indent=2))


def test_workflow_honors_accepted_native_id_without_rewriting_main(
    tmp_path, monkeypatch, authenticate_request,
):
    import sqlite3
    from datetime import datetime, timezone

    from tests.test_run_provider_session import _CountingProvider
    from tinyassets.providers.native_catalogue import NativeCatalogue, NativeModel

    # The owner accepted discovered scope on this executor; a named native pin
    # is then admitted on the executor's fresh enumeration. Neither alone
    # launches: an unaccepted executor is held, and so is an unenumerated id.
    monkeypatch.setattr("tinyassets.external_agents.registry.PACKAGES", {})
    monkeypatch.setattr(_CountingProvider, "native_credential_service", "codex", raising=False)

    async def enumerate_models(self, *, universe_dir, credential_snapshot_dir):
        assert credential_snapshot_dir.is_dir()
        return NativeCatalogue(
            (NativeModel("future-native-model", frozenset({"text"})),),
            "future-native-model", datetime.now(timezone.utc),
        )

    monkeypatch.setattr(_CountingProvider, "enumerate_models", enumerate_models, raising=False)

    branch = _branch(node_count=1)
    branch.node_defs[0].llm_policy["preferred"]["model"] = "future-native-model"
    before = branch.to_dict()
    response, provider, _ = _run_branch(
        tmp_path, monkeypatch, authenticate_request, branch,
        model_access={"codex": ModelAccess("discovered")},
    )
    assert response["terminal_status"] == "completed", response["terminal_error"]
    assert branch.to_dict() == before
    from tinyassets.provider_assignment import load_provider_assignment

    assert load_provider_assignment(tmp_path, universe_id="universe_alice").provider == "codex"
    assert provider.calls[0].native_model_id == "future-native-model"
    with sqlite3.connect(db_path(tmp_path)) as conn:
        rows = conn.execute("SELECT record_json FROM provider_invocation_reservations").fetchall()
    assert len(rows) == 1
    row = json.loads(rows[0][0])
    assert row["state"] == "succeeded"
    assert row["selection"]["model_id"] == "future-native-model"
    assert row["selection"]["model_evidence"]["basis"] == "executor_enumerated"


def test_native_catalogue_exposes_declared_ids_with_honest_basis(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tinyassets.providers import served_model_plan

    monkeypatch.setattr(served_model_plan, "_resolve_serving_source", lambda *args: None)
    monkeypatch.setattr("tinyassets.providers.call.get_provider_router", lambda: SimpleNamespace(
        _providers={"codex": SimpleNamespace(is_available=lambda: True)},
    ))
    models = served_model_plan._native_models(
        tmp_path, tmp_path / "universe", "owner", SimpleNamespace(
            provider="codex", access=ModelAccess("explicit", ("", "new-id", "future-id")),
        ),
    )
    # THIS SOURCE's own models. Filtered by basis because a subscription source now
    # also carries the reviewed public list (models/subscription.json) and the owner's
    # own verified ids -- both candidates to grant, with their own bases, asserted in
    # tests/test_public_model_lists.py. ModelAccess canonicalizes accepted membership;
    # this is not fallback order.
    own = [m for m in models.models
           if m.availability_basis in ("executor_default", "owner_declared",
                                       "executor_enumerated")]
    assert [m.model_id for m in own] == ["", "future-id", "new-id"]
    assert [m.availability_basis for m in own] == [
        "executor_default", "owner_declared", "owner_declared",
    ]
    assert models.default_model_id == ""




@pytest.mark.parametrize("method", ["complete", "complete_json"])
@pytest.mark.parametrize("requested", ["", "future-native-model"])
def test_claude_both_paths_emit_only_requested_primary(method, requested):
    from tinyassets.providers.claude_provider import ClaudeProvider

    class ObservedLaunch(Exception):
        pass

    with (
        patch("tinyassets.providers.claude_provider._resolve_claude_cmd",
              return_value=(["claude"], False)),
        fake_owned_spawn(
            "tinyassets.providers.claude_provider", side_effect=ObservedLaunch,
        ) as spawn,
    ):
        with pytest.raises(ObservedLaunch):
            asyncio.run(getattr(ClaudeProvider(), method)(
                "hello", "", ModelConfig(native_model_id=requested),
            ))
    argv = spawn.call_args.args
    if requested:
        assert argv[argv.index("--model") + 1] == requested
    else:
        assert "--model" not in argv
    assert "--fallback-model" not in argv
