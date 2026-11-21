/**
 * The `SANDBOX` interface (ADR-034 decision 4.2; `engines-replan.md` §2.1 step 3; its types
 * the contract's, `contract/src/sandbox.ts`): what the owner's object asks of the
 * sandbox Worker (`platform/sandbox`), the only holder of the E2B key, over a service
 * binding (`wrangler.jsonc`, held in the owner config guard's `SERVICES`: a Worker that
 * reaches nothing but E2B, with one class of its own per owner that
 * serializes that owner's E2B calls and renews each sandbox before its runtime ceiling).
 *
 * The object reaches it only bound to its own owner (`boundSandbox`, built once from the
 * object's `ctx.id`): no call names an owner, so none can name another's sandbox, with its
 * synced files and vendor sign-in. The binding's owner argument is the wire's alone, and the
 * sandbox-bound guard holds that nothing else in the source reads the binding.
 *
 * - `start`: resume or create the owner's sandbox and start the in-VM runner for one run, in
 *   the background (E2B's `commands.run(..., { background: true })`), answering at once.
 *   `tokens` and `network` come only with the start that opened the sandbox's lease: the
 *   sandbox is then (re)configured with that network, whose transform carries the egress
 *   token, and the runner's sync agent takes the sync token. A run that joins a live lease
 *   gets neither: its sandbox is configured already. Every start, and every sign-in's, hands
 *   the engine's vendor hosts (`vendorHosts`), which the sandbox Worker lets out beside that
 *   network, so it reads no source record. `config` is the run's whole environment, which
 *   the runner sets as it is, from an empty one. The runner reaches the object only by `urls`: the
 *   run's MCP door, its event door and the sign-in report door (`signin`: each engine that signs in
 *   with the vendor's own login, whether its record's `status_argv` succeeds, and the models it
 *   offers; presence only, never a credential), all on the egress host, where E2B's transform sets
 *   the egress token outside the VM.
 * - `cancel`: stop the run's CLI (SIGTERM, then a kill after a grace; "Stop a run with
 *   SIGTERM", Claude's headless docs). The run settles from the runner's last report, or
 *   `unknown` if the runner is lost.
 * - `release`: the lease ended, so the sandbox is paused unless a run started in
 *   it since (zero idle tail).
 * - `unwait`: the owner has no run waiting for a sandbox any more, so it leaves
 *   the platform's line and the next owner in it is rung.
 * - `erase` (the erasure's step 2): every sandbox of the owner killed, paused ones
 *   included, and the sandbox Worker's own state of the owner deleted.
 *
 * A start may answer `sandbox_waiting` (`SANDBOX_WAITING`): E2B runs as many sandboxes as
 * its plan allows, and the run waits (`waiting`), never refused, until the platform's line
 * rings the owner (`sandboxRung`, through the doors Worker's ring consumer).
 * - `meter`: the Worker's own log of the owner's sandboxes started, paused,
 *   resumed and killed, each with its authority and time, after a cursor; the object reads
 *   it into its sandbox time (`sandbox-time.ts`), and the Worker may drop what a cursor
 *   passed. It limits nothing: the founder's cost view.
 */
import { type RefusalCode, refusalOf } from "../../contract/src/refusal";
import { keyed } from "../../contract/src/checks";
import {
  type CredentialKind, type SandboxService, START_CODES, type Started,
} from "../../contract/src/sandbox";
import { engineHosts } from "../../sources/src/served";
import { clockMs } from "./clock";
import { namedOwner } from "./door";
import { LANE_CALL_MS } from "./schedule";

/** The sandbox Worker as one owner's object reaches it: each method less its owner. */
export type OwnerSandbox = {
  readonly [M in keyof SandboxService]: SandboxService[M] extends
    (owner: string, ...rest: infer A) => infer R ? (...rest: A) => R : never;
};

/**
 * `service` bound to the owner the object `id` names: the one place the object hands the
 * sandbox Worker an owner. The owner Worker binds no Durable Object namespace and is handed no
 * `ctx.exports` (the config and self-reach guards), so the only id its code holds is its own
 * object's.
 */
