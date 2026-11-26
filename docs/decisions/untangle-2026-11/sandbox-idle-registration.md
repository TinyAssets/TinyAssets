# Releasing an observed sandbox wait

An owner can be idle when it asks to leave and have a new waiting run by the time Slots
receives the request. At `287dd097` an ordinary Workerd regression lost the newer
turn. When a peer refreshed instead, the same idle leave correctly advanced the line.

## What identifies a wait

Two local Slots objects on `1b98974a` each registered A, then B. One registered A
again and the other registered B again. After eviction the five application tables
matched byte for byte when serialized in table and row order. Both queues still had
A at position 1 and B at position 2, with the next position counter at 4.
Neither the FIFO position nor the queue-wide counter identified who had refreshed.

At `b2ef470c` a repeat wait takes a fresh value from the existing allocator while
keeping the owner's position and backoff. An apparently idle Owner reads the value,
rechecks its runs and releases that registration conditionally at Slots. A normal
wake of a marked wait still goes straight to readmission. Erasure can remove the
owner irrespective of its registration.

## Verified behavior

- In six three-Worker cases, the same owner kept a refreshed turn, a peer's refresh
  allowed the idle head to leave, both start-reply orders launched the waiting run
  once, and cancellation advanced the next owner both warm and after eviction.
- Standalone, 56 Owner tests passed. The sandbox suite had 167 passes and the prior
  successful-placement notification failure. The combined candidate had eight focused
  Owner passes and 214 sandbox passes. The 15 failing sandbox test names matched
  the previous combined run exactly. Both full platform typechecks passed.
- A first-schema fixture with two queued owners, an outstanding reservation and
  prior backoff survived migration and eviction. Old registrations could neither
  remove a refreshed wait nor remove the owner after it left and rejoined.

## Mixed versions and remaining scope

The ring still carries just the owner. A real Doors consumer acknowledged and dropped
an experimental ring carrying an extra registration field. Its ordinary message
reached Owner. Changing the shared erasure message parser would reach a second queue.

A new Owner with the old Sandbox woke a marked wait. When later idle, its registration
read failed and the ring consumer requested retry. The head retained its place.
In the opposite combination an older Owner still issued its original unconditional
leave. It lost the same owner's refreshed wait, while a peer refresh advanced correctly.
Old Slots code refuses the newer on-disk schema. These are coordinator integration
constraints. The idle-leave change does not recover registrations already lost.

## Duplicate delivery and local eligibility

The first ring clears the run's wait flag, making it eligible for local readmission.
A second ring can arrive before that starts. At `88f59875` the ordinary Owner test
issued a leave. At `244ce35e` a duplicate delivery consults the same eligibility
query the runs lane uses. A woken sandbox wait can retain its turn while it can be
readmitted. When tier capacity prevents readmission, the peer can advance.

The two ordinary Owner files passed 57 tests and the combined Owner file passed nine.
All 5,184 SQLite scenarios on each of Node 22 and 24 preserved the previous choice
of the next run in ordinary admission. The new leave decision matched the expected
result for each scenario, including 234 where a ready run kept its turn.

Both full platform typechecks passed. In a separate three-Worker replay with both
owners eligible, the older run launched first and the peer launched second. Sampling
the FIFO after the duplicate was insufficient to verify this: the scheduler could
already have started the first run and correctly advanced the peer. The provider's
launch record and the Owner's outgoing calls distinguished those outcomes.

With its tier temporarily unavailable, the older owner released its registration
and the peer started. Reading file status with the restored tier and draining due
work left the earlier run waiting in this replay. A subsequent directory creation
and drain started it. Review how a fresh control record makes local work runnable
on a read before relying on tier restoration alone to restart an idle owner.

## One placement notification can arrive too early

Two local three-Worker rehearsals on the registration candidate added an experimental
notification to the owner acquiring a sandbox, before releasing its registration and
notifying the next owner. Both notifications reached the test queue. The new placement
was a second run for the same owner under a different authority. The peer retained
the next turn for an allocation.

