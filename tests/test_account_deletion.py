"""Self-service account deletion (tinyassets.account_deletion + the app route).

The floor is cross-user: deleting A must remove everything that is A's and
touch nothing that is B's. Every data test here builds two users and asserts
both halves.

The deleted row set is derived from the live schema rather than a hand-written
list, so the tests that matter most are the ones that would catch a *rule* being
wrong: a person-keyed table reaching into another universe, a new table with a
person's id in it, a cascade removing rows nobody counted.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import urllib.error
from pathlib import Path

import pytest

from tinyassets import account_deletion
from tinyassets.account_deletion import (
    AccountDeletionBlocked,
    AccountDeletionError,
    delete_account,
)
from tinyassets.daemon_server import (
    ensure_universe_registered,
    get_founder_home,
    grant_universe_access,
    set_founder_home,
)
from tinyassets.storage import webhook_hooks
from tinyassets.storage.outbound_connections import ConnectionLedger

A, B = "user_01AAAAAAAAAAAAAAAAAAAAAAAA", "user_01BBBBBBBBBBBBBBBBBBBBBBBB"
HOME_A, HOME_B = "u-0000000000000001", "u-0000000000000002"


def _seed_user(base: Path, sub: str, home: str) -> Path:
    universe_dir = base / home
    universe_dir.mkdir()
    (universe_dir / "soul.md").write_text("# soul\n", encoding="utf-8")
    (universe_dir / ".credential-vault.json").write_text("[]", encoding="utf-8")
    (universe_dir / "memory").mkdir()
    (universe_dir / "memory" / "notes.md").write_text("private", encoding="utf-8")
    set_founder_home(base, founder_sub=sub, universe_id=home, platform_generated=True)
    ensure_universe_registered(base, universe_id=home, universe_path=universe_dir)
    grant_universe_access(
        base, universe_id=home, actor_id=sub, permission="admin", granted_by=sub
    )
    webhook_hooks.mint(
        base,
        universe_id=home,
        branch_def_id=f"bd-{home}",
        owner_principal_id=sub,
    )
    return universe_dir


def _seed_outbound(base: Path) -> Path:
    path = base / "outbound.db"
    ConnectionLedger(path)  # creates the schema exactly as production does
    conn = sqlite3.connect(str(path))
    with conn:
        for sub, home, tag in ((A, HOME_A, "a"), (B, HOME_B, "b")):
            conn.execute(
                "INSERT INTO outbound_connections (connection_id, owner_user_id, "
                "connection_class, scopes_json, provider, destination, credential_ref) "
                "VALUES (?, ?, 'compute', '[]', 'openrouter', 'https://x', ?)",
                (f"conn-{tag}", sub, f"cred-{tag}"),
            )
            conn.execute(
                "INSERT INTO outbound_connection_grants (grant_id, connection_id, "
                "owner_user_id, universe_id, granted_at) VALUES (?, ?, ?, ?, 1.0)",
                (f"grant-{tag}", f"conn-{tag}", sub, home),
            )
            conn.execute(
                "INSERT INTO outbound_connector_artifacts (artifact_id, owner_user_id, "
                "connector_definition_json, mcp_client_config_json, created_at) "
                "VALUES (?, ?, '{}', '{}', 1.0)",
                (f"art-{tag}", sub),
            )
        # B remixed A's artifact: the edge references A's row and must go with it.
        conn.execute(
            "INSERT INTO outbound_connector_artifact_edges (parent_artifact_id, "
            "child_artifact_id, remixed_by_user_id, created_at) "
            "VALUES ('art-a', 'art-b', ?, 1.0)",
            (B,),
        )
    conn.close()
    return path


def _seed_auth(base: Path) -> Path:
    path = base / ".auth.db"
    conn = sqlite3.connect(str(path))
    with conn:
        conn.execute(
            "CREATE TABLE access_tokens (token TEXT PRIMARY KEY, user_id TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE refresh_tokens (token TEXT PRIMARY KEY, user_id TEXT NOT NULL)"
        )
        conn.execute("CREATE TABLE oauth_clients (client_id TEXT PRIMARY KEY)")
        for sub, tag in ((A, "a"), (B, "b")):
            conn.execute("INSERT INTO access_tokens VALUES (?, ?)", (f"at-{tag}", sub))
            conn.execute("INSERT INTO refresh_tokens VALUES (?, ?)", (f"rt-{tag}", sub))
        conn.execute("INSERT INTO oauth_clients VALUES ('client-shared')")
    conn.close()
    return path


def _rows(db: Path, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return [tuple(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _exec(db: Path, sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(str(db))
    try:
        with conn:
            conn.execute(sql, params)
    finally:
        conn.close()


@pytest.fixture
def two_users(tmp_path: Path) -> Path:
    base = tmp_path / "data"
    base.mkdir()
    _seed_user(base, A, HOME_A)
    _seed_user(base, B, HOME_B)
    _seed_outbound(base)
    _seed_auth(base)
    return base


# --------------------------------------------------------------------------- #
# the cross-user floor
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("has_home", [True, False])
def test_shared_copy_provenance_deletes_by_owner_across_home_changes(two_users: Path, has_home):
    from tinyassets import command_center_update_registry as registry
    from tinyassets.command_center_update_executor import _RECEIPTS, _STATUS
    from tinyassets.command_center_update_policy import _SCHEMA as policy_schema
    from tinyassets.storage import db_path

    path = db_path(two_users)
    with sqlite3.connect(path) as conn:
        for statement in registry._SCHEMA + policy_schema + (_RECEIPTS, _STATUS):
            conn.execute(statement)
        for owner, home in ((A, HOME_A), (A, "former-home"), (B, HOME_A), (B, HOME_B)):
            key = owner + home
            conn.execute(
                "INSERT INTO command_center_adoptions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (owner, home, key, "install-" + key, "source", "source", "ui", "hash", 1, "", ""),
            )
            conn.execute(
                "INSERT INTO command_center_update_requests VALUES (?,?,?,?,?,?)",
                (owner, home, key, "request-" + key, "digest", '{}'),
            )
            conn.execute(
                "INSERT INTO command_center_update_policies VALUES (?,?,?,?,?,?,?,?)",
                (owner, home, key, 1, 1, '{}', "grant-" + key, "digest"),
            )
            conn.execute(
                "INSERT INTO command_center_policy_requests VALUES (?,?,?,?,?,?)",
                (owner, home, key, "policy-request-" + key, '{}', "digest"),
            )
            conn.execute(
                "INSERT INTO command_center_auto_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("auto-" + key, owner, home, key, "grant-" + key, '{}', None, 0,
                 "pending", "", 1.0),
            )
            conn.execute(
                "INSERT INTO command_center_auto_status VALUES (?,?,?,?,?,?)",
                (owner, home, key, "auto-" + key, '{}', 1.0),
            )
        if not has_home:
            conn.execute("DELETE FROM founder_home WHERE founder_sub=?", (A,))
        plan = account_deletion.deletion_plan(
            conn, principal=A, home=HOME_A if has_home else "")
        tables = ("command_center_adoptions", "command_center_update_requests",
                  "command_center_update_policies", "command_center_policy_requests",
                  "command_center_auto_receipts", "command_center_auto_status")
        for table in tables:
            assert plan[table] == [("owner_id", "principal")]
    delete_account(two_users, founder_sub=A, cancel_billing=lambda home: "cancelled",
                   delete_identity=lambda sub: "deleted")
    for table in tables:
        assert _rows(path, f'SELECT owner_id, universe_id FROM "{table}" ORDER BY universe_id') == [
            (B, HOME_A), (B, HOME_B)]


def test_model_preferences_removed_for_current_and_former_home_only_for_owner(two_users: Path):
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.model_preferences import ModelPreferences
    from tinyassets.storage.model_preferences import ModelPreferenceStore

    store = ModelPreferenceStore(two_users)
    prefs = ModelPreferences("explicit", ModelRef("owned:future", "opaque-new-model"), ())
    store.save(A, HOME_A, expected_generation=0, policy=prefs)
    store.save(A, "former-home", expected_generation=0, policy=prefs)
    other = store.save(B, HOME_B, expected_generation=0, policy=prefs)
    delete_account(two_users, founder_sub=A, cancel_billing=lambda home: "cancelled",
                   delete_identity=lambda sub: "deleted")
    assert store.get(A, HOME_A).policy is None
    assert store.get(A, "former-home").policy is None
    assert store.get(B, HOME_B) == other


def test_agent_journal_deletion_counts_cascades_and_former_home(two_users: Path):
    from tinyassets.providers.agent_chat_codec import decode_openai_chat_agent
    from tinyassets.storage.agent_turn_journal import AgentTurnJournal
    from tinyassets.storage.agent_turn_records import RoundInput, dump

    journal = AgentTurnJournal(two_users)
    candidate = RoundInput("owned:test", "model", dump({"version": 1, "tools": [
        {"type": "function", "function": {"name": "tool", "description": "",
                                         "parameters": {"type": "object"}}},
    ]}), "binding", "reservation", 1, "sha256:" + "a" * 64, "sha256:" + "b" * 64)
    reply = decode_openai_chat_agent({"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": [{"id": "call", "type": "function",
        "function": {"name": "tool", "arguments": "{}"}}],
    }}]}, source_ref="owned:test", requested_model="model", tool_names=frozenset({"tool"}))
    turns = []
    for owner, home in ((A, HOME_A), (A, "former-home"), (B, HOME_B)):
        # Model a genuine past home, then restore the current one before deletion.
        from tinyassets.daemon_server import set_founder_home

        set_founder_home(two_users, founder_sub=owner, universe_id=home, platform_generated=True)
        turn = journal.create(owner, home, prompt="private", system="exact")
        turn = journal.begin_round(owner, home, turn.turn_id,
            expected_generation=turn.generation, candidate=candidate).snapshot
        turn = journal.finish_inference(owner, home, turn.turn_id,
            expected_generation=turn.generation, ordinal=1, reply=reply).snapshot
        turns.append(turn)
    set_founder_home(two_users, founder_sub=A, universe_id=HOME_A, platform_generated=True)
    receipt = delete_account(two_users, founder_sub=A, cancel_billing=lambda home: "cancelled",
                             delete_identity=lambda sub: "deleted")
    assert journal.get(A, HOME_A, turns[0].turn_id) is None
    assert journal.get(A, "former-home", turns[1].turn_id) is None
    assert journal.get(B, HOME_B, turns[2].turn_id) == turns[2]
    for table in ("agent_turns", "agent_turn_rounds", "agent_turn_tools"):
        assert receipt["rows_deleted"][table] == 2


def test_agent_session_records_and_rules_go_with_the_account_and_no_one_elses(
    two_users: Path,
):
    """Custom Rules and review switches live in .agent-sessions/<home>/rules.db,
    beside the universe rather than inside it, so removing the home alone left
    them behind after deletion."""
    from tinyassets import agent_rules
    from tinyassets.agent_sessions import RECORDS_DIR

    for home in (HOME_A, HOME_B):
        agent_rules.set_rule(two_users / home, "app.read", agent_rules.ASK_FIRST)
    records_a = two_users / RECORDS_DIR / HOME_A
    records_b = two_users / RECORDS_DIR / HOME_B
    assert (records_a / "rules.db").is_file() and (records_b / "rules.db").is_file()

    receipt = delete_account(two_users, founder_sub=A, cancel_billing=lambda home: "cancelled",
                             delete_identity=lambda sub: "deleted")

    assert not records_a.exists()
    assert receipt["unfinished_phases"] == []
    assert [r.behaviour for r in agent_rules.list_rules(two_users / HOME_B)
            if r.action_class == "app.read"] == [agent_rules.ASK_FIRST]


def test_account_deletion_stops_the_accounts_activities_first(two_users: Path, monkeypatch):
    """Harness D2: a running activity is fenced and its run cancelled before its
    store is removed, so it cannot act for the deleted account."""
    from tinyassets import agent_activities, runs
    from tinyassets.agent_sessions import RECORDS_DIR

    cancelled = []
    monkeypatch.setattr(runs, "request_cancel", lambda base, run_id: cancelled.append(run_id))
    aid = agent_activities.create(two_users / HOME_A, owner_principal=A, title="t", brief="b",
                                  origin_kind="ask")["activity_id"]
    generation = agent_activities.claim(two_users / HOME_A, aid, replaceable=lambda r: False)
    agent_activities.bind_run(two_users / HOME_A, aid, generation, "run-a")
    other = agent_activities.create(two_users / HOME_B, owner_principal=B, title="t", brief="b",
                                    origin_kind="ask")["activity_id"]

    receipt = delete_account(two_users, founder_sub=A, cancel_billing=lambda home: "cancelled",
                             delete_identity=lambda sub: "deleted")

    assert cancelled == ["run-a"]
    assert not (two_users / RECORDS_DIR / HOME_A).exists()
    assert receipt["unfinished_phases"] == []
    assert agent_activities.get(two_users / HOME_B, other)["status"] == "scheduled"


def test_a_symlinked_records_root_is_refused_and_reported_not_followed(
    two_users: Path, tmp_path: Path,
):
    from tinyassets.agent_sessions import RECORDS_DIR

    elsewhere = tmp_path / "elsewhere"
    (elsewhere / HOME_A).mkdir(parents=True)
    (elsewhere / HOME_A / "keep.txt").write_text("not the account's", encoding="utf-8")
    try:
        (two_users / RECORDS_DIR).symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("this host cannot create symlinks")

    receipt = delete_account(two_users, founder_sub=A, cancel_billing=lambda home: "cancelled",
                             delete_identity=lambda sub: "deleted")

    assert (elsewhere / HOME_A / "keep.txt").is_file()
    assert "agent_session_records" in receipt["unfinished_phases"]


def test_records_that_are_not_a_directory_are_reported_not_skipped(two_users: Path):
    from tinyassets.agent_sessions import RECORDS_DIR

    (two_users / RECORDS_DIR).mkdir()
    (two_users / RECORDS_DIR / HOME_A).write_text("odd", encoding="utf-8")

    receipt = delete_account(two_users, founder_sub=A, cancel_billing=lambda home: "cancelled",
                             delete_identity=lambda sub: "deleted")

    assert "agent_session_records" in receipt["unfinished_phases"]


def test_deleting_a_removes_all_of_a_and_none_of_b(two_users: Path):
    base = two_users
    root_db = base / ".tinyassets.db"
    billed: list[str] = []
    identities: list[str] = []

    receipt = delete_account(
        base,
        founder_sub=A,
        cancel_billing=lambda home: billed.append(home) or "cancelled",
        delete_identity=lambda sub: identities.append(sub) or "deleted",
    )

    # A: gone everywhere.
    assert not (base / HOME_A).exists()
    assert get_founder_home(base, A) == ""
    for table in ("universes", "branches", "universe_rules"):
        assert _rows(
            root_db, f"SELECT 1 FROM {table} WHERE universe_id = ?", (HOME_A,)
        ) == []
    assert _rows(root_db, "SELECT 1 FROM universe_acl WHERE actor_id = ?", (A,)) == []
    assert webhook_hooks.list_for_universe(base, universe_id=HOME_A) == []
    outbound = base / "outbound.db"
    for table in (
        "outbound_connections", "outbound_connection_grants", "outbound_connector_artifacts"
    ):
        assert _rows(
            outbound, f"SELECT 1 FROM {table} WHERE owner_user_id = ?", (A,)
        ) == []
    assert _rows(outbound, "SELECT 1 FROM outbound_connector_artifact_edges") == []
    auth = base / ".auth.db"
    for table in ("access_tokens", "refresh_tokens"):
        assert _rows(auth, f"SELECT 1 FROM {table} WHERE user_id = ?", (A,)) == []
    assert billed == [HOME_A]
    assert identities == [A]

    # B: untouched.
    assert (base / HOME_B / "soul.md").is_file()
    assert (base / HOME_B / "memory" / "notes.md").read_text(encoding="utf-8") == "private"
    assert get_founder_home(base, B) == HOME_B
    assert _rows(root_db, "SELECT 1 FROM universes WHERE universe_id = ?", (HOME_B,)) == [(1,)]
    assert len(_rows(root_db, "SELECT 1 FROM branches WHERE universe_id = ?", (HOME_B,))) >= 1
    b_sql = "SELECT permission FROM universe_acl WHERE universe_id = ?"
    assert _rows(root_db, b_sql, (HOME_B,)) == [("admin",)]
    assert len(webhook_hooks.list_for_universe(base, universe_id=HOME_B)) == 1
    assert _rows(outbound, "SELECT connection_id FROM outbound_connections") == [("conn-b",)]
    assert _rows(outbound, "SELECT grant_id FROM outbound_connection_grants") == [("grant-b",)]
    assert _rows(outbound, "SELECT artifact_id FROM outbound_connector_artifacts") == [("art-b",)]
    assert _rows(auth, "SELECT token FROM access_tokens") == [("at-b",)]
    assert _rows(auth, "SELECT 1 FROM oauth_clients") == [(1,)]

    # The receipt is content-free and says what happened.
    assert receipt["home_id"] == HOME_A
    assert receipt["home_removed"] is True
    assert receipt["home_staged_path"] == ""
    assert receipt["billing"] == "cancelled"
    assert receipt["identity"] == "deleted"
    assert receipt["unfinished_phases"] == []
    assert receipt["rows_deleted"]["founder_home"] == 1
    assert receipt["rows_deleted"]["universes"] == 1
    assert receipt["rows_deleted"]["webhook_hooks"] == 1
    assert receipt["rows_deleted"]["outbound:outbound_connections"] == 1
    assert A not in json.dumps(receipt) and HOME_B not in json.dumps(receipt)
    # The staging dir exists only mid-operation, so no root-level dot-dir lingers
    # for the data-root scanners to trip over.
    assert not (base / ".deleting").exists()
    assert account_deletion.pending_deletions(base) == []


def test_a_persons_rows_inside_someone_elses_universe_are_left_alone(two_users: Path):
    """The rule that decides this is ``deletion_plan``: a universe-scoped table is
    touched by home only. Sweeping it by person would delete A's authored rows
    out of B's universe — the one thing the platform must never do."""
    base = two_users
    root_db = base / ".tinyassets.db"
    _exec(
        root_db,
        "INSERT INTO branches (branch_id, universe_id, name, status, created_by, "
        "created_at, updated_at) "
        "VALUES ('br-a-in-b', ?, 'a-branch', 'active', ?, 1.0, 1.0)",
        (HOME_B, A),
    )
    before = _rows(root_db, "SELECT COUNT(*) FROM branches WHERE universe_id = ?", (HOME_B,))

    delete_account(base, founder_sub=A, delete_identity=lambda s: "deleted")

    assert _rows(root_db, "SELECT 1 FROM branches WHERE branch_id = 'br-a-in-b'") == [(1,)]
    assert _rows(
        root_db, "SELECT COUNT(*) FROM branches WHERE universe_id = ?", (HOME_B,)
    ) == before


