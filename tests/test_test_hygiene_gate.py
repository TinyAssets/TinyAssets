"""The PR-relative test hygiene gate judges what a PR changes, never main's calendar."""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import test_hygiene_gate as gate  # noqa: E402
import test_inventory as inv  # noqa: E402

TODAY = dt.date(2026, 10, 1)
WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _marker(reason: str, kind: str = "skip") -> inv.Marker:
    return inv.Marker("tests/test_x.py", 3, kind, reason, "function")


# ---- which new markers need an owner and an end --------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        "Windows refuses to rename a folder in use",
        "real stage requires Linux bubblewrap",
        "shellcheck not installed",
        "git not available",
    ],
)
def test_an_environment_guard_needs_nothing_more(reason):
    assert gate.marker_problem(_marker(reason), TODAY) is None


@pytest.mark.parametrize(
    "reason",
    [
        "wiki file_bug patches not landed yet",
        "awaits live persona-step replay implementation per host directive",
        "TINYASSETS_TEST_POSTGRES_DSN is required for PostgreSQL proof",
        "Linux implementation pending",  # pending wording beats the platform word
    ],
)
def test_a_suppression_needs_an_owner_and_an_end(reason):
    assert "owner" in gate.marker_problem(_marker(reason), TODAY)
    assert "expires" in gate.marker_problem(_marker(reason + " owner=dev"), TODAY)
    assert gate.marker_problem(_marker(reason + " owner=dev expires=2026-12-01"), TODAY) is None
    assert gate.marker_problem(_marker(reason + " owner=dev runs-in=pg-proof"), TODAY) is None


def test_an_expiry_must_be_a_near_future_date():
    def why(date):
        return gate.marker_problem(_marker(f"pending owner=a expires={date}"), TODAY)

    assert "already past" in why("2026-09-30")
    assert "days out" in why("2027-06-01")
    assert "not a date" in why("2026-13-40")
    assert why("2026-10-01") is None


def test_an_xfail_is_never_an_environment_guard():
    assert gate.marker_problem(_marker("broken on Linux", kind="xfail"), TODAY) is not None


# ---- hermeticity of NEW tests ---------------------------------------------------


def _findings(src: str) -> tuple[list[str], list[str]]:
    import ast

    tree = ast.parse(src)
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    block, report = gate.hermeticity(func, src.splitlines())
    return [w for _, w in block], [w for _, w in report]


@pytest.mark.parametrize(
    "src, blocked",
    [
        ("def test_a():\n    time.sleep(1)\n", "real sleep"),
        ("def test_a():\n    time.sleep(0.5)\n", "real sleep"),
        ("def test_a():\n    time.sleep(0.5)  # hermetic-ok: lock timing\n", None),
        # The escape hatch is a comment with a reason, not a bare substring.
        ("def test_a():\n    time.sleep(0.5)  # hermetic-ok\n", "real sleep"),
        ("def test_a():\n    hermetic_ok = time.sleep(1)\n", "real sleep"),
        # A copy's DESTINATION is its second argument.
        ("def test_a(tmp_path):\n    shutil.copy('/fixture.txt', tmp_path / 'x')\n", None),
        ("def test_a(tmp_path):\n    shutil.copy(tmp_path / 'x', '/etc/x')\n", "/etc/x"),
        ("def test_a():\n    urllib.request.urlopen(u)\n", "network"),
        ("def test_a():\n    (Path.home() / '.cfg').write_text('x')\n", "Path.home"),
        ("def test_a():\n    Path.cwd().joinpath('out').mkdir()\n", "Path.cwd"),
        ("def test_a():\n    Path('/var/tmp/x').write_bytes(b'')\n", "/var/tmp/x"),
        ("def test_a():\n    open('/etc/x', 'w')\n", "/etc/x"),
        ("def test_a():\n    shutil.rmtree(os.path.expanduser('~/x'))\n", "expanduser"),
        # Reading a real location, or writing under tmp_path, is not blocked.
        ("def test_a():\n    open('/etc/passwd')\n", None),
        ("def test_a():\n    assert Path.home().exists()\n", None),
        ("def test_a(tmp_path):\n    (tmp_path / 'x').write_text('y')\n", None),
        ("def test_a(tmp_path):\n    open(tmp_path / 'x', 'w')\n", None),
    ],
)
def test_new_tests_are_blocked_only_for_what_flakes_or_pollutes(src, blocked):
    block, _ = _findings(src)
    if blocked is None:
        assert block == []
    else:
        assert len(block) == 1 and blocked in block[0]


