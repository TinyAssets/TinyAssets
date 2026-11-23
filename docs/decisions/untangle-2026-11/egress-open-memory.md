# egress-open-memory as built (2026-11-22)

Owed by `fact15-caps-2.md` (#5784's review): `EGRESS_LIMITS.open`, 16,000 open egress calls an
owner, also bounds `Egress.#live`, each open call's end signal held in the owner isolate, and
the counts test (`owner/guards/memory-counts.test.ts`) named it in `UNCOUNTED`. Fact 15: the
open count is a technical ceiling, its reason the 10 GB; this adds its second reason's
figure. Research first (Fact 14), read at `d83cf7c83`.

## Research

### How the counts test counts what an owner object holds

- **Its terms** (`counts`): the pool's units, the relay's tails, the relay's per-object terms
  (`relay-memory.ts`: each a count the code enforces times a cost the code constructs, once
  for each owner object the isolate holds), and the resting baseline. A figure the code does
  not construct is a declared cost the workerd memory leg (`memory/leg.mjs`) measures on the
  real path and fails past: `OWNER_BASELINE_BYTES`, each hold's units, `MOVE_RECORD_BYTES`
  (256 B a record against 155 to 208 measured).
- **So `#live` counts the same way:** a count (`EGRESS_LIMITS.open`) times a declared cost the
  leg holds, once for each owner object.

### What an open call holds, in workerd

Measured with a scratch harness first: an owner object returning an `IdentityTransformStream`'s
readable over RPC to a door in another isolate, which holds a read waiting on it inside its
own call (as `doors/src/egress.ts` does: a read in a call already returned never sees the
close), the owner isolate collected and read through the inspector (`usedSize +
backingStorageSize`) while all are held. Bytes a call, at 2,000 to 16,000, two runs each:

| What `#live` holds a call | at 4,000 | at 16,000 |
|---|---|---|
| today's entry: the writer, a flag, and the row's connection, lease and run copied beside it | 296 to 297 | 296 to 298 |
| the writer and the flag | 152 to 156 | 153 |
| the writer alone | 132 to 134 | 133 |
| an RPC callback stub the door passes instead of a stream (200 calls) | | 104 |

Flat in the count (2,000: 307; 8,000: 299), so the 16,000 is a product, not a guess; the 400 B
of #5784's review was a small run's (200 calls read 380 to 395). Every signal reached its
door when the object closed it, at every size.

### Whether `#live` needs to exist

- **A row cannot be the signal.** The end is a push into the door's running invocation (a
  policy write or a cancel ends a stream at once, `egressOpen`'s contract). A door watching the
  row would poll: 16,000 reads a poll at the bound, and an end late by the poll's period.
- **A callback stub is smaller (104 B) but loses an end.** When the object's instance goes, the
  stream it returned errors at the door, which settles `ended` (`doors/src/egress.ts`); a
  stub the door handed in gives the door nothing when the owner side goes.
- **What the entry copies, the row already holds, unchangeably.** `connection`, `lease` and
  `run` are written once to the row and never change (`egress_reservations_kept` aborts any
  update); `end` is their only reader. The flag (`reserving`) is the one fact the row does not
  keep (whether the call is metered), and adding it would be a migration for 20 B.

## Decided

1. **`#live` holds only its signal and its flag**; `end` reads what each covers from its open
   row (`SELECT ... WHERE state = 'open' AND ended IS NULL`, the `by_state` index, the rows
   read before any is marked), and ends those whose signal this instance holds. Its callers
   and their predicates are unchanged. An open call now holds about half (157 B against 297).
   `end`'s return value (a count no caller read) goes.
2. **`OPEN_CALL_BYTES`, 256 B, beside `EGRESS_LIMITS` in `egress.ts`**: the counts test's term
   `egress_per_object` (`EGRESS_LIMITS.open` x `OPEN_CALL_BYTES`, 3.91 MiB, once for each
   owner object) leaves `UNCOUNTED`. The isolate's counts: 74.7 MiB of 122.07, 47.4 MiB left,
   five owner objects' terms in all (was eight relay terms alone).
3. **The leg's egress case (`signals`)**: the store's isolate stands as the egress door's and
   opens `EGRESS_LIMITS.open` calls one after another through `LegOwner.legOpen` (the real
   `Egress.open` over the object's own storage, in a gate unit; its reads answer every check
   as a live lease's connector call passes, each lease id fresh as `leaseByToken` reads it),
   each signal held with a read waiting; sampled while all are held; then `Egress.drop`, and
   every read must end. Checks: the case within `EGRESS_LIMITS.open` x `OPEN_CALL_BYTES`, and
   all 16,000 ends within a beat (review, below). Measured: 2.35 and 2.39 MiB (154 and 157 B a
   call). One run in the leg, at the bound: each open reads the open rows (below), so the
   opens are the case's time, about 35 s.
4. **`limits-audit.md`**: the `open` row's reason names `OPEN_CALL_BYTES` and the leg's figure;
   the live terms list it.

## Found and left

- **An open reads every open row, twice** (`#count`, and `#recover`'s lost rows through the
  `by_state` index), so the open's SQL grows with the calls open: near the bound, several ms
  an open (0.8 ms at the start, 3.3 ms near the bound: the leg's 16,000 opens about 35 s in
  workerd). Inside a request's CPU, and
  one owner opens far fewer (210 at the paid tier's most): left.
- **`end`'s `#live.has` check** keeps a row left open by an earlier instance unmarked, as
  before: its signal errored when that instance went, and its door settles it `ended`.

## Review (2026-11-22)

- **The leg's per-hold check skips by a measure, not a path name:** it runs where a hold was
  sampled, so `signals` (whose one checkpoint holds no unit) needs no case of its own.
- **The case's time is its ends, not its opens, and grows as the square.** Timed in the leg
  (local, `1d828cf9e`): the 16,000 opens 35 s (0.8 ms an open at the start, 3.3 ms near the
  bound, the open rows' reads), the sample 0.2 s, then 111.5 s for the 16,000 ends to reach
  their reads after `drop`; at 4,000 calls the ends took 5.3 s (21 times for 4 times the
  calls). The ends' wait is capped at 120 s (`LANE_LOOKS` x `LANE_POLL_MS`): 93% of it
  locally. The whole case, 135 to 148 s, is one `/run` fetch, whose headers come at its end,
  against Node fetch's 300 s headers timeout. CI's slower runner takes it past both.

### The ends' square, researched (2026-11-22)

A scratch workerd harness: an owner object holding `n` writers, their readables returned over
RPC to a door isolate, each read waiting; then the owner ends them all, and the time until the
last read ends. The owner issues every end in O(n) (12 ms for 2,000, 67 ms for 16,000).

| How the owner ends them | 2,000 | 4,000 | 8,000 | 16,000 |
|---|---|---|---|---|
| `writer.close()`, not awaited (as `#release` did) | 1.2 s | 5.7 s | 23.6 s | about 110 s (the leg) |
| `writer.close()`, every close awaited in the call | 1.5 s | 6.4 s | 29.2 s | |
| `writer.close()` of half of 8,000 (4,000 ends) | | | 6.4 s | |
| `writer.abort()` | | 0.27 s | 0.53 s | 1.1 s |
| a JS `ReadableStream`'s `controller.close()` | | 0.14 s | 0.28 s | 0.62 s |

- **The square is the closes', in workerd, not the reader's:** spreading the reads over 4, 40,
  400 or 800 door invocations (each its own request) changed nothing (5.6 to 7.0 s at 4,000,
  26.6 to 29.5 s at 8,000), and closing 4,000 of 8,000 costs what closing 4,000 of 4,000 does:
  it grows with the closes in flight at once, not with the reads held. So it is the owner's
  path, and production's: a policy write, a suspension or an erasure at the bound would reach
  its doors over about two minutes, past a beat.
- **The stand-in is faithful as it was:** one isolate holding every read is not what made it
  slow, so it stays.
- **The fix: an abort.** `#release` aborts the writer (`abort("ended")`): the door's read
  rejects, which it already takes as the end (`doors/src/egress.ts` `signal.read().then(stop,
  stop)`; the owner tests' readers too), and linear in the count. A JS stream is faster still,
  but would change what `OPEN_CALL_BYTES` measures, for half a second at the bound: not taken.

### Fitted (2026-11-22, `1ed458fb6`)

- **The leg's ends check** is its time, not only its count: the 16,000 ends reach their reads
  within a beat (`EGRESS_BEAT_S`, 60 s, times the runner's speed), since a door not stopped by
  then beats as if nothing ended. The store fails at once past the lane wait (120 s).
- **Measured, the case alone:** ends 1.26 s, the whole case 36.2 s. With `close()` put back:
  ends 109.9 s, the case 145.1 s, and the leg fails ("past a beat"). No owner test holds the
  time: a test would need thousands of real opens to see the square, and one asserting an
  abort rather than a close would hold the implementation, not the behaviour; the leg holds
  the behaviour, at the bound, in workerd.

## Owed

- [x] Research where the ends' square lies (the owner isolate closing every writer at once,
  or the one reader isolate holding all 16,000 reads, which no door does) and whether a
  prompt stop at `EGRESS_LIMITS.open` holds in production; then fit the case's ends to its
  ceilings with room, or the cost, before merge (`untangle/egress-open-memory`).
  Done (above): the square was the owner's closes, in production's path too; `#release`
  aborts. Three full `test:memory` runs (`1ed458fb6`, 200 to 204 s each): ends 1.16 to
  1.19 s against a beat's 60 s (2% of it; 3.2 s at CI's 2.7 times, 5%) and the store's
  120 s; the case 36.5 to 38.0 s against Node fetch's 300 s headers timeout (13%; 103 s at
  2.7 times, 34%); 2.21 to 2.23 MiB, 145 to 146 B a call, against 3.91 MiB.