def test_access_a_held_on_another_universe_is_revoked(two_users: Path):
    """The deliberate exception: an access grant is the deleted person's access,
    so it goes wherever it points — without taking anything from the granting
    universe."""
    base = two_users
    root_db = base / ".tinyassets.db"
    grant_universe_access(base, universe_id=HOME_B, actor_id=A, permission="read")

    delete_account(base, founder_sub=A, delete_identity=lambda s: "deleted")

    assert _rows(root_db, "SELECT 1 FROM universe_acl WHERE actor_id = ?", (A,)) == []
    b_sql = "SELECT permission FROM universe_acl WHERE universe_id = ? AND actor_id = ?"
    assert _rows(root_db, b_sql, (HOME_B, B)) == [("admin",)]
    assert (base / HOME_B / "soul.md").is_file()


def test_a_second_founder_on_the_same_home_blocks_the_deletion(two_users: Path):
    """founder_home is keyed by principal, so two people CAN reference one
    universe. Destroying it would delete the other person's whole universe."""
    base = two_users
    shared = "user_01SSSSSSSSSSSSSSSSSSSSSSSS"
    set_founder_home(base, founder_sub=shared, universe_id=HOME_A, platform_generated=True)

    with pytest.raises(AccountDeletionBlocked) as exc:
        delete_account(base, founder_sub=A, delete_identity=lambda s: "deleted")

    assert "another founder" in str(exc.value)
    assert (base / HOME_A / "soul.md").is_file(), "a blocked deletion changes nothing"
    assert get_founder_home(base, A) == HOME_A