- When the original wait reply was held, Owner processed its notification while the
  original start was still queued. After the peer started and the held reply arrived,
  the original run was waiting. The line was empty. Freeing another slot produced
  no further notification.
- When the original wait had already been saved, the notification woke it for
  readmission. The peer and the original run both started.

Keep this experimental send out of the candidate. A successful enqueue does not establish
that Owner had saved the affected wait. The placement design still needs to retain a
way to wake that run across a delayed reply and eviction while honoring the peer's turn.

## Readmission when local capacity returns

On `a9f8e30c`, a fresh control record from a binding or a KV read wakes the
Owner schedule when the stored copy changes. Setting the owner's tier uses
the schedule that already checks local work. The run is still subject to
its conversation, its tier and the sandbox line.

Both a larger limit and a free-to-paid assignment failed to restart the run
on `936dd497` in local workerd. The three Owner suites on the staged tree
passed all 75 cases, including direct KV refresh, and full Node 24 platform
types passed. Two independent three-Worker replays used two owners. The
eligible owner launched first. When its tier was missing, the peer launched
first and reading the restored record restarted the waiting owner without
another ring or directory write. In both replays each owner launched once.

- [x] Keep the fresh control record through repeated checks of the same call.
  An ordinary code script saw the current free limit of five. Without a tool
  call it kept five. After a collection read, the owner's kept copy instead
  showed six from the original binding. The nested-call regression fails on
  `a9f8e30c`. On `6027c2fb` an interrupted chunk write also restored the
  previous free limit when its final unit rechecked the caller.

At `71c8e589` each external Owner call consumes its control observation once.
Subsequent units and nested tools still recheck their caller. The checked
binding retains its freshness signal for sign-in. A later independent call
can hand back the same bytes. The wire format carries no source revision.

The five affected Owner Worker suites passed 153 tests, including sign-in,
files, code mode, and runs. The binding and control guards passed 101 cases,
the Owner door and config guards passed 145, and full Node 24 types passed.
With the other staged sandbox changes, full types and 14 focused Worker
cases passed. A combined Worker attempt exited 137 before the serial pass.
The earlier combined tier-only check passed nine cases and full types on
serial retry. A stronger chunk-write control starts with the old record
already kept. The baseline still restored that old record after the newer
read. The fixed write retained the new record and then accepted an
independent delivery of the old bytes.

## Sign-in can also place the sandbox

The same placement path serves a run and an interactive engine sign-in. In
the ordinary sandbox regression the sign-in made a box, the peer kept the
next allocation, and a direct retry of the earlier run joined the existing
box. Only the peer received a notification. The existing run-sharing control
passed. A disposable extra send made both tests pass. The crossed-reply
case above still rules out treating that send alone as a fix.

Four local Owner, Sandbox and Slots replays used a fake provider. With the
earlier wait saved, a successful sign-in stranded that run whether the two
calls used the same authority or different authorities. The peer launched,
a capacity release produced no further notification, and an explicit ring
started the original run. With the wait reply held and distinct authorities,
the same sequence stranded the run after the reply arrived. With the reply
held under the same authority, sign-in instead returned `sandbox_lost`.
The original registration remained first, and both runs subsequently
launched once in queue order. The first trial incorrectly expected sign-in
to succeed in this last case. Its refusal and retained place were then
verified together.

## A notification crossing two dispatched runs

The new Owner regression holds two real start replies at the sandbox boundary,
delivers the same ring twice, then releases the replies in either order. Both
runs were already dispatched when the ring arrived. Each needs one opportunity
to retry. The first retry can launch while the other parks again. The existing
Owner missed the first retry in both orders.

A disposable Owner recorded the ring on each outstanding dispatch, consuming
it once when that dispatch received its wait answer. Both orders worked.
Three delayed duplicate deliveries crossing three successive attempts at
full capacity caused three extra attempts. The run parked after the fourth
attempt and a further scheduler pass made no call. A generic queue message
has no placement identity with which to recognize later redelivery.