@pytest.mark.parametrize(
    "src",
    [
        "def test_a():\n    time.sleep(0.05)\n",
        "def test_a():\n    x = datetime.now()\n",
        "def test_a():\n    date.today()\n",
    ],
)
def test_calendar_reads_and_short_sleeps_are_reported_not_blocked(src):
    block, report = _findings(src)
    assert block == [] and len(report) == 1


# ---- against a real git history -------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "r"
    (root / "tests").mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "core.autocrlf", "false")
    return root


def _commit(repo: Path, files: dict[str, str | None]) -> str:
    for rel, text in files.items():
        if text is None:
            _git(repo, "rm", "-q", rel)
            continue
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", "c", "--allow-empty")
    return _git(repo, "rev-parse", "HEAD")


BASE = """import pytest


def test_keep():
    assert 1 == 1
    assert 2 == 2


def test_other():
    assert 3 == 3
"""


def _run(repo, base, head, body=""):
    delta = gate.compute_delta(base, head, repo, TODAY)
    return delta, gate.evaluate(delta, body)


REMOVAL = "Test-Removal: wrong -- it asserted the retired v1 envelope shape"


def test_removing_an_existing_test_needs_a_stated_reason(repo):
    base = _commit(repo, {"tests/test_a.py": BASE})
    head = _commit(repo, {"tests/test_a.py": BASE.split("\n\ndef test_other")[0] + "\n"})
    delta, failures = _run(repo, base, head)
    assert delta.removed_tests == 1
    assert failures and "Test-Removal" in failures[0]
    assert _run(repo, base, head, REMOVAL)[1] == []


def test_loosening_assertions_is_tampering(repo):
    base = _commit(repo, {"tests/test_a.py": BASE})
    head = _commit(repo, {"tests/test_a.py": BASE.replace("    assert 2 == 2\n", "")})
    _, failures = _run(repo, base, head)
    assert failures and "loosened (2 -> 1)" in failures[0]


def test_skipping_an_existing_test_is_tampering(repo):
    base = _commit(repo, {"tests/test_a.py": BASE})
    skipped = BASE.replace(
        "def test_other",
        '@pytest.mark.skip(reason="flaky owner=a expires=2026-10-20")\ndef test_other',
    )
    head = _commit(repo, {"tests/test_a.py": skipped})
    _, failures = _run(repo, base, head)
    assert failures and "already existed" in failures[0]


def test_a_feature_pr_may_not_carry_test_removal(repo):
    base = _commit(repo, {"tests/test_a.py": BASE, "tinyassets/x.py": "A = 1\n"})
    head = _commit(
        repo,
        {
            "tests/test_a.py": BASE.split("\n\ndef test_other")[0] + "\n",
            "tinyassets/x.py": "A = 1\nB = 2\n",
        },
    )
    _, failures = _run(repo, base, head, REMOVAL)
    assert failures and "Split the test change" in failures[0]
    # Saying `retired` is not enough while the PR only ADDS product code.
    retired = "Test-Removal: retired -- the v1 envelope and its reader are gone"
    assert _run(repo, base, head, retired)[1]


