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
import {
  namedRefusal, type Refusal, type RefusalCode, refusalOf,
} from "../../contract/src/refusal";
import { keyed, object } from "../../contract/src/checks";
import {
  type CredentialKind, NETWORK_STATES, type SandboxService, START_CODES, type Started,
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
 * sign-in and model hosts; none on a key.
 */
export function vendorHosts(engine: string, credential: CredentialKind): string[] {
  return credential === "vendor_login_in_sandbox" ? engineHosts(engine) : [];
}

/**
 * What one call to the sandbox Worker came to, read by the contract (`read`: its answer, or
 * undefined for one out of the contract's shape):
 * - `answered`: the answer, as the contract has it;
 * - `refused`: a refusal it threw that the contract reads, by its code;
 * - `malformed`: an answer came, returned or thrown as a refusal, out of the contract's shape
 *   (another version's code, say): the call is over and its fault is the sandbox Worker's.
 *   `cause` names it: a refusal's code, or the contract's own `fields` and only the names of
 *   the answer's keys, since a bug's answer is no place a log can trust to hold nothing
 *   secret: a code or field's value as it came only in the shape of a contract code (`shown`);
 * - `unanswered`: the call threw a plain error, its answer lost on the way or a sandbox
 *   Worker's fault it does not refuse (a start's at or after its launch, a re-apply's), which
 *   the object cannot tell from a loss.
 *
 * The answer is read outside the call's catch, so one out of shape is never taken for a lost
 * one. Each call's own reader says what each means for its job (`startOutcome`,
 * `networkOutcome`).
 */
/** A value in the shape of a contract code, a boolean, a number or none; else its type. */
function shown(value: unknown): string {
  if (typeof value === "string") {
    return /^[a-z][a-z0-9_]{0,31}$/.test(value) ? value : "string";
  }
  return typeof value === "object" && value !== null ? "object" : String(value);
}

type SandboxAnswer<T> = { readonly kind: "answered"; readonly answer: T }
  | { readonly kind: "refused"; readonly refusal: Refusal }
  | { readonly kind: "malformed"; readonly cause: string }
  | { readonly kind: "unanswered"; readonly error: unknown };

async function sandboxAnswer<T>(call: () => Promise<unknown>, fields: readonly string[],
                                read: (value: unknown) => T | undefined):
    Promise<SandboxAnswer<T>> {
  let value: unknown;
  try {
    value = await call();
  } catch (error) {
    const refusal = refusalOf(error);
    if (refusal !== null) {
      return { kind: "refused", refusal };
    }
    return namedRefusal(error)
      ? { kind: "malformed", cause: `a refusal of code ${shown((error as { code?: unknown }).code)}` }
      : { kind: "unanswered", error };
  }
  const answer = read(value);
  if (answer !== undefined) {
    return { kind: "answered", answer };
  }
  return { kind: "malformed", cause: object(value)
    ? [...fields.map((field) => `${field} ${shown(value[field])}`),
       `keys ${Object.keys(value).join(" ")}`].join(", ")
    : typeof value };
}

/** A start that did not answer: whether its job launched is not known. */
const START_LOST = "start_lost";
/** A start answered out of the contract's shape: the sandbox Worker's bug. */
const START_MALFORMED = "start_malformed";

/**
 * What came of a start, a run's or a sign-in's, as the object acts on it (`sandboxAnswer`):
 * - the sandbox's answer (`Started`): started, or one of the contract's start codes;
 * - a refusal it threw, by its code: the sandbox Worker refuses only before it launches
 *   anything (its runner's two alerts, and `sandbox_start_failed` its own fault there:
 *   `SandboxService.start`), and the runtime's overload of an object (`object_overloaded`) is
 *   thrown before the call runs, so nothing started;
 * - `started: null`: no answer the object can read, so the job may have launched, and is
 *   stopped in case it did. Its `state` says what the job's start came to:
 *   - `unknown` (`START_LOST`): the call was `unanswered`;
 *   - `failed` (`START_MALFORMED`): it was `malformed`. The call is over, but an answer out of
 *     the contract says nothing of what the sandbox Worker launched.
 */
export type StartOutcome = Started
  | { readonly started: false; readonly code: RefusalCode }
  | { readonly started: null; readonly code: typeof START_LOST; readonly state: "unknown" }
  | { readonly started: null; readonly code: typeof START_MALFORMED; readonly state: "failed" };

/** A start's answer as the contract has it (`Started`), or undefined. */
function started(value: unknown): Started | undefined {
  if (keyed(value, ["started"]) && value["started"] === true) {
    return { started: true };
  }
  const code = keyed(value, ["code", "started"]) && value["started"] === false
    ? START_CODES.find((each) => each === value["code"]) : undefined;
  return code === undefined ? undefined : { started: false, code };
}

/**
 * What came of `start`, a call to the sandbox Worker's `start` or `signin` for `job` (as its
 * log lines name it), each cause but a start logged as itself.
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
    const got = await sandboxAnswer(start, ["started", "code"], started);
    if (got.kind === "answered") {
      return got.answer;
    }
    if (got.kind === "malformed") {
      console.error(`the sandbox answered ${job}'s start out of the contract's shape (${
        got.cause}), a sandbox Worker bug: it failed, and is stopped in case it launched`);
      return { started: null, code: START_MALFORMED, state: "failed" };
    }
    if (got.kind === "unanswered") {
      console.error(`${job}'s start did not answer: whether it launched is unknown`, got.error);
      return { started: null, code: START_LOST, state: "unknown" };
    }
    const { refusal } = got;
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
}

/**
 * What came of `reapply`, a call to the sandbox Worker's `network` for a live lease, as the
 * lease's record takes it (`Files.networkAnswered`): `applied` or `at_resume` as answered,
 * else `failed`: `lost` (no sandbox of that lease), a refusal, an answer out of the
 * contract's shape (the sandbox Worker's bug) or a plain throw, each cause but `lost` logged
 * as itself. A re-apply launches nothing and replaces the sandbox's network whole, never with
 * an older one (the Worker's own check), so whatever a failed one did, sending it again is
 * safe: the lane does, past its retry clock, and nothing is stopped. Until one lands, the
 * lease's calls under the new generation are refused: fail-closed.
 */
export async function networkOutcome(reapply: () => Promise<unknown>):
    Promise<"applied" | "at_resume" | "failed"> {
  const got = await sandboxAnswer(reapply, ["state"], (value) => keyed(value, ["state"])
    ? NETWORK_STATES.find((each) => each === value["state"]) : undefined);
  if (got.kind === "answered") {
    return got.answer === "lost" ? "failed" : got.answer;
  }
  if (got.kind === "malformed") {
    console.error(`the sandbox answered a lease's network re-apply out of the contract's shape (${
      got.cause}), a sandbox Worker bug: it failed, and the lane tries again`);
  } else if (got.kind === "refused") {
    console.error(`the sandbox refused a lease's network re-apply (${got.refusal.code}); ` +
                  "the lane tries again");
  } else {
    console.error("a lease's network re-apply did not reach the sandbox; the lane tries again",
                  got.error);
  }
  return "failed";
}
