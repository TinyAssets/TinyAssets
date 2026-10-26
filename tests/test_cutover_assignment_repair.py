"""The stopped repair removes absence, preserves subscription membership and reanchors."""

import json
import sqlite3
from dataclasses import replace

import pytest

from tinyassets.cutover_assignment_repair import repair


@pytest.mark.parametrize("live", ["broker", "volume"])
def test_repair_refuses_live_writers_before_opening_owner_data(tmp_path, monkeypatch, live):
    import os
    import socket

    from tinyassets import cutover_assignment_repair as cutover

    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "self/mountinfo").write_text("")
    monkeypatch.setattr(cutover, "PROC_ROOT", proc, raising=False)
    sockpath = tmp_path / "broker.sock"
    monkeypatch.setattr(cutover, "BROKER_SOCKET", sockpath, raising=False)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        if live == "broker":
            listener.bind(str(sockpath))
            listener.listen()
        else:
            (proc / "123").mkdir(parents=True)
            dev = tmp_path.stat().st_dev
            (proc / "123/mountinfo").write_text(
                f"31 20 {os.major(dev)}:{os.minor(dev)} / /home/owner rw - xfs /dev/loop0 rw\n")
        with pytest.raises(RuntimeError, match="broker|attached"):
            repair(tmp_path, tmp_path / "absent-ledger.db")


STAMP, DIGEST = "2026-01-01T00:00:00Z", "sha256:" + "0" * 64


def _accepted(overrides=None):
    """Three accepted members (HTTP, CLI, HTTP) with their definitions and bindings.

    ``overrides`` maps a member index to definition fields that differ from this
    owner's center: a row naming another owner or center still carries a valid
    content address, so only an ownership check can reject it.
    """
    from tinyassets.provider_assignment_manifest import AssignmentCandidate
    from tinyassets.provider_work_authority import ProviderWorkBinding, ProviderWorkBindingState
    from tinyassets.providers.definition import ProviderDefinition, _definition_id

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
        fields.update((overrides or {}).get(index, {}))
        definition = ProviderDefinition(
            id=_definition_id(**fields), **fields, visibility="private", created_at=STAMP
        )
        definitions.append(definition.as_dict())
        provider = method + ":" + definition.id
        binding = ProviderWorkBinding(
            1,
            f"binding-{index}",
            1,
            DIGEST,
            ProviderWorkBindingState.ACTIVE,
            "owner",
            "center",
            provider,
            DIGEST,
            ("converse",),
            ("writer",),
            1,
            DIGEST,
            0,
            10,
            100,
            0,
            "2099-01-01T00:00:00Z",
            STAMP,
            STAMP,
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
                DIGEST,
            )
        )
    return definitions, members, bindings


def _owner_volume(tmp_path, members, bindings, *, stored, grants):
    """Write the center's definition store, the broker grant ledger and the owner DB."""
    from tinyassets.provider_assignment import (
        ProviderAssignment,
        ensure_provider_assignment_schema,
        provider_assignment_digest,
        store_provider_assignment_in_transaction,
    )
    from tinyassets.provider_assignment_manifest import manifest_digest
    from tinyassets.storage import db_path
    from tinyassets.storage.provider_work_authority import _payload

    (tmp_path / "center").mkdir()
    (tmp_path / "center/provider_definitions.json").write_text(json.dumps(stored))
    ledger = tmp_path / "ledger.db"
    with sqlite3.connect(ledger) as conn:
        conn.execute("CREATE TABLE outbound_connection_grants(owner_user_id,grant_id)")
        conn.executemany(
            "INSERT INTO outbound_connection_grants VALUES ('owner',?)",
            [(grant,) for grant in grants],
        )
    conn.close()
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
            DIGEST,
            DIGEST,
            STAMP,
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
    conn.close()  # The last connection's close is what checkpoints the WAL away.
    _checkpointed_wal(ledger)
    _checkpointed_wal(db_path(tmp_path))
    return ledger


def _sidecars(path):
    return [
        suffix
        for suffix in ("-wal", "-shm", "-journal")
        if path.with_name(path.name + suffix).exists()
    ]


def _checkpointed_wal(path):
    """Leave the store the way production owner storage sits: WAL mode, index
    checkpointed away. A `mode=ro` open of exactly this writes a -wal and -shm."""
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    assert _sidecars(path) == []


