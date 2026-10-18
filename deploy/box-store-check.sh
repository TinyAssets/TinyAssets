#!/usr/bin/env bash
set -euo pipefail
store="${1:-/var/lib/boxhostd/owners}"
[[ "$(findmnt --mountpoint "$store" -no FSTYPE)" == xfs ]] || {
    echo 'box store is not XFS mounted at the expected path' >&2; exit 1;
}
options=",$(findmnt --mountpoint "$store" -no OPTIONS),"
[[ "$options" == *,pquota,* || "$options" == *,prjquota,* ]] || {
    echo 'box store lacks project quotas' >&2; exit 1;
}
# XFS never lists nodiscard (it is the default); only an enabled discard shows.
[[ "$options" != *,discard,* ]] || {
    echo 'box store is mounted with discard' >&2; exit 1;
}

# Setup alone records filesystem identity. Runtime erasure only reads it.
if [[ "${2:-}" == --initialize ]]; then
    uuid="$(findmnt --mountpoint "$store" -no UUID)"
    python3 - "$store" "$uuid" <<'PY'
import os
import sys
from pathlib import Path
root, uuid = Path(sys.argv[1]), sys.argv[2]
if not uuid or root.stat().st_dev == root.parent.stat().st_dev:
    raise SystemExit("owner store mount identity unavailable")
proof = root.parent / ("." + root.name + "-identity")
expected = "xfs " + uuid + "\n"
try:
    fd = os.open(proof, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
except FileExistsError:
    fd = os.open(proof, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != 0 or info.st_mode & 0o077 or stream.read() != expected:
            raise SystemExit("owner store identity changed; refusing setup")
else:
    with os.fdopen(fd, "w") as stream:
        stream.write(expected)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
PY
fi
