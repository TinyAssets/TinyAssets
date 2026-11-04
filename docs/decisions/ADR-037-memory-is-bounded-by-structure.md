# ADR-037: Memory Is Bounded by Structure

## Status

Accepted (2026-11-04: the three rulings below apply the founder's standing rules to the decisions `memshape_research.md` §6 put to him).
- **It replaces** the byte guard and its grants that the platform memory fix's first design used (`memory_research.md`); PR 1 (#5407) removed them.
- **It extends** ADR-035: a large body already lives in R2 by the files plane's chunks. This ADR says how much of one an isolate may hold at once.

## Date

2026-11-04

## Context

A Worker isolate has 128 MB of memory (Cloudflare's `workers/platform/limits`). One owner's object, the doors and the files Worker each run many calls in one isolate. Past 128 MB the isolate is reset and every call in it fails.

The first design counted bytes. Each path asked a guard for a grant of its worst case, and a memory leg asserted absolute peaks. Its review (`rvmem_report.md`) found that the grants could not be derived: a worst case depends on V8's copies, the framework and garbage. The absolute peaks measured the framework and the sampler as much as the path.

`memshape_research.md` (§0, §2, §4, §5) gives the shape that replaced it:
- large data moves only as streams or 4 MiB chunks, never whole;
- a path that holds chunks takes one slot of its isolate's pool while it holds them;
- CI proves a path's cost does not depend on its data's size, by differences between sizes, never by an absolute peak.

**The stop that was a sampler artifact.** PR 2's first leg (`6de199dfa`) found that a Durable Object sending chunks over RPC seemed to keep every chunk until its method returned. That would have broken the shape. `rpcretain_research.md` showed it was the sampler: the leg collected (`/gc`) and then read the heap in a separate step, and the burst of RPCs ran between them. An event log showed no collection at all between the first and the last of eight sends. Paced, or sampled where the path waits, every form is flat. **A swept read must be atomic with its collection.** The leg now samples at checkpoints where the path is suspended on the driver, and it carries a control that keeps every chunk and must read as growing.

The research and review cited here (`memory_research.md`, `memshape_research.md`, `rpcretain_research.md`, `rvmem_report.md`) are the builders' working notes, kept outside the repo; this ADR restates every figure it relies on. Each ceiling and unit cost, with its derivation, is in `openspec/changes/clean-sheet-platform/limits-audit.md` ("Memory bounded by structure").

## Decision

1. **The rule.** An isolate's memory is a sum of counts the code enforces times costs the code constructs. Nothing is estimated at run time.
   - Large data moves as streams or 4 MiB chunks (`UNIT_BYTES`, the files plane's `CHUNK_BYTES`).
   - A path holding chunks takes one slot (`owner/src/slots.ts`) for as long as it holds them, and only then, never across a wait on another service. A slot costs `CHUNK_SLOT_BYTES`: two chunks in hand and the files plane's copy of one, 12 MiB.
   - A pool counts slots, never bytes. Its capacity is its Worker's own (`OWNER_CHUNK_SLOTS`, 4, for the owner object).
   - A slot not free is waited for, first come first served, for up to `SLOT_WAIT_MS` (10 s), with at most four times the capacity waiting. Past either the call is refused `busy` before it changes anything. The doors answer it `503` with `Retry-After: 1`.
   - Retained data (the relay's tails) is one exact budget an isolate (`SharedBudget`), split across the objects that hold any. The object holding the most drops its oldest first.
   - The pool and the tail budget are the platform's one pinned exemption from the no-module-state rule (`module-state.test.ts`). They hold counts and membership, never an owner's data.
2. **Ruling 1: the design point** is "rarely exceeded, loud and retryable, with no acknowledged write lost". "Never exceeded" cannot be proven on Workers: what the 128 MB charges, and when V8 collects, are not documented. Output gates and confirmed writes back the design point, and production watches it.
3. **Ruling 2: `file_edit` is no longer bound by `WHOLE_BYTES`.** A chunk-wise edit (`owner/src/edit.ts`) holds one slot whatever the file's size. Its ceiling, `EDIT_BYTES_MAX`, is derived from what does grow with the file:
   - subrequests: at most 4n for n chunks, of the 10,000 an invocation has;
   - CPU: measured in workerd at about 5 ms a chunk for the worst edit, about 10 s at 2,000 chunks, of a request's 30 s.

   So it is 2,000 chunks (8,000 MiB). No invented limit replaces the old one.
4. **Ruling 3: large bodies at the doors.**
   - MCP keeps the SDK's 4 MiB (`MCP_BODY_MAX`). The SDK materializes a message whole, so its ceiling is what the doors' isolate holds a few at a time.
   - `/api` JSON stays at 16 MiB, weighted in the doors' slots: one large call at a time a doors isolate, others queued and then refused `busy`. This comes with PR 3b.
   - The raw streamed body on `/api` comes with PR 3b, after the go-ahead deploy.
5. **What CI proves.**
   - **The workerd memory leg** (`npm run test:memory -w owner`, its own CI leg). It runs the platform's own edit, window and slot code in standalone workerd with `--expose-gc`, sampling heap plus backing stores at checkpoints, each a swept collection while the path waits. It asserts:
     - the control that keeps every chunk grows by at least three quarters of what it adds;
     - each path at S and 4S differs by at most a quarter chunk (an 8 against a 32 MiB edit; a 4 against a 16 MiB body read);
     - each path's peak is at most a slot's cost;
     - N and 2N concurrent units grow by at most a slot's cost per unit.

     Measured 2026-11-04: the control grows 24.01 MiB of 24; both differences 0 MiB; the edit's peak 8.05 MiB, the read's 4.01 MiB; 10.01 MiB per concurrent edit.
   - **The counts test** (`owner/guards/memory-counts.test.ts`). From the source constants, the owner isolate's slots (4 x 12 MiB), its tails (8 MiB), its resting baseline (`OWNER_BASELINE_BYTES`, 8 MiB, measured in workerd by `memory/baselines.mjs`) and what no unit models (`UNMODELED_BYTES`, 24 MiB: workerd's RSS with garbage left uncollected less its resting RSS) sum to 88 MiB, within 128,000,000 bytes.
   - CI does not claim an absolute figure. Differences cancel the baseline and the framework.
6. **What production proves**, after the go-ahead deploy:
   - each Worker's `memoryUsageBytes`, P50 and P999, read from Cloudflare's GraphQL analytics. The baseline, the unmodeled term and the pool sizes are then set from it in a one-line change that cites the reading;
   - an alert on any `exceededMemory` outcome;
   - a one-Worker probe (`rpcretain_research.md` §5), with a control that proves it can see a real hold. It answers whether the 128 MB counts garbage that V8 has not collected yet.

## Consequences

- Each new path that holds large data uses chunks or a stream and one slot. It adds no byte guard and no grant.
- Past the pool, a call is refused `busy` and retried. It is never a lost write.
- A Worker's pool size changes in one line, and only with a cited measurement. The counts test fails if the sum passes the isolate.
- **Open**, for PR 3b's leg and production:
  - V8's uncollected garbage. With no forced collection, chunks made and dropped level off at about 65 to 80 MiB of workerd's RSS. Byte streams crept to 112 to 177 MiB within 2 GiB without levelling off (`rpcretain_research.md` §0).
  - Parked streams across isolates.
  - The `/mcp` request factor.
