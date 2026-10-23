"""The stopped repair removes absence, preserves subscription membership and reanchors."""

import json
import sqlite3
from dataclasses import replace

import pytest

from tinyassets.cutover_assignment_repair import repair


@pytest.mark.parametrize("absence", ["grant", "definition", "all_definitions"])
def test_repair_reanchors_and_is_idempotent(tmp_path, absence):
    from tinyassets.provider_assignment import (
        ProviderAssignment,
        ensure_provider_assignment_schema,
        load_provider_assignment_in_transaction,
        provider_assignment_digest,
        store_provider_assignment_in_transaction,
    )
    from tinyassets.provider_assignment_manifest import AssignmentCandidate, manifest_digest
    from tinyassets.provider_work_authority import ProviderWorkBinding, ProviderWorkBindingState
    from tinyassets.providers.definition import ProviderDefinition, _definition_id
    from tinyassets.storage import db_path
    from tinyassets.storage.provider_work_authority import _payload, _record

    stamp, digest = "2026-01-01T00:00:00Z", "sha256:" + "0" * 64
    definitions, members, bindings = [], [], []
    for index, method in enumerate(("api_key_http", "subscription_cli", "api_key_http")):
        fields = dict(
            universe_id="center",
            owner_user_id="owner",
            access_method=method,
            protocol="cli_json" if method == "subscription_cli" else "chat_messages",
            model="fixture",
            ref=f"grant-{index}",
        )
        definition = ProviderDefinition(
            id=_definition_id(**fields), **fields, visibility="private", created_at=stamp
        )
        definitions.append(definition.as_dict())
        provider = method + ":" + definition.id
        binding = ProviderWorkBinding(
            1,
            f"binding-{index}",
            1,
            digest,
            ProviderWorkBindingState.ACTIVE,
            "owner",
            "center",
            provider,
            digest,
            ("converse",),
            ("writer",),
            1,
            digest,
            0,
            10,
            100,
            0,
            "2099-01-01T00:00:00Z",
            stamp,
            stamp,
        )
        binding = replace(binding, binding_digest=binding.expected_digest())
        bindings.append(binding)
        members.append(
            AssignmentCandidate(
                provider,
                binding.binding_id,
                1,
                binding.binding_digest,
                f"reference-{index}",
                1,
                digest,
            )
        )
    (tmp_path / "center").mkdir()
    (tmp_path / "center/provider_definitions.json").write_text(
        json.dumps(
            []
            if absence == "all_definitions"
            else definitions[1:]
            if absence == "definition"
            else definitions
        )
    )
    ledger = tmp_path / "ledger.db"
    with sqlite3.connect(ledger) as conn:
        conn.execute("CREATE TABLE outbound_connection_grants(owner_user_id,grant_id)")
        conn.execute("INSERT INTO outbound_connection_grants VALUES ('owner','grant-2')")
        if absence == "definition":
            conn.execute("INSERT INTO outbound_connection_grants VALUES ('owner','grant-0')")
    with sqlite3.connect(db_path(tmp_path)) as conn:
        ensure_provider_assignment_schema(conn)
        conn.execute(
            "CREATE TABLE provider_work_bindings(binding_id PRIMARY KEY,generation,state,"
            "owner_user_id,universe_id,provider,binding_digest,record_json)"
        )
        conn.execute(
            "CREATE TABLE agent_bindings(universe_id,created_by,configuration_json,revision)"
        )
        conn.execute(
            "INSERT INTO agent_bindings VALUES ('center','owner',?,1)",
            (json.dumps({"provider_ref": "binding-0", "name": "My agent"}),),
        )
        for binding in bindings:
            conn.execute(
                "INSERT INTO provider_work_bindings VALUES (?,?,?,?,?,?,?,?)", _payload(binding)
            )
        assignment = ProviderAssignment(
            "center",
            "owner",
            "ready",
            1,
            members[0].provider,
            "binding-0",
            1,
            members[0].binding_digest,
            "reference-0",
            1,
            digest,
            digest,
            stamp,
            manifest_digest(members[0].provider, tuple(members)),
            tuple(members),
        )
        assignment = replace(
            assignment,
            assignment_digest=provider_assignment_digest(
                **{
                    k: getattr(assignment, k)
                    for k in (
                        "owner_user_id",
                        "universe_id",
                        "provider",
                        "generation",
                        "binding_id",
                        "credential_reference_id",
                        "credential_reference_generation",
                        "credential_reference_digest",
                        "manifest_digest",
                    )
                }
            ),
        )
        store_provider_assignment_in_transaction(conn, assignment)
    changed = repair(tmp_path, ledger)
    remaining = [] if absence == "all_definitions" else members[1:]
    assert changed == [
        {
            "universe_id": "center",
            "removed": sorted(m.provider for m in members if m not in remaining),
            "old_root": members[0].provider,
            "root": min(m.provider for m in remaining) if remaining else None,
        }
    ]
    assert repair(tmp_path, ledger) == []
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.row_factory = sqlite3.Row
        repaired = load_provider_assignment_in_transaction(conn, universe_id="center")
        assert {m.provider for m in repaired.candidates} == {m.provider for m in remaining}
        for member in repaired.candidates:
            binding = _record(
                conn.execute(
                    "SELECT * FROM provider_work_bindings WHERE binding_id=?", (member.binding_id,)
                ).fetchone()
            )
            assert binding.assignment_digest == repaired.assignment_digest
            assert binding.binding_digest == member.binding_digest
        assert json.loads(
            conn.execute("SELECT configuration_json FROM agent_bindings").fetchone()[0]
        ) == {"provider_ref": repaired.binding_id, "name": "My agent"}


def test_cooling_saved_default_is_not_stale(monkeypatch):
    from types import SimpleNamespace

    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.model_policy import Catalog, Interaction, ModelPolicy, ModelRef
    from tinyassets.providers.served_model_plan import _recover_stale_default

    saved = ModelRef("source", "fixture")
    plan = AgentModelPlan(
        Catalog("owner", "center", ()),
        ModelPolicy(1, "explicit", (), saved_default=saved),
        Interaction(False, frozenset({"text"}), frozenset()),
        cooling_sources=("source",),
    )
    monkeypatch.setattr(AgentModelPlan, "next_candidate", lambda *args: None)
    catalogue = SimpleNamespace(
        connection_id="source", models=[SimpleNamespace(model_id="fixture")]
    )
    assert _recover_stale_default(plan, [catalogue], {"source"}, "owner", "center") is plan
    assert _recover_stale_default(plan, [], {"source"}, "owner", "center") is plan
