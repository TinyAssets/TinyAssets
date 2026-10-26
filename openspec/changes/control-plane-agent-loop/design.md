# Design: the thin agent loop (target architecture S7)

## Context

`AgentTurnCoordinator` already is a vendor-neutral HTTP agent loop: it calls
the model through `ApiKeyHttpProvider`, which reaches the upstream only via
`ConnectionLedger.resolve_exact_scoped_proxy` and the credential-blind broker
worker, and it journals every round and tool call in `agent_turns`. What it
was not is *thin*: each turn ran `asyncio.run` on the worker thread that
claimed it, and its tools came from the per-command-center engine MCP process,
whose four file/shell tools each start a bubblewrap jail. This change keeps
the coordinator, the router, the broker and the journal, and changes where the
turn lives and where its tools run.

## Decisions

### 1. Chat and workflow turns share the execution owner's loop

`ExecutionOwner` submits turns in the caller's context to one loop thread.
Provider-assignment admission identifies asynchronous holders by task, preserves
writer priority with synchronous callers, and waits without blocking the loop.
Journal writes and usage reservation/finalization run off-loop; cancellation
drains an in-flight commit before releasing its authority. Journal mutations
retain the existing owner lease and reject stale generations. Startup orphan
adoption records the prior generation beside the successor fence; that marker
keeps the row recovery-only after adoption, including across another crash.

This checkpoint does not complete the cutover: the provider-admission lock's
move to platform state requires a protected migration with no mixed old/new
workers. The shared broker stream is present. The native package launch path
and the remaining owner-journal storage migration still require coordinated
integration and proof.

### 2. Tools are routed by name, once, never by the model

`open_loop_tools` builds the turn's inventory from its grant:

| Tools | Where they run | Why there |
|---|---|---|
| `read` `write` `edit` `bash` | the turn's box, `BoxProvider.start_exec` | user content lives in the box (D1) |
| `history` `activity` | the loop, read-only | platform-visible records; listing never wakes a box (D7) |
| every other served tool | the engine route, unchanged | its gates (rules, auto-review, consent) live there |

The handle is bound once (`BoxProvider.bind(cc, account=owner, turn=turn_id)`)
when the session opens and held by the session; no call looks a box up by name.

### 3. `op_id` is the journal position; unknown holds

`op_id = "{turn_id}:{round}:{call}"`, computed by the coordinator from the
journal row it has just marked `started`. The D2 contract makes `start_exec`
idempotent by `op_id`, so:

- a lost `start_exec` reply is asked again with the same `op_id`, once;
- a broken stream is resumed from its last offset, once;
- anything still unresolved, `unknown_after_restore` included, raises an
  unknown outcome. The coordinator journals `unknown`, the turn ends
  `held_tool_unknown`, and nothing is re-issued under a new id.

Only `BoxOperationRefused` -- the box host refusing BEFORE the operation
existed (stale epoch, foreign handle) -- is reported as not sent, and only on
the first attempt: refused on the retry means the first may have run.

`edit` is two executions (`op_id/read`, `op_id/write`). The write fills a
temp file, then, under an exclusive `flock` on the target's DIRECTORY, checks
that the target still has the sha256 of the bytes read and renames the temp
file over it. A plain `write` renames under the same lock. The lock is the
directory's because the rename replaces the file's inode: a lock on the file
let a waiter on the old inode and a newcomer on the new one run together, and
an edit was lost (refute round 3, reproduced). Every write through the box
tools is therefore ordered: of two edits that read the same bytes, the second
finds the hash changed and refuses, and neither is silently lost
(`test_concurrent_edits_never_silently_lose_one`, red with the lock removed).

Every wait on the box is bounded and owned:

- each blocking provider call runs on its own thread, so a cancel never queues
  behind the reads waiting for it;
- a cancel the box does not acknowledge within the grace period, and an
  execution whose end is not confirmed by an `exit` event, are unknown
  outcomes;
- a `start_exec` reply that arrives after the turn stopped waiting, however
  late, is cancelled by the thread that receives it (exactly one side cancels);
- box calls in flight per process are bounded (`MAX_BOX_CALLS`), and cancels
  separately (`MAX_BOX_CANCELS`); a box host that stops answering exhausts a
  bound and new calls are refused before they are sent (a cancel that cannot
  be sent is an unknown outcome), rather than threads accumulating.

### 4. The scripts are the tool jail's

The four tools run the same `sh` scripts and return the same text as
`universe_tools`, with argv (never a command string to the box API) and the
box's root (`/cc`, or the handle's `root`). `tests/test_agent_loop_box_tools.py`
pins argument parity with the engine's tools and runs the scripts on a real
POSIX shell.

