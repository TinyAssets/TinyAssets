/**
 * The gate Worker (`gate.ts` builds it): it stands in a test's config for one binding, in
 * front of the real Worker or object that binding names (`REAL`), and forwards every call to
 * it unchanged unless the test armed that call through the gate's `Control` entrypoint (bound
 * to the test Worker alone):
 * - held before it is sent, or after its answer, until the test lets it go;
 * - its bytes changed in flight (the first byte of each byte array it carries);
 * - refused, with the refusal the test gave, crossing as a real refusal crosses.
 * It records what crossed, by method and by name: an object's name, or for a service the call's
 * first argument when it is a string (every service the gates front takes its owner, or the
 * owner's namespace, first), so a test arms and reads one owner's calls alone.
 *
 * Its methods are the ones its config names (`GATE`), defined on the prototype at module
 * load, so a name outside them is refused by the runtime as the real binding refuses it. For
 * an object (`GATE.object`) the class is a Durable Object that forwards to the real object of
 * the same name; otherwise it is the default entrypoint.
 *
 * A held call and the test's calls are different requests of this Worker, and workerd does
 * not wake one request by a promise another resolves: it cancels the held call as hung
 * (`b23_research.md`, question 2). So every piece of state is plain module state, and each
 * request polls it inside itself. No promise is ever handed from one request to another.
 */
import { DurableObject, env, WorkerEntrypoint } from "cloudflare:workers";

/** The gate's config, its one binding, as JSON. */
export interface GateSetup {
  readonly methods: readonly string[];
  readonly object: boolean;
}

/** A refusal as it crosses RPC: an error's name and message, and its own fields. */
interface InjectedRefusal {
  readonly code: string;
  readonly message: string;
  readonly name?: string;
  readonly retryAfterS?: number;
  readonly facts?: Readonly<Record<string, unknown>>;
}

/** What one arming does to the next matching call. */
type Act =
  | { readonly hold: "before" | "after" }
  | { readonly corrupt: true }
  | { readonly refuse: InjectedRefusal };

/** One arming: a method, the name it is for (any when absent), how often. */
export type Arm = Act & {
  readonly method: string;
  readonly name?: string;
  /** How many matching calls it acts on, 1 when absent. */
  readonly times?: number;
};

/** A call held at the gate now. */
export interface Held {
  readonly id: number;
  readonly method: string;
  readonly name: string | null;
  readonly at: "before" | "after";
}

/** One call that crossed: its method, its name, its arguments (bytes as a count). */
export interface Crossed {
  readonly method: string;
  readonly name: string | null;
  readonly args: unknown;
}

/** The names no RPC method may take (Cloudflare's reserved and disallowed methods). */
const RESERVED = Object.freeze(["fetch", "connect", "dup", "constructor", "alarm",
                                "webSocketMessage", "webSocketClose", "webSocketError"]);

const SETUP = JSON.parse((env as unknown as { GATE: string }).GATE) as GateSetup;

const arms: { arm: Arm; left: number }[] = [];
const held = new Map<number, Held>();
const crossed: Crossed[] = [];
let lastId = 0;

/** `value` with every byte array in it replaced by its length: what a record keeps. */
function described(value: unknown): unknown {
  if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) {
    return { bytes: value.byteLength };
  }
  if (Array.isArray(value)) {
    return value.map(described);
  }
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(Object.entries(value).map(([key, each]) => [key, described(each)]));
  }
  return value;
}

/** `value` copied with the first byte of each byte array in it flipped, and how many were. */
function changed(value: unknown, count: { n: number }): unknown {
  if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) {
    const bytes = value instanceof ArrayBuffer ? new Uint8Array(value.slice(0))
      : new Uint8Array(value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength));
    if (bytes.length > 0) {
      bytes[0] = bytes[0]! ^ 1;
      count.n++;
    }
    return bytes;
  }
  if (Array.isArray(value)) {
    return value.map((each) => changed(each, count));
  }
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(Object.entries(value).map(([key, each]) =>
      [key, changed(each, count)]));
  }
  return value;
}

/** The arming the call `method` on `name` meets, used up by it; null for none. */
function taken(method: string, name: string | null): Act | null {
  const index = arms.findIndex(({ arm }) => arm.method === method
                                            && (arm.name === undefined || arm.name === name));
  if (index < 0) {
    return null;
  }
  const found = arms[index]!;
  if (--found.left <= 0) {
    arms.splice(index, 1);
  }
  return found.arm;
}

/** Held here until the test lets this call go (`Control.release`), polled in this request. */
async function hold(method: string, name: string | null, at: Held["at"]): Promise<void> {
  const id = ++lastId;
  held.set(id, { id, method, name, at });
  while (held.has(id)) {
    await scheduler.wait(2);
  }
}

