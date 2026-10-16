"""PR-139 slice 4 permission-decision fixtures."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tinyassets.auth.provider import (
    Identity,
    PermissionContext,
    PermissionScope,
)
from tinyassets.daemon_server import initialize_author_server
from tinyassets.resolution import FIXTURE_LOCATION, validate_decision_payload
from tinyassets.storage import (
    ALL_CAPABILITIES,
    CAP_SUBMIT_REQUEST,
    DEFAULT_USER_CAPABILITIES,
    actor_has_capability,
    db_path,
    ensure_host_account,
)
from tinyassets.universe_soul import ensure_universe_soul, has_soul


def _fixture_decision(case_id: str):
    payload = json.loads(Path(FIXTURE_LOCATION).read_text(encoding="utf-8"))
    for case in payload["fixture_cases"]:
        if case["case_id"] == case_id:
            return validate_decision_payload(case["decision"])
    raise AssertionError(f"fixture case not found: {case_id}")


def test_identity_can_replays_with_soul_and_resolver_fixture(tmp_path: Path, monkeypatch) -> None:
    universe_dir = tmp_path / "permission-test-universe"
    ensure_universe_soul(
        universe_dir,
        purpose="Permission consolidation staging universe.",
    )
    with monkeypatch.context() as patch:
        patch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        assert has_soul(universe_dir, owner="test-owner")

    decision = _fixture_decision("surface-mismatch-reframe-preserves-claims")
    identity = Identity(
        user_id="user::operator",
        username="operator",
        capabilities=[CAP_SUBMIT_REQUEST],
    )
    scope = PermissionScope(
        universe_id=universe_dir.name,
        resource_type="request",
        resource_id="req-1",
    )
    context = PermissionContext(
        actor_id=identity.user_id,
        presented_grants=tuple(identity.capabilities),
        resolver_decision=decision,
    )

    verdict = identity.can(CAP_SUBMIT_REQUEST, scope=scope, context=context)
    replay = identity.can(CAP_SUBMIT_REQUEST, scope=scope, context=context)

    assert verdict.allowed is True
    assert verdict.reason == "action grant present"
    assert verdict.resolver_decision_status == "resolved"
    assert verdict.evidence_handles == tuple(decision.evidence_handles)
    assert verdict.to_dict() == replay.to_dict()


def test_known_capability_enumeration_matches_legacy_paths() -> None:
    operator = {
        "user_id": "user::operator",
        "username": "operator",
        "capabilities": list(ALL_CAPABILITIES),
        "token_type": "master_api_key",
    }
    user = {
        "user_id": "user::alice",
        "username": "alice",
        "capabilities": list(DEFAULT_USER_CAPABILITIES),
        "token_type": "session",
    }

    for capability in ALL_CAPABILITIES:
        assert actor_has_capability(operator, capability) is True
        assert actor_has_capability(user, capability) is (
            capability in DEFAULT_USER_CAPABILITIES
        )

    assert actor_has_capability(operator, "unknown_action") is False


def test_author_server_accounts_store_explicit_grants(tmp_path: Path) -> None:
    initialize_author_server(tmp_path)
    account = ensure_host_account(tmp_path, "operator")

    assert set(ALL_CAPABILITIES).issubset(set(account["capabilities"]))
    assert ("is" + "_host") not in account

    with sqlite3.connect(db_path(tmp_path)) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(user_accounts)").fetchall()
        }
    assert ("is" + "_host") not in columns