### 5. Session binding and the mandatory thin route

`make_interactive_agent_turn` selects `ThinLoopChatAdapter` exactly when an
admitted box binding is present. Owner-split keeps the engine-tool adapter.
`control_plane/session.start` binds the authenticated owner/center and stable
durable send or run identity before `load_agent`. File reads use the bound
provider and refuse a changed generation across definition loading. Explicit
parent objects carry the binding across executor boundaries; tools reuse it
instead of rebinding under the later journal turn ID. The ta bridge separately
checks the admitted session identity and retains journal-position operation IDs.

An invalid binding means `box_unavailable` before inference. A client disconnect
does not cancel the box. Only an explicit owner stop or durable run cancellation
calls `end_session`; ordinary completion releases references after dependent
work drains. Same-session reconnect retains operation identity.

The configured production provider has no host-file fallback. The current
owner-split image and cells remain staged until C6's protected migration and
coordinated deletion. Native discovery selects BoxLauncher from its receipted
operation, while the CLI runner's strict host paths and selected git-grant
handoff still need cutover integration. Factory selection alone is not proof
of production parity.

## Measurement: memory per waiting turn

`scripts/measure_agent_loop_memory.py`, 2026-10-01, the Linux oracle image
(python 3.11.16), 500 concurrent turns, a mock SSE server in its own process
holding every stream open with `: ping` until all 500 wait, then completing
them (each folded by `agent_chat_codec.fold_chat_stream`):

| Context per turn | RSS before | RSS with 500 waiting | Per waiting turn |
|---|---|---|---|
| 64 KiB | 59.9 MiB | 171.3 MiB | **228 KiB** |
| 8 KiB | 60.4 MiB | 89.9 MiB | **61 KiB** |

Command: `docker run --rm -v <tree>:/src:ro -w /src tinyassets-linux-oracle:<tag>
python -B scripts/measure_agent_loop_memory.py --turns 500 [--context-kb 8]`.

Read it with what it excludes: the turns share one loop (the target shape;
see decision 1 for why production turns do not yet), and the transport is a
direct streaming client to the mock, i.e. the loop side of S6's streaming
broker. **Today's broker spawns
one worker process per in-flight round**: measured at 29 MiB RSS (18 MiB
anonymous) just for its imports, before it resolves a credential. Until S6,
that, not the loop, is the per-waiting-turn cost of an HTTP turn. Against the
CLI path (~77 MB PSS per waiting subprocess in production) the loop's own
share is two to three orders of magnitude smaller, inside the ~1 MB D6 estimated.

## Risks

- **Interface drift.** `BoxExec` is a structural subset of D2 written before
  the `BoxProvider` module landed; event and status attribute names
  (`kind`/`data`/`offset`/`code`, `state`) are assumptions to reconcile with
  the S4 driver.
- **The write lock orders the box tools, not every process in the box.** A
  `bash` command that writes a file without taking the lock is not ordered
  against an edit of it, the same residual any editor has. `flock`
  (util-linux) is a box-image requirement; without it a write fails loudly.
- **Stream reads must not block forever.** A box call that never returns keeps
  its thread and its slot. The S4 driver must give `stream` an I/O deadline
  (or end it on `cancel`), so a dead box host surfaces as an unknown outcome
  instead of a slot leak.
- **Two tool routes during the cutover.** Owner-split admission uses the engine
  adapter; an admitted box binding selects the thin loop and its box tools. No
  turn ever has both.

## Appendix R. Cross-family refute (gpt-6-astra), three rounds, cap reached

- **Round 1 (5c38ccf0): REJECT, 5 x P1.** Shared loop vs the thread-keyed
  admission lock; cancel during a slow start orphaned the execution; an
  unconfirmed timeout reported as completed; cancel queued behind reads on a
  shared executor; edit's hash check not atomic. All acted on in bb9c7653
  (decision 1 replaced; executor rewritten).
- **Round 2 (bb9c7653): REJECT.** Closed: shared loop, unconfirmed timeout,
  executor queueing. Open: a start reply later than the grace period; edit
  races. New: unbounded cancel wait, thread accumulation, late failures to the
  loop handler. All acted on in 7421361a and 487be261 (refused reader after a
  started command is unknown, found while writing the round-3 brief).
- **Round 3 (487be261): REJECT.** Closed: late start reply, bounded cancel,
  late failures, reader classification, script quoting. Open, both reproduced:
  the file lock's inode is replaced by the rename (an edit lost), and cancel
  threads bypassed the bound. Both fixed in c6418af3 (directory lock, cancel
  slots), each with a test that is red on the old code; NOT re-reviewed, per
  the three-round cap. Taken to the lead with this list.