/** One call through the gate: recorded, then acted on as armed, then forwarded. */
async function cross(method: string, name: string | null, args: unknown[],
                     real: () => Record<string, (...args: unknown[]) => Promise<unknown>>):
  Promise<unknown> {
  crossed.push({ method, name, args: described(args) });
  const act = taken(method, name);
  if (act !== null && "refuse" in act) {
    const { code, message, name: errorName = "Refusal", retryAfterS, facts } = act.refuse;
    throw Object.assign(new Error(message), {
      name: errorName, code, ...(retryAfterS === undefined ? {} : { retryAfterS }),
      ...(facts === undefined ? {} : { facts }) });
  }
  if (act !== null && "hold" in act && act.hold === "before") {
    await hold(method, name, "before");
  }
  let sent = args;
  if (act !== null && "corrupt" in act) {
    const count = { n: 0 };
    sent = changed(args, count) as unknown[];
    if (count.n === 0) {
      throw new Error(`the gate was armed to change ${method}'s bytes, and it carried none`);
    }
  }
  const answer = await real()[method]!(...sent);
  if (act !== null && "hold" in act && act.hold === "after") {
    await hold(method, name, "after");
  }
  // The answer's own fields, never the real call's result itself: handed on, that result
  // keeps the caller's object referenced (it cannot be evicted), as no direct call does.
  return Array.isArray(answer) ? [...answer]
    : Object.getPrototypeOf(answer ?? 0) === Object.prototype ? { ...answer as object } : answer;
}

/** Each of the config's methods defined on `target`, forwarding through `cross`. */
function define(target: object, forward: (self: never, method: string, args: unknown[]) =>
  Promise<unknown>): void {
  for (const method of SETUP.methods) {
    if (RESERVED.includes(method) || method in target) {
      throw new Error(`the gate cannot define ${method}: it is reserved, or already defined`);
    }
    Object.defineProperty(target, method, {
      value: function (this: never, ...args: unknown[]) {
        return forward(this, method, args);
      },
      writable: true, configurable: true, enumerable: false,
    });
  }
}

type Real = Record<string, (...args: unknown[]) => Promise<unknown>> & Fetcher;

/** The gate before a service: the default entrypoint. */
export default class Gate extends WorkerEntrypoint<{ REAL: Real }> {
  static {
    if (!SETUP.object) {
      define(this.prototype, (self: Gate, method, args) =>
        cross(method, typeof args[0] === "string" ? args[0] : null, args, () => self.env.REAL));
    }
  }

  override fetch(request: Request): Promise<Response> {
    return this.env.REAL.fetch(request);
  }
}

/** The gate before an object: the real object of the same name behind it. */
export class GatedObject extends DurableObject<{ REAL: DurableObjectNamespace }> {
  static {
    if (SETUP.object) {
      define(this.prototype, (self: GatedObject, method, args) =>
        cross(method, self.#name(), args, () => self.#real()));
    }
  }

  #name(): string {
    const name = this.ctx.id.name;
    if (name === undefined) {
      throw new Error("the gate forwards only to an object reached by its name (getByName)");
    }
    return name;
  }

  #real(): Real {
    return this.env.REAL.getByName(this.#name()) as unknown as Real;
  }

  override fetch(request: Request): Promise<Response> {
    return this.#real().fetch(request);
  }
}

/** The test's handle on the gate. */
export class Control extends WorkerEntrypoint {
  /** The next matching call (or `times` of them) acted on as `arm` says. */
  async arm(arm: Arm): Promise<void> {
    if (!SETUP.methods.includes(arm.method)) {
      throw new Error(`the gate has no method ${arm.method}`);
    }
    const acts = ["hold", "corrupt", "refuse"].filter((act) => act in arm);
    const hold = (arm as { hold?: unknown }).hold;
    if (acts.length !== 1 || (acts[0] === "hold" && hold !== "before" && hold !== "after")) {
      throw new Error("an arming holds a call before or after it, changes its bytes, or " +
                      `refuses it: one of those, not ${JSON.stringify(arm)}`);
    }
    const times = arm.times ?? 1;
    if (!Number.isSafeInteger(times) || times < 1) {
      throw new Error(`an arming acts on at least one call, not ${times}`);
    }
    arms.push({ arm, left: times });
  }

  /** The calls held now, after waiting up to `waitMs` for one to be. */
  async held(waitMs = 0): Promise<Held[]> {
    for (const end = Date.now() + waitMs; held.size === 0 && Date.now() < end;) {
      await scheduler.wait(2);
    }
    return [...held.values()];
  }

  /** The held call `id` let go, or every held call; loud for a call that is not held. */
  async release(id?: number): Promise<void> {
    if (id === undefined) {
      held.clear();
    } else if (!held.delete(id)) {
      throw new Error(`no call ${id} is held at the gate`);
    }
  }

  /**
   * The gate as it started: every arming dropped, every held call let go and the record
   * emptied. Module state outlives a test (only a test file gets a fresh copy of the gate),
   * so a suite calls this after each test.
   */
  async disarm(): Promise<void> {
    arms.length = 0;
    held.clear();
    crossed.length = 0;
  }

  /** What crossed (named `name`, or all), oldest first, taken from the record. */
  async seen(name?: string): Promise<Crossed[]> {
    const mine = crossed.filter((call) => name === undefined || call.name === name);
    const rest = crossed.filter((call) => !mine.includes(call));
    crossed.length = 0;
    crossed.push(...rest);
    return mine;
  }
}
