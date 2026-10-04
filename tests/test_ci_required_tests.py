"""Tests for the `required-tests` gate's own decision logic.

The gate decides whether every other test result blocks a merge, so its logic
needs the same scrutiny as the code it guards — a bug here fails open silently.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci_required_tests.py"
_spec = importlib.util.spec_from_file_location("ci_required_tests", _SCRIPT)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _tc(**attrib) -> ET.Element:
    return ET.Element("testcase", attrib)


# ---- node id reconstruction ------------------------------------------------


def test_node_id_module_level_function():
    el = _tc(file="tests/test_x.py", classname="tests.test_x", name="test_y")
    assert gate.node_id(el) == "tests/test_x.py::test_y"


def test_node_id_inside_a_class():
    el = _tc(file="tests/test_x.py", classname="tests.test_x.TestThing", name="test_y")
    assert gate.node_id(el) == "tests/test_x.py::TestThing::test_y"


def test_node_id_normalises_windows_separators():
    el = _tc(file="tests\\smoke\\test_x.py", classname="tests.smoke.test_x", name="t")
    assert gate.node_id(el) == "tests/smoke/test_x.py::t"


def test_node_id_without_file_attribute_still_identifies_the_test():
    """A failure must never be dropped just because `file` is missing."""
    el = _tc(classname="tests.test_x.TestThing", name="test_y")
    assert gate.node_id(el) == "tests.test_x.TestThing::test_y"


# ---- quarantine file parsing -----------------------------------------------


def test_parse_quarantine_splits_tolerated_and_flaky(tmp_path):
    f = tmp_path / "q.txt"
    f.write_text(
        "# a comment\n"
        "\n"
        "tests/test_a.py::test_one\n"
        "flaky owner=dev expires=2026-10-15 tests/test_b.py::test_two\n"
        "tests/test_c.py::test_three  # trailing comment\n",
        encoding="utf-8",
    )
    tolerated, flaky, problems = gate.parse_quarantine(f)
    assert tolerated == {"tests/test_a.py::test_one", "tests/test_c.py::test_three"}
    assert flaky == {"tests/test_b.py::test_two"}
    assert problems == []


def test_a_flaky_entry_must_name_an_owner_and_an_expiry(tmp_path):
    """A quarantined test still runs; the owner and date say who ends it and when."""
    f = tmp_path / "q.txt"
    f.write_text("flaky owner=dev tests/test_b.py::test_two\n", encoding="utf-8")
    _, flaky, problems = gate.parse_quarantine(f)
    assert flaky == {"tests/test_b.py::test_two"}
    assert len(problems) == 1 and "owner= and expires=" in problems[0]


def test_ledger_fields_lead_so_a_parameter_id_is_never_eaten():
    """Fields come BEFORE the node id: a parameter id may end in ` owner=b]`,
    but no node id starts with `owner=`."""
    line = "flaky owner=a expires=2026-10-15 tests/t.py::t[x owner=b]  # c"
    parsed = gate.split_ledger_line(line)
    assert parsed == (True, "tests/t.py::t[x owner=b]", {"owner": "a", "expires": "2026-10-15"})
    assert gate.split_ledger_line("tests/t.py::t[a b]") == (False, "tests/t.py::t[a b]", {})
    assert gate.split_ledger_line("   # only a comment") is None


def test_the_quarantine_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "MAX_QUARANTINE", 2)
    f = tmp_path / "q.txt"
    f.write_text("".join(f"tests/test_a.py::t{i}\n" for i in range(3)), encoding="utf-8")
    assert any("the cap is 2" in p for p in gate.parse_quarantine(f)[2])


def test_budgets_bound_skips_and_summed_seconds(monkeypatch):
    monkeypatch.setattr(gate, "MAX_REQUIRED_SKIPPED", 2)
    monkeypatch.setattr(gate, "MAX_TEST_SECONDS", 100)
    assert gate.budget_failures({"a", "b"}, 100.0) == []
    over = gate.budget_failures({"a", "b", "c"}, 100.5)
    assert len(over) == 2 and "SKIPPED" in over[0] and "100s" in over[1]


def test_parse_quarantine_reports_malformed_lines(tmp_path):
    f = tmp_path / "q.txt"
    f.write_text("not-a-node-id\n", encoding="utf-8")
    tolerated, flaky, problems = gate.parse_quarantine(f)
    assert not tolerated and not flaky
    assert len(problems) == 1 and "not a pytest node id" in problems[0]


def test_parse_quarantine_missing_file_is_empty_not_an_error(tmp_path):
    assert gate.parse_quarantine(tmp_path / "nope.txt") == (set(), set(), [])


# ---- outcome collection ----------------------------------------------------


def _junit(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "junit.xml"
    p.write_text(f"<testsuites><testsuite>{body}</testsuite></testsuites>", encoding="utf-8")
    return p


def test_collect_outcomes_classifies_pass_fail_error(tmp_path):
    j = _junit(
        tmp_path,
        '<testcase file="tests/t.py" classname="tests.t" name="ok"/>'
        '<testcase file="tests/t.py" classname="tests.t" name="bad"><failure/></testcase>'
        '<testcase file="tests/t.py" classname="tests.t" name="boom"><error/></testcase>',
    )
    failing, ran = gate.collect_outcomes(j)
    assert failing == {"tests/t.py::bad", "tests/t.py::boom"}
    assert ran == {"tests/t.py::ok", "tests/t.py::bad", "tests/t.py::boom"}


def test_collect_outcomes_excludes_skipped_from_ran(tmp_path):
    """A skipped test proves nothing, so it must not mark a quarantine entry stale."""
    j = _junit(
        tmp_path,
        '<testcase file="tests/t.py" classname="tests.t" name="s"><skipped/></testcase>',
    )
    failing, ran = gate.collect_outcomes(j)
    assert failing == set()
    assert ran == set()


# ---- the repo's real quarantine file ---------------------------------------


def test_vacuity_floor_fails_small_runs():
    # A mass skip/deselect/deletion must not produce a green gate.
    msg = gate.vacuity_failure(10)
    assert msg is not None and "vacuous" in msg


def test_vacuity_floor_passes_full_runs():
    assert gate.vacuity_failure(gate.MIN_RAN_FLOOR) is None


def test_vacuity_floor_is_meaningfully_high():
    # The floor only works if it sits far above any trivial subset run.
    assert gate.MIN_RAN_FLOOR >= 5000


def test_deselect_everything_attack_is_blocked(tmp_path):
    """The exact bypass shape: keep a few passing tests, drop the rest.

    Such a run yields zero new failures and zero stale entries — green under
    the pre-2026-08-02 logic. Only the ran-count floor catches it.
    """
    suite = ET.Element("testsuite")
    for i in range(5):
        suite.append(_tc(file="tests/test_x.py", classname="tests.test_x", name=f"test_{i}"))
    junit = tmp_path / "tiny.xml"
    ET.ElementTree(suite).write(junit, encoding="utf-8")

    failing, ran = gate.collect_outcomes(junit)
    assert failing == set()  # nothing failed...
    assert len(ran) == 5  # ...because almost nothing ran
    assert gate.vacuity_failure(len(ran)) is not None  # and that is the finding


def test_repo_quarantine_file_is_wellformed():
    """The committed list must always parse — a malformed line fails the gate."""
    _, _, problems = gate.parse_quarantine(gate.QUARANTINE)
    assert problems == [], f"malformed quarantine entries: {problems}"


def test_the_quarantine_cap_only_ratchets_down():
    """Deleting ledger entries must lower MAX_QUARANTINE in the same change, so
    the headroom never quietly grows back into room for new quarantines."""
    tolerated, flaky, _ = gate.parse_quarantine(gate.QUARANTINE)
    entries = len(tolerated) + len(flaky)
    assert gate.MAX_QUARANTINE - entries <= gate.QUARANTINE_SLACK, (
        f"{entries} ledger entries; lower MAX_QUARANTINE to at most "
        f"{entries + gate.QUARANTINE_SLACK}"
    )


@pytest.mark.parametrize("attr", ["QUARANTINE", "REPO_ROOT"])
def test_module_constants_exist(attr):
    assert getattr(gate, attr) is not None


def test_node_id_collection_error_has_no_double_colon():
    """A collection error records an empty classname; the id must stay clean."""
    el = _tc(file="tests/test_x.py", classname="", name="tests.test_x")
    assert gate.node_id(el) == "tests/test_x.py::tests.test_x"


def test_min_ran_below_floor_is_rejected_at_parse_time():
    """A low `--min-ran` must fail closed, not silently disable the floor.

    Cross-family review rated this BLOCKING: argparse honours the LAST
    occurrence of a repeated flag, so `--min-ran 10700 --min-ran 1` sets the
    real floor to 1 while any check scanning for the first match still reads
    10700 — and a mass-deselected suite then merges green. Validating only in
    the workflow-shape test would leave every other caller exposed, so the
    refusal lives here, at the point of enforcement.
    """
    with pytest.raises(argparse.ArgumentTypeError) as excinfo:
        gate._min_ran_arg(str(gate.MIN_RAN_FLOOR - 1))
    assert "MIN_RAN_FLOOR" in str(excinfo.value)


def test_min_ran_at_or_above_floor_is_accepted():
    """The escape hatch is lowering MIN_RAN_FLOOR itself, in the same PR."""
    assert gate._min_ran_arg(str(gate.MIN_RAN_FLOOR)) == gate.MIN_RAN_FLOOR
    assert gate._min_ran_arg("10700") == 10700


# ---- sharding: partition ---------------------------------------------------


def test_parse_shard_accepts_in_range_and_rejects_the_rest():
    assert gate.parse_shard("1/6") == (1, 6)
    assert gate.parse_shard("6/6") == (6, 6)
    for bad in ("0/6", "7/6", "3", "a/b", "1/0"):
        with pytest.raises(argparse.ArgumentTypeError):
            gate.parse_shard(bad)


def test_every_real_test_file_has_exactly_one_owner_and_every_shard_gets_work():
    """Complete and disjoint over the repo's ACTUAL test files, at the CI count."""
    files = sorted(
        p.relative_to(gate.REPO_ROOT).as_posix()
        for p in (gate.REPO_ROOT / "tests").rglob("test_*.py")
    )
    assert len(files) > 100
    owners = {f: gate.shard_of(f, 6) for f in files}
    assert set(owners.values()) == set(range(1, 7))
    # Stable across calls and separator styles: every shard job computes the
    # partition independently, so any nondeterminism would drop or double files.
    assert all(gate.shard_of(f, 6) == owners[f] for f in files)
    assert all(gate.shard_of(f.replace("/", "\\"), 6) == owners[f] for f in files)