def test_another_persons_request_in_my_universe_blocks_the_deletion(two_users: Path):
    """Their request cascades into their admissions and tasks. The operator path
    refuses in this situation; so does this one."""
    base = two_users
    root_db = base / ".tinyassets.db"
    _exec(
        root_db,
        "INSERT INTO user_requests (request_id, universe_id, user_id, request_type, text, "
        "status, created_at, updated_at) "
        "VALUES ('req-b', ?, ?, 'ask', 'theirs', 'done', 1.0, 1.0)",
        (HOME_A, B),
    )

    with pytest.raises(AccountDeletionBlocked) as exc:
        delete_account(base, founder_sub=A, delete_identity=lambda s: "deleted")

    assert "another person's requests" in str(exc.value)
    assert _rows(root_db, "SELECT 1 FROM user_requests WHERE request_id = 'req-b'") == [(1,)]


def test_my_own_dependent_rows_are_deleted_and_counted_not_silently_cascaded(
    two_users: Path,
):
    """``request_admissions`` and ``branch_tasks_v2`` carry ON DELETE CASCADE
    onto ``user_requests``, so deleting the parent alone would take them
    invisibly and the receipt would understate what was destroyed."""
    base = two_users
    root_db = base / ".tinyassets.db"
    _exec(
        root_db,
        "INSERT INTO user_requests (request_id, universe_id, user_id, request_type, text, "
        "status, created_at, updated_at) "
        "VALUES ('req-a', ?, ?, 'ask', 'mine', 'done', 1.0, 1.0)",
        (HOME_A, A),
    )
    _exec(
        root_db,
        "INSERT INTO branch_tasks_v2 (branch_task_id, admission_id, request_id, universe_id, "
        "branch_def_id, trigger_source, priority_weight, status, queued_at) "
        "VALUES ('task-a', 'adm-a', 'req-a', ?, 'bd-x', 'user_request', 1.0, 'succeeded', "
        "'2026-01-01')",
        (HOME_A,),
    )
    _exec(
        root_db,
        "INSERT INTO request_admissions (admission_id, request_id, branch_task_id, tenant_id, "
        "actor_id, universe_id, idempotency_key_hash, body_digest, body_digest_version, "
        "trigger_source, accepted_priority_weight, priority_policy_version, state, "
        "result_json, created_at, updated_at) "
        "VALUES ('adm-a', 'req-a', 'task-a', 'tenant', ?, ?, 'hash', 'digest', 'v1', "
        "'user_request', 1.0, 'v1', 'committed', '{}', '2026-01-01', '2026-01-01')",
        (A, HOME_A),
    )

    receipt = delete_account(base, founder_sub=A, delete_identity=lambda s: "deleted")

    assert _rows(root_db, "SELECT 1 FROM request_admissions WHERE admission_id = 'adm-a'") == []
    assert _rows(root_db, "SELECT 1 FROM branch_tasks_v2 WHERE branch_task_id = 'task-a'") == []
    assert _rows(root_db, "SELECT 1 FROM user_requests WHERE request_id = 'req-a'") == []
    rows = receipt["rows_deleted"]
    assert rows.get("request_admissions") == 1, rows
    assert rows.get("branch_tasks_v2") == 1, rows
    assert rows.get("user_requests") == 1, rows


