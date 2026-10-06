"""DA7: the admission-generation restart contract, F1 decided (b).

Pure plan tests for every DA7 case, plus the staging sweep and F1(b) alarm
records on a real filesystem. The retired-broker-child log access runs in
the production-image probe (scripts/role_center_admission_probe.py).
"""
import os
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
contract = runpy.run_path(str(ROOT / "deploy/role_admission_contract.py"))
reconcile, Refused = contract["reconcile"], contract["ContractRefused"]


def row(generation, event, principal, center, machine=300001):
    return dict(generation=generation, event=event, principal=principal, center=center,
                machine=machine)


def stable(principals, generation, missing=None, direction="forward"):
    return dict(direction=direction, state="stable", principals=principals,
                generation=generation, missing=missing or {})


def test_signup_new_center_and_deletion_between_restarts():
    journal = stable({"u-a": "alice", "u-c": "carol"}, 4)
    rows = [row(5, "admit", "dave", "u-d"),          # signup
            row(6, "admit", "alice", "u-a2"),        # another center
            row(7, "retire", "carol", "u-c")]        # account deleted
    plan = reconcile(journal=journal, discovered={"u-a": "alice", "u-d": "dave",
                                                  "u-a2": "alice"}, rows=rows)
    assert plan["principals"] == {"u-a": "alice", "u-d": "dave", "u-a2": "alice"}
    assert plan["missing"] == {} and plan["alarms"] == [] and plan["adopt"] == []
    assert plan["generation"] == 7


def test_deleting_the_only_center_then_a_signup():
    journal = stable({"u-a": "alice"}, 1)
    empty = reconcile(journal=journal, discovered={}, rows=[row(2, "retire", "alice", "u-a")])
    assert empty["principals"] == {} and empty["missing"] == {} and empty["generation"] == 2
    after = reconcile(journal=stable({}, 2), discovered={"u-b": "bob"},
                      rows=[row(3, "admit", "bob", "u-b")])
    assert after["principals"] == {"u-b": "bob"} and after["generation"] == 3


@pytest.mark.parametrize("discovered,rows,match", [
    ({"u-a": "alice", "u-x": "mallory"}, [], "unexplained"),
    ({"u-a": "bob"}, [], "owner changed"),
    ({"u-a": "alice"}, [row(2, "admit", "bob", "u-a")], "another owner"),
    ({"u-a": "alice", "u-r": "rita"}, [row(2, "admit", "rita", "u-r"),
                                       row(3, "retire", "rita", "u-r")], "unexplained"),
])
def test_unexplained_trees_and_owner_changes_refuse(discovered, rows, match):
    with pytest.raises(Refused, match=match):
        reconcile(journal=stable({"u-a": "alice"}, 1), discovered=discovered, rows=rows)


def test_orphan_published_before_its_row_is_adopted_only_when_its_label_matches():
    journal = stable({"u-a": "alice"}, 1)
    found = {"u-a": "alice", "u-o": "alice"}
    plan = reconcile(journal=journal, discovered=found, rows=[],
                     adoptable=lambda center, owner: (center, owner) == ("u-o", "alice"))
    assert plan["adopt"] == [("alice", "u-o")]
    assert plan["principals"] == found
    with pytest.raises(Refused, match="unexplained"):
        reconcile(journal=journal, discovered=found, rows=[])


def test_pending_deletion_is_explained_with_or_without_its_tree():
    journal = stable({"u-a": "alice", "u-d": "dora"}, 1)
    present = reconcile(journal=journal, discovered={"u-a": "alice", "u-d": "dora"},
                        rows=[], pending={"u-d"})
    assert present["principals"] == {"u-a": "alice", "u-d": "dora"}  # stays bound for pass one
    gone = reconcile(journal=journal, discovered={"u-a": "alice"}, rows=[], pending={"u-d"})
    assert gone["missing"] == {} and gone["alarms"] == []  # mid-deletion, not lost
    assert gone["principals"] == {"u-a": "alice"}


def test_f1_b_missing_center_starts_everyone_else_and_is_rechecked():
    journal = stable({"u-a": "alice", "u-l": "lost"}, 3)
    plan = reconcile(journal=journal, discovered={"u-a": "alice"}, rows=[])
    assert plan["principals"] == {"u-a": "alice"}          # everyone else starts
    assert plan["missing"] == {"u-l": "lost"}              # held, unbound
    assert plan["alarms"] == [dict(center="u-l", principal="lost")]
    again = reconcile(journal=stable({"u-a": "alice"}, 3, missing=plan["missing"]),
                      discovered={"u-a": "alice"}, rows=[])
    assert again["missing"] == {"u-l": "lost"} and again["alarms"]  # re-checked, still loud
    restored = reconcile(journal=stable({"u-a": "alice"}, 3, missing=plan["missing"]),
                         discovered={"u-a": "alice", "u-l": "lost"}, rows=[])
    assert restored["principals"]["u-l"] == "lost" and restored["missing"] == {}
    retired = reconcile(journal=stable({"u-a": "alice"}, 3, missing=plan["missing"]),
                        discovered={"u-a": "alice"}, rows=[row(4, "retire", "lost", "u-l")])
    assert retired["missing"] == {} and retired["alarms"] == []