class _FakeConfig:
    def __init__(self, root: Path, shard: str | None):
        self.rootpath = root
        self._shard = shard

    def getoption(self, name, default=None):
        assert name == "--ci-shard"
        return self._shard


def test_ignore_collect_skips_only_other_shards_test_files(tmp_path):
    f = tmp_path / "tests" / "test_x.py"
    f.parent.mkdir()
    f.write_text("", encoding="utf-8")
    owner = gate.shard_of("tests/test_x.py", 4)
    other = owner % 4 + 1
    assert gate.pytest_ignore_collect(f, _FakeConfig(tmp_path, f"{owner}/4")) is None
    assert gate.pytest_ignore_collect(f, _FakeConfig(tmp_path, f"{other}/4")) is True
    # Unsharded runs, directories and conftests are never filtered: a conftest
    # skipped in some shard would change fixtures under that shard's tests.
    assert gate.pytest_ignore_collect(f, _FakeConfig(tmp_path, None)) is None
    assert gate.pytest_ignore_collect(f.parent, _FakeConfig(tmp_path, f"{other}/4")) is None
    for name in ("conftest.py", "__init__.py"):
        special = f.parent / name
        special.write_text("", encoding="utf-8")
        for i in range(1, 5):
            assert gate.pytest_ignore_collect(special, _FakeConfig(tmp_path, f"{i}/4")) is None