def test_audit_rows_survive_without_the_person_or_the_content(two_users: Path):
    """/legal promises 'audit records with the actor replaced by an opaque id and
    their content emptied'. This is the test that makes that sentence true."""
    base = two_users
    root_db = base / ".tinyassets.db"
    _exec(
        root_db,
        "INSERT INTO action_records (action_id, universe_id, visibility, actor_type, "
        "actor_id, action_type, target_type, target_id, summary, created_at, payload_json) "
        "VALUES ('act-a', ?, 'public', 'user', ?, 'write', 'page', 'my-secret-page', "
        "'A wrote something private', 1.0, '{\"secret\": 1}')",
        (HOME_A, A),
    )

    receipt = delete_account(base, founder_sub=A, delete_identity=lambda s: "deleted")

    row = _rows(
        root_db,
        "SELECT actor_id, summary, target_id, payload_json FROM action_records "
        "WHERE action_id = 'act-a'",
    )
    assert len(row) == 1, "the audit row itself survives"
    actor, summary, target, payload = row[0]
    assert actor.startswith("deleted:") and A not in actor
    assert summary == "" and target == "" and payload == "{}"
    assert receipt["rows_deleted"]["action_records (redacted)"] == 1


def test_a_grant_the_person_made_to_another_universe_goes_with_their_connection(
    two_users: Path,
):
    """`outbound_connection_grants.connection_id` references
    `outbound_connections` with no ON DELETE clause, and the satellite sweep runs
    with foreign keys ON. A grant the deleted person made to SOMEONE ELSE'S
    universe is not selected by the home predicate, so deleting the parent
    connection would raise and abandon the whole store. It is the person's own
    grant of their own credential, so it goes with them."""
    base = two_users
    outbound = base / "outbound.db"
    _exec(
        outbound,
        "INSERT INTO outbound_connection_grants (grant_id, connection_id, "
        "owner_user_id, universe_id, granted_at) VALUES ('grant-a-in-b', 'conn-a', ?, ?, 1.0)",
        (A, HOME_B),
    )

    receipt = delete_account(base, founder_sub=A, delete_identity=lambda s: "deleted")

    assert receipt["unfinished_phases"] == [], receipt["unfinished_phases"]
    assert _rows(outbound, "SELECT 1 FROM outbound_connections WHERE owner_user_id = ?", (A,)) == []
    assert _rows(outbound, "SELECT grant_id FROM outbound_connection_grants") == [("grant-b",)]
    # B's own connection and grant are untouched.
    assert _rows(outbound, "SELECT connection_id FROM outbound_connections") == [("conn-b",)]


