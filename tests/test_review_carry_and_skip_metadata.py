"""A receipt survives metadata-only test commits; skip metadata may follow the marker."""

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
        path.write_bytes(text.encode("utf-8"))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "c")
    return _git(root, "rev-parse", "HEAD")


_M = (
    "import pytest\n"
    "\n"
    "# a note\n"
    "@pytest.mark.verifies(\n"
    '    "spec:Old"\n'
    ")\n"
    "def test_m():\n"
    '    TEXT = """\n'
    "# inside a string\n"
    '"""\n'
    "    X = (1\n"
    "    )\n"
    "    assert TEXT and X\n"
    "\n"
    "\n"
    "@pytest.mark.skipif(\n"
    "    True,\n"
    '    reason="old reason",\n'
    ")\n"
    "def test_s():\n"
    "    assert True\n"
    "\n"
    "\n"
    '@pytest.mark.xfail(True, reason="old", strict=True)\n'
    "def test_x():\n"
    '    check(reason="a")  # trailing\n'
)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(
        tmp_path,
        {
            "tinyassets/a.py": "A = 1\n",
            "tests/test_a.py": "def test_a():\n    assert True\n",
            "tests/conftest.py": "",
            "tests/helpers.py": "X = 1\n",
            "tests/test_m.py": _M,
            "tests/test_pm.py": 'import pytest\n\npytestmark = pytest.mark.skip(reason="x")\n',
        },
    )
    reviewed = _commit(
        tmp_path, {"tinyassets/a.py": "A = 2\n", "tests/test_a.py": "def test_a():\n    assert 2\n"}
    )
    return tmp_path, base, reviewed


def _carries(root, base, reviewed, head):
    key = review.diff_key(base, head, cwd=root)
    return review.same_change_as(base, key, head=head, cwd=root)(reviewed)


def _m(old, new, count=1):
    assert old in _M
    return {"tests/test_m.py": _M.replace(old, new, count)}


@pytest.mark.verifies(
    "development-coordination-runtime:"
    "A review receipt is bound to the reviewed change, not the commit"
)
@pytest.mark.parametrize(
    "files",
    [
        _m('"spec:Old"', '"spec:New",\n    "spec:Other"'),
        _m("def test_x", '@pytest.mark.verifies("spec:X")\ndef test_x'),
        _m("@pytest.mark.verifies(\n", '@pytest.mark.verifies(\n    "spec:Extra",\n'),
        _m("import pytest\n", 'import pytest\npytestmark = pytest.mark.verifies("spec:M")\n'),
        _m('reason="old reason"', 'reason="new reason owner=x"'),
        _m('reason="old",', 'reason="runs-in=linux-ci",'),
        _m("# a note\n", "# a better note\n#   and more\n"),
        _m("# a note\n", ""),
        {"tests/test_a.py": "# reviewed file, now commented\ndef test_a():\n    assert 2\n"},
        {
            **_m('reason="old reason",', 'reason="r",  # owner=x'),
            "tests/test_a.py": "def test_a():\n    # why\n    assert 2\n",
        },
    ],
    ids=[
        "verifies-ids", "verifies-decorator-added", "verifies-continuation", "pytestmark-added",
        "skip-reason", "xfail-reason", "comment-edit", "comment-removed",
        "comment-in-reviewed-file", "several-files",
    ],
)
def test_a_metadata_only_commit_keeps_the_receipt(repo, files):
    root, base, reviewed = repo
    head = _commit(root, files)
    assert _carries(root, base, reviewed, head)