def test_real_pytest_shards_cover_every_test_exactly_once(tmp_path):
    """Drive pytest itself with `-p ci_required_tests`, the way the gate does.

    The unit tests above prove the hook's answers; this proves pytest actually
    loads the module as a plugin and honours them, so the union of the shards
    is exactly the unsharded run.
    """
    proj = tmp_path / "proj"
    (proj / "tests" / "sub").mkdir(parents=True)
    (proj / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (proj / "tests" / "conftest.py").write_text("", encoding="utf-8")
    expected = set()
    # Directories named like modules: pytest asks the hook about a directory
    # before descending, so hashing one would split it from its own files.
    # Several are needed so at least one directory and its file hash apart.
    rels = [f"tests/{'sub/' if i % 3 == 0 else ''}test_m{i}.py" for i in range(12)]
    rels += [f"tests/gen{i}.py/test_inner{i}.py" for i in range(6)]
    assert any(
        gate.shard_of(r.rsplit("/", 1)[0], 3) != gate.shard_of(r, 3) for r in rels[12:]
    ), "no directory/file pair hashes apart; the regression case is not exercised"
    for rel in rels:
        (proj / rel).parent.mkdir(parents=True, exist_ok=True)
        (proj / rel).write_text(
            "def test_a():\n    pass\n\n\ndef test_b():\n    pass\n", encoding="utf-8"
        )
        expected |= {f"{rel}::test_a", f"{rel}::test_b"}

    env = dict(os.environ)
    env["PYTHONPATH"] = str(_SCRIPT.parent)
    seen: dict[str, int] = {}
    for index in (1, 2, 3):
        junit = tmp_path / f"j{index}.xml"
        proc = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                "-p", "ci_required_tests", f"--ci-shard={index}/3",
                "-o", "junit_family=xunit1", f"--junitxml={junit}", "tests",
            ],
            cwd=proj, env=env, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        _, ran = gate.collect_outcomes(junit)
        assert ran, f"shard {index} ran nothing; the split did not spread 12 files"
        for nid in ran:
            assert nid not in seen, f"{nid} ran in shards {seen[nid]} and {index}"
            seen[nid] = index
    assert set(seen) == expected


