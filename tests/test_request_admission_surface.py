from __future__ import annotations

import asyncio
import inspect
import json
import math
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import tinyassets.universe_server as universe_server
from tinyassets.api import universe as universe_api
from tinyassets.auth.provider import Identity
from tinyassets.daemon_server import (
    ensure_universe_registered,
    grant_universe_access,
    initialize_author_server,
)
from tinyassets.storage import CAP_GRANT_CAPABILITIES, db_path
from tinyassets.storage.accounts import (
    create_or_update_account,
    grant_capabilities,
    issue_priority_grant,
    revoke_priority_grant,
)

REQUEST_FIELDS = {
    "idempotency_key",
    "graph_id",
    "text",
    "request_type",
    "branch_id",
    "pickup_incentive",
    "directed_daemon_id",
    "directed_daemon_instruction",
    "priority_weight",
}
SUCCESS_FIELDS = {
    "universe_id",
    "admission_id",
    "admission_state",
    "request_id",
    "branch_task_id",
    "request_status",
    "trigger_source",
    "accepted_priority_weight",
    "priority_weight_cap",
    "priority_policy_version",
    "idempotent_replay",
    "directed_daemon_id",
}


def _connect(base_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(base_path))
    conn.row_factory = sqlite3.Row
    return conn


def _add_universe(base_path: Path, universe_id: str) -> None:
    universe_dir = base_path / universe_id
    universe_dir.mkdir(parents=True, exist_ok=True)
    ensure_universe_registered(
        base_path,
        universe_id=universe_id,
        universe_path=universe_dir,
    )


def _actor(base_path: Path, username: str) -> str:
    return str(
        create_or_update_account(base_path, username=username)["user_id"]
    )


def _authenticate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    actor_id: str,
    tenant_id: str = "tenant-a",
    capabilities: list[str] | None = None,
) -> None:
    identity = Identity(
        user_id=actor_id,
        username=actor_id,
        capabilities=capabilities or ["tinyassets.universe.write"],
        metadata={"org_id": tenant_id},
    )
    monkeypatch.setattr(
        "tinyassets.auth.middleware.current_identity",
        lambda: identity,
    )


@pytest.fixture
def admission_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str | Path]:
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY",
        "test-only-request-admission-secret-32-bytes",
    )
    monkeypatch.setattr(
        universe_server,
        "write_gate_rejection",
        lambda _tool: None,
    )
    monkeypatch.setattr(
        universe_api,
        "_universe_loop_dispatch",
        lambda _udir: ("loop-branch", {"mode": "v2"}),
    )
    initialize_author_server(tmp_path)
    _add_universe(tmp_path, "universe-a")
    issuer_id = _actor(tmp_path, "issuer")
    subject_id = _actor(tmp_path, "subject")
    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id=issuer_id,
        permission="admin",
        granted_by=issuer_id,
    )
    grant_capabilities(
        tmp_path,
        user_id=issuer_id,
        capabilities=[CAP_GRANT_CAPABILITIES],
        granted_by=issuer_id,
        universe_id="universe-a",
    )
    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id=subject_id,
        permission="write",
        granted_by=issuer_id,
    )
    _authenticate(monkeypatch, actor_id=subject_id)
    return {
        "base_path": tmp_path,
        "issuer_id": issuer_id,
        "subject_id": subject_id,
    }


def _request(
    *,
    key: str = "request-key-0001",
    text: str = "Build the next verified scene.",
    priority_weight: float = 0.0,
) -> dict:
    return {
        "target": "request",
        "idempotency_key": key,
        "graph_id": "universe-a",
        "text": text,
        "request_type": "general",
        "branch_id": "",
        "pickup_incentive": "",
        "directed_daemon_id": "",
        "directed_daemon_instruction": "",
        "priority_weight": priority_weight,
    }


def _table_count(base_path: Path, table: str) -> int:
    with _connect(base_path) as conn:
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return 0
            raise
    return int(row[0])


