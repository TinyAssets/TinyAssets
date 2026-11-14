# T1c as built (2026-11-14)

T1c is split at its natural seams, in three PRs (T1c-a, T1c-b, T1c-c), each green on its own (B23's "about four PRs" for T1).

## T1c-a: the gates

1. **What T1c-a builds:** the `FILES` and `GATEWAY` gates in the owner suite (`ci/gate.ts`, as `SANDBOX`'s and the `SLOTS` gate are), and the five test classes and two archive hooks they replace:
   - `GatedOwnerDO` (`watched`, `faulted`, `GatewayCall`, `GatewayFaults`), `RungOwnerDO`, `ArchivingOwnerDO` (`ChainHold`), `DivergingOwnerDO` and `PurgingOwnerDO`, with their namespaces;
   - `Internals.chainStep` and `Internals.archiveBetweenPasses`, with `ChainStep`, `ArchiveMoveSeen` and the archive's two parts.

   It also builds the owner suite's lease clock under a fake `Date` (list 6, item 6j) and the object's `admitted` held by a test.
2. **What T1c-b builds, from `main` once T1c-a merges:** the real entry. That is `main: src/index.ts` and `Internals` deleted with the classes left (`OwnerDO`'s null starter, `SeededOwnerDO`, `FauxOwnerDO`, `SwitchedOwnerDO`, `CachedOwnerDO`), `test/worker.ts`, `test/namespaces.ts` and `test/admitted.ts`. It covers:
   - the erasure and the control record under a fake `Date`;
   - the unbound-`ERASURES` throw and `RecordingErasures`, the queue consumed by a fake Worker;
   - the one-file starter by a Vite alias;
   - the rig admitting by the door;
   - the doors' entry with no third constructor argument;
   - `BackupParts`' `sizes?` and `pool?`.
3. **A service gate names each call by its first argument when it is a string** (`ci/gate-worker.ts`). This extends T0's gate, and its "a name on a service gate is refused" case goes.
   - Every service the gates front takes its owner, or the owner's namespace, first.
   - Without a name, an arming meets the first matching call of any owner. The archive lanes of a file's earlier owners still run on their alarms, so that call need not be the test's own.
   - `owner/test/support.ts` `gateFor(gate, name)` arms, waits for, releases and reads back one name's calls. `spaceOf` gives an owner's files namespace, the `FILES` gate's name.
4. **An arming holds the next matching call, not every call until cleared** (the old promise gates did that). A test that lets a call go waits until it is held first (`held`), and sends it if the runtime has not.
5. **The chain-read tests (N9, N10) name the chain by one of its hot messages**, so no root read from a segment comes before the fences. The `FILES` gate holds the read's first archived read:
   - before it is sent: past the fences unit, for N10;
   - after its answer: before the hot unit, for N9.

   N9's chain now has an archived part (its first four moved), because a chain that is all hot makes no outside call to hold (`b23_research.md` question 2).
6. **The purge's bounds are read at the `FILES` gate** (`archive-mail.test.ts` `watchMoves`), in place of the `test_moves` table.
   - Every move uploads its segments before the unit that names them. The gate holds each upload of the owner's namespace while the move's row (`archive_moves`) and the fences are read.
   - The fences' change from one move's first upload to the next one's is the first move's commit: the segments it replaced and the records it wrote.
7. **The diverging move is a `putChunks` changed in flight** (`corrupt`): R2 refuses it by the chunk's hash, as research question 2 measured.
8. **A refresh answered already past its expiry** is a real answer, not a changed one. The gate cannot change an answer, and a fake `Date` past the fake's half-hour token would also lapse the sandbox lease (60 s).
   - The gateway's fake upstream answers the refresh token `rt-lapsed` with a one-second token. That is one line in `gateway/test/fake-upstream.ts`, lane V's file.
   - The test holds the answer at the gate while the object's clock passes the second.
9. **The lease clock** is `runs-support.ts` `later(seconds)`, a fake `Date` (a lease's life and one by default), in place of `UPDATE sandbox_leases SET lease_expires = 1`, in every test that moved it: the owner suite's `egress`, `runs`, `runs-authority`, `files-lease` and `storage-full`, and the doors' `egress`. The heartbeat test moves the clock 30 s and sees the lease renewed from there.
   - The `SANDBOX` gate's armings name their owner too (`whileOut(owner, …)`, `gateFor`), so no other owner's lane meets them.
10. **`admitted` held by a test** (`doorbell.test.ts`, "claims nothing more once the owner is suspended between a pass's claims"):
    - Two wakes are due in one pass. The `GATEWAY` gate holds the first ring, and the owner is suspended there.
    - The second wake stays pending and unclaimed, and is never rung.
    - It fails when the object hands the ledger `() => true`. No test failed on that before (T1b4's R1).

## Owed

- **T1c-b**, as item 2 says.
- **T1c-c: `ledger-support.ts` `ledgerParts`, `ledgerOn` and `inLedger`.** They copy the object's wiring of the ledger, and about 70 test reaches use them. `ballast` posts up to 50,000 messages in the ledger to seed production's ceilings, about 9 s a seed; through the doors that would be 50,000 door calls a seed. It is its own PR, after T1c-b (`tasks.md` holds the three lines), and is not built here.
  - **Decided (the coordinator, 2026-11-14):** the object exports its one ledger wiring function, the one it calls to build its ledger from its state and env. The tests call that same function in place of `ledgerParts`, so there is one wiring and no copy.
  - Test seeding is not routed through the doors, and no bulk SQL seeds (it would copy the post path's row shapes, which T1b5 rejected).
  - `ballast` keeps seeding through the ledger's own API, on the ledger that function builds.
- **The gate has no withdrawal of one arming.** `disarm` clears every arming, held call and record, so `watchMoves` disarms at its end.