Two local three-Worker replays also added a disposable placement notification
at Slots. Delivering only actual queue messages launched both owners once in
the expected order, whether the earlier wait reply was held or already saved.
An earlier harness that withheld later real messages timed out in the saved
case. The corrected harness delivered one placement notification to the
original owner in each case.

There is a separate obligation at Slots. With the seat already removed, a
rejected send before enqueue left the placed owner with no notification. A
send accepted before its acknowledgement was lost left that owner notified.
After eviction, the peer still received its normal head notification in both
cases. The two expectations passed and both injected send failures surfaced
as unhandled rejections. The experiment does not establish reliable delivery.
Placement recovery, duplicate delivery across attempts, and mixed Worker
versions remain open.

On `e288ba98` the complete standalone sandbox suite had 167 passes and two
failures, the ordinary placement and sign-in placement regressions. The joined
suite had 213 passes and 17 failures, retaining all 15 earlier failures. The
two additions were sign-in placement and the outstanding reservation count
during erasure. The erasure test passed in isolation on the same checkout.
Six additional Owner tests covering erasure, discard and socket entry passed.

On the real Owner with a start reply outstanding, an initial attempt to read
runtime-private metadata failed. Comparing all 86 readable table contents
across a ring found that only the jobs table differed. Controls with and
without the ring then confirmed that every other table retained the same
rows. This comparison does not identify the cause of a jobs-table change.
The permanent two-reply regression failed in both orders on the staged
Owner (two starts observed, three expected at the first retry), passed both
on the disposable dispatch experiment, and passed the full Node 24 platform
typecheck with the ordinary source. The experiment has no shipped migration.

At `21f5231a`, all 13 ordinary Owner sandbox-run cases ran together. The
eleven existing cases passed and only the two new reply orders failed. The
joined checkout ran the same complete file with exactly the same results.
Full Node 24 platform types passed on both. The next design must recover a
missed placement after eviction, preserve the next owner's allocation, and
avoid using the same delayed notification to trigger successive attempts.

## Which placement evidence survives at the queue boundary

At `f532e726`, the existing Doors consumer acknowledges and drops a ring
that contains a valid owner and an additional placement field. Its ordinary
owner-only control reaches the Owner. The regression now checks the actual
queue entry and verifies the dropped message produced no leave request.
Erasure uses the same strict owner-only parser. A producer cannot start
adding a placement token and rely on a successful send or acknowledgement
as proof that the Owner saw it.

Two real Slots objects began with the same owners at positions one and two.
One completed a placement for the head. The other released its observed wait.
After eviction the full contents of their readable SQLite tables matched,
the peer was first in both, and the only queue recipient in either history
was the peer. After the peer left, the tables matched again and neither
object had an alarm. The comparisons passed on this branch and on the joined
sandbox candidate. A subsequent lookup of current line membership alone
cannot recover the missing distinction.

Any design retaining the owner-only doorbell must preserve the placement
outcome independently of the removed wait until the Owner can consume it.
An extended message would also need compatible Doors before producer
activation and a recovery path for old messages. Keep erasure's parser strict.

## A provider resume can succeed before preparation fails

With two slots, a paused owner and its peer waited in that order. When room
returned, the first owner's provider resume succeeded but its runner's
prepare failed. The real Sandbox Worker left that owner at the head. The
peer was refused the spare slot. This failed on both the standalone and
joined candidates.

An ordinary resume now records its use of the turn after the provider
confirms it and Sandbox records the running box, before contacting the
runner. A background resume that holds its own slot bypasses this step.
The new case and the previous prepare-on-retry case passed together on
each candidate. The peer launched in the spare slot, then the first owner
repaired preparation and launched in its original box. A lost provider
response is a separate case: confirmation is absent. Reliable notice to a
different run of the placed owner is still open.

On `da6d24cf` the provider connection and runner preparation are two
distinct steps. The follow-up makes that boundary explicit in the source.
The ordinary start releases its turn between the steps. A held resume goes
through both without releasing a queued turn. A new Workerd control verified
that a different authority of the same owner kept its existing turn while
the alarm resumed an active leased sandbox. The peer remained second on
both candidates.

