#!/usr/bin/env bash
# Emit one matching open proposal per line for the watcher and its cooldown job.
set -euo pipefail
gh api --paginate "repos/$GH_REPO/pulls?state=open&base=main&per_page=100" |
  jq -c --arg repo "$GH_REPO" --arg login "$1" --arg prefix "$PREFIX" '
    .[] | select(.user.login == $login and
                 (.head.repo.full_name // "" | ascii_downcase) == ($repo | ascii_downcase) and
                 (.head.ref | startswith($prefix)) and
                 any(.labels[]; .name == "okf-watch"))'