# Bypasses from both reviews of PR #5201, as a new file and as lines in an
# existing test; each must void the receipt.
_BYPASSES = {
    "alias": "import tinyassets\nm = tinyassets\nm.check = lambda *a: True\n",
    "setitem": "import operator, os\noperator.setitem(os.environ, 'K', 'v')\n",
    "annotation": "import operator, os\ndef f(a: operator.setitem(os.environ, 'K', 'v')): pass\n",
    "monkeypatch": "import os, pytest\n"
    "mp = pytest.MonkeyPatch()\nmp.setattr(os, 'getuid', lambda: 0)\n",
    "environ": "import os\nos.environ['K'] = 'v'\n",
    "setattr": "import os\nsetattr(os, 'sep', 'x')\n",
    "global": "def test_b():\n    global G\n",
    "default-arg-call": "import os\ndef test_b(x=os.chdir('/')):\n    assert 1\n",
    "star-import": "from os import *\n",
    "inert-test": "def test_b():\n    assert 1\n",
}


@pytest.mark.verifies(
    "development-coordination-runtime:"
    "A review receipt is bound to the reviewed change, not the commit"
)
@pytest.mark.parametrize(
    "files",
    [
        *({"tests/test_aaa_new.py": text} for text in _BYPASSES.values()),
        *(_m("@pytest.mark.skipif(", text + "@pytest.mark.skipif(") for text in _BYPASSES.values()),
        {"tinyassets/a.py": "A = 3\n"},
        {"tests/conftest.py": "import os\n"},
        {"tests/helpers.py": "X = 2\n"},
        {"tests/test_a.py": "def test_a():\n    assert True\n"},
        {"tests/test_a.py": None},
        {"tests/test_m.py": None},
        {"tests/test_unit/test_new_comment.py": "# only a comment\n"},
        _m("    True,\n", "    False,\n"),
        _m("strict=True", "strict=False"),
        _m('reason="old"', 'reason=f"{print()}"'),
        _m('reason="old reason"', 'reason="x" + str(print())'),
        _m('check(reason="a")', 'check(reason="b")'),
        _m("# inside a string", "# changed inside a string"),
        _m("    X = (1\n", '    X = (1\n    @pytest.mark.verifies("spec:Y")\n'),
        _m('"spec:Old"', "str(print())"),
        _m("# a note\n", "# a note\n\n"),
        _m("  # trailing", "  # changed trailing"),
        _m('@pytest.mark.xfail(True, reason="old", strict=True)\n', ""),
        _m("import pytest\n", "# -*- coding: utf-7 -*-\nimport pytest\n"),
        {
            "tests/test_pm.py": 'import pytest\n\npytestmark = pytest.mark.skip(reason="x")\n'
            'pytestmark = pytest.mark.verifies("spec:A")\n'
        },
    ],
    ids=[
        *(f"new-file-{name}" for name in _BYPASSES),
        *(f"lines-{name}" for name in _BYPASSES),
        "product", "conftest", "helper", "reviewed-test-reverted", "reviewed-test-deleted",
        "test-deleted", "new-comment-only-file", "skip-condition", "xfail-strict",
        "reason-fstring", "reason-expression", "reason-of-other-call", "comment-in-string",
        "verifies-inside-parens", "verifies-non-literal", "blank-line", "trailing-comment",
        "marker-removed", "coding-cookie", "pytestmark-overrides-skip",
    ],
)
def test_anything_else_voids_the_receipt(repo, files):
    root, base, reviewed = repo
    head = _commit(root, files)
    assert not _carries(root, base, reviewed, head)


@pytest.mark.verifies(
    "development-coordination-runtime:"
    "A review receipt is bound to the reviewed change, not the commit"
)
def test_a_mode_change_voids_the_receipt(repo):
    root, base, reviewed = repo
    _git(root, "update-index", "--chmod=+x", "tests/test_m.py")
    _git(root, "commit", "-q", "-m", "x")
    assert not _carries(root, base, reviewed, _git(root, "rev-parse", "HEAD"))


@pytest.mark.verifies(
    "development-coordination-runtime:"
    "A review receipt is bound to the reviewed change, not the commit"
)
def test_a_key_for_a_different_head_never_carries(repo):
    root, base, reviewed = repo
    head = _commit(root, {"tests/test_a.py": "# note\ndef test_a():\n    assert 2\n"})
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
