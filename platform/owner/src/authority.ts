/**
 * Outside-client authority over the agent (agent research §4; tasks 5, sub-slice A2): what
 * a conversation may do, decided at each operation it makes.
 *
 * Every conversation runs under an authority, kept in pi's own `ta.authority` document
 * (scope conversation, the newest only, copied into a fork): the owner's, an outside
 * client's stamp, or none. None is the document's initial value, so a conversation the
 * platform did not make as a thread, or as a task's conversation below one, can do
 * nothing: the owner's case is explicit, never the absence of a stamp. A stamp is the
 * client, its grant's generation and its sign-in time, as scheduled work's is, plus where
 * the conversation acts: the universe (the command center) and the agent a scoped grant's
 * triples name, fixed when the thread is made, as the Python's run context fixed them for
 * `check_identity`. A thread's authority is written where the thread is made, in pi's
 * creating commit, and recorded in the object's threads (`threads.ts`); every
 * conversation a task makes takes its owner conversation's through pi's
 * `conversationCreated`, over any copy a fork brought, and a fork holds its parent's
 * (`agent.ts`).
 *
 * The authority is enforced where every effect passes, which no conversation can select
 * away: the environment pi builds for each tool call, replays included (`files-env.ts`),
 * re-checks it on every operation (a read needs `read_files`, a change `write_files` in its
 * own unit, `exec` a grant that is not scoped), and the model provider re-checks it in
 * each call's reservation and dispatch units (`usage.ts`, `gateway-provider.ts`), so a
 * revoked client's turn spends nothing more, whatever abort it missed. Under a client's
 * stamp each check reads, synchronously, that the client's grant still stands at the
 * stamped generation (`Grants.standing`: not changed, not revoked, no revoke after the
 * sign-in) and, for a scoped grant, that it holds the capability where the conversation
 * acts. A full grant holds every capability, as at the doors.
 *
 * `ta.authority`, a trusted extension installed first, is the early gate: its
 * `beforeTool` refuses a call before its intent is recorded, for a model's call and a
 * nested one (`call.parent`) alike, with a fixed reason and a refusal receipt. Each tool's
 * need: `read` needs `read_files`; `write` needs `write_files`; `edit`, which reads the
 * file it changes, needs both; `bash` is refused to a scoped grant; `ta` takes its own
 * map's, by the capability called (`TA_CAPABILITIES`: the catalog's and each platform
 * capability's; a tool `ta` reaches has none, so a scoped grant is refused it, and the
 * `ta:` names are reserved so no call aliases an op); any other tool is refused. `ta`
 * re-derives its need from the arguments it actually received and re-checks it inside its
 * own `execute` (`ta.ts`), since a later hook may rewrite a call's arguments after this
 * gate approved them.
 *
 * Code mode (`codemode`, pi's, H1) needs `run_code`, which the owner holds and a client
 * holds only where its scoped grant names it (`EXPLICIT`: a full grant does not hold it),
 * reached through `ta` alone; each tool a script calls
 * is a nested call this gate holds to its own need, so a script reaches what its caller's
 * authority allows and nothing more. A `codemode` call
 * that is not `ta`'s own nested call (`call.parent` set by anything but `ta`'s call under
 * `CODEMODE_KEY`: a script's `tools.codemode`) is refused, so scripts cannot start scripts.
 *
 * Out of scope now: only platform code makes a top-level conversation (a guard holds it to
 * `OwnerAgent.thread`), and only platform code selects extensions (a guard keeps every
 * selection out of the sources), since a conversation's own selection decides which hooks
 * run; the enforcement points above do not depend on either.
 */
import { Invalid, Refused } from "./errors";
import { codePoints, isPrintable } from "./text";

/** The capabilities of a scoped grant the agent's tools need (`grants.ts`'s `CAPABILITIES`). */
export type ToolCapability = "read_files" | "write_files" | "propose_policy" | "converse"
  | "run_graph" | "run_code";

/**
 * The capabilities a client holds only where a scoped grant names them, never by a full
 * grant: code mode's (`run_code`), many tool and model calls in one call with no model
 * round trip between them.
 */
export const EXPLICIT: readonly ToolCapability[] = Object.freeze(["run_code"] as const);

/** The need of what only the owner itself may do, whatever grant a client holds. */
export const OWNER_ONLY = "owner";

/**
 * What an operation needs: each of these capabilities (none: only a standing authority),
 * the shell (`null`), or the owner itself (`OWNER_ONLY`).
 */
export type Need = readonly ToolCapability[] | null | typeof OWNER_ONLY;

