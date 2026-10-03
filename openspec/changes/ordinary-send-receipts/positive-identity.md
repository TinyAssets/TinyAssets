# Positive serving identity: inherited process handle and epoch channel

Current candidate; independent exact-head design approval and parent scope
coordination required BEFORE production edits. Supersedes design.md sections 7–8's
rejected owner_state-positive interpretation. Existing owner_state behavior stays
unchanged for ALL callers. No receipt/runtime/liveness source files changed yet.

## Trust and interface

The actual launcher is engine_mcp_http._EngineServer.start, called by the serving
process, and the engine target is `python -m tinyassets.engine_mcp_server`.
Use that existing trusted launch boundary. An engine request, model, receipt
payload, route query, saved PID, lock sidecar or heartbeat cannot construct a pin.

The original serving process explicitly creates an owned ServingEpoch before
preparation or engine launch. Its unique epoch label is non-secret correlation,
never authority. It creates BOTH:

1. A pidfd on ITSELF with os.pidfd_open(os.getpid(), 0). This names the kernel
   process object; the engine never reopens a numeric PID or checks pid/start-time
   guesses. Supported deployment already uses Linux; unsupported/denied capture
   makes keyed admission unavailable, with no degraded identity fallback.
2. A nonblocking CLOEXEC pipe. Only the issuing epoch owns its writer. Explicit
   retirement closes that writer irreversibly; exec also closes it. The read end
   is observational. A pidfd alone is insufficient because a kernel process can
   stay alive across serving retirement or exec.

The receipt row stores the server-owned epoch label plus original BOOT/payload/
scope. PREPARED -> STARTED checks BOTH current BOOT and current active epoch;
a new lifespan in the same process cannot start the old epoch's preparation.
Enqueue/publication also check the exact original active epoch. Existing scope,
CAS, effect authority and no-replay rules still apply. An epoch is not a lease,
leader-election mechanism, handover permission or a provider-work capability.

Narrow new process_liveness interface, separate from owner_state:

- `ServingEpoch`: private owner of self-pidfd, pipe pair, label and lifecycle lock.
- `epoch.launch_binding()`: context holds the epoch lock across Popen, exports
  duplicate pidfd/read descriptors and their exact immutable label. Never exports
  the writer. Exits by closing parent copies on success AND spawn failure.
- `adopt_engine_issuer(...)`: internal one-time bootstrap only; consumes the
  trusted launch metadata, validates the observer descriptors, immediately marks
  them CLOEXEC/non-inheritable, and owns their lifecycle. No public endpoint or
  model-callable constructor. Missing/invalid pins refuse keyed delivery.
- `observer.state(expected_epoch)`: returns positive ALIVE only for an exact
  stored-label match, a valid permitted original process handle, and an open
  epoch channel with no data/EOF/error. Every other result is UNKNOWN/held.
- `epoch.retire()` / `observer.close()`: irreversible, idempotent, synchronized
  ownership transitions; closed objects never act on recycled descriptor numbers.

Actual names may follow repository style; none of these methods grants authority.

## Positive observation and ambiguity

Validate descriptor type/permission using pidfd_send_signal(fd, 0), which performs
checks without delivering a signal. Poll the original pidfd and epoch reader
nonblocking. Termination, data, EOF, HUP, ERR, NVAL, permission failure, unsupported
syscall, malformed metadata, closed descriptor, or any unexpected probe error
holds. Do not convert generic lock failure into ALIVE. No lock files or numeric
PID fields are consulted by this observer. No alternate credentials or permission
changes are attempted on denial.

Trusted launcher construction establishes which epoch the inherited kernel
objects belong to. The engine cannot assert that arbitrary inherited descriptors
belong to a supplied epoch. Bootstrap metadata is overwritten by the launcher,
consumed before ordinary initialization, and removed from the child environment;
it is private internal launch data, not a new user/deployment setting or secret.
The same validated label is immutable in the root receipt. Existing engine owner/
home/agent/session/live/root and current authority checks are still mandatory.
A matching live pin cannot borrow another owner's receipt or start anything.

At keyed take, preserve author -> steering order: verify exact root binding,
STARTED and open frontier, require the positive observation, commit attempt state,
then observe again before returning input. If identity becomes unknown, return no
input and retain attempted custody. The final observation is point-in-time, not
instantaneous revocation; death afterward leaves already-admitted delivery
uncertain and never requeues/replays. Neither an observer nor a new epoch acquires
a second root start. Old terminals remain read-only recoverable.

## Descriptor and epoch lifetime

Serialize export/admission against retirement. Lock ordering for serving mutations
is existing maintenance barrier -> epoch gate -> author -> one subordinate store;
no author-held caller may acquire the epoch gate in reverse. No provider call is
under an epoch/SQL writer. Engine take has only the observational pin and does not
acquire the serving process's gate. Root-specific state/closure still serializes
against concurrent enqueue as already designed.