def _volume_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("absence", ["grant", "definition", "all_definitions"])
def test_repair_reanchors_and_is_idempotent(tmp_path, absence):
    from tinyassets.provider_assignment import load_provider_assignment_in_transaction
    from tinyassets.storage import db_path
    from tinyassets.storage.provider_work_authority import _record

    definitions, members, bindings = _accepted()
    ledger = _owner_volume(
        tmp_path,
        members,
        bindings,
        stored=(
            []
            if absence == "all_definitions"
            else definitions[1:]
            if absence == "definition"
            else definitions
        ),
        grants=["grant-2", "grant-0"] if absence == "definition" else ["grant-2"],
    )
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


@pytest.mark.verifies("owner-daemon-in-box:Cutover accounts for loss without guessing ownership")
def test_dry_run_previews_the_real_plan_and_writes_nothing(tmp_path):
    from tinyassets.storage import db_path

    definitions, members, bindings = _accepted()
    ledger = _owner_volume(
        tmp_path, members, bindings, stored=definitions[1:], grants=["grant-0", "grant-2"]
    )
    untouched = _volume_bytes(tmp_path)
    preview = repair(tmp_path, ledger, dry_run=True)
    assert preview == [
        {
            "universe_id": "center",
            "removed": [members[0].provider],
            "old_root": members[0].provider,
            "root": min(m.provider for m in members[1:]),
        }
    ]
    # No file created, removed or changed anywhere under the volume -- which holds
    # the ledger too -- and no WAL index beside either WAL database.
    assert _volume_bytes(tmp_path) == untouched
    assert _sidecars(db_path(tmp_path)) == [] and _sidecars(ledger) == []
    assert repair(tmp_path, ledger) == preview
    assert _volume_bytes(tmp_path) != untouched
    # The repair run leaves its own WAL behind: the preview must read that too.
    assert repair(tmp_path, ledger, dry_run=True) == []


@pytest.mark.verifies("owner-daemon-in-box:Cutover accounts for loss without guessing ownership")
def test_dry_run_plans_every_schema_the_repair_run_accepts(tmp_path):
    from tinyassets.storage import db_path

    definitions, members, bindings = _accepted()
    ledger = _owner_volume(
        tmp_path, members, bindings, stored=definitions[1:], grants=["grant-0", "grant-2"]
    )
    index = "idx_provider_assignment_owner"
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(f"DROP INDEX {index}")
    conn.close()
    _checkpointed_wal(db_path(tmp_path))
    untouched = _volume_bytes(tmp_path)
    preview = repair(tmp_path, ledger, dry_run=True)
    assert preview == [
        {
            "universe_id": "center",
            "removed": [members[0].provider],
            "old_root": members[0].provider,
            "root": min(m.provider for m in members[1:]),
        }
    ]
    # The schema the repair run creates is prepared on the copy, not the volume.
    assert _volume_bytes(tmp_path) == untouched
    assert repair(tmp_path, ledger) == preview
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (index,)
        ).fetchone() == (1,)


@pytest.mark.verifies("owner-daemon-in-box:Cutover accounts for loss without guessing ownership")
@pytest.mark.parametrize(
    "reason,override",
    [
        ("definition_center_mismatch", {"universe_id": "other-center"}),
        ("definition_owner_mismatch", {"owner_user_id": "other-owner"}),
    ],
)
def test_a_foreign_definition_is_reported_instead_of_aborting_the_owner(tmp_path, reason, override):
    definitions, members, bindings = _accepted({0: override})
    ledger = _owner_volume(
        tmp_path, members, bindings, stored=definitions, grants=["grant-0", "grant-2"]
    )
    survivor = min(m.provider for m in members[1:])
    planned = {
        "universe_id": "center",
        "removed": [members[0].provider],
        "old_root": members[0].provider,
        "root": survivor,
        "foreign_definitions": {reason: 1},
    }
    assert repair(tmp_path, ledger, dry_run=True) == [planned]
    assert repair(tmp_path, ledger) == [planned]
    # The foreign row stays in the store, is reported again, and changes nothing more.
    assert json.loads((tmp_path / "center/provider_definitions.json").read_text()) == definitions
    assert repair(tmp_path, ledger) == [
        {
            "universe_id": "center",
            "removed": [],
            "old_root": survivor,
            "root": survivor,
            "foreign_definitions": {reason: 1},
        }
    ]


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


pytestmark = pytest.mark.verifies('owner-daemon-in-box:C3')