# ---- sharding: the aggregate -----------------------------------------------


def _shard(
    dir_: Path,
    index: int,
    total: int,
    body: str | None,
    exit_code: int = 0,
    selection: str | None = None,
) -> None:
    record = {"shard": index, "total": total, "pytest_exit": exit_code}
    if selection is not None:
        record["selection"] = selection
    (dir_ / f"junit-shard-{index}.json").write_text(json.dumps(record), encoding="utf-8")
    if body is not None:
        (dir_ / f"junit-shard-{index}.xml").write_text(
            f"<testsuites><testsuite>{body}</testsuite></testsuites>", encoding="utf-8"
        )


def _cases(module: str, n: int, fail: int = 0) -> str:
    return "".join(
        f'<testcase file="tests/{module}.py" classname="tests.{module}" name="t{i}">'
        f"{'<failure/>' if i < fail else ''}</testcase>"
        for i in range(n)
    )


@pytest.fixture()
def shards(tmp_path):
    d = tmp_path / "shards"
    d.mkdir()
    return d


def _aggregate(
    shards: Path,
    expected: int = 3,
    min_ran: int = 1,
    result: str = "success",
    expect_selection: str | None = None,
    must_cover: list[str] | None = None,
) -> int:
    return gate.aggregate(
        shards,
        expected,
        shards.parent / "junit.xml",
        min_ran,
        result,
        expect_selection=expect_selection,
        must_cover=must_cover,
    )


def test_aggregate_passes_when_every_shard_is_present_and_clean(shards):
    for i in (1, 2, 3):
        _shard(shards, i, 3, _cases(f"test_s{i}", 4))
    assert _aggregate(shards) == 0
    # The union is written back as ONE junit, the shape --emit-quarantine reads.
    _, ran = gate.collect_outcomes(shards.parent / "junit.xml")
    assert len(ran) == 12


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", ""])
def test_aggregate_fails_when_the_shard_jobs_did_not_succeed(shards, result):
    """Complete, clean files do not outvote a failed shard job."""
    for i in (1, 2, 3):
        _shard(shards, i, 3, _cases(f"test_s{i}", 4))
    assert _aggregate(shards, result=result) == 1


def test_aggregate_fails_on_a_missing_shard(shards, capsys):
    """The headline property: a lost shard can never read as green."""
    _shard(shards, 1, 3, _cases("test_s1", 4))
    _shard(shards, 3, 3, _cases("test_s3", 4))
    assert _aggregate(shards) == 1
    assert "missing shard(s) [2]" in capsys.readouterr().out


def test_aggregate_fails_when_a_shard_ran_a_different_split(shards):
    _shard(shards, 1, 3, _cases("test_s1", 4))
    _shard(shards, 2, 3, _cases("test_s2", 4))
    _shard(shards, 3, 4, _cases("test_s3", 4))
    assert _aggregate(shards) == 1


@pytest.mark.parametrize("exit_code", [2, 3, 4, 5])
def test_aggregate_fails_on_a_shard_exit_nothing_explains(shards, exit_code):
    _shard(shards, 1, 3, _cases("test_s1", 4))
    _shard(shards, 2, 3, _cases("test_s2", 4), exit_code=exit_code)
    _shard(shards, 3, 3, _cases("test_s3", 4))
    assert _aggregate(shards) == 1