The sole writer must never reach an engine/provider/utility child. Explicit
pass_fds includes only duplicate observer handles. All originals and exported
parent copies remain CLOEXEC; observer bootstrap restores CLOEXEC immediately.
Fork hooks close the copied writer and owned observer handles WITHOUT acquiring
inherited Python locks. Export duplicates remain a separate, bounded launch
resource, so deliberate Popen descriptor delegation still works. Every failed
capture/adoption/spawn must close exactly the handles it owns; no arbitrary caller
FD is accepted. There is no repair-by-reopening after descriptor loss.

A replacement engine can inherit the same still-active epoch. A replacement
serving lifespan gets a NEW label and new handles; it cannot revive the retired
epoch. One serving writer/lifespan remains required. No overlap takeover, timeout
lease, PID-based fallback, or dependency on unmerged per-role UID changes.

## Required startup/teardown boundary (hard dependency)

process_liveness.py and engine_steering.py ALONE cannot securely supply these
handles. Current _EngineServer.start has no pass_fds, and current HTTP lifespan
teardown has no issuer retirement. The launcher/bootstrap/lifecycle changes below
are necessary, not optional glue. Do not implement a numeric-PID shortcut to keep
the file list smaller.

Proposed lifecycle: establish the epoch only after existing cloud admission and
writer-barrier checks; start engine supervision from the admitted HTTP lifespan
once storage and run-recovery prerequisites are ready. Move the existing early
engine start in main into that lifecycle, preserving its existing enable flag and
current serving-owner predicates; leave broker/provider policy unchanged. Factory
startup and main startup must use the SAME owned lifecycle, never double-start.
On exit/startup failure, first retire the epoch, then stop/join its engine supervisor
and children, then release existing lifecycle resources/barrier. Start a new epoch
only after old supervision has stopped. A stop that cannot join remains explicitly
unavailable; do not start a competing supervisor or publish replacement routes.
The supervisor must not respawn/export after epoch retirement. Existing run-
recovery-before-engine-start ordering remains mandatory for every startup route.

The engine module adopts observer handles before FastMCP/plugin/provider imports
or child spawning. Process descriptor observation is Linux-only in this candidate;
on unsupported systems the new keyed path fails closed without changing legacy
owner_state, privileges, deployment flags or credentials. No claim that UID
separation is implemented; the trusted platform launcher/engine plus existing
provider jail are the current boundary.

## Exact necessary implementation files

Already assigned/current receipt surface:

- tinyassets/process_liveness.py — NEW separate owned identity interface only.
- tinyassets/engine_steering.py — exact verified scope/receipt/epoch delivery check.
- tinyassets/universe_server.py — ONLY receipt admission and startup/teardown hooks;
  no budget-specific changes to universe_intelligence or served_model_plan.

ADDITIONAL launcher/bootstrap files needing parent coordination:

- tinyassets/engine_mcp_http.py — explicit observer-only delegation, export cleanup,
  and an owned stop/join lifecycle for the existing supervisor.
- tinyassets/engine_mcp_server.py — earliest one-time observer adoption/cleanup.

Each has its exact mirror at
`packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/` followed by
the canonical path above. No singleton_lock.py, provider/effect review, broker,
new endpoint, system setting, secret, grant or UID-migration change.

Focused tests: new tests/test_ordinary_receipt_issuer_identity.py and
 tests/test_ordinary_receipt_engine_delivery.py; existing tests/test_engine_mcp_routes.py,
 tests/test_owner_steering.py and startup/run-recovery ordering fixtures as affected.
Existing conservative process_liveness consumers/tests stay semantically unchanged.
Existing receipt/custody/projection and separately coordinated deletion files remain
as previously enumerated. Budget lane owns universe_intelligence.converse,
_call_writer, extract_learning and served_model_plan until its head is reconciled.

## Executable evidence and its limits

`proofs/positive_identity.py` is a standalone stdlib design model. Its companion
`proofs/test_positive_identity.py` runs actual synthetic serving -> engine child
launches with pass_fds and the three-store receipt model. Cases include genuine
one-time delivery; death/reaping; cleanup contention; denied/errored probes;
retirement with original PID alive; exec; same-process new epoch; engine respawn;
stale/substituted identity and scope; unsupported capture; invalid descriptors;
close/export races; deterministic descriptor-number reuse; fork cleanup; provider
child noninheritance; spawn-failure cleanup; attempted custody after issuer death;
and no second start. It does not force actual kernel PID-number reuse: it proves
there is no numeric-PID lookup after original handle capture, and demonstrates
that a different live numeric PID cannot replace the dead handle.

The 19 original protocol cases remain required. The older engine_incarnation.py
contains two explicitly named CHARACTERIZATION tests of the rejected lock-helper
proposal; their passing demonstrates its flaw, not this proposal's acceptance.
Production launcher authentication, all lifecycle failures, migration/reset and
account-deletion path verification remain implementation proof obligations.
No live provider/account/device or real schema was touched.

Primary API references: [Python pidfd_open](https://docs.python.org/3/library/os.html#os.pidfd_open),
[Python pidfd_send_signal](https://docs.python.org/3/library/signal.html#signal.pidfd_send_signal),
[Linux pidfd_open](https://man7.org/linux/man-pages/man2/pidfd_open.2.html),
[Linux pipe](https://man7.org/linux/man-pages/man7/pipe.7.html).
