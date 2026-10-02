"""The test inventory measures the suite statically, and the ledger stays real."""

from __future__ import annotations

import ast
import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import test_inventory as inv  # noqa: E402

MARKED = """import pytest

pytestmark = [pytest.mark.skipif(True, reason="module guard runs-in=ci")]
np = pytest.importorskip("numpy")


@pytest.mark.skip(reason="pending expires=2026-11-01")
def test_a():
    pass


@pytest.mark.skipif(True)
def test_b():
    pytest.skip("no procfs on this host")


class TestC:
    @pytest.mark.xfail(reason="known broken")
    def test_c(self):
        pass
"""


def test_markers_are_found_with_scope_reason_and_expiry():
    found = inv.scan_markers(ast.parse(MARKED), "tests/test_m.py")
    by_kind = sorted((m.kind, m.scope, m.reason, m.expires) for m in found)
    assert by_kind == [
        ("importorskip", "call", "importorskip(numpy)", None),
        ("skip", "call", "no procfs on this host", None),
        ("skip", "function", "pending expires=2026-11-01", "2026-11-01"),
        ("skipif", "function", "<no reason; condition True>", None),
        ("skipif", "module", "module guard runs-in=ci", None),
        ("xfail", "function", "known broken", None),
    ]


def test_iter_tests_counts_functions_and_test_class_methods():
    names = [f.name for _, f in inv.iter_tests(ast.parse(MARKED))]
    assert names == ["test_a", "test_b", "test_c"]


def test_duplicate_bodies_include_decorators_and_arguments():
    one = ast.parse("@pytest.mark.parametrize('t', A)\ndef test_x(t):\n    assert t\n").body[0]
    two = ast.parse("@pytest.mark.parametrize('t', B)\ndef test_y(t):\n    assert t\n").body[0]
    same = ast.parse(
        "@pytest.mark.parametrize('t', A)\ndef test_z(t):\n    '''doc'''\n    assert t\n"
    ).body[0]
    assert inv._norm_body(one) != inv._norm_body(two)
    assert inv._norm_body(one) == inv._norm_body(same)