def test_retiring_a_behaviour_deletes_its_code_and_its_tests_together(repo):
    base = _commit(repo, {"tests/test_a.py": BASE, "tinyassets/x.py": "A = 1\nOLD = 2\n"})
    head = _commit(
        repo,
        {
            "tests/test_a.py": BASE.split("\n\ndef test_other")[0] + "\n",
            "tinyassets/x.py": "A = 1\nNEW = 3\n",
        },
    )
    retired = "Test-Removal: retired -- OLD is gone, and so is the test of it"
    assert _run(repo, base, head, retired)[1] == []


def test_retired_never_covers_weakening_a_surviving_test(repo):
    base = _commit(repo, {"tests/test_a.py": BASE, "tinyassets/x.py": "A = 1\nOLD = 2\n"})
    head = _commit(
        repo,
        {
            "tests/test_a.py": BASE.replace("    assert 2 == 2\n", ""),
            "tinyassets/x.py": "A = 1\nNEW = 3\n",
        },
    )
    retired = "Test-Removal: retired -- OLD is gone, and so is the test of it"
    failures = _run(repo, base, head, retired)[1]
    assert failures and "Split the test change" in failures[0]


def test_moving_tests_into_a_file_pytest_never_collects_is_removal(repo):
    base = _commit(repo, {"tests/test_a.py": BASE})
    _git(repo, "mv", "tests/test_a.py", "tests/helpers.py")
    _git(repo, "commit", "-q", "-m", "hide")
    head = _git(repo, "rev-parse", "HEAD")
    delta, failures = _run(repo, base, head)
    assert delta.removed_tests == 2 and failures


def test_a_body_copied_into_a_helper_module_does_not_count_as_a_move(repo):
    base = _commit(repo, {"tests/test_a.py": BASE})
    head = _commit(
        repo,
        {
            "tests/test_a.py": BASE.split("\n\ndef test_other")[0] + "\n",
            "tests/helpers.py": "def test_other():\n    assert 3 == 3\n",
        },
    )
    delta, failures = _run(repo, base, head)
    assert delta.removed_tests == 1 and failures


def test_a_surviving_copy_never_vouches_for_a_deleted_test(repo):
    dup = BASE + "\n\ndef test_copy():\n    assert 3 == 3\n"
    base = _commit(repo, {"tests/test_a.py": dup})
    head = _commit(
        repo, {"tests/test_a.py": dup.replace("\n\ndef test_copy():\n    assert 3 == 3\n", "")}
    )
    delta, _ = _run(repo, base, head)
    assert delta.removed_tests == 1


def test_moving_a_test_to_another_file_is_not_removal(repo):
    other = "\n\ndef test_other():\n    assert 3 == 3\n"
    base = _commit(repo, {"tests/test_a.py": BASE})
    head = _commit(
        repo,
        {
            "tests/test_a.py": BASE.split("\n\ndef test_other")[0] + "\n",
            "tests/test_b.py": "import pytest\n" + other,
        },
    )
    delta, failures = _run(repo, base, head)
    assert delta.removed_tests == 0 and failures == []


def test_a_new_env_guarded_test_is_fine_and_needs_no_reason(repo):
    base = _commit(repo, {"tests/test_a.py": BASE})
    new = BASE + (
        '\n\n@pytest.mark.skipif(True, reason="needs Linux bubblewrap")\n'
        "def test_new(tmp_path):\n    assert tmp_path\n"
    )
    head = _commit(repo, {"tests/test_a.py": new})
    delta, failures = _run(repo, base, head)
    assert delta.new_tests == 1 and failures == []


def test_a_new_pending_skip_needs_owner_and_end(repo):
    base = _commit(repo, {"tests/test_a.py": BASE})
    new = BASE + (
        '\n\n@pytest.mark.skip(reason="feature not landed yet")\ndef test_new():\n    assert True\n'
    )
    head = _commit(repo, {"tests/test_a.py": new})
    _, failures = _run(repo, base, head)
    assert len(failures) == 1 and "owner" in failures[0]