def test_a_commons_pointer_survives_its_binder_being_deleted(two_users: Path):
    """`canonical_bindings` says which branch is canonical for a goal — a pointer
    other people rely on. `bound_by_actor_id` records who bound it, which is
    attribution, not ownership, so the row stays and the name goes. Found by
    auditing the full root schema, not by the small fixture."""
    base = two_users
    root_db = base / ".tinyassets.db"
    conn = sqlite3.connect(str(root_db))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(canonical_bindings)")}
    finally:
        conn.close()
    if not cols:
        pytest.skip("canonical_bindings is not created in this schema")
    values = {
        "goal_id": "goal-1", "branch_def_id": "bd-1", "bound_by_actor_id": A,
        "bound_at": 1.0, "scope_token": "", "branch_version_id": "bv-1",
        "visibility": "public",
    }
    used = [c for c in cols if c in values]
    _exec(
        root_db,
        f"INSERT INTO canonical_bindings ({','.join(used)}) "
        f"VALUES ({','.join('?' for _ in used)})",
        tuple(values[c] for c in used),
    )

    receipt = delete_account(base, founder_sub=A, delete_identity=lambda s: "deleted")

    rows = _rows(root_db, "SELECT bound_by_actor_id FROM canonical_bindings")
    assert len(rows) == 1, "the commons pointer must survive its binder"
    assert A not in rows[0][0] and rows[0][0].startswith("deleted:")
    assert receipt["rows_deleted"].get("canonical_bindings (redacted)") == 1


def test_a_principal_with_no_home_still_loses_grants_tokens_and_identity(two_users: Path):
    base = two_users
    stranger = "user_01CCCCCCCCCCCCCCCCCCCCCCCC"
    grant_universe_access(base, universe_id=HOME_B, actor_id=stranger, permission="read")
    _exec(base / ".auth.db", "INSERT INTO access_tokens VALUES ('at-c', ?)", (stranger,))
    seen: list[str] = []

    receipt = delete_account(
        base,
        founder_sub=stranger,
        delete_identity=lambda sub: seen.append(sub) or "deleted",
    )

    assert receipt["home_id"] == ""
    assert receipt["home_removed"] is True
    assert receipt["billing"] == "not_configured"
    assert seen == [stranger]
    root_db = base / ".tinyassets.db"
    assert _rows(root_db, "SELECT 1 FROM universe_acl WHERE actor_id = ?", (stranger,)) == []
    auth = base / ".auth.db"
    assert _rows(auth, "SELECT 1 FROM access_tokens WHERE user_id = ?", (stranger,)) == []
    b_sql = "SELECT permission FROM universe_acl WHERE universe_id = ? AND actor_id = ?"
    assert _rows(root_db, b_sql, (HOME_B, B)) == [("admin",)]
    assert (base / HOME_B / "soul.md").is_file()


def test_other_root_stores_lose_the_persons_data_too(two_users: Path):
    """Round 3 found `.authoring.db` and `.automations.db` untouched because the
    sweep named two files. Every store at the data root is visited now."""
    base = two_users
    authoring = base / ".authoring.db"
    conn = sqlite3.connect(str(authoring))
    with conn:
        conn.execute(
            "CREATE TABLE authoring_sessions (session_id TEXT PRIMARY KEY, "
            "owner_id TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO authoring_sessions VALUES ('sess-a', ?)", (A,))
        conn.execute("INSERT INTO authoring_sessions VALUES ('sess-b', ?)", (B,))
    conn.close()
    automations = base / ".automations.db"
    conn = sqlite3.connect(str(automations))
    with conn:
        conn.execute(
            "CREATE TABLE automations (automation_id TEXT PRIMARY KEY, "
            "universe_id TEXT NOT NULL, owner_principal_id TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO automations VALUES ('au-a', ?, ?)", (HOME_A, A))
        conn.execute("INSERT INTO automations VALUES ('au-b', ?, ?)", (HOME_B, B))
    conn.close()
    blobs = base / ".authoring_blobs"
    (blobs / "sess-a").mkdir(parents=True)
    (blobs / "sess-a" / "handle-1").write_text("A's upload", encoding="utf-8")
    (blobs / "sess-b").mkdir(parents=True)
    (blobs / "sess-b" / "handle-2").write_text("B's upload", encoding="utf-8")

    receipt = delete_account(base, founder_sub=A, delete_identity=lambda s: "deleted")

    assert _rows(authoring, "SELECT session_id FROM authoring_sessions") == [("sess-b",)]
    assert _rows(automations, "SELECT automation_id FROM automations") == [("au-b",)]
    assert not (blobs / "sess-a").exists(), "A's uploaded files must go with their rows"
    assert (blobs / "sess-b" / "handle-2").read_text(encoding="utf-8") == "B's upload"
    assert receipt["rows_deleted"]["authoring:authoring_sessions"] == 1
    assert receipt["rows_deleted"]["automations:automations"] == 1
    assert receipt["rows_deleted"]["authoring_blobs (directories)"] == 1


def test_a_foreign_terminal_row_in_my_universe_blocks_the_deletion(two_users: Path):
    """A stopped daemon B started inside A's universe is nobody's active work,
    so no liveness check catches it — but it is still B's row."""
    base = two_users
    root_db = base / ".tinyassets.db"
    _exec(
        root_db,
        "INSERT INTO author_runtime_instances (instance_id, universe_id, author_id, "
        "provider_name, model_name, status, created_by, created_at, updated_at, "
        "metadata_json) "
        "VALUES ('inst-b', ?, 'auth-1', 'claude', 'sonnet', 'stopped', ?, 1.0, 1.0, '{}')",
        (HOME_A, B),
    )
    conn = sqlite3.connect(str(root_db))
    has_owner = "created_by" in {
        r[1] for r in conn.execute("PRAGMA table_info(author_runtime_instances)")
    }
    conn.close()
    if not has_owner:
        pytest.skip("author_runtime_instances has no owner column in this schema")

    with pytest.raises(AccountDeletionBlocked) as exc:
        delete_account(base, founder_sub=A, delete_identity=lambda s: "deleted")

    assert "another person's rows" in str(exc.value)
    survived = _rows(
        root_db, "SELECT 1 FROM author_runtime_instances WHERE instance_id = 'inst-b'"
    )
    assert survived == [(1,)]
    assert (base / HOME_A / "soul.md").is_file()


def test_an_unreachable_payment_processor_is_unfinished_work(two_users: Path):
    """Nothing raises when Stripe is unreachable — `cancel_stripe_billing`
    returns "unavailable". Silence there is the harm: the subscription may still
    be charging, so it has to count as unfinished and leave a receipt."""
    base = two_users

    receipt = delete_account(
        base,
        founder_sub=A,
        cancel_billing=lambda home: "unavailable",
        delete_identity=lambda sub: "deleted",
    )

    assert receipt["billing"] == "unavailable"
    assert receipt["unfinished_phases"] == ["billing"]
    assert receipt["host_receipt_path"]
    pending = account_deletion.pending_deletions(base)
    assert len(pending) == 1 and pending[0]["billing"] == "unavailable"