The reboot case remains open. After a confirmed resume and a failed prepare,
the retry after eviction issued prepare and launch in both pause modes.
That satisfies a full-memory resume. The filesystem-only resume also needs
recover before launch. The same permanent regression failed on both
candidates; the full-memory control passed on both.

A separate four-case Worker experiment at `9f5297b0` lost the connect
response after the fake provider made the same box running. After eviction
both retries launched without preparing, the filesystem case omitted
recovery, and the peer still waited despite spare capacity. With an
acknowledged response, both modes prepared, the filesystem case recovered,
and the peer launched. The existing running-box path sees no pause mode in
the fake's running info. Preserve the observed resume requirement across
the provider call and its completion before relying on that path. Also
verify retry after a lost recovery-command acknowledgement before choosing
how to complete the obligation.

## What survives an interrupted resume or restore

On `c325ee55` the ordinary sandbox Worker and guard entry passed 170 and
failed three. The failures were notification after ordinary placement,
notification after sign-in placement, and filesystem recovery after failed
preparation. The combined candidate passed 217 and failed 17. Both full
platform typechecks passed. An earlier unfiltered invocation included the
privileged runner and other suites. It recorded 267 passes, 26 failures and
177 skips standalone, and 314 passes, 42 failures and 179 skips combined.
Some privileged cases could not start a namespace. Those runs do not establish
privileged acceptance.

A disposable resume candidate saved the observed pause mode before connecting.
Twelve local Worker scenarios covered both modes, eviction, failure before and
after a provider action, and loss of a command acknowledgement. A lost prepare
response repeated prepare. A lost recover response repeated recover. An
acknowledged recover followed by failed info did not repeat recover. All used
a fake provider. A separate experiment exercised the shipped runner with
synthetic children. On Node 22 and 24, sequential recovery while the recorded
child was alive left one child. Two recoveries that both observed a dead PID
before either saved its replacement left two live children for the same run
in either release order. On Node 24, interrupting just one recovery after
spawn and before recording its PID left a live child. A later sequential
retry spawned a second. The PID file identified only the later child.
Serializing the calls alone does not cover interruption between spawn and
publication. Vendor execution and live provider behavior were outside the test.

A local Owner Worker restored the same saved generation twice. It held a
waiting run at the time of backup. On the first restore, admission, a fresh
tier read and draining due work left the run waiting. A ring dispatched it.
After that dispatch the Owner was lost again. Restoring the original generation
produced the same waiting row. Another ring issued a second successful start
request for the same run ID to a recording Sandbox. The first two-restore test
used the aborted stub a second time and failed at reset. The rerun obtained
a fresh stub for each loss and passed. The existing restore rule for a live
run with a missing lease does not cover this saved wait. Determine how Owner
can reconcile the later execution before replaying a restored wait. The
recording service counted start requests, not effects from a real engine.

A separate offline Slots model exercised 216 SQLite histories. A durable
placement record outside the FIFO survived failure before enqueue, missing
acknowledgement and eviction. Matching the acknowledgement to one placement
preserved newer records. Finishing an older placement could still delete the
same owner's newer registration with an unconditional take. A conditional
take alone suppressed the placement when the registration had already changed.
Record the placement and release the observed registration as distinct events.
An empty FIFO removes its alarm, so pending delivery also needs a retry wake.
Eight failed sends to one owner blocked a peer when each retry started with
the oldest undelivered send. Independent send attempts reached the peer in
all eight cases.

On the Owner side, waiting for every outstanding dispatch before acknowledging
one placement allowed a fast run to keep trying while another dispatch stayed
open. Eight duplicate deliveries produced ten fast attempts and two slow ones.
In 72 model schedules, recording receipt once and marking the already outstanding
dispatches in the same Owner transaction let each affected run retry once.
These models have not established a mixed-version protocol. Older Doors drops
a ring with an extra field and older Owner cannot acknowledge a new placement.
Recovery after a lost provider response, interrupted runner recovery, restored
waits and retry of the notification itself remain distinct integration cases.

