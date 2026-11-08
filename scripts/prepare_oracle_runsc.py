"""Fetch the production-pinned runtime into a disposable oracle output directory."""

import hashlib
import http.client
import io
import re
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# GitHub's release download answers 502 or stalls now and then; every required
# shard fetches here first, so one bad answer used to fail the whole run before
# any test ran. The archive is pinned by digest, so a retry can only ever give
# the same bytes or fail the digest check below. A 4xx other than 408 or 429
# (a missing pinned release, a refusal) is a real fault and fails at once.
ATTEMPTS = 5
FIRST_WAIT_S = 2
TIMEOUT_S = 60


def passing(failure):
    if isinstance(failure, urllib.error.HTTPError):
        return failure.code >= 500 or failure.code in (408, 429)
    return isinstance(failure, (OSError, http.client.HTTPException))


def fetched(url, *, fetch, sleep):
    for attempt in range(ATTEMPTS):
        try:
            with fetch(url, timeout=TIMEOUT_S) as response:
                return response.read()
        except Exception as failure:
            if attempt == ATTEMPTS - 1 or not passing(failure):
                raise
            wait = FIRST_WAIT_S * 2 ** attempt
            print(f"runtime fetch failed ({failure}); retrying in {wait}s",
                  file=sys.stderr)
            sleep(wait)
    raise AssertionError("unreachable")


def prepare(destination, *, fetch=urllib.request.urlopen, sleep=time.sleep):
    source = (Path(__file__).resolve().parents[1] /
              "deploy/install-boxhostd-preflight.sh").read_text()
    release, archive_sha, runsc_sha = (
        re.search(r"^" + name + r"=(\S+)$", source, re.M)[1]
        for name in ("release", "archive_sha", "runsc_sha"))
    url = f"https://github.com/google/gvisor/releases/download/{release}/gvisor-x86_64.tar.bz2"
    payload = fetched(url, fetch=fetch, sleep=sleep)
    if hashlib.sha256(payload).hexdigest() != archive_sha:
        raise ValueError("pinned runtime archive digest mismatch")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:bz2") as archive:
        archive.extractall(destination, filter="data")
    if hashlib.sha256((destination / "runsc").read_bytes()).hexdigest() != runsc_sha:
        raise ValueError("pinned runtime executable digest mismatch")


if __name__ == "__main__":
    prepare(Path(sys.argv[1]))