def test_aggregate_fails_when_a_shard_wrote_no_junit(shards):
    _shard(shards, 1, 3, _cases("test_s1", 4))
    _shard(shards, 2, 3, None, exit_code=1)
    _shard(shards, 3, 3, _cases("test_s3", 4))
    assert _aggregate(shards) == 1


def test_aggregate_fails_when_a_shard_junit_is_corrupt(shards):
    for i in (1, 2, 3):
        _shard(shards, i, 3, _cases(f"test_s{i}", 4))
    (shards / "junit-shard-2.xml").write_text("<testsuites><testsu", encoding="utf-8")
    assert _aggregate(shards) == 1


def test_aggregate_fails_when_one_test_ran_in_two_shards(shards):
    _shard(shards, 1, 3, _cases("test_s1", 4))
    _shard(shards, 2, 3, _cases("test_s1", 1))
    _shard(shards, 3, 3, _cases("test_s3", 4))
    assert _aggregate(shards) == 1


def test_aggregate_fails_on_a_new_failure_in_any_shard(shards):
    _shard(shards, 1, 3, _cases("test_s1", 4))
    _shard(shards, 2, 3, _cases("test_s2", 4, fail=1), exit_code=1)
    _shard(shards, 3, 3, _cases("test_s3", 4))
    assert _aggregate(shards) == 1


def test_aggregate_applies_the_vacuity_floor_to_the_union(shards):
    for i in (1, 2, 3):
        _shard(shards, i, 3, _cases(f"test_s{i}", 4))
    assert _aggregate(shards, min_ran=13) == 1
    assert _aggregate(shards, min_ran=12) == 0


def test_shard_floor_sits_below_one_shard_of_the_full_floor():
    """A shard floor above ~1/6 of the suite would fail a healthy small shard."""
    assert 500 <= gate.MIN_RAN_FLOORS["shard"] < gate.MIN_RAN_FLOOR // 6


# ---- PR-time affected selection --------------------------------------------


def _selection_args(tmp_path, entries, shard=None, exclude=None):
    sel = tmp_path / "affected.txt"
    sel.write_text("".join(f"{e}\n" for e in entries), encoding="utf-8")
    excl = None
    if exclude is not None:
        excl = tmp_path / "heavy.txt"
        excl.write_text("# heavy\n" + "".join(f"{e}\n" for e in exclude), encoding="utf-8")
    return argparse.Namespace(
        affected=str(sel), shard=shard, exclude_from=str(excl) if excl else None
    )


def test_affected_all_means_the_whole_surface(tmp_path):
    assert gate._read_selection(_selection_args(tmp_path, ["ALL"])) is None
    with pytest.raises(SystemExit):
        gate._read_selection(_selection_args(tmp_path, ["ALL", "tests/test_x.py"]))


def test_affected_slices_cover_the_selection_exactly_once_minus_heavy(tmp_path):
    real = sorted(
        p.relative_to(gate.REPO_ROOT).as_posix()
        for p in (gate.REPO_ROOT / "tests").glob("test_*.py")
    )[:40]
    heavy = real[:3]
    slices = [
        gate._read_selection(_selection_args(tmp_path, real, (i, 4), heavy))
        for i in range(1, 5)
    ]
    flat = [f for s in slices for f in s]
    assert sorted(flat) == sorted(set(real) - set(heavy))
    assert len(flat) == len(set(flat))


def test_affected_drops_a_missing_path_with_a_warning(tmp_path, capsys):
    picked = gate._read_selection(_selection_args(tmp_path, ["tests/test_no_such_file.py"]))
    assert picked == []
    assert "missing path" in capsys.readouterr().out


def test_affected_empty_slice_is_a_green_no_op_without_pytest(tmp_path):
    sel = tmp_path / "affected.txt"
    sel.write_text("", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--affected", str(sel), "--profile", "affected",
         "--shard", "1/4", "--junit", str(tmp_path / "j.xml")],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nothing to run" in proc.stdout
    assert "+ " not in proc.stdout, "pytest must not start for an empty slice"


@pytest.mark.parametrize(
    "argv",
    [
        ["--affected", "x.txt"],
        ["--profile", "affected"],
        ["--affected", "x.txt", "--profile", "affected", "--include-from", "y.txt"],
    ],
)
def test_affected_flags_are_refused_out_of_pairing(argv):
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), *argv], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode != 0
    assert "affected" in proc.stderr


def test_affected_floor_is_zero_and_only_reachable_through_affected():
    assert gate.MIN_RAN_FLOORS["affected"] == 0


