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
