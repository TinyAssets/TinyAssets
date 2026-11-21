# Owner front-door tests as built (2026-11-18)

List 6 item 9 names two faults the owner tests used to inject inside the object.
The owner's existing, namespace-scoped FILES gate can exercise both through the
same calls used in production.

The run test writes valid JSON padded past `INLINE_BYTES`. It holds the real
`getChunks` answer, rewrites the definition at the file door, and releases the
original bytes. Admission must return `agent_changed`; the run list stays empty.

The erasure tests write a chunked file and refuse the owner's `deleteNamespace`
call. After the put grace, the object keeps its step-2 record, data and alarm.
A single refusal permits a later job or refused door to finish the wipe.
Repeated refusals exercise the job's retry and rescheduling. The gate's
namespace-delete record, together with the fake clock and the tombstone,
checks the interval between attempts. The separate alarm-before-wipe test
still checks the ordering specific to Lifecycle.