/**
 * Each core tool's need; `null`: refused to a scoped client whatever it holds (the shell
 * reaches the owner's whole sandbox). A tool missing from the map has none.
 */
export const TOOL_CAPABILITIES: Readonly<Record<string, Need>> = Object.freeze({
  read: Object.freeze(["read_files"] as const),
  write: Object.freeze(["write_files"] as const),
  edit: Object.freeze(["read_files", "write_files"] as const),
  bash: null,
  codemode: Object.freeze(["run_code"] as const),
  model_complete: Object.freeze(["run_code"] as const),
});

/** pi's code mode tool, and the key `ta` makes its nested call under: no other may. */
export const CODEMODE_TOOL = "codemode";
export const CODEMODE_KEY = "codemode";

/**
 * Each `ta` capability's need, by the name `ta` calls (`ta:catalog` for the catalog, as the
 * Python's `check_identity` named it), each as the object's door for the same thing holds
 * it: the catalog and the conversation's own scheduled work need only a standing
 * authority; the owner's policy is the owner's alone to read (`policyRead`); a proposal
 * needs `propose_policy` (`policyPropose`); file usage needs `read_files` (`fileUsage`); a
 * nested model call spends as the turn's own calls do, under a standing authority, re-
 * checked in its reservation and dispatch; a launch of an agent needs `run_graph`
 * (`agentRun`), here where the calling conversation acts and again, in the launch's own
 * recording unit, where the launched run will act; a delegation to a subagent is a launch
 * too (`run_graph`), its named agent's scope checked where that agent acts as well, in its
 * admission's unit (A5). A name missing is refused to a scoped grant.
 * The `ta:` names are the ops' own: a call naming one is refused.
 */
export const TA_CAPABILITIES: Readonly<Record<string, Need>> = Object.freeze({
  "ta:catalog": Object.freeze([] as const),
  "policy:show": OWNER_ONLY,
  "policy:propose": Object.freeze(["propose_policy"] as const),
  "wake:list": Object.freeze([] as const),
  "files:usage": Object.freeze(["read_files"] as const),
  "model": Object.freeze([] as const),
  "agent:run": Object.freeze(["run_graph"] as const),
  "agent:delegate": Object.freeze(["run_graph"] as const),
  "codemode": Object.freeze(["run_code"] as const),
});
/** The tool `ta`, and the prefix of its ops' own names. */
export const TA_TOOL = "ta";
export const TA_OP = "ta:";

/** The fixed reasons a call is refused with: the model and the receipts see only these. */
export const AUTHORITY_CHANGED = "outside client authority no longer stands";
export const NOT_GRANTED = "outside client capability not granted";
export const NO_SHELL = "the shell is not granted to a scoped outside client";
export const NO_AUTHORITY = "this conversation runs under no authority";
export const NOT_OWNER = "only the owner may use this capability";
export const CODEMODE_NESTED = "code mode runs only through ta: a script cannot start a script";

/** The receipt's rule for a refused tool call, and a refused model call's code. */
export const AUTHORITY_RULE = "outside_authority";
/** A model call's refusal code when its request names no conversation the object made. */
export const NO_CONVERSATION = "no_conversation";

/** A refusal's fixed code: the part before its colon (`owner_suspended: ...`), else itself. */
export function refusalCode(message: string): string {
  return /^[a-z_]+:/.exec(message)?.[0].slice(0, -1) ?? message;
}

/** The tools the model is offered, exactly, on every conversation the object creates. */
export const MODEL_TOOLS: readonly string[] = Object.freeze(["read", "write", "edit", "bash",
                                                             "ta"]);
/** A model call's refusal code when it would offer the model any other tools. */
export const TOOL_SURFACE = "tool_surface";

/**
 * Whether a request offering the tools `names` may reach the model: exactly
 * `MODEL_TOOLS` (in any order, each once), or none (`ta`'s nested model call, a
 * compaction's summary). Any other set, a sixth tool or a stand-in under a model tool's
 * name counted twice among them, is refused, whatever path widened it.
 */
export function offersFive(names: readonly string[]): boolean {
  return names.length === 0 || (names.length === MODEL_TOOLS.length
    && MODEL_TOOLS.every((name) => names.includes(name)));
}

/** The authority a conversation runs under: the client's, as it was admitted, and where. */
export type Stamped = {
  client: string;
  generation: number;
  authenticated_at: number | null;
  /** The universe (command center) a scoped grant's triples name; none for a full grant's. */
  universe: string | null;
  /** The agent the conversation acts as. */
  agent: string;
};

