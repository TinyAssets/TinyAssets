/**
 * The `SANDBOX` interface (ADR-034 decision 4.2; `engines-replan.md` §2.1 step 3; its types
 * the contract's, `contract/src/sandbox.ts`): what the owner's object asks of the
 * `tinyassets-sandbox` Worker (`platform/sandbox`, slice S), the only holder of the E2B key,
 * over a service binding. Slice R defined it; slice S built the
 * Worker, bound it (`wrangler.jsonc`) and held it in the owner config guard's `SERVICES`
 * (a Worker that reaches nothing but E2B, with one class of its own per owner that
 * serializes that owner's E2B calls and renews each sandbox before its runtime ceiling).
 *
 * - `start`: resume or create the owner's sandbox and start the in-VM runner for one run, in
 *   the background (E2B's `commands.run(..., { background: true })`), answering at once.
 *   `tokens` and `network` come only with the start that opened the sandbox's lease: the
 *   sandbox is then (re)configured with that network, whose transform carries the egress
 *   token, and the runner's sync agent takes the sync token. A run that joins a live lease
 *   gets neither: its sandbox is configured already. `config` is the run's whole
 *   environment (slice E's `runEnv`, G4's `sandboxEnv`), which the runner sets as it is,
 *   from an empty one. The runner reaches the object only by `urls`: the run's MCP door,
 *   its event door and the sign-in report door (`signin`: each engine that signs in with
 *   the vendor's own login, whether its record's `status_argv` succeeds, and the models it
 *   offers; presence only, never a credential), all on the egress host, where E2B's
 *   transform sets the egress token outside the VM.
 * - `cancel`: stop the run's CLI (SIGTERM, then a kill after a grace; "Stop a run with
 *   SIGTERM", Claude's headless docs). The run settles from the runner's last report, or
 *   `unknown` if the runner is lost.
 * - `status`: whether the run's runner still runs, for slice S's lost-runner checks; the
 *   object's status doors read the run's receipt, never this.
 * - `release` (slice S): the lease ended, so the sandbox is paused unless a run started in
 *   it since (zero idle tail).
 * - `unwait` (slice S): the owner has no run waiting for a sandbox any more, so it leaves
 *   the platform's line and the next owner in it is rung.
 * - `erase` (slice S; the erasure's step 2): every sandbox of the owner killed, paused ones
 *   included, and the sandbox Worker's own state of the owner deleted.
 *
 * A start may answer `sandbox_waiting` (`SANDBOX_WAITING`): E2B runs as many sandboxes as
 * its plan allows, and the run waits (`waiting`), never refused, until the platform's line
 * rings the owner (`sandboxRung`, through the doors Worker's ring consumer).
 * - `meter` (slice P): the Worker's own log of the owner's sandboxes started, paused,
 *   resumed and killed, each with its authority and time, after a cursor; the object reads
 *   it into its sandbox time (`sandbox-time.ts`), and the Worker may drop what a cursor
 *   passed. It limits nothing: the founder's cost view.
 */
import type { Started } from "../../contract/src/sandbox";

/**
 * The start's answer when the E2B team runs as many sandboxes as its plan allows (the
 * control record's `sandbox_concurrency_ceiling`, or E2B's own refusal), or another owner
 * waits ahead in the platform's line: the run is never refused for it. It waits
 * (`waiting`, the app's "waiting to start", `sandbox_wait`) until the line rings the owner,
 * and the `runs` lane then starts it again, admitted whole (slice S).
 */
export const SANDBOX_WAITING = "sandbox_waiting";

/** A start's answer, checked: a refusal's code is a fixed code, never prose. */
export function started(value: unknown): Started {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const answer = value as Record<string, unknown>;
    if (answer["started"] === true && Object.keys(answer).length === 1) {
      return { started: true };
    }
    if (answer["started"] === false && typeof answer["code"] === "string"
        && /^[a-z][a-z0-9_]{0,63}$/.test(answer["code"]) && Object.keys(answer).length === 2) {
      return { started: false, code: answer["code"] };
    }
  }
  throw new Error("the sandbox's start answered out of shape");
}
