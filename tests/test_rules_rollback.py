"""Previous-image readers and writers remain usable after the sheet migration."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

from tinyassets import agent_rules, agent_sessions

OLD_UPSERT = (
    "INSERT INTO rules (agent, action_class, connection, operation, behaviour, note, "
    "seeded, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?) "
    "ON CONFLICT(agent, action_class, connection, operation) DO UPDATE SET "
    "behaviour = excluded.behaviour, note = excluded.note, seeded = 0, "
    "updated_at = excluded.updated_at"
)
OLD_COLUMNS = "id,agent,action_class,connection,operation,behaviour,note,seeded,updated_at"


def old_database(tmp_path):
    home = tmp_path / "data" / "home"
    home.mkdir(parents=True)
    path = agent_sessions._records_dir(home) / "rules.db"
    rows = [
        (4, "main", "app.write", "", "", "ask_first", "owner's note", 0, 123.25),
        (19, "main", "money.move", "", "", "hand_off", "seed note", 1, 456.5),
    ]
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(agent_rules._SCHEMA)
        conn.executemany(f"INSERT INTO rules ({OLD_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?)", rows)
        conn.execute("UPDATE sqlite_sequence SET seq=91 WHERE name='rules'")
        conn.commit()
    return home, path, rows


def test_old_format_migration_preserves_values_and_deleted_id_high_water(tmp_path):
    home, _, rows = old_database(tmp_path)
    with closing(agent_rules._connect(home)) as conn:
        assert conn.execute(f"SELECT {OLD_COLUMNS} FROM rules ORDER BY id").fetchall() == rows
        conn.execute(OLD_UPSERT, ("main", "people.message", "", "", "hand_off", "new", 789.5))
        assert conn.execute(
            "SELECT id FROM rules WHERE action_class='people.message'"
        ).fetchone() == (92,)
        conn.execute(OLD_UPSERT, ("main", "app.write", "", "", "hand_off", "tightened", 800.5))
        assert conn.execute(
            "SELECT id,behaviour,note,seeded,updated_at FROM rules WHERE id=4"
        ).fetchone() == (4, "hand_off", "tightened", 0, 800.5)
    assert agent_rules.decide(home, "app.write").behaviour == "hand_off"


@pytest.mark.parametrize(
    "crash_after",
    [
        "ADD COLUMN record_kind",
        "ADD COLUMN grant_json",
        "INSERT INTO approval_grants",
        "CREATE UNIQUE INDEX",
    ],
)
def test_interrupted_migration_leaves_old_table_and_upsert_usable(
    tmp_path, monkeypatch, crash_after
):
    home, path, rows = old_database(tmp_path)
    original = sqlite3.connect

    class Crash(BaseException):
        pass

    class InterruptedConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            result = super().execute(sql, *args)
            if crash_after in sql:
                raise Crash()
            return result

    monkeypatch.setattr(
        agent_rules.sqlite3,
        "connect",
        lambda *a, **kw: original(*a, **kw, factory=InterruptedConnection),
    )
    with pytest.raises(Crash):
        agent_rules._connect(home)
    with closing(original(path)) as conn:
        assert conn.execute(f"SELECT {OLD_COLUMNS} FROM rules ORDER BY id").fetchall() == rows
        assert "record_kind" not in {r[1] for r in conn.execute("PRAGMA table_info(rules)")}
        assert conn.execute("SELECT seq FROM sqlite_sequence WHERE name='rules'").fetchone() == (
            91,
        )
        conn.execute(OLD_UPSERT, ("main", "app.write", "", "", "hand_off", "after crash", 900))
        conn.commit()
        assert conn.execute("SELECT behaviour FROM rules WHERE id=4").fetchone() == ("hand_off",)
    monkeypatch.setattr(agent_rules.sqlite3, "connect", original)
    assert agent_rules.decide(home, "app.write").behaviour == "hand_off"


def test_initial_sheet_migration_preserves_overlapping_grants_and_old_edits(tmp_path):
    home, path, _ = old_database(tmp_path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DROP TABLE rules")
        conn.execute(
            agent_rules._SCHEMA.replace(
                "UNIQUE(agent, action_class, connection, operation)",
                "record_kind TEXT NOT NULL DEFAULT 'behavior', decision_id TEXT UNIQUE, "
                "grant_json TEXT NOT NULL DEFAULT '{}'",
            )
        )
        conn.execute(
            "CREATE UNIQUE INDEX behavior_rule_key ON rules "
            "(agent,action_class,connection,operation) WHERE record_kind='behavior'"
        )
        for i in range(1, 4):
            conn.execute(
                "INSERT INTO rules (agent,action_class,connection,operation,behaviour,"
                "updated_at,record_kind,decision_id,grant_json) "
                "VALUES ('main','app.write','service','POST','do_if_preapproved',1,"
                "'preapproval',?,?)",
                (str(i), json.dumps({"revoked": i == 3})),
            )
        conn.commit()
    grants = agent_rules.list_rules(home)
    assert len(grants) == 2
    assert all(r.id > 3 and r.behaviour == "hand_off" for r in grants)
    assert agent_rules.delete_rule(home, grants[0].id)
    assert len(agent_rules.list_rules(home)) == 1
    with closing(agent_rules._connect(home)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM approval_grants").fetchone() == (3,)
        assert conn.execute("SELECT COUNT(*) FROM rules").fetchone() == (1,)
        conn.execute(OLD_UPSERT, ("main", "app.write", "service", "POST", "hand_off", "owner", 2))
    result = agent_rules.decide(home, "app.write", connection="service", operation="POST")
    assert result.behaviour == "hand_off"
    assert result.rule_id > 0
    with closing(agent_rules._connect(home)) as conn:
        assert all(json.loads(row[0])["revoked"] for row in conn.execute(
            "SELECT grant_json FROM approval_grants"))


def test_process_death_during_migration_recovers_old_database(tmp_path):
    home, path, rows = old_database(tmp_path)
    # Exit without unwinding: SQLite must recover its journal on the next open.
    script = """
import os
import sqlite3
import sys
from pathlib import Path
from tinyassets import agent_rules
original = sqlite3.connect
class Dies(sqlite3.Connection):
    def execute(self, sql, *args):
        result = super().execute(sql, *args)
        if 'INSERT INTO approval_grants' in sql:
            os._exit(73)
        return result
agent_rules.sqlite3.connect = lambda *a, **kw: original(*a, **kw, factory=Dies)
agent_rules._connect(Path(sys.argv[1]))
"""
    result = subprocess.run([sys.executable, "-c", script, str(home)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 73, result.stderr
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute(f"SELECT {OLD_COLUMNS} FROM rules ORDER BY id").fetchall() == rows
        assert "record_kind" not in {r[1] for r in conn.execute("PRAGMA table_info(rules)")}
        conn.execute(OLD_UPSERT, ("main", "app.write", "", "", "hand_off", "recovered", 900))
        conn.commit()
    assert agent_rules.decide(home, "app.write").behaviour == "hand_off"
