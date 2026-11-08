import http.client
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