def test_first_volume_and_forward_after_reverse_seed_rows_and_never_retire():
    first = reconcile(journal=None, discovered={"u-a": "alice", "u-b": "bob"}, rows=[])
    assert first["seed"] == [("alice", "u-a"), ("bob", "u-b")]
    reverse = stable({"u-a": "alice"}, 5, direction="reverse")
    rows = [row(1, "admit", "alice", "u-a"), row(5, "admit", "alice", "u-r")]
    legacy = reconcile(journal=reverse, discovered={"u-a": "alice", "u-r": "alice",
                                                    "u-n": "nina"}, rows=rows)
    assert legacy["seed"] == [("nina", "u-n")]             # legacy image created u-n
    assert legacy["generation"] == 5 and legacy["missing"] == {}
    gone = reconcile(journal=reverse, discovered={"u-a": "alice"}, rows=rows)
    assert gone["seed"] == []                              # no retire is ever inferred
    with pytest.raises(Refused, match="retired"):
        reconcile(journal=None, discovered={"u-a": "alice"},
                  rows=[row(1, "admit", "alice", "u-a"), row(2, "retire", "alice", "u-a")])
    with pytest.raises(Refused, match="differs"):
        reconcile(journal=None, discovered={"u-a": "bob"}, rows=[row(1, "admit", "alice", "u-a")])


def test_interrupted_journal_keeps_exact_matching():
    journal = dict(direction="forward", state="migrating", principals={"u-a": "alice"},
                   generation=2)
    assert reconcile(journal=journal, discovered={"u-a": "alice"},
                     rows=[row(3, "admit", "bob", "u-b")])["principals"] == {"u-a": "alice"}
    with pytest.raises(Refused, match="authority changed"):
        reconcile(journal=journal, discovered={"u-a": "alice", "u-b": "bob"}, rows=[])


def test_a_log_delta_must_be_well_formed_and_above_the_journal():
    for rows in ([row(1, "admit", "bob", "u-b")], [row(3, "grant", "bob", "u-b")],
                 [row(3, "admit", "bob", "../x")], [dict(row(3, "admit", "bob", "u-b"), x=1)]):
        with pytest.raises(Refused, match="invalid"):
            reconcile(journal=stable({}, 2), discovered={}, rows=rows)


def test_journal_fields_and_phase_lag():
    plan = reconcile(journal=stable({"u-a": "alice"}, 1), discovered={"u-a": "alice",
                     "u-o": "alice"}, rows=[], adoptable=lambda *_: True)
    fields = contract["journal_fields"](plan, [row(9, "admit", "alice", "u-o")])
    assert fields == dict(principals={"u-a": "alice", "u-o": "alice"}, missing={}, generation=9)
    explained = contract["phase_explained"]
    assert explained({"u-a": 1}, {"u-a": 1}, {"u-a": 1, "u-o": 1})
    assert explained({"u-a": 1, "u-o": 1}, {"u-a": 1}, {"u-a": 1, "u-o": 1})
    assert not explained({"u-z": 1}, {"u-a": 1}, {"u-a": 1, "u-o": 1})


def test_canonical_encoding_matches_the_daemon_helper():
    from tinyassets.role_center_admission import canonical_label

    for machine in (300001, 300777, 399999):
        assert contract["canonical_label"](machine) == canonical_label(machine)


@pytest.mark.skipif(os.name != "posix", reason="descriptor-relative POSIX removal")
def test_staging_is_swept_before_the_inventory(tmp_path):
    staging = tmp_path / ".role-admission" / ("a" * 32) / "g"
    (staging / "root").mkdir(parents=True)
    (tmp_path / ".role-admission" / "stray").write_text("x")
    (tmp_path / "keep").mkdir()
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert contract["clear_staging"](fd) == 5
        assert contract["clear_staging"](fd) == 0
    finally:
        os.close(fd)
    assert sorted(os.listdir(tmp_path)) == ["keep"]


@pytest.mark.skipif(os.name != "posix", reason="descriptor-relative POSIX records")
def test_f1_b_alarm_is_loud_and_files_a_concern_record(tmp_path, capsys):
    state = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        written = contract["raise_alarms"](state, [dict(center="u-l", principal="lost")],
                                           now=0)
        assert contract["raise_alarms"](state, []) == []
    finally:
        os.close(state)
    assert written == ["1970-01-01-missing-center-u-l.md"]
    record = tmp_path / "admission-concerns" / written[0]
    assert "u-l" in record.read_text() and "retire" in record.read_text()
    assert oct(record.stat().st_mode & 0o777) == "0o600"
    assert "ROLE ADMISSION ALARM: command center u-l" in capsys.readouterr().err