# ---- sharding: packing by measured duration ---------------------------------


def test_pack_puts_longest_first_on_the_least_loaded_shard():
    durations = {"a": 10.0, "b": 6.0, "c": 5.0, "d": 4.0, "e": 1.0}
    owner = gate.pack(sorted(durations), durations, 2)
    # a(10)->1; b(6)->2; c(5)->2 (6<10); d(4)->1 (10<11); e(1)->2 (11<14).
    assert owner == {"a": 1, "b": 2, "c": 2, "d": 1, "e": 2}


def test_pack_is_deterministic_and_order_independent():
    durations = {f"tests/test_{i}.py": float(i % 7) for i in range(50)}
    files = sorted(durations)
    first = gate.pack(files, durations, 6)
    assert gate.pack(list(reversed(files)), durations, 6) == first
    assert set(first.values()) == set(range(1, 7))


def test_a_file_the_table_does_not_know_weighs_the_median():
    durations = {"x": 1.0, "y": 2.0, "z": 30.0}
    # Median 2.0: the new file lands like a 2-second file, not like a free one.
    owner = gate.pack(["x", "y", "z", "new"], durations, 2)
    assert owner["z"] != owner["new"]
    assert owner["new"] == owner["x"] == owner["y"]


def test_a_missing_table_means_equal_weights_said_out_loud(tmp_path, capsys):
    assert gate.load_durations(tmp_path / "nope.json") == {}
    assert "WARNING" in capsys.readouterr().out


def test_a_file_outside_the_packed_set_still_has_exactly_one_owner():
    rel = "tests/never_on_disk/test_ghost.py"
    owners = {gate.shard_of(rel, 6) for _ in range(3)}
    assert owners == {gate._hash_shard(rel, 6)}