def test_an_unconfigured_identity_provider_is_unfinished_work(two_users: Path):
    """`not_configured` means the WorkOS user still exists, while /account says
    the sign-in is removed. The host has to be told."""
    receipt = delete_account(
        two_users, founder_sub=A, delete_identity=lambda sub: "not_configured"
    )
    assert receipt["identity"] == "not_configured"
    assert receipt["unfinished_phases"] == ["identity"]
    assert account_deletion.pending_deletions(two_users)


def test_the_fence_is_written_before_anything_is_staged(two_users: Path, monkeypatch):
    """Round 3 reproduced the race: with the tombstone written last, a second
    device reached first-contact between staging and commit and rebuilt a home
    that outlived the deletion. The fence must already be up when staging runs."""
    from tinyassets.api.first_contact import principal_is_deleted

    base = two_users
    seen: list[bool] = []
    real_stage = account_deletion._stage_home

    def _watching_stage(root, home):
        seen.append(principal_is_deleted(root, A))
        return real_stage(root, home)

    monkeypatch.setattr(account_deletion, "_stage_home", _watching_stage)
    delete_account(base, founder_sub=A, delete_identity=lambda s: "deleted")

    assert seen == [True], "the tombstone must exist before the home is staged"


def test_the_tombstone_keeps_no_identifier(two_users: Path):
    """It outlives the account, so it must not retain the WorkOS user id of
    someone who asked to be deleted — /account says the sign-in is removed."""
    from tinyassets.api.first_contact import principal_is_deleted

    base = two_users
    delete_account(base, founder_sub=A, delete_identity=lambda s: "deleted")

    stored = _rows(base / ".tinyassets.db", "SELECT founder_sub FROM deleted_principals")
    assert len(stored) == 1
    assert A not in stored[0][0]
    assert stored[0][0] == account_deletion.principal_digest(A)
    assert principal_is_deleted(base, A) is True
    assert principal_is_deleted(base, B) is False


def test_a_cascade_into_an_unvisited_table_is_still_counted(two_users: Path):
    """Deleting `user_accounts` cascades into `user_sessions` before the sorted
    plan reaches it. Counting as we go reported only the parent."""
    base = two_users
    root_db = base / ".tinyassets.db"
    _exec(
        root_db,
        "INSERT INTO user_accounts (user_id, username, display_name, is_active, "
        "created_at, updated_at, metadata_json) "
        "VALUES (?, 'a', 'A', 1, 1.0, 1.0, '{}')",
        (A,),
    )
    _exec(
        root_db,
        "INSERT INTO user_sessions (session_token, user_id, created_at, last_seen, "
        "expires_at, metadata_json) "
        "VALUES ('sess-a', ?, 1.0, 1.0, 9999999999.0, '{}')",
        (A,),
    )

    receipt = delete_account(base, founder_sub=A, delete_identity=lambda s: "deleted")

    assert _rows(root_db, "SELECT 1 FROM user_sessions WHERE user_id = ?", (A,)) == []
    assert receipt["rows_deleted"].get("user_accounts") == 1
    assert receipt["rows_deleted"].get("user_sessions") == 1, receipt["rows_deleted"]


# --------------------------------------------------------------------------- #
# the schema-derived rule itself
# --------------------------------------------------------------------------- #


def test_every_person_keyed_table_is_covered_or_deliberately_kept(two_users: Path):
    """The guard against a migration adding a table full of someone's data that
    deletion never touches: every live root table carrying a person or universe
    column must be in the plan, preserved on purpose, redacted, or blocking."""
    from tinyassets.account_deletion import (
        BLOCKING_TABLES,
        INDIRECTLY_SCOPED_TABLES,
        PRESERVED_TABLES,
        PRINCIPAL_KEYS,
        REDACTED_TABLES,
        deletion_plan,
    )

    conn = sqlite3.connect(str(two_users / ".tinyassets.db"))
    try:
        plan = deletion_plan(conn, principal=A, home=HOME_A)
        tables = [
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not str(r[0]).startswith("sqlite_")
        ]
        uncovered = []
        for table in tables:
            cols = {str(r[1]) for r in conn.execute(f'PRAGMA table_info("{table}")')}
            carries_identity = bool(cols & set(PRINCIPAL_KEYS)) or "universe_id" in cols
            handled = (
                table in plan
                or table in PRESERVED_TABLES
                or table in REDACTED_TABLES
                or table in BLOCKING_TABLES
                or table in INDIRECTLY_SCOPED_TABLES
            )
            if carries_identity and not handled:
                uncovered.append(table)
    finally:
        conn.close()
    assert uncovered == [], (
        "these tables hold a person's or a universe's rows and account deletion "
        f"has no policy for them: {uncovered}"
    )


def test_created_by_is_not_a_deletion_key():
    """Authorship is not ownership. Sweeping ``created_by`` would delete the rows
    this person authored inside other people's universes."""
    assert "created_by" not in account_deletion.PRINCIPAL_KEYS


# --------------------------------------------------------------------------- #
# refusals and failures
# --------------------------------------------------------------------------- #


def test_anonymous_and_empty_principals_are_refused(two_users: Path):
    for bad in ("", "  ", "anonymous"):
        with pytest.raises(AccountDeletionError):
            delete_account(two_users, founder_sub=bad, delete_identity=lambda s: "deleted")
    assert (two_users / HOME_A).is_dir() and (two_users / HOME_B).is_dir()


def test_an_escaping_home_binding_is_refused_before_anything_changes(two_users: Path):
    base = two_users
    evil = "user_01EEEEEEEEEEEEEEEEEEEEEEEE"
    set_founder_home(base, founder_sub=evil, universe_id="../outside", platform_generated=True)
    (base.parent / "outside").mkdir()
    (base.parent / "outside" / "keep.txt").write_text("x", encoding="utf-8")

    with pytest.raises(AccountDeletionError):
        delete_account(base, founder_sub=evil, delete_identity=lambda s: "deleted")

    assert (base.parent / "outside" / "keep.txt").is_file()
    assert get_founder_home(base, evil) == "../outside", "refusal must change nothing"


def test_billing_and_identity_failures_are_reported_not_hidden(two_users: Path, caplog):
    base = two_users

    def _boom(_: str) -> str:
        raise RuntimeError("stripe down sk_live_SHOULD_NOT_APPEAR")

    with caplog.at_level("ERROR"):
        receipt = delete_account(
            base, founder_sub=A, cancel_billing=_boom, delete_identity=_boom
        )

    assert receipt["billing"] == "error"
    assert receipt["identity"] == "error"
    assert receipt["unfinished_phases"] == ["billing", "identity"]
    assert receipt["home_removed"] is True, "the data still goes; the failure is reported"
    assert not (base / HOME_A).exists()
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 2
    assert "sk_live_SHOULD_NOT_APPEAR" not in " ".join(errors)
    # A durable, content-free receipt tells the host exactly what to finish.
    pending = account_deletion.pending_deletions(base)
    assert len(pending) == 1
    assert pending[0]["unfinished_phases"] == ["billing", "identity"]
    assert A not in json.dumps(pending[0])


