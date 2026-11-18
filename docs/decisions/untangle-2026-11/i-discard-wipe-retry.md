# Import discard wipe retry as built (2026-11-18)

A pending import can hold private collection rows without having a files namespace.
On f934871, the import discard latched its namespace before SQLite's deleteAll completed.
After an injected storage failure, the next delivery on the same instance acknowledged the
discard while its collection table and private row still existed. An owner with files
had the same failure. A rehearsal wipe uses that answer as evidence for the cutover.

The erasure module owns both the database wipe and the retry state. It retains the
namespace before attempting the bare database wipe, and records completion only after that
wipe resolves. The last prefix sweep still runs on every successful delivery, including
after an earlier sweep failed. Concurrent deliveries join the active attempt. Each call
passes the door's existing binding and admission checks. Other doors stay closed once the
database wipe begins. After a successful wipe the same instance returns its original
namespace, including a genuine null for SQL alone.

The workerd regression runs through the import door, refuses deleteAll once at the storage
boundary, reads the retained private row directly, and requires the table to be gone before
accepting a retry. The files gate holds a real prefix deletion so that overlapping requests
can be counted. The discard leaves no tombstone. Once the object is evicted, another
instance can answer null. Preserving the witness across that boundary and fencing a
restaged database remain part of the import attempt protocol.

The 24 import cases passed on this change. The four behavior recordings matched
all 88,378 canonical lines on f934871. The memory leg returned 99 measurements and
all 66 verdicts passed. An independent workerd case held the final files sweep,
lost its answer, wrote a late object, retried the discard and evicted the owner.
Its assertions passed on this branch, but workerd also reported the existing
post-eviction scheduler error against the removed jobs table. Running that same
case with the independent schedule fix at `74218055` completed cleanly. The two
changes also passed the combined 43 owner import and erasure cases.
