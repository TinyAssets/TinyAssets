"""Fetch the production-pinned runtime into a disposable oracle output directory."""

import hashlib
import io
import re
import sys
import tarfile
import urllib.request
from pathlib import Path


def prepare(destination, *, fetch=urllib.request.urlopen):
    source = (Path(__file__).resolve().parents[1] /
              "deploy/install-boxhostd-preflight.sh").read_text()
    release, archive_sha, runsc_sha = (
        re.search(r"^" + name + r"=(\S+)$", source, re.M)[1]
        for name in ("release", "archive_sha", "runsc_sha"))
    url = f"https://github.com/google/gvisor/releases/download/{release}/gvisor-x86_64.tar.bz2"
    with fetch(url, timeout=120) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != archive_sha:
        raise ValueError("pinned runtime archive digest mismatch")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:bz2") as archive:
        archive.extractall(destination, filter="data")
    if hashlib.sha256((destination / "runsc").read_bytes()).hexdigest() != runsc_sha:
        raise ValueError("pinned runtime executable digest mismatch")


if __name__ == "__main__":
    prepare(Path(sys.argv[1]))
