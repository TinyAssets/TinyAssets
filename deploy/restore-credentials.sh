#!/bin/bash
# Restore credential files and ledger rows for every account.
# Does not reset credentials, rotate box secrets, close admission, or swap the volume.
set -euo pipefail
mode="${1:-inspect}"
source_name="${2:-auto}"
case "$mode" in
  inspect|inspect-legacy|dry-run|apply) ;;
  *) echo "diagnostic refused: bad_mode" >&2; exit 2 ;;
esac
archive='^tinyassets-(data|snapshot)-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}Z\.tar\.gz$'
retained='^\.tinyassets-restore-old\.[A-Za-z0-9]+$'
if [[ "$source_name" != "auto" ]] \
    && [[ ! "$source_name" =~ $archive ]] \
    && [[ ! "$source_name" =~ $retained ]]; then
  echo "diagnostic refused: bad_source" >&2
  exit 2
fi
set +e
set -a
if [[ -f /etc/tinyassets/env ]]; then
  # A bad line in the env file must not abort the restore.
  # shellcheck disable=SC1091
  . /etc/tinyassets/env
fi
set +a
set -e
here="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$here/restore_credentials.py" ]]; then
  script="$here/restore_credentials.py"
else
  script="$here/../tinyassets/restore_credentials.py"
fi
exec python3 -B "$script" --mode "$mode" --source "$source_name"
