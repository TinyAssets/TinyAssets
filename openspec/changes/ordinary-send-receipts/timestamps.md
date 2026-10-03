# Preserve event time across exact receipt projection

Design addendum, no runtime change. The reported failure reference
`57795a2128824e98a56f38f370bcfb6c` is a correlation observation only. The displayed
founder time reportedly changed from 3:02 PDT to 3:17 after navigation, while the
failure displayed 3:17. No live request/account/provider was inspected. This is
not evidence of a fifteen-minute backend run.

## Existing source and hermetic characterization

`sendTurn` captures `opts.sentAt` or client `Date.now()` and renders the founder
bubble with that value. `record_failure` and `record_exchange_turns` callers in
`universe_server` omit `ts`; conversation_store `_record_pair` then assigns its
recording time to BOTH founder and terminal rows (or uses the earliest supplied
interjection for the founder). `drawHistoryTurns` renders stored `ts` on reload.
The original ordinary send time is absent from this terminal projection.

A second mismatch is `settleSteered`: when returning an undelivered/unknown
steered line to the queue it overwrites both `opts.sentAt` and queue `ts` with
`Date.now()`, although the already-visible bubble retains its initial timestamp.

`proofs/test_timestamp_projection.py` executes the actual store and real rendered
app in Chromium with synthetic local transport/data. Two cases demonstrate live
send echo -> terminal persistence -> actual page reload for success and failure;
a third demonstrates the steering requeue mismatch. These tests deliberately
PASS on the defect. They are characterization, not fixed-behavior acceptance.
No network outside the local fixture, provider/effect or actual device is used.

## Required exact-receipt behavior

- Capture `client_sent_at` once at the user's original send/queue action, persist
  it with the scoped local intent, and include it as immutable informational
  request metadata in preparation. Restores, resends under the same receipt,
  steering transitions and rerenders preserve it. Repeated identical text under
  distinct receipts keeps distinct original timestamps.
- Validate finite/range-safe client metadata; absent/invalid remains unknown.
  Treat client time as a client clock observation, never authentication, ordering
  authority, admission proof, duplicate detection or permission to replay. Bind
  normalized metadata into the preparation snapshot. Changed metadata on a known
  key is a conflict, never a reason to recreate it. Do not silently repair a key
  by overwriting its original time.
- Record separate server `prepared_at`, `admitted_at`, `terminal_at` and first
  `projected_at` at their actual durable boundaries. Repeated reads/projections
  do not advance these fields. Preparation is not execution admission; delayed
  projection is not completion. Server clocks can change too; no display-derived
  elapsed-time or mixed client/server clock subtraction is a runtime duration.
- Project founder time from the exact immutable client timestamp and terminal
  time from the exact terminal snapshot. If original send time is unavailable,
  label any admission time explicitly as accepted time or leave send time unknown;
  do not relabel terminal/render time as sent time. Legacy history remains honest
  about available data; do not retroactively recover it by matching text/time.
- Use exact receipt/row links for founder/failure pairing and settlement.
  `drawHistoryTurns` currently infers a failure pair from equal timestamps; split
  timestamps MUST NOT silently break that affordance or replace it with text
  matching. Receipt projection supplies exact relationship identity. Preserve
  store turn order/pagination independently of potentially skewed client clocks.
- Account/home/agent switching never borrows another scope's local timestamp.
  Timestamp correction cannot settle an unknown receipt, flush held work, or
  replay any request. Unknown legacy outcomes stay unknown.

Implementation touches exact receipt metadata, conversation projection/read
shape and app rendering; parent must coordinate those shared files with existing
lanes. Before runtime acceptance add fixed-behavior tests for exact success/failure
reload, lost response and delayed projection, repeated text, multiple surfaces,
queue/steer/retry, invalid/skewed/missing times and scope switching. This addendum
specifies the target; the current characterization does not implement it.
