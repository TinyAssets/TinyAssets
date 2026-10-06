Still reviewing: I've read the journal, the fixtures and the CI wiring. Next I'm checking design D2 and tasks 1.1/2.1 against the slice.

**Floor / correctness review: L7 working tree vs b945fb3b3a**

The slice is safe as dormant code and the durable-acceptance core holds up, but it needs adapting before it lands: one CI gap, one missing cross-user check, and one overstated evidence label. `tests/test_ingress_journal.py` passes locally (16 passed); I didn't run the Linux oracle. The generated plugin mirror is identical.

**What holds up (no finding):**
- **Durable acceptance:** writes take an exclusive lock with full sync, and success is returned only after commit.
- **Exact bytes and keys:** payloads must be raw bytes, and the payload hash is bound to the five-part key, so reusing a key with different bytes raises `Conflict`.
- **Revocation:** the current-authority check is entered before every connection and held across the importer.
- **Lost acknowledgements:** a lost import ack is re-resolved by lookup through `ingress_imports` keyed by `ingress_id`. Unit tests cover rollback before commit, a lost ack after commit, and two replays racing.
- **Reply cursors:** sequences are gap-checked, a repeated append is a no-op, and nothing can be appended after the terminal event.
- **Tombstones:** expired keys keep their identity and hash, refuse resubmission, and nonterminal rows are never deleted.

**Findings**

1. **CI (likely, not verified):** `tests/test_deploy_during_traffic.py` is not listed in `.github/heavy-test-files.txt`, so the required Linux CI will collect it.
   - It imports playwright at module top and launches sandboxed Chromium. That needs the user-namespace AppArmor profile, which only `deploy-during-traffic.yml:22-27` installs.
   - Expected outcome: required shards go red or run a heavy browser test twice. `gh pr checks 4506` reports nothing yet, so I couldn't confirm.
   - Fix: add the file to the heavy list so the dedicated workflow owns it.

2. **Floor, unsafe foundation despite dormancy:** `tinyassets/storage/ingress_journal.py:258-285` never checks that the envelope's identity matches the runtime scope that `reserve` writes under.
   - The journal keys on `(principal, command_center, thread, operation, key)`. Runtime admission dedupes on `(owner, universe, session, key_hash)` (`conversation_run_admissions.py:348-351`).
   - Nothing asserts `principal == scope.owner` or `command_center == scope.universe`, or links thread to session. The stored `scope_digest` is only checked against itself.
   - Failure scenario: an adapter builds the runtime scope from the wrong principal and imports user A's exact bytes as an admission owned by user B. The mapping table records it as valid.
   - Task 2.1 explicitly requires cross-user guards. Fix: pass the runtime scope into `import_in_transaction`, assert equality before calling `reserve`, and store the runtime scope in `ingress_imports`.

3. **Correctness, minor (same root as 2):** thread and session don't line up.
   - Two journal threads using the same `client_send_id` under one universe session land on one admission.
   - Same message: `IntegrityError` on `UNIQUE(runtime_id)`. Different message: `IntentConflict`.
   - It fails loudly, but it refuses a legitimate second send.

4. **CI and evidence honesty:**
   - `tests/fixtures/deploy_traffic_acceptance.py:73-80` appends a terminal `CUTOVER_SEND_FINISHED` event without executing anything.
   - `effects == 1` is guaranteed by the fixture's own `INSERT OR IGNORE`, not by any execution guard.
   - Yet `tests/test_deploy_during_traffic.py:229` records `"cutover_send": "GREEN"`.
   - The 202 also doesn't depend on the origin listener at all, because the edge never contacts the origin in durable mode.
   - Fix: relabel it (e.g. `acceptance_import: GREEN`, `execution: fixture`, `long_turn: RED`) and say so in `l7-evidence.md` when the Linux run is recorded. As it reads now, it invites a "send continuity GREEN" claim.

5. **Minor, not floor:**
   - `ingress_journal.py:152`: the admission policy runs inside the journal's write transaction. Any outside quota it charges isn't rolled back if the commit fails, so "retries do not consume admission twice" only holds once the commit has succeeded.
   - `initialize()` (`ingress_journal.py:77-89`) sets 0600 only on the main database file, not the WAL/shm sidecar files, and `mkdir` doesn't tighten a directory that already exists. Harmless in a single-tenant container; fix before any shared host.

**Task order:** task 1.1 (the full Compose/app/multi-surface harness) is still incomplete. That doesn't block landing this dormant, no-production-ingress slice as a draft. It does block checking 1.1 or 2.1, wiring any production adapter, and anything that touches ingress. Fix 2 belongs to 2.1's "cross-user guards" requirement. Public rollout stays blocked, and nothing in the tree claims merge-ready or zero-impact. I found no collision with another lane.

VERDICT: ADAPT
