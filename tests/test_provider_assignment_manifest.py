from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest

from tinyassets.provider_assignment import (
    ProviderAssignment,
    ensure_provider_assignment_schema,
    load_provider_assignment_in_transaction,
    provider_assignment_digest,
    store_provider_assignment_in_transaction,
)
from tinyassets.provider_assignment_manifest import (
    AssignmentCandidate,
    ModelAccess,
    manifest_digest,
)


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    ensure_provider_assignment_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def _candidate(provider="z-anchor", access=ModelAccess()):
    return AssignmentCandidate(
        provider,
        f"binding-{provider}",
        1,
        f"binding-digest-{provider}",
        f"custody-{provider}",
        1,
        f"custody-digest-{provider}",
        access,
    )


def _assignment(*, generation=1, legacy=False, candidates=None):
    anchor = _candidate()
    if candidates is None:
        candidates = () if legacy else (anchor, _candidate("a-fallback", ModelAccess("discovered")))
    digest = "" if legacy else manifest_digest(anchor.provider, candidates)
    identity = {
        "universe_id": "u-owner",
        "owner_user_id": "owner",
        "generation": generation,
        "provider": anchor.provider,
        "binding_id": anchor.binding_id,
        "credential_reference_id": anchor.credential_reference_id,
        "credential_reference_generation": anchor.credential_reference_generation,
        "credential_reference_digest": anchor.credential_reference_digest,
    }
    return ProviderAssignment(
        **identity,
        state="ready",
        binding_generation=anchor.binding_generation,
        binding_digest=anchor.binding_digest,
        updated_at="2026-09-09T20:00:00Z",
        assignment_digest=provider_assignment_digest(**identity, manifest_digest=digest),
        manifest_digest=digest,
        candidates=candidates,
    )


def _store(conn, assignment):
    conn.execute("BEGIN")
    store_provider_assignment_in_transaction(conn, assignment)
    conn.commit()


def _read(conn):
    return load_provider_assignment_in_transaction(conn, universe_id="u-owner")


def test_manifest_is_order_independent_but_root_and_cost_scope_are_bound():
    first = _candidate()
    second = _candidate("a-fallback", ModelAccess("explicit", ("new/model",), (("input_usd", 3),)))
    digest = manifest_digest(first.provider, (first, second))
    assert digest == manifest_digest(first.provider, (second, first))
    assert digest != manifest_digest(second.provider, (first, second))
    changed = replace(second, access=ModelAccess("explicit", ("new/model",), (("input_usd", 4),)))
    assert digest != manifest_digest(first.provider, (first, changed))
    # Final binding signatures are intentionally outside manifest identity.
    assert digest == manifest_digest(first.provider, (first, replace(second, binding_digest="new")))


def test_manifest_round_trip_and_legacy_digest(connection):
    root = _assignment()
    _store(connection, root)
    loaded = _read(connection)
    expected_candidates = tuple(sorted(root.candidates, key=lambda c: c.provider))
    assert loaded == replace(root, candidates=expected_candidates)
    assert loaded.candidates[0].provider != loaded.provider  # anchor is not positional
    legacy = _assignment(legacy=True)
    payload = {
        key: getattr(legacy, key)
        for key in (
            "binding_id",
            "credential_reference_digest",
            "credential_reference_generation",
            "credential_reference_id",
            "generation",
            "owner_user_id",
            "provider",
            "universe_id",
        )
    }
    payload["schema_version"] = 1
    expected = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )
    assert legacy.assignment_digest == expected
    assert legacy.assignment_digest != root.assignment_digest


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM provider_assignment_candidates WHERE provider = 'a-fallback'",
        "DELETE FROM provider_assignment_candidates WHERE provider = 'z-anchor'",
        "UPDATE provider_assignment_candidates SET binding_digest = 'tampered'",
        "UPDATE provider_assignment_candidates SET credential_reference_generation = 2",
        "UPDATE provider_assignment_candidates SET assignment_generation = 2",
        "UPDATE provider_assignment_candidates SET constraints_json = '{}'",
        "UPDATE provider_assignments SET manifest_digest = 'tampered'",
        "UPDATE provider_assignments SET binding_digest = 'tampered'",
    ],
)
def test_tampering_or_missing_members_fails_readback(connection, statement):
    _store(connection, _assignment())
    connection.execute(statement)
    with pytest.raises(RuntimeError, match="provider assignment"):
        _read(connection)


def test_same_generation_candidate_digest_is_bound_to_universe(connection):
    _store(connection, _assignment())
    connection.execute("""
        INSERT INTO provider_assignment_candidates
        SELECT 'u-other', assignment_generation, provider, binding_id, binding_generation,
               binding_digest, credential_reference_id, credential_reference_generation,
               credential_reference_digest, constraints_json, candidate_digest
          FROM provider_assignment_candidates WHERE universe_id = 'u-owner'
    """)
    assert _read(connection).universe_id == "u-owner"
    from tinyassets.provider_assignment_manifest import load_candidates

    with pytest.raises(ValueError, match="candidate digest"):
        load_candidates(connection, "u-other", 1)