export function boundSandbox(service: SandboxService, id: DurableObjectId): OwnerSandbox {
  const owner = () => namedOwner(id);
  const bound: OwnerSandbox = {
    start: (start) => service.start(owner(), start),
    cancel: (request) => service.cancel(owner(), request),
    release: (request) => service.release(owner(), request),
    signin: (signin) => service.signin(owner(), signin),
    signinInput: (request) => service.signinInput(owner(), request),
    signinCancel: (request) => service.signinCancel(owner(), request),
    network: (request) => service.network(owner(), request),
    unwait: () => service.unwait(owner()),
    erase: () => service.erase(owner()),
    meter: (request) => service.meter(owner(), request),
  };
  return Object.freeze(bound);
}

/**
 * The vendor hosts a job's sandbox network is let out to (`SandboxStart.hosts`,
 * `SandboxSignin.hosts`): on the vendor's own sign-in (a sign-in's always), the engine's
 * sign-in and model hosts and the chosen region's; none on a key.
 */
export function vendorHosts(engine: string, credential: CredentialKind,
                            region: string | null): string[] {
  return credential === "vendor_login_in_sandbox" ? engineHosts(engine, region) : [];
}

/** A start that did not answer: whether its job launched is not known. */
const START_LOST = "start_lost";
/** A start answered out of the contract's shape: the sandbox Worker's bug. */
const START_MALFORMED = "start_malformed";

/**
 * What came of a start, a run's or a sign-in's, as the object acts on it:
 * - the sandbox's answer (`Started`): started, or one of the contract's start codes;
 * - a refusal it threw, by its code: the sandbox Worker refuses only before it launches
 *   anything (its runner's two alerts, `sandbox/src/control.ts`), and the runtime's overload
 *   of an object (`object_overloaded`) is thrown before the call runs, so nothing started;
 * - `started: null`: no answer the object can read, so the job may have launched. Either the
 *   call threw (`START_LOST`: its answer lost on the way, or the sandbox Worker's own fault,
 *   which crosses as a plain error the object cannot tell from a loss), or it answered out
 *   of shape (`START_MALFORMED`).
 */
export type StartOutcome = Started
  | { readonly started: false; readonly code: RefusalCode }
  | { readonly started: null; readonly code: typeof START_LOST | typeof START_MALFORMED };

/**
 * What came of `start`, a call to the sandbox Worker's `start` or `signin` for `job` (as its
 * log lines name it), each cause but a start logged as itself. Its answer is read outside
 * the call's catch, so one out of shape is never taken for a lost start.
 *
 * A transient refusal says the same start succeeds later (`refusal.ts`), and nothing started
 * (above), so the same start (its job's id, its admission's tokens and network) is sent again
 * after the refusal's own `Retry-After`, while its job still `stands` (not cancelled or ended
 * meanwhile) and that wait ends within `LANE_CALL_MS` of the first send: the bound on one
 * outside call a lane's row makes, so the waits add no more than that to a pass (a send's
 * own time is the sandbox Worker's, bounded by nothing: `untangle/lane-start-bound`). Past it
 * the refusal stands, by its own code.
 */
export async function startOutcome(job: "a run" | "a sign-in", start: () => Promise<unknown>,
                                   stands: () => Promise<boolean>): Promise<StartOutcome> {
  const bound = clockMs() + LANE_CALL_MS;
  for (;;) {
    let value: unknown;
    try {
      value = await start();
    } catch (error) {
      const refusal = refusalOf(error);
      if (refusal === null) {
        console.error(`${job}'s start did not answer: whether it launched is unknown`, error);
        return { started: null, code: START_LOST };
      }
      const waitMs = (refusal.retryAfter ?? 0) * 1000;
      if (refusal.transient && clockMs() + waitMs <= bound) {
        console.warn(`the sandbox refused ${job}'s start for the moment (${refusal.code}): ` +
                     "nothing started, so it is sent again");
        await new Promise((resolve) => setTimeout(resolve, waitMs));
        if (await stands()) {
          continue;
        }
      }
      console.error(`the sandbox refused ${job}'s start (${refusal.code}): nothing started`);
      return { started: false, code: refusal.code };
    }
    if (keyed(value, ["started"]) && value["started"] === true) {
      return { started: true };
    }
    const code = keyed(value, ["code", "started"]) && value["started"] === false
      ? START_CODES.find((each) => each === value["code"]) : undefined;
    if (code !== undefined) {
      return { started: false, code };
    }
    console.error(`the sandbox answered ${job}'s start out of the contract's shape, a sandbox ` +
                  "Worker bug: whether it launched is unknown");
    return { started: null, code: START_MALFORMED };
  }
}
