---
severity: P1
title: Scheduler marks an event delivered before it admits its run
filed: '2026-10-05'
summary: A process exit between the committed scheduler delivery marker and run_fn loses the occurrence; replay skips it as already delivered.
---

## Verified code defect

At baseline `29fa5b4686f6df23e22f0a403e6a7046cd54f0e4`, `tinyassets/scheduler.py:482` (`Scheduler._dispatch_event`) reads `scheduler_delivered_events`, inserts the delivery marker in a SQLite connection context, and exits that context before calling `_run_fn` around line 548. Exiting the successful SQLite context commits the marker. The handler's exception branch only logs; it does not create recoverable pending work or undo the marker.

Deterministic failure sequence: an active matching subscription receives event E; its delivery marker commits; the process exits before `_run_fn`; the same E is replayed after restart; the `already` check skips the subscription. No run was admitted, but durable state says delivered. A handler exception before admission produces the same lost retry. This is source-proven control flow, not a claimed new production reproduction or an attribution of the founder's specific incidents.

## Resolution contract

`openspec/changes/zero-impact-deploys/design.md` D6 and task 2.3 own the planned fix: make delivery state a recoverable claim linked to durable work, with stable event/subscription identity and an atomic admission or transactional outbox. Merely moving the marker after `_run_fn` creates a duplicate-run crash window and is insufficient.

Before resolving, inject termination after claim commit and before run admission, and after admission before acknowledgement. Replay must produce exactly one run per event/subscription; a failed admission must remain retryable, subject to current authorization. No runtime fix is included in this design lane.
