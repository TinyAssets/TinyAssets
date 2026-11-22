/**
 * The owner object's runner's steps, by door (`owner/doors.json`): for each door
 * of the contract's table (`contract/doors.json`, what every caller of it sees), how the
 * object takes its call. The runner (`owner-do.ts`, `#call`) does each step from its row, in
 * this order:
 *
 * 1. **bind**: the object's own owner, its admission for the door's class (the contract's),
 *    and the sandbox, inbound or import binding its `caller` kind takes;
 * 2. **account**: who calls (`caller`), then the row's `guard`, if any;
 * 3. **scope**: the door's capability (the contract's), held where the caller acts (`scope`:
 *    the caller's own place, the agent its request names, or a place the door's handler
 *    reads from the request);
 * 4. **fields**: the door's request held to its arguments (the contract's); `fieldsFirst`
 *    checks them before the scope (where the scope's place is read from them);
 * 5. **decide**: at a row that names an act (the contract's `act`), an agent's call at an
 *    `account` or `visitors` door, once its condition holds, held to the owner's rules on
 *    that act (`actions.ts`), in a unit of its own before the handler, once a call.
 *
 * Then the door's handler, in its module (`handler`), runs in the row's `gate`: one unit of
 * the write gate (`door`), a quiet one (`quiet`), a unit and then the schedule synced
 * (`synced`; `filing`, whatever the unit did), or units of its own (`units`).
 *
 * Node runs this module as it is (`owner/write-door-map.ts`), so its imports name their files.
 */
import raw from "../doors.json" with { type: "json" };

import type { BindingKind } from "../../contract/src/binding.ts";
import { among, frozen, object } from "../../contract/src/checks.ts";
import {
  type DoorRow, doorProblems, doorRow, doorTable, UNBOUND_DOORS,
} from "../../contract/src/doors.ts";

/** The owner's account: acting itself, in its session, through an outside client, or a run. */
const ACCOUNT = Object.freeze(["account", "session", "run"] as const);
/** A file reader: the owner's account, or the sync agent at its lease. */
const READER = Object.freeze(["account", "session", "run", "sync"] as const);
/** The owner's account but a run's, or the sync agent at its lease: the file changes, a lease. */
const LEASER = Object.freeze(["account", "session", "sync"] as const);
const RESERVED = Object.freeze(["reserved"] as const);
const IMPORT = Object.freeze(["import"] as const);
const EGRESS = Object.freeze(["egress"] as const);

/**
 * Who a door takes, as its account step binds them, each with the binding kinds it takes
 * (`contract/src/binding.ts`): the bind step parses a call's binding with the parser of the
 * kind it presents, and refuses a kind its caller does not take.
 * - `account`: the owner's own account, acting itself or through a verified outside client
 *   admitted against its grant, or a run on the egress host as its initiator;
 * - `visitors`: `account`, and another owner visiting (origin null): the collections;
 * - `owner`: the owner itself, on its own account, never an outside client nor a run
 *   (`action`: what only the owner may do; `live`: in its live session, receipting nothing);
 * - `session`: the owner in its live session, a widening, whose refusals leave a receipt
 *   under `rule` of the request (or of `receipt`, in its place); `action`, null when an
 *   outside client may ask in it, and `registering`, an API key's registration;
 * - `reserved`: the platform's reserved caller, with no outside client and no session
 *   (`action`: its act, `admission` or `tier`);
 * - `erasure`: the platform's reserved caller, at an erasure under way;
 * - `import`: the platform's reserved caller with an import binding (the doors' import
 *   consumer, `doors/src/import/`), while the owner is `pending`: the `import` class's doors
 *   alone, deleted at the cutover;
 * - `discard`: the same binding, while the owner is `pending` or its name is a rehearsal's
 *   (`rehearsalRun`, `import-message.ts`), in any state: the import's discard alone;
 * - `egress`; `runner`, with its runner's secret (`closed`: its refusal once the lease is
 *   gone); `run`, with the run its path names;
 * - `sync`: the sync agent's token, at its live lease (the request's, when it names one);
 * - `reader`, `changes`: a file reader's account, or the sync agent's;
 * - `lease`: the owner itself (`action`), or the sync agent at its lease;
 * - `inbound`: an inbound event's route, at its binding's revision;
 * - `none`: no binding at all, at the contract's doors that take none (`UNBOUND_DOORS`): their
 *   steps name no caller, and every other door's names one of the others.
 */
