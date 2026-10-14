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
