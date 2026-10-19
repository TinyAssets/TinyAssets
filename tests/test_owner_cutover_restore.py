"""Restore the pre-cutover old layout into private staging.

Root Linux proof uses real tar, SQLite and byte comparisons. Production rollback
eligibility and stopped-service installation are the separate C4 deployment lane.
"""

import json
import shutil
import tarfile
from pathlib import Path

import pytest

from tests.test_owner_cutover_census import _fingerprint, _sql
from tests.test_owner_cutover_census import source as source
from tinyassets.boxes.layout import OWNER_BOX_MIGRATION

REPO = Path(__file__).resolve().parents[1]


def _shim(directory, name, body):
    path = directory / name
    path.write_text("#!/usr/bin/env bash\nset -eu\n" + body)
    path.chmod(0o755)


@pytest.mark.parametrize("interruption", ["copied_tree", "box_records", "removed_host_tree"])
def test_paired_restore_after_half_move_is_byte_exact(source, tmp_path, interruption):
    volume = tmp_path / "volume/_data"
    shutil.copytree(source, volume)
    store = tmp_path / "owners"
    store.mkdir()
    before = _fingerprint(volume)
    data_archive = tmp_path / "tinyassets-snapshot-2026-10-17T00-00-00Z.tar.gz"
    box_archive = tmp_path / "tinyassets-box-store-2026-10-17T00-00-00Z.tar.gz"
    with tarfile.open(data_archive, "w:gz") as archive:
        archive.add(volume, arcname="_data")
    with tarfile.open(box_archive, "w:gz") as archive:
        archive.add(store, arcname="owners")

    # Failures after any of these writes must restore the original schema and
    # marker as well as the owner bytes. Never require the destination schema
    # to exist in the live half-moved volume to restore the source generation.
    (volume / ".layout.json").write_text(json.dumps({
        "layout": 2, "split": "owner-split", "state": "migrating", "migration": OWNER_BOX_MIGRATION,
    }))
    shutil.copytree(volume / "a1", store / "300001/a1")
    if interruption in {"box_records", "removed_host_tree"}:
        _sql(volume / ".broker/state/owner-identities.db", """
            CREATE TABLE box_records (principal TEXT, machine INT, project_id INT, bhard_bytes INT);
            INSERT INTO box_records VALUES ('alice',300001,300001,1073741824);
        """)
    if interruption == "removed_host_tree":
        shutil.rmtree(volume / "a1")
        _sql(volume / ".tinyassets.db", "DELETE FROM agent_turns WHERE owner_user_id='alice'")

    from tests.test_box_layout_migration import _archive
    from tinyassets.cutover_restore import restore_old_layout
    _archive(data_archive, data_archive.read_bytes())
    restored = restore_old_layout(data_archive, tmp_path / "old-layout")
    assert _fingerprint(restored) == before
    assert (store / "300001/a1/notes.txt").read_text() == "private history for a1"
    assert (restored / "a1/notes.txt").read_text() == "private history for a1"
    assert json.loads((restored / ".layout.json").read_text())["state"] == "stable"

    assert json.loads((volume / ".layout.json").read_text())["state"] == "migrating"