def test_rollback_preserves_old_root_and_members(connection):
    _store(connection, _assignment())
    previous = _read(connection)
    connection.execute("BEGIN")
    store_provider_assignment_in_transaction(connection, _assignment(generation=2))
    assert _read(connection).generation == 2
    connection.rollback()
    assert _read(connection) == previous
    assert (
        connection.execute(
            "SELECT count(*) FROM provider_assignment_candidates WHERE assignment_generation = 2"
        ).fetchone()[0]
        == 0
    )


def test_legacy_root_ignores_leftover_children(connection):
    _store(connection, _assignment())
    legacy = _assignment(legacy=True)
    _store(connection, legacy)
    assert _read(connection) == legacy


def test_existing_schema_migrates_without_rewriting_legacy_rows():
    legacy = _assignment(legacy=True)
    old_columns = (
        "universe_id",
        "owner_user_id",
        "state",
        "generation",
        "provider",
        "binding_id",
        "binding_generation",
        "binding_digest",
        "credential_reference_id",
        "credential_reference_generation",
        "credential_reference_digest",
        "assignment_digest",
        "updated_at",
    )
    declarations = []
    for name in old_columns:
        kind = "INTEGER" if name.endswith("generation") else "TEXT"
        if name == "universe_id":
            kind += " PRIMARY KEY"
        declarations.append(f"{name} {kind} NOT NULL")
    with sqlite3.connect(":memory:", isolation_level=None) as conn:
        conn.execute(f"CREATE TABLE provider_assignments ({', '.join(declarations)})")
        conn.execute(
            "INSERT INTO provider_assignments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(getattr(legacy, name) for name in old_columns),
        )
        conn.execute("BEGIN")
        ensure_provider_assignment_schema(conn)
        conn.rollback()
        assert "manifest_digest" not in {
            row[1] for row in conn.execute("PRAGMA table_info(provider_assignments)")
        }
        ensure_provider_assignment_schema(conn)
        assert _read(conn) == legacy
        _store(conn, _assignment())
        assert _read(conn).manifest_digest


def test_write_requires_transaction_and_rejects_bad_anchor(connection):
    root = _assignment()
    with pytest.raises(ValueError, match="active transaction"):
        store_provider_assignment_in_transaction(connection, root)
    connection.execute("BEGIN")
    with pytest.raises(ValueError, match="anchor"):
        store_provider_assignment_in_transaction(connection, replace(root, binding_digest="wrong"))
    connection.rollback()
    assert _read(connection) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model_scope": "unknown"},
        {"model_scope": "explicit"},
        {"model_scope": "discovered", "model_ids": ("a",)},
        {"model_scope": "explicit", "model_ids": ("a", "a")},
        {"model_scope": "explicit", "model_ids": ("\n",)},
        {"cost_caps": (("input_usd", True),)},
        {"cost_caps": (("input_usd", -1),)},
        {"cost_caps": ()},
        {"cost_caps": (("input_usd", 1), ("input_usd", 2))},
    ],
)
def test_invalid_access_is_not_silently_broadened(kwargs):
    with pytest.raises(ValueError):
        ModelAccess(**kwargs)


def test_native_default_has_explicit_representation():
    access = ModelAccess("explicit", ("",))
    assert ModelAccess.from_json(json.dumps(access.document())) == access


def test_access_equality_matches_readback_for_unsorted_sets():
    access = ModelAccess("explicit", ("z", "a"), (("z_units", 2), ("a_units", 1)))
    assert access == ModelAccess.from_json(json.dumps(access.document()))


def test_saved_model_scope_does_not_override_live_connected_source(tmp_path, monkeypatch):
    from tests.test_open_serving_bind import _bound_and_serving
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_serving_binding import resolve_current_serving_provider_authority
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    universe, _, _ = _bound_and_serving(tmp_path, monkeypatch)
    root = load_provider_assignment(tmp_path, universe_id="u-owner")
    candidate = AssignmentCandidate(
        *(
            getattr(root, key)
            for key in (
                "provider",
                "binding_id",
                "binding_generation",
                "binding_digest",
                "credential_reference_id",
                "credential_reference_generation",
                "credential_reference_digest",
            )
        ),
        access=ModelAccess("discovered"),
    )
    manifest = manifest_digest(root.provider, (candidate,))
    identity = {
        key: getattr(root, key)
        for key in (
            "owner_user_id",
            "universe_id",
            "provider",
            "generation",
            "binding_id",
            "credential_reference_id",
            "credential_reference_generation",
            "credential_reference_digest",
        )
    }
    root = replace(
        root,
        manifest_digest=manifest,
        candidates=(candidate,),
        assignment_digest=provider_assignment_digest(**identity, manifest_digest=manifest),
    )
    with SQLiteProviderWorkAuthorityStore(tmp_path).connection() as conn:
        _store(conn, root)
    assert resolve_current_serving_provider_authority(
        tmp_path,
        universe_dir=universe,
        universe_id="u-owner",
        owner_user_id="owner-1",
    ).provider == root.provider
