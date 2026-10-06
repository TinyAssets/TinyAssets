# Verification contract

No production loads or app prompts. These are synthetic local/CI fixtures.

| Fixture | Required evidence |
|---|---|
| Fixed files in workspaces, runtime and other universe paths | Exact logical bytes per named category; contents never opened; input files unchanged |
| Scratch source and quarantine, own versus foreign lease | Only canonically derived owned lease locations counted; row path strings ignored; no foreign identity in output |
| Released scratch rows | Cleanup-confirmed AVAILABLE history is not confused with currently allocated bytes; inspect state semantics before deciding traversal |
| Hard links and sparse files | Unique inode counted once per snapshot with declared category precedence; logical size not claimed as allocated disk |
| Symlink, swapped directory, FIFO and inaccessible entry | No target traversal/content access; sanitized partial/unavailable result, all descriptors closed |
| Disappearing tree, missing/legacy/corrupt lease DB | Explicit incomplete scope, no schema initialization or quota reconciliation |
| Active SQLite WAL | Real sidecar file sizes included; no checkpoint/database writes |
| Entry, depth, lease-row and cooperative time budget | Bounded observation with explicit truncation; no complete zero fabricated |
| Same-key cold requests; different-key saturation | One scan per key; bounded distinct scans; unavailable busy evidence remains explicit |
| Cache hit after authority change or directory replacement | Existing current admin gate remains authoritative; no stale-authority or replaced-root reuse |
| Unsupported descriptor host | Explicit unavailable measurement; independent status and total-unavailable contract still work |

Windows tests verify portable cache/status/unsupported-host paths, not POSIX
traversal. Local Docker previously failed before tests; the authorized GitHub
Ubuntu/Python 3.11 route must execute new POSIX tests plus affected existing
resource-status/workspace-helper tests. Compare relevant outcomes and skip census
against a pinned pre-change tree. No quarantine edits to excuse regressions.

Before delivery: plugin rebuild, Ruff, strict OpenSpec validation, exact-head
cross-family approval and required CI. After delivery: authenticated public canary
and deployed revision containment. Rendered acceptance remains a separate
owner-held task; no test script or successful rollout substitutes for it.
