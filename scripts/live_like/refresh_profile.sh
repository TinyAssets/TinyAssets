#!/usr/bin/env bash
# Lead-only local operation. Never upload or retain extracted backup data.
set -euo pipefail

archive="${1:?usage: refresh_profile.sh <backup.tar.gz>}"
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
mkdir -p "$scratch/data"
tar -xzf "$archive" -C "$scratch/data" --no-same-owner --no-same-permissions
# Accept a backup with one enclosing directory, but never guess among several.
root="$scratch/data"
if test "$(find "$root" -mindepth 1 -maxdepth 1 -type d | wc -l)" -eq 1 \
   && test ! -e "$root/.layout.json"; then
  root="$(find "$root" -mindepth 1 -maxdepth 1 -type d -print -quit)"
fi
python3 "$repo/scripts/live_like/shape_profile.py" profile --data "$root" \
  --out "$scratch/fresh.json" --check-redaction
python3 "$repo/scripts/live_like/shape_profile.py" diff \
  --profile "$repo/deploy/live_like/shape-profile.json" --fresh "$scratch/fresh.json"
echo "Fresh redacted profile: $scratch/fresh.json (removed when this command exits)"
echo "To refresh, rerun profile --data on an extracted local backup and review the JSON diff."
