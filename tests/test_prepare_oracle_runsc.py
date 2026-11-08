import importlib.util
import urllib.error
from pathlib import Path

import pytest

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


def test_a_502_or_a_stall_is_retried_and_the_bytes_come_back():
    failures = [urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None),
                TimeoutError("The read operation timed out")]
    fetch, calls = flaky(failures)
    waits = []
    assert runsc.fetched("u", fetch=fetch, sleep=waits.append) == b"bytes"
    assert len(calls) == 3
    assert waits == [2, 4]


def test_it_gives_up_after_its_attempts_with_the_last_failure():
    failures = [TimeoutError("stall")] * runsc.ATTEMPTS
    fetch, calls = flaky(failures)
    with pytest.raises(TimeoutError):
        runsc.fetched("u", fetch=fetch, sleep=lambda s: None)
    assert len(calls) == runsc.ATTEMPTS


def test_anything_else_is_not_retried():
    fetch, calls = flaky([ValueError("not a network failure")])
    with pytest.raises(ValueError):
        runsc.fetched("u", fetch=fetch, sleep=lambda s: None)
    assert len(calls) == 1
