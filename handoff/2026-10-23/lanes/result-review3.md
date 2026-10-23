Broker custody op is owner-scoped from the socket and admission-checked; now checking login/disconnect, discovery egress, the repair and the image build.

Custody/lease checked; now reviewing the box-side discovery egress path.

Discovery egress goes through the in-box forwarder to the broker with no redirects — clean. Now the cutover repair.

Image build checks out (committed lock + digest, isolated env). Checking where the box's /tmp actually lives and backup coverage of the custody file.

Found a broker-side bound gap in custody; verifying the last review2 items (connection_uses) now.

I'm disagreeing with the lead. One HIGH and one MEDIUM finding below are defects the reshape introduced. The rest of what you asked me to break held up. I only read code and ran no tests: the custody and lease tests need Linux tmpfs (`scripts/linux_oracle.py`), and this is a Windows host.

## Findings

**1. HIGH: disconnecting a subscription source permanently blocks every later model-access change** (in-box; it does exist in the target shape and breaks correctness for the owner)
- **Where:** `providers/subscription_runtime.py:571-586` (`disconnect_source`), `api/model_access_requests.py:101`, `onboarding/source_connect.py:63-64`, `provider_serving_binding.py:498`
- **Cause:** disconnect deletes custody and the `connection` marker, but leaves `subscription_cli:<id>` in the assignment's members. Runtime pruning is gone, so nothing else removes it.
- **Scenario:** an owner has Claude Code and OpenRouter, disconnects Claude Code, then connects another source.
  - `_offer_pool_access` still includes claude-code.
  - `bind_serving_provider` resolves every member (`:498`). For claude-code that goes through `subscription_catalog.context()`, whose `require_accepted()` now raises because the marker is gone, so the bind fails.
  - The owner can't remove the member either: `capture_action` requires `previous ⊆ proposed`.
  - The cutover repair can't help: it deliberately keeps subscription members and only runs once.
- **Fix direction:** `disconnect_source` should remove its member from the assignment, re-anchoring the root, in the same step. That is an explicit departure, unlike the transient-failure pruning that review2 flagged.

**2. MEDIUM: the broker's custody store has no limit per owner** (floor: credential broker)
- **Where:** `broker/subscription_custody.py:243-271`
- **Cause:** the box chooses any `source` matching `[a-z0-9][a-z0-9-]{1,63}`. Every `begin` adds an entry, and every `save` can store up to 2 MB. All of an owner's entries live in one JSON file that is re-read and rewritten in full on every operation.
- **Scenario:** a looping or compromised box calls `begin` or `save` across thousands of made-up source ids. The broker's disk and its worker threads are shared, so every owner suffers.
- **Fix direction:** cap the number of entries and total bytes per owner on the broker side. The box's check against declared source paths doesn't count, because the broker can't trust the box.

**3. MEDIUM: a refreshed token is lost when writeback fails** (in-box)
- **Where:** `providers/subscription_lease.py:482-492`, `owner_channel.py:49-65`
- **Cause:** writeback makes one attempt over one socket with a 30-second timeout, and the temporary directory is wiped either way.
- **Scenario:** a run refreshes the token with a provider that rotates refresh tokens (Claude, Codex). The broker restarts or is unreachable at that moment, for example during a deploy. Custody keeps the old, now-invalid refresh token, and the owner has to sign in again.
- **Related:** a run cancelled while the CLI is rewriting its credential file captures and saves whatever partial bytes are there.
- **Fix direction:** retry the save a bounded number of times, and only capture state files that parse.

**4. LOW-MEDIUM: disconnect fails when the source record no longer resolves** (in-box)
- **Where:** `subscription_runtime.py:703`, via the `__init__` lookup `sources(self.home)[source_id]`
- **Scenario:** the owner deletes or breaks a declared source override after connecting it. A malformed override removes the record entirely, so disconnect raises `KeyError` and the custody entry stays in the broker until the account is erased.
- **Fix direction:** delete custody by source id without building a runtime.

**5. LOW: leftovers after a crash or an expired login** (in-box)
- **Crashed daemon:** if the daemon is killed while the box stays awake, the `/tmp/ta-native-*` lease, which still holds live tokens, remains until the box stops. Neither startup nor disconnect sweeps it.
- **Expired login:** `login_operation` drops an expired Login from `_logins` without joining it. Disconnect therefore doesn't wait for it, and if that thread is still in `install()` it can recreate `~/.ta-cli/<id>` after the `rmtree`. These are package files, not credentials.

**6. LOW: the legacy-acceptance upgrade misses a second record change** (in-box migration)
- **Where:** `subscription_lease.py:512-520`
- **Cause:** `upgrade_connection` only reverts `location`, but the records also gained `lock_sha256` in `0ead9f6`. Any marker digested from the pre-`0ead9f6` record won't upgrade, so that owner has to reconnect.
- **Impact:** these records aren't on main, so this only matters if they were ever hotfixed live.
- **Stale doc:** `design.md` "Immutable subscription dependency closure" still says the digest patch "has not been applied", but it has been.

**7. LOW: the shared OAuth flow accepts the page's `prepared` offer and policy together without re-deriving them** (floor: credential flow; mostly pre-existing)
- **Where:** `onboarding/model_connect.py` `oauth_begin`
- **What changed:** the broker no longer derives the offer from the destination's hosts. The old code's page-supplied `discovery_hosts` already allowed the same pairing, so this changes little.
- **What's safe:** confidential client secrets are still re-pinned to the directory's token URL (`tokens.py:162`).

**8. LOW: the repair's `--stopped` flag enforces nothing** (one-shot cutover tool)
- **Where:** `cutover_assignment_repair.py:136`
- **Scenario:** run against a live box, a grant committed between the ledger read and the repair can get a brand-new API member revoked. The design relies on the operator stopping both services first.
- **Fix direction:** refuse to run while the volume is attached or the broker socket answers.

## Review2 findings 1–6
- **1 (`live()` stripping subscription members):** fixed. Access now includes every member, and the root is preserved.
- **2 (transient failure pruning a source):** fixed, since pruning on exceptions is gone. But removing all runtime departure handling caused finding 1.
- **3 (image trusting the registry):** fixed. The lock is committed and its SHA-256 matches the record, `verify_lock` passes, the build uses `npm ci` only, and the environment is stripped. It loads committed records only (`records(strict=True)`).
- **4 (disconnect not stopping a sign-in):** fixed. Disconnect cancels and joins pending logins, then deletes under the source lock. Caveat in finding 5.
- **5 (drain not waiting for a cancelled sign-in):** no longer reachable. Login state is on tmpfs, and custody is only written under `activity.lock` after a cancel check.
- **6 (overriding a briefly unavailable pin):** fixed. A default only counts as stale when its source is gone or a catalogue that was read successfully lacks the model.

## Checked and clean
- **Cross-owner custody:** the owner comes from the socket (`_box_owner`), never from the document. `_box_scope` checks admission on every operation, `check_not_deleted` runs under the vault lock, and erasure and disaster recovery both delete `.subscription-state.json`.
- **Leases:** they use a 0700 directory on tmpfs (the gVisor `/tmp` is mounted tmpfs, `gvisor_runtime.py:114`). `run()` refuses to execute without a lease, and concurrent runs are serialized by an flock across processes, so no refresh is lost between two runs.
- **Broker:** it stores only opaque base64 entries in its own namespace and holds no path to owner data.
- **Discovery:** it goes through the in-box forwarder, and only the broker interprets that traffic. It runs with `follow_redirects=False`, `trust_env=False`, and a bounded response size.

VERDICT: DISAGREE_EVIDENCE
