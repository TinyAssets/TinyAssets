import http.client
import importlib.util
import os
import re
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from scripts import linux_oracle as oracle

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_oracle_runsc.py"
spec = importlib.util.spec_from_file_location("prepare_oracle_runsc", SCRIPT)
runsc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runsc)

pytestmark = pytest.mark.verifies(
    "ci-linux-proof-venue:The required shards run the unchanged gate runner",
)


class Answer:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def flaky(failures, body=b"bytes"):
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        if len(calls) <= len(failures):
            raise failures[len(calls) - 1]
        return Answer(body)

    return fetch, calls


def status(code):
    return urllib.error.HTTPError("u", code, "status", {}, None)


def test_a_502_a_stall_and_a_cut_body_are_retried_and_the_bytes_come_back():
    failures = [status(502), TimeoutError("The read operation timed out"),
                http.client.IncompleteRead(b"part"), status(429)]
    fetch, calls = flaky(failures)
    waits = []
    assert runsc.fetched("u", fetch=fetch, sleep=waits.append) == b"bytes"
    assert len(calls) == 5
    assert waits == [2, 4, 8, 16]


def test_it_gives_up_after_its_attempts_with_the_last_failure():
    failures = [TimeoutError("stall")] * runsc.ATTEMPTS
    fetch, calls = flaky(failures)
    with pytest.raises(TimeoutError):
        runsc.fetched("u", fetch=fetch, sleep=lambda s: None)
    assert len(calls) == runsc.ATTEMPTS


@pytest.mark.parametrize("failure", [status(404), status(403), ValueError("no")])
def test_a_missing_release_or_any_other_fault_fails_at_once(failure):
    fetch, calls = flaky([failure])
    with pytest.raises(type(failure)):
        runsc.fetched("u", fetch=fetch, sleep=lambda s: None)
    assert len(calls) == 1


def test_prepare_retries_then_still_holds_the_bytes_to_the_pin(tmp_path):
    fetch, calls = flaky([status(503)], body=b"not the pinned archive")
    with pytest.raises(ValueError, match="archive digest mismatch"):
        runsc.prepare(tmp_path / "out", fetch=fetch, sleep=lambda s: None)
    assert len(calls) == 2


def test_the_runtime_prep_says_no_test_ran_when_it_stops():
    done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert done.returncode != 0
    assert f"{oracle.NO_TEST_RAN} the oracle's runtime was not prepared" in done.stderr


# The image build. Merge group run 44079286405's shard 5/6, verbatim but for the tag.
BASE = "mirror.gcr.io/library/python:3.11-slim@sha256:" + "a" * 64
REFUSED = [
    f"#2 [internal] load metadata for {BASE}\n",
    "#2 ERROR: unexpected status from HEAD request to https://mirror.gcr.io/v2/library/"
    "python/manifests/3.11-slim: 429 Too Many Requests\n",
]
UNAVAILABLE = [f"ERROR: failed to resolve source metadata for {BASE}: 503 Service Unavailable\n"]


class Docker:
    """`docker build`, answering each attempt with the next (exit code, output lines)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        self.returncode, lines = self.answers.pop(0)
        self.stdout = iter(lines)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def build(tmp_path, monkeypatch, answers):
    (tmp_path / "Dockerfile").write_text(f"FROM {BASE} AS builder\nRUN true\nFROM {BASE}\n")
    docker, waits = Docker(answers), []
    monkeypatch.setattr(oracle.subprocess, "Popen", docker)
    monkeypatch.setattr(oracle.time, "sleep", waits.append)
    try:
        oracle._build(tmp_path, "tinyassets-linux-oracle:test")
    except SystemExit as stop:
        return docker, waits, str(stop.code)
    return docker, waits, None


def test_a_busy_registry_is_retried_with_backoff_on_the_production_base(
        tmp_path, monkeypatch, capsys):
    docker, waits, stop = build(tmp_path, monkeypatch,
                                [(1, REFUSED), (1, UNAVAILABLE), (0, ["#15 DONE\n"])])
    assert stop is None and waits == [15, 30]
    assert all(["--build-arg", f"BASE={BASE}"] == c[2:4] for c in docker.commands)
    assert "429 Too Many Requests" in capsys.readouterr().out


def test_a_registry_still_busy_after_the_attempts_fails_loudly(tmp_path, monkeypatch, capsys):
    docker, waits, stop = build(tmp_path, monkeypatch, [(1, REFUSED)] * oracle.BUILD_ATTEMPTS)
    assert len(docker.commands) == oracle.BUILD_ATTEMPTS and waits == [15, 30, 60]
    assert stop.startswith(oracle.NO_TEST_RAN) and "429 Too Many Requests" in stop


@pytest.mark.parametrize("output", [
    ["E: Unable to locate package nodejs\n"],
    [f"ERROR: {BASE}: not found\n"],
    ["#9 ERROR: process \"pip install\" did not complete: 503 Service Unavailable\n"],
])
def test_a_real_build_error_is_never_retried(tmp_path, monkeypatch, capsys, output):
    docker, waits, stop = build(tmp_path, monkeypatch, [(1, output)])
    assert len(docker.commands) == 1 and waits == []
    assert stop == f"{oracle.NO_TEST_RAN} the image build failed (1)"


@pytest.mark.parametrize("bases", [
    ["mirror.gcr.io/library/python:3.11-slim"],
    [BASE, BASE.replace("a" * 64, "b" * 64)],
])
def test_the_production_image_must_name_one_pinned_base(tmp_path, bases):
    (tmp_path / "Dockerfile").write_text("".join(f"FROM {b}\n" for b in bases))
    with pytest.raises(SystemExit, match="one base pinned by digest"):
        oracle._base(tmp_path)


def test_a_new_production_base_rebuilds_the_image(tmp_path):
    tags = []
    for digest in ("a", "b"):
        (tmp_path / "docker").mkdir(exist_ok=True)
        (tmp_path / oracle.DOCKERFILE).write_text("ARG BASE\nFROM ${BASE}\n")
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "Dockerfile").write_text(f"FROM {BASE.replace('a' * 64, digest * 64)}\n")
        tags.append(oracle._image_tag(tmp_path))
    assert tags[0] != tags[1]


def test_the_oracle_image_names_no_base_of_its_own():
    root = SCRIPT.parents[1]
    assert "@sha256:" in oracle._base(root)
    text = (root / oracle.DOCKERFILE).read_text(encoding="utf-8")
    assert re.findall(r"^FROM\s+(\S+)", text, re.M) == ["${BASE}"]


def test_the_jail_probe_says_no_test_ran_when_it_refuses(tmp_path):
    fake = tmp_path / "bwrap"
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(0o755)
    done = subprocess.run(["bash", "-c", oracle.JAIL_PROBE], capture_output=True, text=True,
                          env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"})
    assert done.returncode == 3
    assert done.stderr.startswith(f"{oracle.NO_TEST_RAN} bubblewrap cannot create a jail")