export const CALLER_BINDINGS = Object.freeze({
  account: ACCOUNT, visitors: ACCOUNT, owner: ACCOUNT, session: ACCOUNT,
  reserved: RESERVED, erasure: RESERVED, import: IMPORT, discard: IMPORT,
  egress: EGRESS, runner: EGRESS, run: Object.freeze(["run"] as const),
  sync: Object.freeze(["sync"] as const), reader: READER, changes: LEASER, lease: LEASER,
  inbound: Object.freeze(["inbound"] as const), none: Object.freeze(["none"] as const),
} satisfies Readonly<Record<string, readonly BindingKind[]>>);
export type CallerKind = keyof typeof CALLER_BINDINGS;
/** A caller that binds: every one but `none`, whose doors take no binding. */
export type Binds = Exclude<CallerKind, "none">;
/** The binding kinds a door of caller `K` takes. */
export type TakenBy<K extends CallerKind> = (typeof CALLER_BINDINGS)[K][number];


/**
 * A check of the caller after its account: a run reads no mailbox (`no_mailbox`); a visitor
 * changes no row it may not (`owner_rows`, `action`: what); only an MCP client files its own
 * card (`own_card`).
 */
const GUARDS = Object.freeze(["no_mailbox", "owner_rows", "own_card"] as const);
type Guard = (typeof GUARDS)[number];

/** How a door's handler runs in the object's write gate (`gate.ts`). */
const GATES = Object.freeze(["door", "quiet", "synced", "filing", "units"] as const);
type Gate = (typeof GATES)[number];

/** A door's steps, by its name. */
export interface DoorSteps {
  readonly door: string;
  /** Who the door takes; absent at a door that takes no binding (`UNBOUND_DOORS`). */
  readonly caller?: Exclude<CallerKind, "none">;
  readonly action?: string | null;
  readonly rule?: string;
  readonly receipt?: unknown;
  readonly registering?: true;
  readonly closed?: "egress_lease_closed";
  readonly live?: true;
  readonly guard?: Guard;
  readonly scope?: "caller" | "agent" | "handler";
  /** The request's field naming the agent a run door acts as, and its default. */
  readonly agent?: { readonly field: string; readonly default?: string };
  readonly fieldsFirst?: true;
  /**
   * The owner module that holds the door's handler (`<module>Doors.<door>`): the object's
   * typed handler map (`door-map.ts`, written from this) holds it to one.
   */
  readonly handler: string;
  readonly gate: Gate;
  /**
   * A new owner's starter seed awaited before the handler, once the caller's authority holds
   * in a unit: a door that touches the files or lists the agents (a `units` row).
   */
  readonly seeded?: true;
}

/**
 * A door as the runner takes it: what every caller sees of it, and its steps, its caller
 * `none` where its contract row takes no binding.
 */
export type OwnerRow = DoorRow & Omit<DoorSteps, "caller"> & { readonly caller: CallerKind };

const STEP_KEYS = Object.freeze(["door", "caller", "action", "rule", "receipt", "registering",
  "closed", "live", "guard", "scope", "agent", "fieldsFirst", "handler", "gate", "seeded"]);

/**
 * Each way `steps` breaks the rules, of its own or against the contract's `rows`, as
 * `door: what`: every row's door stepped once, and nothing else stepped.
 */