def test_an_expired_marker_blocks_only_prs_that_touch_its_file(repo):
    expired = BASE.replace(
        "def test_other",
        '@pytest.mark.skip(reason="pending owner=a expires=2026-09-01")\ndef test_other',
    )
    base = _commit(repo, {"tests/test_a.py": expired, "tests/test_b.py": BASE})
    untouched = _commit(repo, {"tests/test_b.py": BASE + "\n\ndef test_z():\n    assert 1\n"})
    assert _run(repo, base, untouched)[1] == []
    touched = _commit(repo, {"tests/test_a.py": expired + "\n\ndef test_z():\n    assert 1\n"})
    failures = _run(repo, untouched, touched)[1]
    assert len(failures) == 1 and "expired 2026-09-01" in failures[0]


def test_adding_a_known_failing_entry_is_tampering(repo):
    base = _commit(repo, {"tests/test_a.py": BASE, ".github/known-failing-tests.txt": "# none\n"})
    head = _commit(repo, {".github/known-failing-tests.txt": "tests/test_a.py::test_keep\n"})
    failures = _run(repo, base, head)[1]
    assert failures and "known-failing entry" in failures[0]


def test_extending_a_quarantine_expiry_is_a_change_not_a_comment(repo):
    line = "flaky owner=a expires=2026-10-05 tests/test_a.py::test_keep\n"
    base = _commit(repo, {"tests/test_a.py": BASE, ".github/known-failing-tests.txt": line})
    head = _commit(
        repo, {".github/known-failing-tests.txt": line.replace("2026-10-05", "2026-12-05")}
    )
    assert _run(repo, base, head)[1]


def test_a_new_unhermetic_test_fails_but_an_old_one_is_not_judged(repo):
    old = BASE.replace("    assert 3 == 3", "    time.sleep(2)\n    assert 3 == 3")
    base = _commit(repo, {"tests/test_a.py": old})
    head = _commit(
        repo,
        {
            "tests/test_a.py": old
            + "\n\ndef test_new():\n    time.sleep(1)\n\n\ndef test_note():\n    date.today()\n"
        },
    )
    failures = _run(repo, base, head)[1]
    assert len(failures) == 1 and "test_new" in failures[0] and "real sleep" in failures[0]


def test_main_moving_under_the_pr_is_not_credited_to_it(repo):
    """Main adds a test after the PR branched. Diffed against current main the
    PR would read as deleting it; against the merge base it deletes nothing."""
    base = _commit(repo, {"tests/test_a.py": BASE})
    _git(repo, "checkout", "-q", "-b", "pr")
    head = _commit(repo, {"tests/test_a.py": BASE + "\n\ndef test_pr():\n    assert 1\n"})
    _git(repo, "checkout", "-q", "-")
    main = _commit(repo, {"tests/test_a.py": BASE + "\n\ndef test_main():\n    assert 2\n"})
    delta, failures = _run(repo, main, head)
    assert delta.removed_tests == 0 and failures == []
    assert base


def test_cli_exit_codes(repo):
    base = _commit(repo, {"tests/test_a.py": BASE})
    head = _commit(repo, {"tests/test_a.py": BASE + "\n\ndef test_more():\n    assert 1\n"})
    argv = ["--base", base, "--head", head, "--root", str(repo), "--today", "2026-10-01"]
    assert gate.main(argv) == 0
    assert gate.main(["--base", "nope", "--head", head, "--root", str(repo)]) == 2


# ---- wiring -----------------------------------------------------------------------


def test_the_gate_runs_in_the_scope_guard_from_the_trusted_base():
    text = (WORKFLOWS / "pr-scope-guard.yml").read_text(encoding="utf-8")
    assert "python scripts/test_hygiene_gate.py" in text
    assert '--base "${BASE_OID}" --head "${HEAD_OID}"' in text
    assert "PR_BODY: ${{ github.event.pull_request.body }}" in text


def test_the_weekly_inventory_rewrites_one_issue():
    text = (WORKFLOWS / "quarantine-oracle.yml").read_text(encoding="utf-8")
    assert "schedule:" in text and "python scripts/test_inventory.py" in text
    assert "gh issue edit" in text and "issues: write" in text