def test_canonical_write_graph_advertises_request_fields() -> None:
    signature = inspect.signature(universe_server.write_graph)
    for field in REQUEST_FIELDS:
        assert field in signature.parameters

    tool = next(
        tool
        for tool in asyncio.run(
            universe_server.mcp.list_tools(run_middleware=False)
        )
        if tool.name == "write_graph"
    )
    assert REQUEST_FIELDS <= set(tool.parameters["properties"])
    assert tool.annotations.idempotentHint is False


def test_all_runtime_manifests_include_rfc8785(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    import subprocess
    import sys

    runtime = tmp_path / "runtime"
    subprocess.run([sys.executable, str(root / "packaging/claude-plugin/build_plugin.py"),
                    "--runtime-root", str(runtime), "--skip-probe"], check=True)
    manifests = (
        root / "pyproject.toml",
        root / "packaging" / "mcpb" / "pyproject.toml",
        runtime / "pyproject.toml",
    )

    for manifest in manifests:
        dependencies = tomllib.loads(
            manifest.read_text(encoding="utf-8")
        )["project"]["dependencies"]
        assert any(
            dependency.startswith("rfc8785")
            for dependency in dependencies
        ), manifest


def test_server_import_probe_does_not_require_uninstalled_runtime_dependency(
) -> None:
    root = Path(__file__).parents[1]
    script = """
import builtins
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "rfc8785":
        raise ModuleNotFoundError("blocked probe dependency")
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import tinyassets.universe_server
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_canonical_write_graph_uses_transactional_result_contract(
    admission_context: dict[str, str | Path],
) -> None:
    first = json.loads(universe_server.write_graph(**_request()))
    replay = json.loads(universe_server.write_graph(**_request()))

    assert set(first) == SUCCESS_FIELDS
    assert first["admission_state"] == "committed"
    assert first["request_status"] == "pending"
    assert first["trigger_source"] == "user_request"
    assert first["accepted_priority_weight"] == 0
    assert first["priority_weight_cap"] == 100
    assert first["priority_policy_version"] == "operator-priority-v1"
    assert first["idempotent_replay"] is False
    assert first["admission_id"]
    assert first["request_id"]
    assert first["branch_task_id"]
    assert replay == {**first, "idempotent_replay": True}
    base_path = Path(admission_context["base_path"])
    assert _table_count(base_path, "request_admissions") == 1
    assert _table_count(base_path, "user_requests") == 1
    assert _table_count(base_path, "branch_tasks_v2") == 1


@pytest.mark.parametrize(
    ("key", "weight"),
    [
        ("", 0),
        ("short-key", 0),
        ("x" * 129, 0),
        ("invalid key spaces", 0),
        ("unicode-key-é-0001", 0),
        ("request-key-0001", True),
        ("request-key-0001", "1"),
        ("request-key-0001", math.nan),
        ("request-key-0001", math.inf),
        ("request-key-0001", -math.inf),
        ("request-key-0001", -1),
        ("request-key-0001", 100.0000001),
        ("request-key-0001", 10**400),
    ],
)
def test_invalid_key_or_numeric_shape_fails_before_persistence(
    admission_context: dict[str, str | Path],
    key,
    weight,
) -> None:
    result = json.loads(
        universe_server.write_graph(
            **_request(key=key, priority_weight=weight)
        )
    )

    assert result == {"error": "request_validation_error"}
    base_path = Path(admission_context["base_path"])
    assert _table_count(base_path, "request_admissions") == 0
    assert _table_count(base_path, "user_requests") == 0
    assert _table_count(base_path, "branch_tasks_v2") == 0


def test_non_utf8_request_field_fails_before_persistence(
    admission_context: dict[str, str | Path],
) -> None:
    result = json.loads(
        universe_server.write_graph(
            **_request(text="invalid-surrogate-\ud800")
        )
    )

    assert result == {"error": "request_validation_error"}
    base_path = Path(admission_context["base_path"])
    assert _table_count(base_path, "request_admissions") == 0
    assert _table_count(base_path, "user_requests") == 0
    assert _table_count(base_path, "branch_tasks_v2") == 0


def test_acl_loss_at_transaction_start_denies_before_replay_lookup(
    admission_context: dict[str, str | Path],
    monkeypatch,
) -> None:
    def lost_access(_conn) -> None:
        raise PermissionError("universe_access_denied")

    monkeypatch.setattr(
        universe_api.permissions,
        "operator_request_transaction_checks",
        lambda _verdict: (lost_access, lambda _conn: None),
    )
    monkeypatch.setattr(
        universe_api,
        "_universe_loop_dispatch",
        lambda _udir: ("", {"error": "universe_loop_not_declared"}),
    )

    result = json.loads(universe_server.write_graph(**_request()))

    assert result == {"error": "universe_access_denied"}
    base_path = Path(admission_context["base_path"])
    assert _table_count(base_path, "request_admissions") == 0
    assert _table_count(base_path, "user_requests") == 0
    assert _table_count(base_path, "branch_tasks_v2") == 0


def test_unauthorized_missing_universe_is_non_enumerating(
    admission_context: dict[str, str | Path],
) -> None:
    request = _request()
    request["graph_id"] = "missing-universe"

    result = json.loads(universe_server.write_graph(**request))

    assert result == {"error": "universe_access_denied"}


def test_committed_replay_survives_later_loop_removal(
    admission_context: dict[str, str | Path],
    monkeypatch,
) -> None:
    first = json.loads(universe_server.write_graph(**_request()))
    monkeypatch.setattr(
        universe_api,
        "_universe_loop_dispatch",
        lambda _udir: ("", {"error": "universe_loop_not_declared"}),
    )

    replay = json.loads(universe_server.write_graph(**_request()))

    assert replay == {**first, "idempotent_replay": True}


@pytest.mark.parametrize(
    "weight",
    [0, 1e-9, math.nextafter(100.0, 0.0), 100],
)
def test_valid_numeric_boundaries_reach_authority_policy(
    admission_context: dict[str, str | Path],
    monkeypatch: pytest.MonkeyPatch,
    weight: float,
) -> None:
    base_path = Path(admission_context["base_path"])
    issuer_id = str(admission_context["issuer_id"])
    subject_id = str(admission_context["subject_id"])
    if weight > 0:
        issue_priority_grant(
            base_path,
            subject_id=subject_id,
            universe_id="universe-a",
            issuer_id=issuer_id,
        )
    _authenticate(monkeypatch, actor_id=subject_id)

    result = json.loads(
        universe_server.write_graph(
            **_request(
                key=f"numeric-key-{weight!r}-0000000000000000",
                priority_weight=weight,
            )
        )
    )

    assert result["accepted_priority_weight"] == weight
    assert result["trigger_source"] == (
        "operator_request" if weight > 0 else "user_request"
    )


def test_unknown_request_target_fields_are_rejected_without_mutation(
    admission_context: dict[str, str | Path],
) -> None:
    result = json.loads(
        universe_server.write_graph(
            **_request(),
            name="goal-only-field",
        )
    )

    assert result == {"error": "request_validation_error"}
    assert _table_count(
        Path(admission_context["base_path"]),
        "request_admissions",
    ) == 0


def test_missing_hmac_secret_fails_closed_before_persistence(
    admission_context: dict[str, str | Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY",
        raising=False,
    )

    result = json.loads(universe_server.write_graph(**_request()))

    assert result == {"error": "request_admission_unavailable"}
    assert _table_count(
        Path(admission_context["base_path"]),
        "request_admissions",
    ) == 0


def test_positive_without_grant_and_invalid_direction_persist_nothing(
    admission_context: dict[str, str | Path],
) -> None:
    base_path = Path(admission_context["base_path"])
    positive = json.loads(
        universe_server.write_graph(
            **_request(
                key="positive-no-grant-key-01",
                priority_weight=1,
            )
        )
    )
    directed_kwargs = _request(key="invalid-directed-key-01")
    directed_kwargs["directed_daemon_id"] = "missing-daemon"
    directed = json.loads(
        universe_server.write_graph(**directed_kwargs)
    )

    assert positive == {"error": "priority_authorization_required"}
    assert directed == {"error": "directed_daemon_not_authorized"}
    assert _table_count(base_path, "request_admissions") == 0
    assert _table_count(base_path, "user_requests") == 0
    assert _table_count(base_path, "branch_tasks_v2") == 0


def test_public_replay_survives_priority_revocation_but_new_key_does_not(
    admission_context: dict[str, str | Path],
) -> None:
    base_path = Path(admission_context["base_path"])
    issuer_id = str(admission_context["issuer_id"])
    subject_id = str(admission_context["subject_id"])
    issue_priority_grant(
        base_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    original_kwargs = _request(
        key="revoked-priority-key-01",
        priority_weight=50,
    )
    first = json.loads(universe_server.write_graph(**original_kwargs))
    revoke_priority_grant(
        base_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )

    replay = json.loads(universe_server.write_graph(**original_kwargs))
    new_key = json.loads(
        universe_server.write_graph(
            **_request(
                key="revoked-priority-key-02",
                priority_weight=50,
            )
        )
    )

    assert replay == {**first, "idempotent_replay": True}
    assert new_key == {"error": "priority_authorization_required"}
    assert _table_count(base_path, "request_admissions") == 1


def test_body_digest_is_rfc8785_bound_without_unicode_normalization(
    admission_context: dict[str, str | Path],
) -> None:
    base_path = Path(admission_context["base_path"])
    composed = "caf\u00e9"
    decomposed = "cafe\u0301"
    first = json.loads(
        universe_server.write_graph(
            **_request(key="unicode-body-key-01", text=composed)
        )
    )
    conflict = json.loads(
        universe_server.write_graph(
            **_request(key="unicode-body-key-01", text=decomposed)
        )
    )
    independent = json.loads(
        universe_server.write_graph(
            **_request(key="unicode-body-key-02", text=decomposed)
        )
    )

    assert first["request_id"] != independent["request_id"]
    assert conflict == {"error": "idempotency_key_body_conflict"}
    with _connect(base_path) as conn:
        rows = conn.execute(
            """
            SELECT idempotency_key_hash, body_digest, body_digest_version
            FROM request_admissions
            ORDER BY created_at, admission_id
            """
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["body_digest"] != rows[1]["body_digest"]
    assert {row["body_digest_version"] for row in rows} == {"rfc8785-v1"}
    assert all(
        row["idempotency_key_hash"] not in {
            "unicode-body-key-01",
            "unicode-body-key-02",
        }
        for row in rows
    )
    assert _table_count(base_path, "user_requests") == 2
    assert _table_count(base_path, "branch_tasks_v2") == 2
    assert _table_count(base_path, "request_admission_events") == 2


def test_same_raw_key_is_independent_across_server_derived_scope(
    admission_context: dict[str, str | Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_path = Path(admission_context["base_path"])
    issuer_id = str(admission_context["issuer_id"])
    subject_id = str(admission_context["subject_id"])
    _add_universe(base_path, "universe-b")
    grant_universe_access(
        base_path,
        universe_id="universe-b",
        actor_id=subject_id,
        permission="write",
        granted_by=issuer_id,
    )
    first = json.loads(
        universe_server.write_graph(
            **_request(key="cross-scope-key-01")
        )
    )
    second_request = _request(key="cross-scope-key-01")
    second_request["graph_id"] = "universe-b"
    _authenticate(monkeypatch, actor_id=subject_id)
    second = json.loads(universe_server.write_graph(**second_request))

    assert first["request_id"] != second["request_id"]
    assert _table_count(base_path, "request_admissions") == 2


def test_lost_delivery_replay_skips_mutation_ledger_and_legacy_writer(
    admission_context: dict[str, str | Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_calls: list[tuple] = []
    monkeypatch.setattr(
        "tinyassets.api.engine_helpers._append_ledger",
        lambda *args, **kwargs: ledger_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        universe_server,
        "_universe_impl",
        lambda **_kwargs: pytest.fail("public request used legacy writer"),
    )
    first = json.loads(universe_server.write_graph(**_request()))
    # The first commit succeeded, but its response is treated as lost. A retry
    # through the canonical connector must reconstruct the original durable IDs.
    replay = json.loads(universe_server.write_graph(**_request()))

    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert len(ledger_calls) == 1
    assert ledger_calls[0][0][0] == (
        Path(admission_context["base_path"]) / "universe-a"
    )
