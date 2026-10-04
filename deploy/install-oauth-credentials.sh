#!/usr/bin/env bash
# Caller holds the shared host-mutation lock. Only values arrive on stdin.
set +x
set -euo pipefail
action="$1"
previous_image="$2"
keys=(TINYASSETS_OAUTH_GOOGLE_CLIENT_ID TINYASSETS_OAUTH_GOOGLE_CLIENT_SECRET)
case "$action" in install|skip|remove) ;; *) exit 1 ;; esac

# Automatic and public-canary rollback both restore this captured image but
# retain the env. Prove its filter without passing any host env or mounts.
# Unavailable/old images disable OAuth, never block an otherwise safe deploy.
if [ "$action" != remove ]; then
    if [[ ! "$previous_image" =~ @sha256:[0-9a-f]{64}$ ]] ||
        ! timeout 30 docker run --rm --network none --entrypoint python "$previous_image" -c '
from tinyassets.platform_secrets import child_env
keys = ("TINYASSETS_OAUTH_GOOGLE_CLIENT_ID", "TINYASSETS_OAUTH_GOOGLE_CLIENT_SECRET",
        "TINYASSETS_OAUTH_FUTURE_CLIENT_SECRET")
assert child_env(dict.fromkeys(keys, "probe")) == {}
' >/dev/null 2>&1; then
        echo '::warning::rollback image lacks proven OAuth child filtering; skipping install and removing OAuth keys'
        action=remove
    fi
fi
case "$action" in
    install)
        env TINYASSETS_ENV_FILE=/etc/tinyassets/env \
            bash /tmp/install-tinyassets-env.sh set-pair "${keys[@]}"
        ;;
    remove)
        env TINYASSETS_ENV_FILE=/etc/tinyassets/env \
            bash /tmp/install-tinyassets-env.sh delete "${keys[@]}"
        ;;
esac