/** The `ta.authority` document's content: the owner's, a client's stamp, or none. */
export type AuthorityState =
  | { by: "owner" }
  | { by: "client"; stamp: Stamped }
  | { by: "none" };

/** A thread's authority: the owner's own (`null`), or the stamp of a client's. */
export function authorityOf(stamp: Stamped | null): AuthorityState {
  return stamp === null ? { by: "owner" } : { by: "client", stamp: { ...stamp } };
}

/** The stamp of an authority, if it is a client's. */
export function stampOf(state: Readonly<AuthorityState> | undefined): Stamped | null {
  return state?.by === "client" ? state.stamp : null;
}

const HEX64 = /^[0-9a-f]{64}$/;
const STAMP_KEYS = "agent,authenticated_at,client,generation,universe";

/** An agent id, or a universe: 1 to `max` printable characters, never a wildcard. */
function label(value: unknown, max: number): value is string {
  return typeof value === "string" && value.length > 0 && value !== "*"
    && codePoints(value) <= max && isPrintable(value);
}

/** An agent's id: its directory's name under `/workspace/agents/`. */
export const AGENT_DIR = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

/** An agent's id as a thread names it: its directory's name (`AGENT_DIR`). */
export function agentId(value: unknown): string {
  if (typeof value !== "string" || !AGENT_DIR.test(value)) {
    throw new Invalid("an agent id is 1 to 128 letters, digits, dots, dashes and " +
                      "underscores");
  }
  return value;
}

/** A stamp as an admitted call gives it, else invalid; null is the owner's own. */
export function stamped(value: unknown): Stamped | null {
  if (value === null) {
    return null;
  }
  const refused = () => new Invalid("a stamp is a client, its grant generation, its sign-in " +
                                    "time, a universe and an agent");
  if (typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).sort().join() !== STAMP_KEYS) {
    throw refused();
  }
  const { client, generation, authenticated_at: at, universe, agent } =
    value as Record<string, unknown>;
  if (typeof client !== "string" || !HEX64.test(client) || typeof generation !== "number"
      || !Number.isSafeInteger(generation) || generation < 1
      || (at !== null && (typeof at !== "number" || !Number.isFinite(at) || at < 0))
      || (universe !== null && !label(universe, 512)) || !label(agent, 128)) {
    throw refused();
  }
  return { client, generation, authenticated_at: at as number | null,
           universe: universe as string | null, agent };
}

/** A standing grant, as the gate reads it: its mode, and whether it holds a triple. */
export interface StandingGrant {
  readonly mode: string;
  holds(scope: { universe: string; agent: string; capability: string }): boolean;
}

/** What the gate reads of the object's grants: `Grants.standing`, with `holds`. */
export interface AuthorityGrants {
  standing(client: unknown, generation: unknown, authenticatedAt: number | null): StandingGrant;
}

/** A tool call as the gate sees it: pi's `ToolHookCall`. */
export interface GatedCall {
  readonly id?: string;
  readonly name: string;
  readonly arguments: unknown;
  readonly parent?: { readonly callId: string } | undefined;
}

/**
 * Why a call may not run whatever its authority, or undefined: a `codemode` call that is
 * not `ta`'s own nested call (its id `<ta's call>/CODEMODE_KEY`), such as a script's.
 */
export function nestingRefusal(call: GatedCall): string | undefined {
  if (call.name !== CODEMODE_TOOL) {
    return undefined;
  }
  const parent = call.parent;
  return parent !== undefined && call.id === `${parent.callId}/${CODEMODE_KEY}`
    ? undefined : CODEMODE_NESTED;
}

/**
 * What a call needs: a core tool's, a `ta` capability's by `ta`'s map (the object's own,
 * `TA_CAPABILITIES`), or nothing known (undefined).
 */
export function capabilityOf(call: GatedCall,
                             ta: Readonly<Record<string, Need>> = TA_CAPABILITIES):
    Need | undefined {
  if (call.name === TA_TOOL) {
    const args = call.arguments as Record<string, unknown> | null;
    const op = typeof args === "object" && args !== null ? args["op"] : undefined;
    const name = op === "call" ? args!["name"] : undefined;
    const called = op === "catalog" ? `${TA_OP}catalog`
      : typeof name === "string" && !name.startsWith(TA_OP) ? name : undefined;
    return called !== undefined && Object.hasOwn(ta, called) ? ta[called] : undefined;
  }
  return Object.hasOwn(TOOL_CAPABILITIES, call.name) ? TOOL_CAPABILITIES[call.name] : undefined;
}

