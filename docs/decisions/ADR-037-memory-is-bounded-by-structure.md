# ADR-037: Memory Is Bounded by Structure

## Status

Accepted (founder, 2026-11-03: "where large data lives is an architecture rule, built in and automatic, not a decision he makes", the standing ruling ADR-035 records; how much of it an isolate holds at once falls under it). Owed list (decision 6), 2026-11-04: the archive's move and rewrite (STORE item 2) are now under units and in the memory leg (`limits-audit.md`).
- **It replaces** the byte guard and its grants that the platform memory fix's first design used; PR 1 (#5407) removed them.
- **It extends** ADR-035: a large body already lives in R2 as the files plane's chunks. This ADR says how much of one an isolate may hold at once.

## Date

2026-11-04

## Context

A Worker isolate has 128 MB of memory (Cloudflare's `workers/platform/limits`). One owner's object, the doors and the files Worker each run many calls in one isolate. Past 128 MB the isolate is reset and every call in it fails.

The first design counted bytes. Each path asked a guard for a grant of its worst case, and a memory leg asserted absolute peaks. Its review found the grants could not be derived: a worst case depends on V8's copies, the framework and garbage. The absolute peaks measured the framework and the sampler as much as the path.

The research that replaced it gives the shape:
- large data moves only as streams or 4 MiB chunks, never whole;
- a path takes units of its isolate's pool for what it holds while it holds it;
- CI proves a path's cost does not depend on its data's size, by differences between sizes, never by an absolute peak.

**The stop that was a sampler artifact.** PR 2's first leg found that a Durable Object sending chunks over RPC seemed to keep every chunk until its method returned. That would have broken the shape. The research showed it was the sampler: the leg collected and then read the heap in two steps, and the burst of RPCs ran between them. Paced, or sampled where the path waits, every form is flat. **A swept read must be atomic with its collection.** The leg now samples at checkpoints where the path is suspended, with a control that keeps every chunk and must read as growing.

The research and reviews (`memory_research.md`, `memshape_research.md`, `rpcretain_research.md`, `rvmem_report.md`) are the builders' working notes, kept outside the repo. Every figure this decision relies on, with its derivation and its date, is in `openspec/changes/clean-sheet-platform/limits-audit.md` ("Memory bounded by structure"), not here: the figures are replaced as production measures them, and this record is not.

## Decision

1. **The rule.** What an isolate holds live is a sum of counts the code enforces times costs the code constructs. Nothing is estimated at run time.
   - Large data moves as streams or 4 MiB chunks (`UNIT_BYTES`, the files plane's chunk).
   - A path takes units of its isolate's one pool (`contract/src/slots.ts`) only while its bytes are in hand: a step's units are taken before the bytes it brings arrive, held while it sends them, and released once they are gone. No step holds units across another step, the begin or commit of a write, or any wait that holds none of its bytes. A chunk-wise edit takes and releases them per chunk read and per chunk made and put.
   - A hold is weighted by what it holds, in units: a body window one, an edit's making step, an append, a whole body's read each its declared cost (`owner/src/holds.ts`).
   - A pool counts units, never bytes. Its capacity, its reserve and its waiters are its Worker's own figures.
   - Units not free are waited for, first come first served within a hold's size class, for up to `SLOT_WAIT_MS`, with a bounded number waiting in each class. Past either the call is refused `busy` before it changes anything; the doors answer `503` with `Retry-After: 1`.
   - A one-unit hold never waits behind a larger one: larger holds leave a reserve free, which only one-unit holds take, and the two classes queue apart and are capped apart. So a co-tenant's small read waits, and is refused, only on other small reads.
   - A larger hold is never starved by one-unit holds: once one is waiting, a new one-unit hold takes only the reserve, so the units they held past it come back to it as they are released.
   - A step's bytes are let go as it ends: the chunks a chunk-wise edit reads are its own, and their buffers are detached once their pieces are taken, so no reference the collector cannot yet rule out keeps a dead chunk alive into the next step.
   - Retained data (the relay's tails) is one exact budget an isolate (`SharedBudget`), split across the objects that hold any. The object holding the most drops its oldest first.
   - The pool and the tail budget are one of the platform's two pinned exemptions from the no-module-state rule (`module-state.test.ts`; the gateway's fetch interposer is the other). They hold counts and membership, never an owner's data.
2. **The queue's terms.** The wait is the queue's own term. Neither Claude's nor ChatGPT's connectors document how long they wait for a tool call; the MCP SDK's default request timeout is the shortest a client is known to keep, and a waiting call is answered `busy` well inside it, so the refusal is told and retried while the client still waits. The leg checks that each class's full queue turns over within the wait at the measured step time. The larger waiters are capped by the memory a waiter holds: no units, but its own request's arguments, which no unit counts yet, as large as the largest body any door takes, so the waiting calls hold no more than the pool's own size again. The one-unit waiters are reads, whose arguments the door has checked; their cap is the queue's turnover.
3. **Ruling 1: the design point** is "rarely exceeded, loud and retryable, with no acknowledged write lost". "Never exceeded" cannot be proven on Workers: what the 128 MB charges, and when V8 collects, are not documented. Output gates and confirmed writes back the design point, and production watches it.
4. **Ruling 2: `file_edit` is no longer bound by `WHOLE_BYTES`.** A chunk-wise edit (`owner/src/edit.ts`) holds a step's units whatever the file's size. Its ceiling, `EDIT_BYTES_MAX`, is derived from what does grow with the file, its time: the worst edit's time a chunk, measured by the leg on the real path, against a request's CPU, with room left for a slower host. No invented limit replaces the old one. Production's wall time a chunk, R2's round trips, is owed live.
5. **Ruling 3: large bodies at the doors.**
   - MCP keeps the SDK's 4 MiB (`MCP_BODY_MAX`). The SDK materializes a message whole, so its ceiling is what the doors' isolate holds a few at a time.
   - `/api` JSON stays at 16 MiB, weighted in the doors' units: one large call at a time a doors isolate, others queued and then refused `busy`. This comes with PR 3b.
   - The raw streamed body on `/api` comes with PR 3b, after the go-ahead deploy.
6. **What CI proves.**
   - **The workerd memory leg** (`npm run test:memory -w owner`, its own CI leg). It runs the real owner object (its SQLite, its doors) and the real `Files` and `Bodies` in standalone workerd with `--expose-gc`, against an in-memory chunk store in another isolate. Every chunk call, and the end of every hold before its release, is a checkpoint: the driver collects the owner isolate and reads its heap and backing stores while the path is suspended, waiting for every concurrent path to park first. Content is distinct in every chunk, so every chunk is remade. It asserts:
     - the control that keeps every chunk reads as growing;
     - the resting isolate, one owner object built, migrated and served, is within `OWNER_BASELINE_BYTES`;
     - each step on its own: at every checkpoint inside a hold, what is live is within the units the pool holds then;
     - each path at a small and a large size, past its steady state, differs by at most a quarter chunk (`fileEdit` through `Files.writeChunks`, `fileAppend`, `Bodies.windows`);
     - each path's peak is within its declared units (and `Bodies.fetch`'s, per chunk);
     - two edits at once hold at most one making step's units more than one;
     - the worst edit's time a chunk keeps `EDIT_BYTES_MAX` inside a request's CPU, and a full queue turns over within the wait.
   - **The counts test** (`owner/guards/memory-counts.test.ts`). From the source constants: the pool's units, the relay's tails, each owner object's relay terms and the measured resting baseline sum within the isolate. It asserts nothing it does not derive.
   - **What neither counts, named, never fitted:**
     - garbage V8 has not yet collected. In workerd it is large and form-dependent, and whether production's 128 MB counts it is unknown;
     - the holds not yet under a unit: a call's RPC arguments (and the waiting calls'), `file_write`'s whole bytes, `file_upload_chunks`' and `file_read_chunks`' two chunks, a placed body as a write sends it (PR 3b); an export's chunks, sync's raw chunks, an inbound body (PR 4); the archive's move (STORE item 2).
   - CI does not claim an absolute figure. Differences cancel the baseline and the framework.
7. **What production proves**, after the go-ahead deploy:
   - the one-Worker probe, with a control that proves it can see a real hold. It answers whether the 128 MB counts garbage V8 has not collected yet;
   - each Worker's `memoryUsageBytes`, P50 and P999, read from Cloudflare's GraphQL analytics. The baseline and the pool sizes are then set from it in a one-line change that cites the reading;
   - an alert on any `exceededMemory` outcome;
   - an edit's wall time a chunk with R2's round trips.

## Consequences

- Each new path that holds large data uses chunks or a stream and takes its units per step. It adds no byte guard and no grant, and the leg runs it at its worst.
- Past the pool, a call is refused `busy` and retried. It is never a lost write.
- A Worker's pool size changes in one line, and only with a cited measurement. The counts test fails if the live terms pass the isolate.
- Until production's probe reads the garbage, the live terms are bounded and the garbage is not: if production counts it, the pool shrinks to fit it.
