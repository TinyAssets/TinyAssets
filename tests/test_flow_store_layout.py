"""A pending-flow table left by an older release is rebuilt, not shifted into.

2026-10-22: hosted_model_flows still had an older layout (an extra
bound_home_id column) after the owner-box cutover. Positional inserts shifted
every column, the expiry sweep then deleted each new flow, and every OpenRouter
sign-in ended in unknown_model_connection.
"""

import sqlite3

import pytest

from tinyassets.connection_oauth import pkce

pytestmark = pytest.mark.verifies('owner-daemon-in-box:C3')

OLD_LAYOUT = (
    "CREATE TABLE hosted_model_flows (handle_digest TEXT PRIMARY KEY, "
    "owner_user_id TEXT NOT NULL, bound_home_id TEXT NOT NULL, preset_id TEXT NOT NULL, "
    "preset_digest TEXT NOT NULL, challenge TEXT NOT NULL, callback_origin TEXT NOT NULL, "
    "created_at REAL NOT NULL, expires_at REAL NOT NULL)"
)


def _columns(path, table):
    with sqlite3.connect(path) as conn:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def test_older_flow_layout_is_rebuilt_and_new_flows_survive_the_sweep(tmp_path):
    path = tmp_path / pkce._DB_NAME
    with sqlite3.connect(path) as conn:
        conn.execute(OLD_LAYOUT)
    with pkce.flows_db(tmp_path) as (conn, now):
        conn.execute("INSERT INTO hosted_model_flows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("d", "owner", "preset", "digest", "challenge", "https://tinyassets.io",
                      now, now + 60, ""))
    assert _columns(path, "hosted_model_flows") == pkce._FLOW_COLUMNS["hosted_model_flows"]
    with pkce.flows_db(tmp_path) as (conn, _now):
        row = conn.execute("SELECT owner_user_id, preset_id FROM hosted_model_flows").fetchone()
    assert tuple(row) == ("owner", "preset")


def test_current_layout_keeps_its_pending_flows(tmp_path):
    with pkce.flows_db(tmp_path) as (conn, now):
        conn.execute("INSERT INTO hosted_model_flows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("d", "owner", "preset", "digest", "challenge", "https://tinyassets.io",
                      now, now + 60, ""))
    with pkce.flows_db(tmp_path) as (conn, _now):
        assert conn.execute("SELECT COUNT(*) FROM hosted_model_flows").fetchone()[0] == 1
