"""The tamper check holds every Python test pytest collects, and a new skip says who and where."""

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_hygiene_gate as hygiene  # noqa: E402

_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}
_TODAY = dt.date(2026, 10, 21)
_FILE = "scripts/tests/test_a.py"
_ONE = "def test_a():\n    assert True\n"
_TWO = _ONE + "\n\ndef test_b():\n    assert True\n"


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True, env=_ENV
    ).stdout.strip()


def _commit(root, files):
    for rel, text in files.items():
        path = root / rel
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "c")
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {_FILE: _ONE})
    return tmp_path, base


def _unexplained(root, base, text):
    head = _commit(root, {_FILE: text})
    delta = hygiene.compute_delta(base, head, root, _TODAY)
    return [f.what for f in delta.unexplained_markers]


_HEAD = "import pytest\n\ndef test_a():\n    assert True\n\n"


@pytest.mark.verifies(
    "development-coordination-runtime:A new skip names its owner and where it runs"
)
def test_skip_metadata_on_the_following_line_passes(repo):
    root, base = repo
    text = _HEAD + (
        "@pytest.mark.skipif(\n"
        "    True,\n"
        '    reason="needs the oracle",\n'
        ")  # owner=c6-owner-box\n"
        "# runs-in=linux_oracle.py\n"
        "def test_new():\n"
        "    assert True\n"
    )
    assert _unexplained(root, base, text) == []


@pytest.mark.verifies(
    "development-coordination-runtime:A new skip names its owner and where it runs"
)
def test_skip_metadata_in_a_string_or_far_away_does_not_count(repo):
    root, base = repo
    text = _HEAD + (
        'NOTE = "# owner=x runs-in=y"\n'
        "\n"
        "\n"
        '@pytest.mark.skipif(True, reason="needs the oracle")\n'
        "def test_new():\n"
        "    assert True\n"
    )
    assert len(_unexplained(root, base, text)) == 1


def test_a_skip_that_ends_names_its_date_in_the_reason(repo):
    root, base = repo
    text = _HEAD + (
        '@pytest.mark.skip(reason="the fixture is rebuilt, owner=lane expires=2026-12-01")\n'
        "def test_new():\n"
        "    assert True\n"
    )
    assert _unexplained(root, base, text) == []


def test_a_case_deleted_beside_the_scripts_needs_a_stated_reason(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {_FILE: _TWO})
    head = _commit(tmp_path, {_FILE: _ONE})
    delta = hygiene.compute_delta(base, head, tmp_path, _TODAY)
    failures = hygiene.evaluate(delta, "")
    assert len(failures) == 1 and f"{_FILE}::test_b (test removed)" in failures[0]
    reason = "Test-Removal: retired -- test_b held a behaviour that is gone"
    assert hygiene.evaluate(delta, reason) == []


def test_a_script_named_like_a_test_is_not_a_test(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {"scripts/test_gate.py": _TWO})
    head = _commit(tmp_path, {"scripts/test_gate.py": None})
    assert hygiene.evaluate(hygiene.compute_delta(base, head, tmp_path, _TODAY), "") == []
