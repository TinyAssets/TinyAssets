/**
 * The `SANDBOX` interface (ADR-034 decision 4.2; `engines-replan.md` §2.1 step 3; its types
 * the contract's, `contract/src/sandbox.ts`): what the owner's object asks of the
 * sandbox Worker (`platform/sandbox`, slice S), the only holder of the E2B key,
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
 *   gets neither: its sandbox is configured already. Every start, and every sign-in's, hands
 *   the engine's vendor hosts (`vendorHosts`), which the sandbox Worker lets out beside that
 *   network, so it reads no source record. `config` is the run's whole
 *   environment (slice E's `runEnv`, G4's `sandboxEnv`), which the runner sets as it is,
 *   from an empty one. The runner reaches the object only by `urls`: the run's MCP door,
 *   its event door and the sign-in report door (`signin`: each engine that signs in with
 *   the vendor's own login, whether its record's `status_argv` succeeds, and the models it
 *   offers; presence only, never a credential), all on the egress host, where E2B's
 *   transform sets the egress token outside the VM.
 * - `cancel`: stop the run's CLI (SIGTERM, then a kill after a grace; "Stop a run with
 *   SIGTERM", Claude's headless docs). The run settles from the runner's last report, or
 *   `unknown` if the runner is lost.
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
import { keyed } from "../../contract/src/checks";
import { type CredentialKind, START_CODES, type Started } from "../../contract/src/sandbox";
import { engineHosts } from "../../sources/src/served";

/**
 * The vendor hosts a job's sandbox network is let out to (`SandboxStart.hosts`,
 * `SandboxSignin.hosts`): on the vendor's own sign-in (a sign-in's always), the engine's
 * sign-in and model hosts and the chosen region's; none on a key.
 */
export function vendorHosts(engine: string, credential: CredentialKind,
                            region: string | null): string[] {
  return credential === "vendor_login_in_sandbox" ? engineHosts(engine, region) : [];
}

/** A start's answer, checked: a refusal's code is one of the contract's start codes. */
export function started(value: unknown): Started {
  if (keyed(value, ["started"]) && value["started"] === true) {
    return { started: true };
  }
  const code = keyed(value, ["code", "started"]) && value["started"] === false
    ? START_CODES.find((each) => each === value["code"]) : undefined;
  if (code !== undefined) {
    return { started: false, code };
  }
  throw new Error("the sandbox's start answered out of shape");
}