export interface AuthorityParts {
  readonly grants: AuthorityGrants;
  /**
   * The owner's admission's `require_work` refusal, or null while it is admitted: a
   * synchronous read of the object's own row (`admission.ts`). Every operation and model
   * call is refused while the owner is not admitted, the owner's own too: a suspension's
   * abort is the prompt stop, this the enforcement.
   */
  readonly admission?: () => string | null;
  /**
   * Keep a refusal receipt of a blocked call, in a unit of its own: the object's policy
   * refusal, under `AUTHORITY_RULE`.
   */
  readonly receipt: (request: { client: string | null; tool: string; reason: string }) =>
    Promise<void>;
}

export class Authority {
  readonly #parts: AuthorityParts;

  constructor(parts: AuthorityParts) {
    this.#parts = parts;
  }

  /** The stamp's grant, standing; else a refusal with the fixed reason. */
  #standing(stamp: Stamped): StandingGrant {
    try {
      return this.#parts.grants.standing(stamp.client, stamp.generation, stamp.authenticated_at);
    } catch (error) {
      if (error instanceof Refused) {
        throw new Refused(AUTHORITY_CHANGED);
      }
      throw error;
    }
  }

  /**
   * That `state` holds `need` (none: only that it stands) where its conversation acts; else
   * a refusal with the fixed reason. The owner's holds everything, none nothing; a client's
   * stamp must stand, never holds what is the owner's alone, and a scoped grant holds each
   * capability (`null`: never).
   */
  require(state: Readonly<AuthorityState> | undefined, need: Need | undefined): void {
    const unadmitted = this.#parts.admission?.() ?? null;
    if (unadmitted !== null) {
      throw new Refused(unadmitted);
    }
    if (state?.by === "owner") {
      return;
    }
    if (state?.by !== "client") {
      throw new Refused(NO_AUTHORITY);
    }
    const stamp = state.stamp;
    const grant = this.#standing(stamp);
    if (need === OWNER_ONLY) {
      throw new Refused(NOT_OWNER);
    }
    const explicit = Array.isArray(need)
      && need.some((capability) => EXPLICIT.includes(capability));
    if (grant.mode === "full" && !explicit) {
      return;
    }
    if (grant.mode === "full") {
      throw new Refused(NOT_GRANTED);  // a full grant names no triple, so no explicit one
    }
    if (need === null) {
      throw new Refused(NO_SHELL);
    }
    if (need === undefined || stamp.universe === null
        || !need.every((capability) => grant.holds({ universe: stamp.universe!,
                                                     agent: stamp.agent, capability }))) {
      throw new Refused(NOT_GRANTED);
    }
  }

  /** Why an operation needing `need` is refused under `state`, or undefined. Synchronous. */
  refusal(state: Readonly<AuthorityState> | undefined, need: Need | undefined):
      string | undefined {
    try {
      this.require(state, need);
      return undefined;
    } catch (error) {
      if (error instanceof Refused) {
        return error.message;
      }
      throw error;
    }
  }

  /** That `state` holds one of `needs`; else the first one's refusal. */
  requireOne(state: Readonly<AuthorityState> | undefined, needs: readonly Need[]): void {
    let first: string | undefined;
    for (const need of needs) {
      const reason = this.refusal(state, need);
      if (reason === undefined) {
        return;
      }
      first ??= reason;
    }
    throw new Refused(first ?? NOT_GRANTED);
  }

  /**
   * A model call's refusal code under `state`, or null when it may spend: the admission's
   * code (`owner_suspended`, `owner_erased`) while the owner is not admitted, else the
   * authority's.
   */
  modelRule(state: Readonly<AuthorityState> | undefined): string | null {
    const unadmitted = this.#parts.admission?.() ?? null;
    if (unadmitted !== null) {
      return refusalCode(unadmitted);
    }
    return this.refusal(state, []) === undefined ? null : AUTHORITY_RULE;
  }

  /**
   * `ta.authority`'s `beforeTool`: the call's block, with its receipt kept, or undefined.
   * The receipt is kept, or its failure logged, before the block returns.
   */
  async decide(state: Readonly<AuthorityState> | undefined, call: GatedCall):
      Promise<{ block: string } | undefined> {
    const reason = nestingRefusal(call) ?? this.refusal(state, capabilityOf(call));
    if (reason === undefined) {
      return undefined;
    }
    try {
      await this.#parts.receipt({ client: stampOf(state)?.client ?? null, tool: call.name,
                                  reason });
    } catch {
      console.error("a tool call's refusal receipt could not be kept");  // refused regardless
    }
    return { block: reason };
  }
}