def test_a_failed_row_phase_still_cancels_the_billing(two_users: Path, monkeypatch, caplog):
    """The phase that must never be skipped is the one that stops the money.
    A broken satellite store must not leave a live subscription behind."""
    base = two_users
    billed: list[str] = []

    def _broken(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(account_deletion, "_delete_satellite_rows", _broken)
    with caplog.at_level("ERROR"):
        receipt = delete_account(
            base,
            founder_sub=A,
            cancel_billing=lambda home: billed.append(home) or "cancelled",
            delete_identity=lambda s: "deleted",
        )

    assert billed == [HOME_A], "billing ran even though an earlier phase failed"
    assert receipt["identity"] == "deleted"
    assert receipt["unfinished_phases"] == ["store:auth", "store:outbound"]
    assert receipt["host_receipt_path"]


def test_nonempty_in_home_sidecar_does_not_strand_deletion_or_billing(two_users):
    from tinyassets.storage.effector_consents import consents_db_path, initialize_consents_db

    home = two_users / HOME_A
    planted = home / ".universe-sidecars"
    planted.mkdir()
    (planted / "owned.txt").write_text("user content", encoding="utf-8")
    initialize_consents_db(home)
    billed = []
    receipt = delete_account(
        two_users, founder_sub=A,
        cancel_billing=lambda h: billed.append(h) or "cancelled",
        delete_identity=lambda _: "deleted",
    )
    assert billed == [HOME_A]
    assert receipt["home_removed"] and not receipt["unfinished_phases"]
    assert not home.exists() and not consents_db_path(home).parent.exists()
    assert not (two_users / ".deleting").exists()
    assert (two_users / HOME_B / "soul.md").exists()


def test_partial_staging_is_receipted_billing_runs_and_retry_resumes(two_users, monkeypatch):
    from tinyassets.storage.effector_consents import consents_db_path, initialize_consents_db

    home = two_users / HOME_A
    initialize_consents_db(home)
    sidecar = consents_db_path(home).parent
    rename = Path.rename
    billed = []

    def fail_sidecar(path, target):
        if path == sidecar:
            raise OSError("injected sidecar staging failure")
        return rename(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", fail_sidecar)
        receipt = delete_account(
            two_users, founder_sub=A,
            cancel_billing=lambda h: billed.append(h) or "cancelled",
            delete_identity=lambda _: "deleted",
        )
    assert billed == [HOME_A]
    assert not receipt["home_removed"]
    assert "home_staging" in receipt["unfinished_phases"]
    assert Path(receipt["home_staged_path"]).is_dir()
    assert account_deletion.pending_deletions(two_users)
    retry = delete_account(
        two_users, founder_sub=A, cancel_billing=lambda _: "none",
        delete_identity=lambda _: "deleted",
    )
    assert retry["home_removed"] and not retry["unfinished_phases"]
    assert not sidecar.exists() and not (two_users / ".deleting").exists()
    assert (two_users / HOME_B / "soul.md").exists()


@pytest.mark.parametrize("linked_parent", [".deleting", ".universe-sidecars"])
def test_staging_rejects_linked_platform_parents_without_touching_peer(
    two_users, linked_parent,
):
    peer = two_users / HOME_B
    try:
        (two_users / linked_parent).symlink_to(peer, target_is_directory=True)
    except OSError:
        pytest.skip("this host cannot create a directory symlink")
    billed = []
    receipt = delete_account(
        two_users, founder_sub=A,
        cancel_billing=lambda h: billed.append(h) or "cancelled",
        delete_identity=lambda _: "deleted",
    )
    assert billed == [HOME_A]
    assert "home_staging" in receipt["unfinished_phases"]
    assert not receipt["home_removed"]
    assert (two_users / HOME_A / "soul.md").exists()
    assert (peer / "soul.md").read_text(encoding="utf-8") == "# soul\n"
    assert not (peer / HOME_A).exists()


def test_a_second_deletion_of_the_same_principal_is_a_clean_noop(two_users: Path):
    delete_account(two_users, founder_sub=A, delete_identity=lambda s: "deleted")
    receipt = delete_account(two_users, founder_sub=A, delete_identity=lambda s: "deleted")
    assert receipt["home_id"] == ""
    assert receipt["rows_deleted"] == {}
    assert (two_users / HOME_B / "soul.md").is_file()


def test_a_deleted_principal_cannot_be_handed_a_fresh_universe(two_users: Path):
    """A second device's token stays valid here until it expires. Without the
    tombstone, first-contact would re-found the account seconds after deletion."""
    from tinyassets.api.first_contact import ensure_founder_home, principal_is_deleted

    base = two_users
    assert principal_is_deleted(base, A) is False
    delete_account(base, founder_sub=A, delete_identity=lambda s: "deleted")

    assert principal_is_deleted(base, A) is True
    assert ensure_founder_home(base, A) == "", "a deleted account must not be re-founded"
    assert get_founder_home(base, A) == ""
    assert principal_is_deleted(base, B) is False


def test_the_operator_reset_still_clears_the_tombstone():
    """A scoped identity reset KEEPS the login, so it must clear the tombstone or
    the reset test identity could never birth a home again."""
    from tinyassets.scoped_reset import MAIN_DB_TABLE_CLASSIFICATIONS

    assert MAIN_DB_TABLE_CLASSIFICATIONS["deleted_principals"] == "reset_binding"


# --------------------------------------------------------------------------- #
# WorkOS identity deletion
# --------------------------------------------------------------------------- #


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_workos_deletion_is_not_configured_without_a_key(monkeypatch):
    monkeypatch.delenv("WORKOS_API_KEY", raising=False)
    assert account_deletion.delete_workos_user(A) == "not_configured"


def test_workos_deletion_skips_non_workos_principals(monkeypatch):
    monkeypatch.setenv("WORKOS_API_KEY", "sk_test_x")
    calls: list[str] = []
    assert account_deletion.delete_workos_user(
        "host:jonathan",
        request=lambda req, timeout: calls.append(req.full_url) or _Resp(200),
    ) == "not_applicable"
    assert calls == []


def test_workos_deletion_sends_a_delete_and_treats_gone_as_done(monkeypatch):
    monkeypatch.setenv("WORKOS_API_KEY", "sk_test_x")
    seen: list[tuple[str, str]] = []

    def _ok(req, timeout):
        seen.append((req.get_method(), req.full_url))
        assert req.get_header("Authorization") == "Bearer sk_test_x"
        return _Resp(200)

    assert account_deletion.delete_workos_user(A, request=_ok) == "deleted"
    assert seen == [("DELETE", f"https://api.workos.com/user_management/users/{A}")]

    def _gone(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, None)

    assert account_deletion.delete_workos_user(A, request=_gone) == "deleted"


def test_workos_deletion_failures_raise_without_the_key(monkeypatch):
    monkeypatch.setenv("WORKOS_API_KEY", "sk_test_SECRET")

    def _fail(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "boom sk_test_SECRET", {}, None)

    with pytest.raises(AccountDeletionError) as exc:
        account_deletion.delete_workos_user(A, request=_fail)
    assert "500" in str(exc.value) and "SECRET" not in str(exc.value)

    def _timeout(req, timeout):
        raise TimeoutError()

    with pytest.raises(AccountDeletionError):
        account_deletion.delete_workos_user(A, request=_timeout)


# --------------------------------------------------------------------------- #
# the app route
# --------------------------------------------------------------------------- #


class _Request:
    def __init__(self, body: dict | None, *, origin: str = "https://tinyassets.io",
                 ctype: str = "application/json") -> None:
        self._raw = json.dumps(body).encode("utf-8") if body is not None else b"{"
        self.headers = {
            "content-type": ctype,
            "origin": origin,
            "host": "tinyassets.io",
            "content-length": str(len(self._raw)),
        }

    async def stream(self):
        yield self._raw


@pytest.fixture
def app_route(monkeypatch, tmp_path):
    from tinyassets import onboarding

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(
        onboarding, "app_config",
        lambda: {"configured": True, "resource": "https://tinyassets.io/mcp"},
    )
    monkeypatch.setattr("tinyassets.api.helpers._base_path", lambda: tmp_path)
    calls: dict[str, list] = {"deleted": [], "dropped": []}
    monkeypatch.setattr(
        "tinyassets.account_deletion.delete_account",
        lambda base, *, founder_sub: calls["deleted"].append((Path(base), founder_sub)) or {
            "home_removed": True, "billing": "none", "identity": "deleted",
            "unfinished_phases": [],
        },
    )
    monkeypatch.setattr(
        onboarding, "_drop_refresh_session", lambda h: calls["dropped"].append(h)
    )
    return onboarding, calls


def _as(identity_user: str | None):
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    ident = None if identity_user is None else Identity(user_id=identity_user, username="a@x")
    return identity_context(ident)


def test_route_requires_a_signed_in_user(app_route):
    onboarding, calls = app_route
    with _as(None):
        resp = asyncio.run(onboarding._handle_account_delete(_Request({"confirm": "DELETE"})))
    assert resp.status_code == 401
    assert calls["deleted"] == []


def test_route_refuses_cross_origin_and_unconfirmed_posts(app_route):
    onboarding, calls = app_route
    with _as(A):
        cross = asyncio.run(onboarding._handle_account_delete(
            _Request({"confirm": "DELETE"}, origin="https://evil.example")))
        form = asyncio.run(onboarding._handle_account_delete(
            _Request({"confirm": "DELETE"}, ctype="application/x-www-form-urlencoded")))
        unconfirmed = asyncio.run(onboarding._handle_account_delete(_Request({"confirm": "yes"})))
        malformed = asyncio.run(onboarding._handle_account_delete(_Request(None)))
    assert cross.status_code == 403 and form.status_code == 403
    assert unconfirmed.status_code == 400
    assert json.loads(unconfirmed.body) == {"error": "confirmation_required"}
    assert malformed.status_code == 400
    assert calls["deleted"] == []


def test_route_deletes_the_signed_in_principal_and_ends_the_session(app_route, tmp_path):
    onboarding, calls = app_route
    handle = "a" * 43  # a well-formed session handle
    with _as(A):
        resp = asyncio.run(onboarding._handle_account_delete(
            _Request({"confirm": "DELETE", "session_ref": handle})))
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["deleted"] is True and body["identity"] == "deleted"
    assert body["unfinished"] == []
    assert calls["deleted"] == [(tmp_path, A)]
    assert calls["dropped"] == [handle]
    set_cookie = resp.headers.get("set-cookie", "")
    assert "ta_rt=" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()
    assert resp.headers.get("cache-control") == "no-store"


def test_route_reports_a_block_with_its_reasons(app_route, monkeypatch):
    onboarding, calls = app_route

    def _blocked(base, *, founder_sub):
        raise AccountDeletionBlocked(
            "another founder is bound to this universe; a vote is still open"
        )

    monkeypatch.setattr("tinyassets.account_deletion.delete_account", _blocked)
    with _as(A):
        resp = asyncio.run(onboarding._handle_account_delete(_Request({"confirm": "DELETE"})))
    assert resp.status_code == 409
    body = json.loads(resp.body)
    assert body["error"] == "deletion_blocked"
    assert body["reasons"] == [
        "another founder is bound to this universe", "a vote is still open",
    ]
    assert calls["dropped"] == [], "a blocked deletion must not sign the user out"


def test_route_reports_a_refusal_without_pretending(app_route, monkeypatch):
    onboarding, calls = app_route

    def _refuse(base, *, founder_sub):
        raise AccountDeletionError("home path escapes the data root")

    monkeypatch.setattr("tinyassets.account_deletion.delete_account", _refuse)
    with _as(A):
        resp = asyncio.run(onboarding._handle_account_delete(_Request({"confirm": "DELETE"})))
    assert resp.status_code == 409
    assert json.loads(resp.body)["error"] == "deletion_refused"
    assert calls["dropped"] == []


# --------------------------------------------------------------------------- #
# the shipped page
# --------------------------------------------------------------------------- #


def _app_html() -> str:
    from tinyassets import onboarding

    return (Path(onboarding.__file__).parent / "app.html").read_text(encoding="utf-8")


def test_the_app_page_carries_the_deletion_path():
    html = _app_html()
    # Every signed-in user, powered or not, lands in the chat (vendor-neutral
    # slice 6 removed the full-page connect screen and its own Account
    # button), so the chat header's Account button is the one entry point.
    chat = html[html.index('id="view-chat"'):html.index("</header>", html.index('id="view-chat"'))]
    assert 'id="btn-account"' in chat
    assert '$("btn-account").addEventListener("click", showAccount);' in html
    assert 'id="btn-delete-account"' in html
    assert '"/app/account/delete"' in html
    assert 'confirm:"DELETE"' in html
    assert "cannot be undone" in html


def test_the_android_shell_cannot_reach_checkout():
    """Play's payments policy is about capability, not layout: hiding the button
    is not enough while the code path still POSTs to the checkout endpoint."""
    html = _app_html()
    start = html.index("async function startSubscribe()")
    guard = html.index("if(NATIVE){", start)
    checkout = html.index('billingFetch("/app/billing/checkout"', start)
    assert guard < checkout, "the native guard must precede any checkout call"
    assert "if(!b || !PLAN || NATIVE) return;" in html, "and the button stays hidden"
