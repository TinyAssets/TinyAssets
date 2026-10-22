"""Skip metadata may sit in a comment on or beside the marker."""

import datetime as dt
import os
import subprocess

import pytest

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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "c")
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {"tests/test_a.py": "def test_a():\n    assert True\n"})
    return tmp_path, base


def _unexplained(root, base, text):
    head = _commit(root, {"tests/test_a.py": text})
    delta = hygiene.compute_delta(base, head, root, dt.date(2026, 10, 21))
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
