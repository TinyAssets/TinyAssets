"""A receipt survives test-only commits; skip metadata may follow the marker."""

import datetime as dt
import os
import subprocess

import pytest

from scripts import drain_review_gate as review
from scripts import test_hygiene_gate as hygiene

_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


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
        path.write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "c")
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(
        tmp_path,
        {
            "tinyassets/a.py": "A = 1\n",
            "tests/test_a.py": "def test_a():\n    assert True\n",
            "tests/conftest.py": "",
        },
    )
    reviewed = _commit(
        tmp_path, {"tinyassets/a.py": "A = 2\n", "tests/test_a.py": "def test_a():\n    assert 2\n"}
    )
    return tmp_path, base, reviewed


def _carries(root, base, reviewed, head):
    key = review.diff_key(base, head, cwd=root)
    return review.same_change_as(base, key, head=head, cwd=root)(reviewed)


@pytest.mark.verifies(
    "development-coordination-runtime:"
    "A review receipt is bound to the reviewed change, not the commit"
)
def test_test_only_followup_keeps_the_receipt(repo):
    root, base, reviewed = repo
    head = _commit(
        root,
        {
            "tests/test_a.py": "def test_a():\n    assert 2  # wrapped\n",
            "tests/unit/test_b.py": "def test_b():\n    assert 1\n",
        },
    )
    assert _carries(root, base, reviewed, head)


@pytest.mark.verifies(
    "development-coordination-runtime:"
    "A review receipt is bound to the reviewed change, not the commit"
)
@pytest.mark.parametrize(
    "files",
    [
        {"tinyassets/a.py": "A = 3\n"},
        {"tests/conftest.py": "import os\n"},
        {"tests/helpers.py": "X = 1\n"},
        {"tests/test_a.py": "def test_a():\n    assert True\n"},
        {"tests/test_shape_registry.py": "def test_x():\n    assert 1\n"},
    ],
    ids=["product", "conftest", "helper", "reviewed-test-reverted", "gate-test"],
)
def test_anything_else_voids_the_receipt(repo, files):
    root, base, reviewed = repo
    head = _commit(root, files)
    assert not _carries(root, base, reviewed, head)


@pytest.mark.verifies(
    "development-coordination-runtime:"
    "A review receipt is bound to the reviewed change, not the commit"
)
def test_a_key_for_a_different_head_never_carries(repo):
    root, base, reviewed = repo
    head = _commit(root, {"tests/test_a.py": "def test_a():\n    assert 3\n"})
    wrong_key = review.diff_key(base, reviewed, cwd=root)
    assert not review.same_change_as(base, wrong_key, head=head, cwd=root)(reviewed)


def _unexplained(root, base, text):
    head = _commit(root, {"tests/test_a.py": text})
    delta = hygiene.compute_delta(base, head, root, dt.date(2026, 10, 21))
    return [f.what for f in delta.unexplained_markers]


_HEAD = "import pytest\n\ndef test_a():\n    assert True\n\n"


@pytest.mark.verifies(
    "development-coordination-runtime:A new skip names its owner and where it runs"
)
def test_skip_metadata_on_the_following_line_passes(repo):
    root, base, _ = repo
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
    root, base, _ = repo
    text = _HEAD + (
        'NOTE = "# owner=x runs-in=y"\n'
        "\n"
        "\n"
        '@pytest.mark.skipif(True, reason="needs the oracle")\n'
        "def test_new():\n"
        "    assert True\n"
    )
    assert len(_unexplained(root, base, text)) == 1