export function stepProblems(steps: unknown, rows: readonly DoorRow[]): string[] {
  if (!Array.isArray(steps)) {
    return ["the steps are a list of rows"];
  }
  const problems: string[] = [];
  const seen = new Set<unknown>();
  for (const step of steps as unknown[]) {
    if (!object(step)) {
      problems.push("steps that are not an object");
      continue;
    }
    const say = (what: string) => problems.push(`${String(step["door"])}: ${what}`);
    const row = rows.find((each) => each.door === step["door"]);
    if (row === undefined) {
      say("the steps of no door");
    }
    if (seen.has(step["door"])) {
      say("stepped twice");
    }
    seen.add(step["door"]);
    for (const key of Object.keys(step)) {
      if (!STEP_KEYS.includes(key)) {
        say(`a key ${key}`);
      }
    }
    const caller = step["caller"];
    if (UNBOUND_DOORS.includes(step["door"] as string)) {
      if ("caller" in step) {
        say("a door that takes no binding names no caller");
      }
    } else if (typeof caller !== "string" || !Object.hasOwn(CALLER_BINDINGS, caller)
               || caller === "none") {
      // Every caller but `none`, which the contract's table implies.
      say("no caller kind");
    }
    if (row !== undefined
        && (row.class === "admission") !== among(["reserved", "erasure"], caller)) {
      say("only the platform's doors read no admission");
    }
    if (row !== undefined && (row.class === "import") !== among(["import", "discard"], caller)) {
      say("only the import's doors take the import class, and they take no other");
    }
    if (step["scope"] !== undefined && !among(["caller", "agent", "handler"], step["scope"])) {
      say("an unknown scope");
    }
    if (step["guard"] !== undefined && !among(GUARDS, step["guard"])) {
      say("an unknown guard");
    }
    if (typeof step["handler"] !== "string" || !/^[a-z]+(-[a-z]+)*$/.test(step["handler"])) {
      say("no handler module");
    }
    if (!among(GATES, step["gate"])) {
      say("no gate");
    }
    if (step["seeded"] !== undefined && (step["seeded"] !== true || step["gate"] !== "units")) {
      say("a seeded door's handler makes its own units");
    }
    if (row?.stop === true && step["gate"] !== "units") {
      say("a door that takes its caller's stop makes its own units (`Door.left`)");
    }
    if (among(["owner", "session"], caller) && !("action" in step)) {
      say("an owner's door names what only the owner may do");
    }
    if (caller === "session" && typeof step["rule"] !== "string") {
      say("a session's door names its receipt's rule");
    }
  }
  for (const row of rows) {
    if (!seen.has(row.door)) {
      problems.push(`${row.door}: a door with no steps`);
    }
  }
  return problems;
}

/** The steps file as it is (the module keeps no state), frozen whole at its first read. */
function steps(): readonly DoorSteps[] {
  return frozen(raw.steps as unknown as readonly DoorSteps[]);
}

/** Loud when the halves have `problems`: a broken table is a bug. */
function held(problems: readonly string[]): void {
  if (problems.length > 0) {
    throw new Error(`the door table is broken: ${problems.join("; ")}`);
  }
}

/** `row` with its steps, held whole: caller `none` where it takes no binding. */
function withSteps(row: DoorRow): OwnerRow {
  const { caller, ...rest } = steps().find((each) => each.door === row.door)!;
  return Object.freeze({ ...row, ...rest,
                        caller: UNBOUND_DOORS.includes(row.door) ? "none" : caller! });
}

/**
 * Every door as the runner takes it, in the contract's order: the two halves held whole, so a
 * door with no steps, steps of no door, or a broken row of either is a bug.
 */
export function ownerRows(): readonly OwnerRow[] {
  held([...doorProblems(doorTable()), ...stepProblems(steps(), doorTable())]);
  return Object.freeze(doorTable().map(withSteps));
}

/** The door named `name` as the runner takes it, both its halves held whole. */
export function ownerRow(name: string): OwnerRow {
  const row = doorRow(name);
  held(stepProblems(steps().filter((each) => each.door === name), [row]));
  return withSteps(row);
}