def test_a_pinned_literal_groups_across_list_and_set_spellings():
    a = ast.parse("assert x == ['a','b','c','d','e','f']")
    b = ast.parse("assert {'f','e','d','c','b','a'} == y")
    short = ast.parse("assert x == ['a','b']")
    [(_, n, da)] = list(inv.pinned_lists(a))
    [(_, _, db)] = list(inv.pinned_lists(b))
    assert n == 6 and da == db
    assert list(inv.pinned_lists(short)) == []


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def fake_repo(tmp_path):
    root = tmp_path / "r"
    files = {
        "tinyassets/__init__.py": "",
        "tinyassets/live.py": "def used():\n    return 1\n",
        "tinyassets/orphan.py": "def lonely():\n    return 2\n",
        "tinyassets/late.py": "def setup():\n    global BOUND\n    BOUND = 1\n",
        "tinyassets/ns/mod.py": "X = 1\n",  # namespace package: no __init__
        "tinyassets/app.py": (
            "from tinyassets.live import used\n"
            "from tinyassets.ns import mod\n"
            "from tinyassets import late\n"
        ),
        "tests/__init__.py": "",
        "tests/test_live.py": (
            "from tinyassets.live import used\n"
            "from tinyassets.late import BOUND\n\n\n"
            "def test_used():\n    assert used() == 1\n"
        ),
        "tests/test_orphan.py": (
            "from tinyassets.orphan import lonely\n\n\n"
            "def test_lonely():\n    assert lonely() == 2\n"
        ),
        "tests/test_dead.py": (
            "import pytest\n\n\ndef test_gone():\n"
            "    from tinyassets.removed import thing\n"
            "    from tinyassets.live import vanished\n"
        ),
        ".github/known-failing-tests.txt": (
            "tests/test_live.py::test_used\n"
            "tests/test_live.py::test_renamed_away\n"
            "tests/test_live.py::tests.test_live\n"
            "tests/test_gone.py::test_x\n"
        ),
        "pyproject.toml": "[project]\nname='x'\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "c")
    return root


def test_build_finds_dead_targets_and_modules_only_tests_reach(fake_repo):
    report = inv.build(fake_repo, [], blame=False, today=dt.date(2026, 10, 1))
    dead = sorted((d["target"], d["why"]) for d in report["dead_imports"])
    # `BOUND` is bound by a `global` statement and `ns` is a namespace
    # package: neither may be reported dead.
    assert dead == [
        ("tinyassets.live.vanished", "name missing"),
        ("tinyassets.removed", "module missing"),
    ]
    assert [m["module"] for m in report["test_only_modules"]] == ["tinyassets.orphan"]
    assert report["totals"]["files"] == 3 and report["totals"]["tests"] == 3


def test_known_failing_entries_are_checked_against_the_tree(fake_repo):
    entries = {e["node"]: e for e in inv.known_failing(fake_repo, blame=False)}
    assert entries["tests/test_live.py::test_used"]["test_exists"]
    assert not entries["tests/test_live.py::test_renamed_away"]["test_exists"]
    # A collection-error id names a module, not a function.
    assert entries["tests/test_live.py::tests.test_live"]["test_exists"]
    assert not entries["tests/test_gone.py::test_x"]["file_exists"]


def test_every_ledger_entry_names_a_test_that_exists():
    """A ledger line for a deleted test can never go stale -- the gate only
    marks an entry stale when it RUNS -- so it would sit forever. Two such
    lines did, until 2026-10-01."""
    gone = [e["node"] for e in inv.known_failing(REPO, blame=False) if not e["test_exists"]]
    assert gone == [], f"delete these lines from .github/known-failing-tests.txt: {gone}"


def test_markdown_renders_from_a_real_report(fake_repo):
    text = inv.to_markdown(inv.build(fake_repo, [], blame=False, today=dt.date(2026, 10, 1)))
    assert "files **3**" in text and "Known-failing" in text


def test_needs_action_reports_dates_and_stale_ledger_lines_without_gating():
    report = {
        "generated_for": "2026-10-01",
        "markers": [
            {
                "file": "tests/a.py",
                "line": 1,
                "kind": "skip",
                "reason": "x expires=2026-09-01",
                "expires": "2026-09-01",
            },
            {
                "file": "tests/b.py",
                "line": 2,
                "kind": "skip",
                "reason": "y expires=2026-10-06",
                "expires": "2026-10-06",
            },
            {
                "file": "tests/c.py",
                "line": 3,
                "kind": "skip",
                "reason": "z expires=2027-01-01",
                "expires": "2027-01-01",
            },
        ],
        "known_failing": [
            {"node": "tests/a.py::t1", "test_exists": False},
            {"node": "tests/a.py::t2", "test_exists": True, "ci_status": "passing"},
            {"node": "tests/a.py::t3", "test_exists": True, "ci_status": "failing"},
        ],
        "dead_imports": [],
        "ci": {
            "skipped_in_ci": [
                {"node": "n1", "reason": "needs bwrap"},
                {"node": "n2", "reason": "needs bwrap"},
            ]
        },
    }
    items = inv.needs_action(report)
    assert any("tests/a.py:1" in i and "EXPIRED 30d" in i for i in items)
    assert any("tests/b.py:2" in i and "expires in 5d" in i for i in items)
    assert not any("tests/c.py" in i for i in items)
    assert any("t1" in i and "no longer exists" in i for i in items)
    assert any("t2" in i and "PASSES" in i for i in items)
    assert not any("t3" in i for i in items)
    assert any(i.startswith("2 test(s) skipped in CI") for i in items)


SPELLINGS = """import pytest as p
import unittest
from pytest import skip as sk


@p.mark.skip(reason="via alias")
def test_a():
    sk("via from-import")


@p.mark.parametrize("x", [p.param(1, marks=p.mark.xfail), p.param(2, marks=[p.mark.skip])])
def test_b(x):
    raise unittest.SkipTest("raised")
"""


def test_aliased_bare_and_raised_suppressions_are_all_seen():
    kinds = sorted((m.kind, m.scope, m.reason) for m in inv.scan_markers(ast.parse(SPELLINGS), "t"))
    assert kinds == [
        ("skip", "call", "raised"),
        ("skip", "call", "via from-import"),
        ("skip", "function", "via alias"),
        ("skip", "param", ""),
        ("xfail", "param", ""),
    ]


RESOLVE = """import pytest
from helpers import test_imported


class Base:
    def test_inherited(self):
        pass


class TestKid(Base):
    pass


class TestPlain:
    def test_here(self):
        pass


def test_mod(x):
    pass
"""


@pytest.mark.parametrize(
    "rest, exists",
    [
        ("test_mod", True),
        ("test_mod[a::b]", True),  # a parameter id may contain '::'
        ("test_mod[x]", True),
        ("test_gone[a::b]", False),
        ("TestPlain::test_here", True),
        ("TestPlain::test_gone", False),
        ("TestGone::test_here", False),
        ("TestKid::test_inherited", True),  # inherited: not provably gone
        ("TestKid::test_anything", True),
        ("test_imported", True),  # imported into the module
        ("tests.test_r", True),  # a collection-error id names the module
    ],
)
def test_ledger_ids_resolve_conservatively(tmp_path, rest, exists):
    f = tmp_path / "test_r.py"
    f.write_text(RESOLVE, encoding="utf-8")
    assert inv.node_resolves(f, rest) is exists


def test_a_passing_flaky_entry_is_not_advised_for_deletion():
    report = {
        "generated_for": "2026-10-01",
        "markers": [],
        "dead_imports": [],
        "known_failing": [
            {"node": "tests/a.py::t", "test_exists": True, "ci_status": "passing", "flaky": True}
        ],
    }
    assert inv.needs_action(report) == []


def test_calendar_reads_and_short_sleeps_are_counted_for_the_weekly_report():
    tree = ast.parse(
        "def test_a():\n    datetime.now()\n    time.sleep(0.1)\n"
        "    time.sleep(2)\n    time.sleep(0)\n"
    )
    assert inv.soft_hermetic_calls(tree) == 2