## A restored wait can reach an existing runner

At `c325ee55`, the recording Sandbox received the same run ID twice with
distinct lease IDs, the same epoch, and a new opener request both times.
A local Worker replay then exercised the real Owner, Files, Sandbox and Slots
with a fake provider. The first start waited at capacity. After backup,
each of two restores of that one generation stayed waiting through admission,
a tier read and draining due work. Ringing the Owner after each restore
sent launch for that run to the same sandbox twice. No ring delivery failure
was needed. The fake provider recorded commands and did not run an engine.

Three isolated Sandbox Worker controls exercised a still-live box, a box
released before eviction, and a fresh box. The live and released boxes each
received a second launch for the same run on a fresh lease. The fresh box
received one launch. All three passed standalone and combined, with both a
repeated epoch and an incremented epoch.

With the shipped launch and cancel commands and a synthetic detached child,
four OS controls on both Node 22 and 24 distinguished what cancellation
reached. Replaying a live run left two children and one state file. Cancel
returned success and the first child still lived. After a settled run, the
second launch started a new child and cancel stopped it. Distinct run IDs
kept separate state files and cancelling one left only the other running.
A fresh run started once and cancel stopped it.

The runner suite now includes a case that keeps a fake CLI active, uploads
the same run under a fresh lease, repeats launch, cancels and checks the
original runner process. The ordinary privileged entry stopped at its root
preflight on this host (exit 2). The native synthetic-child result establishes
the spawn and signal behavior independently. It does not establish a vendor
effect. Before relying on replay, reconcile the restored Owner's dispatch
with the surviving execution and verify cancellation in the privileged suite.
Both full platform typechecks passed with the new runner case.

## Where replay evidence ends

Standalone and combined, two real Worker histories started with the same
running box and the same Sandbox SQL fixture. One launched run A, the other
run B. Releasing their common lease while the provider still reported live
work removed the launch rows in each. After eviction all application SQL
tables compared equal. A later absence from that table cannot distinguish
the two past launches.

Each candidate also passed two restore comparisons with the real Owner,
Files, Sandbox and Slots and a fake provider. With the original box retained,
two restores of the same waiting generation sent launch twice to that box.
With the original box gone after the first launch, the second restore sent
the same run to a new box. A record kept only on the first box would be
unavailable in the latter history.

The ordinary pin and undo paths exposed a separate restore failure. On both
candidates, pinning the earlier waiting generation after the run launched,
wiping, and restoring that pinned generation loaded the waiting row. A ring
sent launch again. Choosing the undo generation written during the pin
instead failed inside restoreStep before admission. SQLite requires a lease
ID, epoch and heartbeat on a queued or running run. The export deliberately
omits the lease ID. The pin requests cancellation of a live run and can
write its generation while the run still has a live state.

The same failure reproduced with the Owner source on main `d662e63e` and
on `c325ee55` by backing up one genuinely running row, losing the Owner
and restoring it. No slot queue, pin or duplicate delivery was needed.
The ordinary restore Worker suite now has this regression. R5r expects a
live run without its lease to settle unknown. The row fails to load before
that settlement can run. Reconcile restore with that rule and retain the
exclusion of the old lease claim. Historical saved waits and safe runner
cancellation require their own evidence.
The standalone full platform typecheck passed with the added restore test.

## Restoring a live run without granting its old lease

A queued or running row needs a 32-character reference to satisfy the runs
table. Restore now generates a fresh reference for that row alone. It does
not insert a sandbox lease or a run grant. This preserves the exported state
for the row comparison and lets the ordinary lost-run settlement record
`unknown/lost` and remove the saved input. Other run states need no reference.
The checked-in regression from `c66d25e1` now checks those outcomes as well
as completion. The focused Worker check and full platform typecheck passed
during development. Replay of an earlier waiting row remains a separate issue.
