Still reviewing: I've read the image, disconnect, drain and fold hunks, and I'm now checking whether the "dead source" filters can drop sources that are still live.

Checking whether broker discovery still blocks the shared connection, then the page XSS.

Nearly done — checking lifecycle scripts at image build, then the page.

The four earlier findings are fixed, but one new hunk has a correctness defect: connecting an API-key source breaks or silently strips an accepted subscription source.

I read the code only and ran no tests. Everything below comes from reading the diff and the code paths it calls.

## Earlier findings: all fixed

- **Discovery stalling the shared broker:** discovery now runs on its own pool of two workers. When both are busy, the next request is refused at once with `discovery_busy`. The whole probe is capped at 35 seconds. Each request opens its own socket, so a slow discovery no longer holds up the shared broker read loop. (`broker/server.py:553-561`, `model_discovery.py:199-221`)
- **Sign-in token sent to a cross-site host:** `choose()` now requires the issuer and the authorization, token and registration endpoints to be on the same site as both model URLs. "Site" comes from the system's public-suffix list, and falls back to an exact host match when that list is missing. `SourceRecord.parse` applies the same rule. (`model_discovery.py:325-328`, `source_record.py:156-160`)
- **Discovered sources assumed flat-rate:** the hard-coded `"flat"` is gone. A discovered model now counts as free only if its catalogue lists both prompt and completion prices and every price is exactly zero. Otherwise it fails with `no_verified_free_models`. (`source_connect.py:15-35, 219`)
- **Source records changing after acceptance:** acceptance now records a digest of the exact source. `invoke`, `context()` and `finish()` check it, so any later change forces a reconnect. A malformed owner override removes the built-in record rather than quietly falling back to it. (`subscription_runtime.py:344-361`)

## New findings

**1. HIGH: connecting an API-key source breaks or strips an accepted subscription source.** `tinyassets/onboarding/source_connect.py:63-88`
- **Cause:** the new `live()` check returns False for anything that isn't `api_key_http`. That includes `subscription_cli:*` members, which `finish()` really does add to assignments.
- **Scenario:** an owner has Claude Code connected, then connects an OpenRouter or sign-in model source. `_offer_pool_access` builds an access list without the subscription member. If the subscription was the root, the root also switches to the new source (line 87).
- **Normal outcome:** `capture_action`'s preservation check still sees the subscription as live and raises "must preserve other accepted providers". The connect fails with a 409, so a mixed-source owner can never add an API source.
- **Transient outcome:** if the CLI status check fails at that moment, the guard lets it through. The owner approves a request that silently removes their subscription source, while the body text says "other accepted sources … stay the same."

**2. MEDIUM: a transient failure permanently removes an accepted source.** `tinyassets/api/connection_uses.py:296-316`, `tinyassets/api/model_access_requests.py:103-110`
- **Cause:** `alive()` and `still_resolves()` treat any exception as "departed". For a subscription source the check runs `subscription_catalog.context()`, which calls `authenticated()`. That runs the CLI's `status_argv` over the network with a 30-second timeout. A ledger or SQLite busy error also counts.
- **Effect:** one timeout during another source's accept drops the owner's existing source and its spending limits from `model_access`. It can also switch the root (`provider=root if root in access else definition_id`). The same failure loosens the "must preserve" guard for client-proposed membership.
- **Fix direction:** only explicit departure errors (definition gone, grant revoked or not accepted) should prune a source; anything else should raise.

**3. MEDIUM: the shared CLI image trusts whatever the registry serves at build time.** `tinyassets/providers/subscription_image.py:19-28`, `subscription_runtime.py:233`
- **Cause:** at build, `ImageInstaller.run` calls `subprocess.run` as root, with the build's full environment and unrestricted network. `claude-code` has `lifecycle_scripts: true`, so `npm ci --ignore-scripts=false` runs install scripts for every package in the tree.
- **What is checked:** only the top-level package's version and integrity are pinned. Dependencies get resolved from the live registry during the build, and `verify_lock` only checks that each has *some* integrity hash on the allowed host. No lockfile is committed.
- **Effect:** a compromised dependency at build time runs as root in the build and ends up in the read-only rootfs that every owner box uses. Before, it ran per owner inside the egress-limited sandbox.
- **What's fine:** at runtime the image path is good. It checks the lock hash against the marker and runs `verify_lock`, uses no network or registry, and only writes config and the lock into the owner's home.
- **Fix direction:** commit each source's `package-lock.json` and check it at build time.

**4. LOW: disconnect doesn't stop an in-flight sign-in for the same source.** `tinyassets/onboarding/subscription_connect.py:61-90`
- **Scenario:** `disconnect()` doesn't take `runtime.lock()` or cancel that source's `Login`. If the user disconnects while a sign-in is waiting, the CLI can write `.claude/.credentials.json` after the state paths were deleted.
- **Effect:** `accept()` then fails because the directory is gone, but the credential stays in the home. That breaks "disconnect leaves nothing".
- **Ruled out:** path traversal and deleting the shared image. Paths are resolved and checked with `relative_to` against the owner's home, a symlink pointing outside is refused, and `/opt/tinyassets/cli` is never touched. One owner can't affect another, since source ids are regex-checked and each owner's home is its own.

**5. LOW: drain doesn't wait for a cancelled sign-in to stop.** `tinyassets/owner_activity.py:42,64,79`
- **Effect:** `_cancel_logins()` only sets the cancel flag. `status()` can report `draining` with `active==0` while the CLI login process is still writing state, so the box can be snapshotted or slept mid-write.
- **Impact:** small. Without the `connection` marker the partial state can't be used.
- **Ruled out:** real turns being dropped. Turns still count in `active` and drain still waits for them; only passive sign-ins are cancelled.

**6. INFO: `_recover_stale_default` overrides an explicit pin when it's only briefly unavailable.** `served_model_plan.py:44-52`
- If the saved default is cooling down, the turn runs in automatic mode on another model.
- It only picks from the owner's accepted models, with per-source caps kept, so it can't widen into a paid model the owner didn't accept.

**Checked and clean:**
- **Bare-domain probing:** `api.<host>` goes through the same SSRF-hardened transport, IP literals are skipped, and the 35-second budget plus 15-second per-probe timeout keep it bounded.
- **Page (`main.js`):** source names, codes, answer titles and the copy button all use `textContent`. I found no `innerHTML` in the added lines, and sign-in links must still be `https:`.

VERDICT: DISAGREE_EVIDENCE
