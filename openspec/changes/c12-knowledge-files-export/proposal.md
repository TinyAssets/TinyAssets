## Why

S17 is unbuilt: the seeded OKF bundle does not receive subsequent knowledge writes, while memory, retrieval, the graph and mini-brain remain second canonical stores. ADR-017 and ADR-018 require owner-owned files with rebuildable indexes and one usable export; C6 makes the owner's box the only place that may read those files.

## What Changes

- Make the owner's editable knowledge files canonical, with OKF as the default layout. Rebuild retrieval indexes from those files inside the box.
- Give the owner one export action for a complete, runnable command-center folder, with credentials excluded.
- Retire second canonical knowledge stores in bounded slices after their readers and writers use files. Do not extend the C6 one-shot owner migration or add owner-data logic to the shared router, broker or waker.

## Capabilities

### New Capabilities

- `command-center-export`: One owner action produces the portable folder defined by ADR-018.

### Modified Capabilities

- `knowledge-retrieval-and-memory`: Owner files become knowledge authority; graph, text and vector indexes are derived and rebuildable.

## Impact

In-box knowledge, memory and retrieval consumers; `tinyassets/daemon_brain.py`; the seeded bundle; `tinyassets/wiki/okf_export.py`; the owner export door and local bundle runner. Shared components retain routing, credential custody and wake responsibilities only. S17's registry row gains a convergence guard when implemented.
