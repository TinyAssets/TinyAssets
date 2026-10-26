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
from pathlib import Path

import pytest

from tinyassets.daemon_server import (
    ensure_universe_registered,
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
                "owner_user_id, granted_at) VALUES (?, ?, ?, 1.0)",
                (f"grant-{tag}", f"conn-{tag}", sub),
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
def two_users(tmp_path: Path, monkeypatch) -> Path:
    base = tmp_path / "data"
    base.mkdir()
    # Production deletes on the one data root; the owner box binds there.
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    _seed_user(base, A, HOME_A)
    _seed_user(base, B, HOME_B)
    _seed_outbound(base)
    _seed_auth(base)
    return base


# --------------------------------------------------------------------------- #
# the cross-user floor
# --------------------------------------------------------------------------- #












































# --------------------------------------------------------------------------- #
# the schema-derived rule itself
# --------------------------------------------------------------------------- #






# --------------------------------------------------------------------------- #
# refusals and failures
# --------------------------------------------------------------------------- #




















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
    def enqueue(root, operation, **arguments):
        assert operation == "enqueue"
        calls["deleted"].append((root, arguments["owner"]))
        return {"status": "deletion_in_progress", "status_url": "/app/account/deletion/job/token",
                "completed_steps": ["withdrawal"], "pending_steps": ["volume"]}

    monkeypatch.setattr("tinyassets.broker.deletion_jobs.request", enqueue)
    monkeypatch.setattr("tinyassets.storage.data_dir", lambda: tmp_path)
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


def test_route_enqueues_only_the_signed_in_principal_and_returns_durable_status(
    app_route, tmp_path,
):
    onboarding, calls = app_route
    with _as(A):
        resp = asyncio.run(onboarding._handle_account_delete(
            _Request({"confirm": "DELETE", "owner": B})))
    assert resp.status_code == 202
    body = json.loads(resp.body)
    assert body["status"] == "deletion_in_progress"
    assert body["completed_steps"] == ["withdrawal"]
    assert resp.headers["location"] == body["status_url"]
    assert calls["deleted"] == [(tmp_path, A)]
    assert calls["dropped"] == []  # credential revocation is job step 6
    assert resp.headers.get("cache-control") == "no-store"


def test_route_reports_pending_withdrawal_without_acknowledging_it(app_route, monkeypatch):
    from tinyassets.broker.deletion_jobs import WithdrawalPending

    onboarding, calls = app_route
    def pending(*args, **kwargs):
        raise WithdrawalPending({"status_url": "/app/account/deletion/job/token",
                                 "completed_steps": [], "failed_step": "withdrawal"})
    monkeypatch.setattr("tinyassets.broker.deletion_jobs.request", pending)
    with _as(A):
        resp = asyncio.run(onboarding._handle_account_delete(_Request({"confirm": "DELETE"})))
    assert resp.status_code == 503
    body = json.loads(resp.body)
    assert body["error"] == "deletion_pending" and body["completed_steps"] == []
    assert resp.headers["location"] == body["status_url"]
    assert calls["dropped"] == []


def test_route_reports_a_refusal_without_pretending(app_route, monkeypatch):
    onboarding, calls = app_route
    def refused(*args, **kwargs):
        raise RuntimeError("off-host record unavailable")
    monkeypatch.setattr("tinyassets.broker.deletion_jobs.request", refused)
    with _as(A):
        resp = asyncio.run(onboarding._handle_account_delete(_Request({"confirm": "DELETE"})))
    assert resp.status_code == 503
    assert json.loads(resp.body) == {"error": "deletion_pending"}
    assert calls["dropped"] == []


# --------------------------------------------------------------------------- #
# the shipped page
# --------------------------------------------------------------------------- #


def _app_html() -> str:
    from tinyassets import onboarding
    from tinyassets.onboarding.app_modules import shell_source

    return shell_source(Path(onboarding.__file__).parent / "app.html")








pytestmark = pytest.mark.verifies('owner-daemon-in-box:C6')
