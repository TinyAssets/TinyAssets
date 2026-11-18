# Event lifetime in the gateway meter

The meter uses the pinned pi-ai adapter for three purposes. Two offline
probes inspect the outgoing request. Settlement in `meteredUsage` reads the
last event of the provider reply. On main `f934871ad` and combined
`151c2a83f` the common `run` function retained an array of every event
until the adapter finished. All three callers can consume the identical
stream while retaining only its last event.

The gateway still drains the stream. pi-ai 1.4.0 queues events not taken
by its iterator, even if a caller waits only for the final result promise.
Settlement needs to distinguish a `done` from an `error` or empty stream.
The adapter still owns the assembled message and its usage calculation.

In a replay on Node 22 and 24 the real adapter read ordinary synthetic
chat-completions SSE in a network namespace with loopback only. The meter
observed 1, 256 and 1,024 text-delta events. Weak references measured the
events still alive after garbage collection with the provider paused before
its final frame. Main and the combined build retained 1, 256 and 1,024.
The changed collector retained one in each case. Complete replies settled
at the same 1,825 tokens and 0.00012197 dollars on the pinned test model.
Missing usage, an empty stream and a cut before any final usage still
returned unknown. Both request-learning probes still read the request.
This comparison measured event lifetime, not the Worker's total memory.

The existing tests in `gateway/test/meter.test.ts` exercise the declared
sources and price rules. The owner's receipt states, model ranking, and
the later pricing of server-side tools are separate decisions.
