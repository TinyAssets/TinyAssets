/**
 * The codemode Worker's RPC contract (`platform/codemode`,
 * `docs/decisions/untangle-2026-11-wave-1.md` A1): its default entrypoint, as the owner's object
 * binds it (`CODEMODE_VM`), which the codemode Worker implements (`codemode/src/index.ts`) and
 * code mode calls (`owner/src/codemode.ts`).
 *
 * The request and the exchange are pi-codemode's own (`CodemodeRemoteRequest`,
 * `CodemodeRemoteExchange`, from `@earendil-works/pi-codemode/portable`), which both sides
 * import from the package; the contract imports no package, so it names them as its two
 * parameters.
 */

/**
 * One code mode execution, named by the owner (`<owner>/<random id>`): the VM runs in that
 * sandbox object, every tool call goes back through `exchange`, and the call resolves once
 * the VM has stopped.
 */
export interface CodemodeWorker<Request, Exchange> {
  codemodeExecute(name: string, request: Request, exchange: Exchange): Promise<void>;
}

/**
 * Interrupt polls per execution, the script's compute between its tool calls, a technical
 * ceiling (`limits-audit.md`): the codemode Worker's cap on every request
 * (`codemode/src/codemode.ts`), which the owner asks for and names when a script reaches it
 * (`owner/src/codemode.ts`). A script that computes holds no clock (the VM has no timers and
 * `Date.now()` stands still while it computes, pi-codemode's "Limits on Cloudflare"), so this
 * is the only bound on a loop that never calls a tool, and it must end one, with its own
 * error, before the caller stops waiting: 60 s is the MCP SDK's and pi's default wait, and
 * claude.ai's (stage batch 3, `untangle-2026-11/stage-batch-3.md`). pi-codemode measured
 * 100,000 polls at about 10 s of a tight loop on Cloudflare; workerd on this build's host took
 * 46 s. So 50,000: about 5 s on Cloudflare, 23 s at the slowest rate seen, both well inside
 * 60 s, and far under the Worker's `cpu_ms` (300 s), whose reach resets the object instead of
 * failing one script. H2's probe 1 measures it live.
 */
export const CODEMODE_INTERRUPT_BUDGET = 50_000;
/**
 * VM memory per execution, a technical ceiling: a script past it fails inside itself
 * (`out of memory`), where the isolate's 128 MB, which every VM placed in it shares, would
 * reset them all. A quarter of the isolate, pi-codemode's own default: Cloudflare does not
 * say how many objects of one class it runs in an isolate at once, so H2's probe 6 holds
 * eight scripts at it at once, twice what one isolate fits, each to end by its own value or
 * memory alone.
 */
export const CODEMODE_MEMORY_BYTES = 32 * 1024 * 1024;

/** The most characters an execution's name takes: the owner's digest, a slash and a UUID. */
export const EXECUTION_NAME_MAX = 512;

/** Whether `value` can name an execution: 1 to `EXECUTION_NAME_MAX` characters. */
export function executionName(value: unknown): value is string {
  return typeof value === "string" && value !== "" && value.length <= EXECUTION_NAME_MAX;
}
