/**
 * The sync agent's heap in the sandbox, which bounds the sync-ignore list it compiles
 * (`LIST_BYTES`, `contract/src/sync-ignore.ts`): (`SANDBOX_HEAP` − `AGENT_PEAK`) /
 * `HEAP_PER_LIST_BYTE` holds `LIST_BYTES` (`sync.test.ts`). Measured in throwaway sandboxes
 * of the pinned template, with the sandbox's own Node, on 2026-11-03, by a one-off probe since
 * deleted (untangle B1); the arithmetic and the runs are `limits-audit.md`'s `LIST_BYTES` row.
 * They change with the sandbox's VM, its Node and the agent, so they are the runner's.
 */

/** The sync agent's heap limit in the sandbox (Node's own, the agent spawned with no flag). */
export const SANDBOX_HEAP = 460_587_008;
/** The sync agent's own heap at its peak over a sync-out of 500,000 entries, measured there. */
export const AGENT_PEAK = 70_027_000;
/** The worst class's heap (and typed arrays) a list byte, its lines' heads diverging. */
export const HEAP_PER_LIST_BYTE = 48;