def test_the_committed_table_packs_the_required_surface_within_tolerance():
    """The founder's bar: no shard more than ~1.5x the median, by measured time."""
    durations = gate.load_durations()
    assert durations, ".github/test-durations.json is missing or empty"
    heavy = [
        line.strip().rstrip("/")
        for line in (gate.REPO_ROOT / ".github" / "heavy-test-files.txt")
        .read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    loads = [0.0] * 6
    for path in (gate.REPO_ROOT / "tests").rglob("test_*.py"):
        rel = path.relative_to(gate.REPO_ROOT).as_posix()
        if not any(rel == h or rel.startswith(h + "/") for h in heavy):
            loads[gate.shard_of(rel, 6) - 1] += durations.get(rel, 0.0)
    ordered = sorted(loads)
    assert ordered[-1] <= 1.5 * ((ordered[2] + ordered[3]) / 2), loads


def test_an_untracked_test_file_does_not_reshuffle_the_packing(tmp_path, monkeypatch):
    """Owners come from tracked files, so a file generated in one job moves nothing."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    for i in range(30):
        (repo / "tests" / f"test_{i:02d}.py").write_text("", encoding="utf-8")
    for args in (["init", "-q"], ["add", "tests"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    monkeypatch.setattr(gate, "REPO_ROOT", repo)
    gate._packed.cache_clear()
    try:
        before = dict(gate._packed(6))
        assert len(before) == 30
        (repo / "tests" / "test_00_generated.py").write_text("", encoding="utf-8")
        gate._packed.cache_clear()
        assert gate._packed(6) == before
    finally:
        gate._packed.cache_clear()


# ---- the affected-only merge gate ------------------------------------------
#
# The selective path cannot use the vacuity floor: a selection can honestly be
# one test, so no count is meaningful. Two checks replace it -- every shard must
# report the digest the `select` job published, and every selected file must
# report at least one case (docs/design-notes/2026-10-02-affected-only-merge-gate.md).


def _selection_file(tmp_path: Path, *entries: str) -> Path:
    path = tmp_path / "affected.txt"
    path.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return path


def test_the_digest_is_order_and_duplicate_insensitive(tmp_path):
    """It answers "the same SET of files?" and must not depend on anything else."""
    a = gate.selection_entries(_selection_file(tmp_path, "tests/b.py", "tests/a.py"))
    b = gate.selection_entries(
        _selection_file(tmp_path, "tests/a.py", "tests/b.py", "tests/a.py")
    )
    assert gate.selection_digest(a) == gate.selection_digest(b)


def test_all_has_its_own_digest(tmp_path):
    """"The whole surface" and "a selection I could not read" must never match."""
    assert gate.selection_entries(_selection_file(tmp_path, "ALL")) is None
    assert gate.selection_digest(None) != gate.selection_digest([])
    assert gate.selection_digest(None) != ""


def test_all_must_be_the_only_entry(tmp_path):
    with pytest.raises(SystemExit):
        gate.selection_entries(_selection_file(tmp_path, "ALL", "tests/a.py"))


def test_aggregate_refuses_a_shard_that_ran_another_selection(shards, capsys):
    """The property the digest exists for: shards cannot each pick their own."""
    for i in (1, 2, 3):
        _shard(shards, i, 3, _cases(f"test_s{i}", 4), selection="sha256:aaa")
    assert _aggregate(shards, expect_selection="sha256:aaa") == 0
    _shard(shards, 2, 3, _cases("test_s2", 4), selection="sha256:bbb")
    assert _aggregate(shards, expect_selection="sha256:aaa") == 1
    out = capsys.readouterr().out
    assert "ran selection sha256:bbb" in out
    # And it is reported as absent too, so the union is never judged complete.
    assert "missing shard(s) [2]" in out


def test_a_manifest_with_no_selection_cannot_satisfy_the_contract(shards, capsys):
    """A shard from before this contract must not pass it by omission."""
    for i in (1, 2, 3):
        _shard(shards, i, 3, _cases(f"test_s{i}", 4))
    assert _aggregate(shards, expect_selection="sha256:aaa") == 1
    assert "ran selection <none>" in capsys.readouterr().out


def test_coverage_fails_when_a_selected_file_reported_nothing(shards, capsys):
    """Replaces the floor, and catches what a ">=5 files" rule would pass.

    One selected file of three reporting no case at all is a collapse -- a
    collection error, a bad slice, or a shard that ran something else. A count
    threshold cannot see it; naming the file does.
    """
    for i in (1, 2):
        _shard(shards, i, 3, _cases(f"test_s{i}", 4), selection="sha256:aaa")
    _shard(shards, 3, 3, "", selection="sha256:aaa")
    verdict = _aggregate(
        shards,
        expect_selection="sha256:aaa",
        must_cover=["tests/test_s1.py", "tests/test_s2.py", "tests/test_s3.py"],
    )
    assert verdict == 1
    out = capsys.readouterr().out
    assert "1 of 3 selected test file(s) reported no case at all" in out
    assert "tests/test_s3.py" in out


def test_coverage_counts_a_skip_as_reported(shards):
    """A platform-guarded file legitimately only skips; that is not a collapse."""
    _shard(shards, 1, 3, _cases("test_s1", 4), selection="sha256:aaa")
    _shard(shards, 2, 3, _cases("test_s2", 4), selection="sha256:aaa")
    _shard(
        shards,
        3,
        3,
        '<testcase file="tests/test_s3.py" classname="tests.test_s3" name="t0">'
        "<skipped/></testcase>",
        selection="sha256:aaa",
    )
    assert (
        _aggregate(
            shards,
            expect_selection="sha256:aaa",
            must_cover=["tests/test_s1.py", "tests/test_s2.py", "tests/test_s3.py"],
        )
        == 0
    )


def test_coverage_passes_a_one_file_selection(shards):
    """The case a floor cannot express: a selection of one test is honest."""
    _shard(shards, 1, 3, _cases("test_s1", 1), selection="sha256:aaa")
    for i in (2, 3):
        _shard(shards, i, 3, "", selection="sha256:aaa")
    assert (
        _aggregate(shards, expect_selection="sha256:aaa", must_cover=["tests/test_s1.py"]) == 0
    )


def test_an_empty_slice_still_reports_a_manifest_and_a_junit(tmp_path, monkeypatch):
    """A shard with nothing to do must look different from a shard that vanished.

    Without both files the aggregate reports it missing and fails the gate --
    which is exactly right for a lost shard, so one that legitimately owns
    nothing has to report in the same shape.
    """
    rel = "tests/test_ci_required_tests.py"
    owner = gate.shard_of(rel, 6)
    idle = 1 + (owner % 6)  # any shard that is not the owner
    junit = tmp_path / "out" / f"junit-shard-{idle}.xml"
    selection = _selection_file(tmp_path, rel)
    # Real argv, so a new flag cannot silently break this the way a hand-built
    # Namespace did: it only fails when the BEHAVIOUR changes.
    monkeypatch.setattr(sys, "argv", [
        "ci_required_tests.py",
        "--junit", str(junit),
        "--affected", str(selection),
        "--shard", f"{idle}/6",
        "--profile", "affected",
        "--selection", "sha256:aaa",
        "--plan-shard",
    ])
    assert gate.main() == 0
    manifest = json.loads(junit.with_suffix(".json").read_text(encoding="utf-8"))
    assert manifest == {
        "shard": idle,
        "total": 6,
        "pytest_exit": 0,
        "selection": "sha256:aaa",
        "slice": 0,
    }
    assert ET.parse(junit).getroot().tag == "testsuites"


def test_the_heavy_list_is_not_held_against_coverage(tmp_path):
    """A selected file that lives in the heavy list runs in `heavy-tests`.

    It must not be required to report here, or every selection touching one
    would fail the gate for a file this job deliberately does not run.
    """
    heavy = tmp_path / "heavy.txt"
    heavy.write_text("tests/test_integration.py\n", encoding="utf-8")
    covered = gate.gating_selection(
        ["tests/test_ci_required_tests.py", "tests/test_integration.py"], str(heavy)
    )
    assert covered == ["tests/test_ci_required_tests.py"]


def test_a_module_scope_skip_never_removes_a_selected_file(tmp_path, monkeypatch):
    """The round-3 escape: absence of node ids is not proof of anything.

    `select` installs only `.[dev]`, so a browser test whose
    `pytest.importorskip` sits at MODULE scope collects nothing there. The first
    pruner read that as "all slow" and dropped the file; the shards -- which DO
    install Playwright -- then never saw it, and the browser no-skip assertion
    filters against the pruned selection and accepts an empty intersection. A
    broken non-slow browser test could land.

    Keeping it is free and self-correcting: the shards collect it, and if
    nothing reports it the coverage check fails and names it.
    """
    skipped = tmp_path / "tests"
    skipped.mkdir()
    (skipped / "test_module_scope_skip.py").write_text(
        'import pytest\n'
        'pytest.importorskip("a_module_that_is_not_installed")\n'
        'def test_never_collected():\n    assert True\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    runnable, prunable = gate.collectible_under_gate(["tests/test_module_scope_skip.py"])
    assert prunable == [], "a module-level skip is not proof the cases are slow"
    assert runnable == ["tests/test_module_scope_skip.py"]


def test_a_file_that_cannot_be_imported_is_kept(tmp_path, monkeypatch):
    """Same asymmetry: a collection error is a failure to report, not to hide."""
    broken = tmp_path / "tests"
    broken.mkdir()
    (broken / "test_broken.py").write_text("import a_module_that_is_not_installed\n",
                                           encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    runnable, prunable = gate.collectible_under_gate(["tests/test_broken.py"])
    assert prunable == []
    assert runnable == ["tests/test_broken.py"]


def test_slow_only_files_are_dropped_before_the_digest():
    """Finding 3 from the #4359 review, fixed at the source.

    The required shards run `-m "not slow"`. A selected file whose tests are
    ALL slow collects nothing here: pytest exits 5 and COVERAGE sees a file
    that reported no case. Neither is a regression -- `slow-tests` runs them --
    so the file leaves the selection before it is sharded or digested.
    """
    runnable, dropped = gate.collectible_under_gate(
        ["tests/test_node_bid_claim_stress.py", "tests/test_ci_required_tests.py"]
    )
    assert dropped == ["tests/test_node_bid_claim_stress.py"], (runnable, dropped)
    assert runnable == ["tests/test_ci_required_tests.py"]


def test_an_empty_selection_prunes_to_nothing_without_running_pytest():
    assert gate.collectible_under_gate([]) == ([], [])


def test_pruning_rewrites_the_file_and_falls_back_to_all_when_empty(tmp_path, monkeypatch):
    """Nothing collectible must not become a selective pass over an empty union."""
    path = tmp_path / "affected.txt"
    path.write_text("tests/test_node_bid_claim_stress.py\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "ci_required_tests.py", "--prune-to-collectible", str(path),
    ])
    assert gate.main() == 0
    assert path.read_text(encoding="utf-8").strip() == "ALL"


def test_pruning_an_all_selection_is_a_no_op(tmp_path, monkeypatch):
    path = tmp_path / "affected.txt"
    path.write_text("ALL\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "ci_required_tests.py", "--prune-to-collectible", str(path),
    ])
    assert gate.main() == 0
    assert path.read_text(encoding="utf-8").strip() == "ALL"
