"""Publisher erasure preserves hash-valid public history and recipient state."""

import gc
import json
import sqlite3

import pytest

from tests.test_command_center_packages import _as
from tests.test_command_center_release_policy import enable
from tests.test_command_center_update_executor import apply, release
from tests.test_command_center_update_executor import published as published
from tinyassets import account_deletion
from tinyassets import command_center_packages as packages
from tinyassets import command_center_release_series as releases
from tinyassets.command_center_updates import digest
from tinyassets.custom_agents import _agent_connect


@pytest.mark.parametrize("home_state", ["original", "changed", "none"])
@pytest.mark.parametrize("legacy_pins", [False, True], ids=["new-pins", "legacy-pins"])
def test_publisher_deletion_erases_evidence_preserves_recipient_and_release_hashes(
    published, home_state, legacy_pins,
):
    data = published
    enable(data)
    target = release(data, style="main { color: teal; }")
    assert apply(data)["applied"]
    package_db = packages.store_dir(data["base"]) / "packages.db"
    # A peer's private pin must survive even if it names the publisher's old home.
    packages.pin(data["base"], universe_id=data["publisher_home"], kind="publish",
                 agent="main", digest="peer-digest",
                 record={"action": {"release_link": {"author_id": data["owner"],
                                                    "identity_hashes": {"ui": "peer-private"}}}})
    with sqlite3.connect(package_db) as conn:
        pin_rows = conn.execute("SELECT request_id, record_json FROM pins").fetchall()
        publisher_requests = {
            request for request, record in pin_rows
            if json.loads(record).get("action", {}).get("release_link", {}).get("author_id")
            == data["publisher"]
        }
        assert len(publisher_requests) == 2
        peer_pins = [row for row in pin_rows if row[0] not in publisher_requests]
        assert len(peer_pins) >= 2
        columns = {row[1] for row in conn.execute("PRAGMA table_info(pins)")}
        if legacy_pins and "owner_id" in columns:
            conn.execute("ALTER TABLE pins DROP COLUMN owner_id")
    recipient_tables = (
        "command_center_adoptions", "command_center_update_policies",
        "command_center_auto_receipts", "command_center_auto_status",
    )
    with _agent_connect(data["base"]) as conn:
        if home_state == "changed":
            conn.execute("UPDATE founder_home SET universe_id=? WHERE founder_sub=?",
                         ("changed-publisher-home", data["publisher"]))
        elif home_state == "none":
            conn.execute("DELETE FROM founder_home WHERE founder_sub=?", (data["publisher"],))
        evidence = conn.execute("SELECT * FROM command_center_release_evidence").fetchall()
        assert len(evidence) == 2
        private_values = {data["publisher_home"]}
        for row in evidence:
            private_values.add(row["request_id"])
            private_values.update(json.loads(row["identity_hashes_json"]).values())
        plan = account_deletion.deletion_plan(conn, principal=data["publisher"], home="")
        for table in ("command_center_release_series", "command_center_release_evidence"):
            assert plan[table] == [("owner_id", "principal")]
        before = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
                  for table in (*recipient_tables, "command_center_releases")}
        assert before["command_center_adoptions"] and before["command_center_auto_receipts"]

    # Publication fixtures leave collectible SQLite connections; release their
    # file handles before Windows stages the publisher's home directory.
    gc.collect()
    receipt = account_deletion.delete_account(
        data["base"], founder_sub=data["publisher"],
        cancel_billing=lambda home: "cancelled", delete_identity=lambda sub: "deleted",
    )
    assert receipt["unfinished_phases"] == []
    assert receipt["rows_deleted"]["command_center_release_evidence"] == 2
    assert receipt["rows_deleted"]["command_center_release_series"] == 1
    # Read raw SQLite: cleanup must migrate even without reopening the package API.
    with sqlite3.connect(package_db) as conn:
        remaining = conn.execute("SELECT request_id, record_json FROM pins").fetchall()
        assert remaining == peer_pins
        assert all(value not in json.dumps(remaining) for value in private_values
                   if value != data["publisher_home"])
    assert receipt["rows_deleted"]["packages:pins"] == 2
    with _agent_connect(data["base"]) as conn:
        for table in ("command_center_release_series", "command_center_release_evidence"):
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == []
        for table, rows in before.items():
            assert [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] == rows
        chain = releases._chain(conn, target["series_id"])
        assert [row["release_id"] for row in chain] == [
            data["first"]["release_id"], target["release_id"],
        ]
        for record in chain:
            public_fields = {k: v for k, v in record.items() if k != "release_id"}
            assert digest(public_fields) == record["release_id"]
            assert not {"identity_hashes", "publisher_home", "request_id"} & record.keys()
            assert all(value not in json.dumps(record) for value in private_values)
        with pytest.raises(ValueError, match="existing series"):
            releases._parent(conn, series_id=target["series_id"], parent_id="",
                             author=data["owner"], uid=data["uid"], identities={})
        with pytest.raises(ValueError, match="ownership"):
            releases._parent(conn, series_id=target["series_id"], parent_id=target["release_id"],
                             author=data["owner"], uid=data["uid"], identities={})
    with _as(data["owner"]):
        listing = releases.list_releases(universe_id=data["uid"], series_id=target["series_id"])
    assert [row["release_id"] for row in listing["releases"]] == [
        row["release_id"] for row in chain
    ]


def test_missing_publisher_evidence_refuses_continuation(published):
    with _agent_connect(published["base"]) as conn:
        conn.execute("DELETE FROM command_center_release_evidence")
    with pytest.raises(ValueError, match="evidence is unavailable"):
        release(published, style="main { color: teal; }")
