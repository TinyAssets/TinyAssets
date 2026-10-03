"""The leak probe blames the test that leaked, not the one running when GC closed it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

LEAKY = '''
import sqlite3
import threading

_KEEP = []


def test_leaks_a_connection(tmp_path):
    conn = sqlite3.connect(tmp_path / "x.db")
    conn.execute("select 1")
    conn = None  # dropped without close: finalized at the next gc


def test_clean(tmp_path):
    with open(tmp_path / "f", "w") as fh:
        fh.write("x")


def test_leaves_a_thread():
    stop = threading.Event()
    t = threading.Thread(target=stop.wait, name="probe-left-behind", daemon=True)
    t.start()
    _KEEP.append(stop)
'''


def _probe(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess:
    (tmp_path / "test_leaky.py").write_text(LEAKY, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p",
         "tests.leak_probe_plugin", "--rootdir", str(tmp_path), "-c", os.devnull,
         str(tmp_path / "test_leaky.py"), *extra],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )


def test_the_leaking_test_is_named_and_the_clean_one_is_not(tmp_path):
    out = tmp_path / "leaks.json"
    run = _probe(tmp_path, "--leak-probe-out", str(out))
    assert run.returncode == 0, run.stdout + run.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    resource = {k.split("::")[-1]: v for k, v in data["resource"].items()}
    threads = {k.split("::")[-1]: v for k, v in data["threads"].items()}
    # sqlite emits the warning only on Python 3.13+; a file handle would on any.
    if sys.version_info >= (3, 13):
        assert list(resource) == ["test_leaks_a_connection"]
    assert "test_clean" not in resource
    assert threads == {"test_leaves_a_thread": ["probe-left-behind"]}


def test_the_plugin_is_inert_without_its_flag(tmp_path):
    run = _probe(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not list(tmp_path.glob("*.json"))
