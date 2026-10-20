"""The only legacy path cleanup is stopped one-shot cutover."""
import json
import sqlite3

import pytest

from tests.support.admission import projection
from tests.test_broker_deletion_jobs import Remote
from tinyassets.broker.admission import digest
from tinyassets.broker.deletion_jobs import STEPS, load_unfinished_jobs
from tinyassets.cutover_deletions import PHASES, collect, finish


def legacy(tmp_path):
    source, store, shared = (tmp_path / part for part in ("source", "store", "shared"))
    for directory in (source, store, shared):
        directory.mkdir()
    (source / ".boxhost").mkdir()
    receipt = {"completed_phases": [phase for phase in PHASES if phase != "owner_tree"],
               "billing_cleanup": {"sub_old": "cancelled"}}
    with sqlite3.connect(source / ".boxhost/boxhost.db") as db:
        db.execute("CREATE TABLE owner_destroys (owner_id TEXT, uid INTEGER, receipt TEXT)")
        db.execute("INSERT INTO owner_destroys VALUES (?,?,?)",
                   ("later", 300002, json.dumps(receipt)))
    target = store / "old-quarantine/300002"
    target.mkdir(parents=True)
    (target / "private").write_text("legacy synthetic private bytes")
    (source / ".disaster-restore.json").write_text(json.dumps({
        "erasure_failures": [{"uid": 300002, "quarantine": str(target)}]}))
    return source, store, shared, target


def test_cutover_completes_legacy_quarantine_before_receipt(tmp_path):
    source, store, shared, target = legacy(tmp_path)
    facts = collect(source, store_root=store)
    records = Remote(tmp_path / "off-host")
    assert not (source / ".broker/state/owner-identities.db").exists()
    assert len(facts.entries()) == 1
    assert finish(facts, shared, store, records) == 1
    assert not target.exists()
    receipt = records.load(digest("later"))
    assert receipt["completed_steps"] == list(STEPS)
    assert receipt["billing_cleanup"] == {"sub_old": "cancelled"}
    assert receipt["results"]["legacy_paths"]
    assert str(store) not in json.dumps(receipt)
    assert load_unfinished_jobs(records) == []
    assert finish(collect(source, store_root=store), shared, store, records) == 1


def test_cutover_refuses_when_legacy_path_cannot_be_deleted(tmp_path, monkeypatch):
    from tinyassets import box_layout_migration

    source, store, shared, target = legacy(tmp_path)
    records = Remote(tmp_path / "off-host")
    def denied(*args):
        raise PermissionError("synthetic cleanup failure")
    monkeypatch.setattr(box_layout_migration, "_remove_tree", denied)
    with pytest.raises(RuntimeError, match="cutover refused"):
        finish(collect(source, store_root=store), shared, store, records)
    assert target.exists()
    job, = load_unfinished_jobs(records)
    assert job["failed_step"] == "legacy_cutover"
    assert "receipt" not in job["completed_steps"]


def test_cutover_missing_billing_authority_refuses_without_inventing_completion(tmp_path):
    source, store, shared, _ = legacy(tmp_path)
    with sqlite3.connect(source / ".boxhost/boxhost.db") as db:
        db.execute("UPDATE owner_destroys SET receipt=?", (json.dumps({
            "completed_phases": [p for p in PHASES if p != "billing"]}),))
    records = Remote(tmp_path / "off-host")
    with pytest.raises(RuntimeError, match="cutover refused"):
        finish(collect(source, store_root=store), shared, store, records)
    assert load_unfinished_jobs(records)[0]["failed_step"] == "legacy_cutover"


def test_census_counts_digest_only_obligations_and_deduplicates_quarantines(tmp_path):
    from tinyassets.cutover_deletions import census_count

    source, _, _, target = legacy(tmp_path)
    (source / ".broker/accounts" / digest("unknown-policy")).mkdir(parents=True)
    (source / ".broker/accounts" / digest("unknown-policy") / ".deleted").touch()
    with sqlite3.connect(source / ".broker/credential-custody.db") as db:
        db.execute("CREATE TABLE deleted_principals (digest TEXT)")
        db.execute("INSERT INTO deleted_principals VALUES (?)", (digest("unknown-custody"),))
    with sqlite3.connect(source / ".boxhost/boxhost.db") as db:
        receipt = json.loads(db.execute("SELECT receipt FROM owner_destroys").fetchone()[0])
        receipt["quarantines"] = [str(target)]
        db.execute("UPDATE owner_destroys SET receipt=?", (json.dumps(receipt),))
    assert census_count(source) == {"pending_deletions": 3, "pending_paths": 1}


def test_cutover_finishes_all_pending_legacy_phases_without_volume_binding(tmp_path, monkeypatch):
    from tests.support.admission import Plans
    from tinyassets.broker.admission import local_mutate
    from tinyassets.broker.owner_identities import OwnerIdentities
    from tinyassets.broker.policy import PolicyStore

    source, store, shared, target = legacy(tmp_path)
    with sqlite3.connect(source / ".boxhost/boxhost.db") as db:
        db.execute("UPDATE owner_destroys SET receipt='{}'")
    identity = shared / ".broker/state/owner-identities.db"
    identity.parent.mkdir(parents=True, mode=0o700)
    identities = OwnerIdentities(identity, initialize=True)
    local_mutate(identity, "later", "erase", routing=projection(identity))
    plans = Plans(shared)
    plans.initialize()
    plans.set_plan("later", "free", event_created=0)
    monkeypatch.setattr("tinyassets.broker.browser_vault.SECRET_ROOT", tmp_path / "private-keys")
    identity_calls = []
    monkeypatch.setattr("tinyassets.router_account_deletion.delete_workos_user",
                        lambda owner: identity_calls.append(owner) or "deleted")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("real API call"))
    records = Remote(tmp_path / "off-host")
    assert finish(collect(source, store_root=store), shared, store, records) == 1
    assert not target.exists()
    assert not PolicyStore(shared, identities)._path("later").exists()
    assert identity_calls == ["later"]
    job = records.load(digest("later"))
    assert job["completed_steps"] == list(STEPS)
    assert finish(collect(source, store_root=store), shared, store, records) == 1
    assert identity_calls == ["later", "later"]
