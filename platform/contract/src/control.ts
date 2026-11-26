/**
 * The platform's control record: one record, `c:control`, in a KV namespace of its own
 * (`CONTROL`), which the founder edits with no deploy. The doors read it on every outside and
 * owner call, and hand the record read fresh in the binding (`doors/src/binder.ts`,
 * `withAccount`); the owner's object at a code-mode script's tool
 * calls and its lanes' passes (`owner/src/control-gate.ts`); the sandbox Worker once per start
 * and once per look (`sandbox/src/control.ts`); and a first deploy writes it closed
 * (`deploy/deploy.ts`). Each reads it here, by this module's key, parse and gates, and
 * nowhere else.
 *
 * **When it can't be read** (the founder's rule, 2026-11-21): a reader serves the last copy it
 * read well formed, of any age, kept by the keeper it hands (`ControlKeeper`), and refuses only
 * the acts that need the record fresh (`FRESH_ONLY`). A record that reads but is not well
 * formed (a bad write, a typo in a kill flip) never uses the copy: it closes every gate, as
 * a kill flip with a typo must.
 *
 * It holds:
 * - **the gates:** `outside`, the outside kill switch; `ordinary`, the maintenance gate (all
 *   owners, the `canary` owners only, or none); `inbound`, the hooks gate (`/hooks/inbound`
 *   serves nothing unless it is on);
 * - **the tier numbers** (`tiers`, ADR-034 decision 8);
 * - **each source's vendor sign-in switch** (`signins`);
 * - **the sandbox's platform numbers**.
 *
 * Every field but the gates is optional, and a record any part of which is malformed is
 * malformed whole: never a guess, and never a key dropped unread. A flip reaches every
 * location within KV's 60 s; no revoke depends on it.
 */

import { keyed, object, whole } from "./checks.ts";
import { Refusal } from "./refusal.ts";
import { principal, SOURCE_ID } from "./text.ts";

/** The record's key in its namespace. */
export const CONTROL_KEY = "c:control";
/** Canary owners the maintenance gate admits; far past a real canary. */
const MAX_CANARY = 100;

/** The outside gate's refusal, a 503 at the doors. */
export const OUTSIDE_OFF = "outside access is switched off";
/** The maintenance gate's refusal, a 503 at the doors. */
export const MAINTENANCE = "TinyAssets is in maintenance";

export const TIER_NAMES = Object.freeze(["free", "paid"] as const);
export type TierName = (typeof TIER_NAMES)[number];
/** The tier of an owner whose plan names none (the owner's schema's default). */
export const DEFAULT_TIER: TierName = "free";

/**
 * The most live runs any tier record may give: what the owner-bound guard costs the owner's
 * database for. A frozen bound, which the owner's schema spells (the frozen-bounds guard holds
 * them equal). The sandbox template's run pool is this many users (`sandbox/template/image.ts`).
 */
export const RUNS_CEILING = 1000;
/**
 * The most storage a tier record may give, in bytes, about 0.5 TB: the owner's archive fence
 * rows less one (`owner/src/segments.ts`, `ARCHIVE_SEGMENTS_MAX`), each a sealed segment's
 * least bytes (`SEGMENT_LEAST_BYTES`, 8 MiB), which the owner's limits audit holds equal. A
 * larger tier needs larger segments first (compaction): a redesign, recorded in
 * `limits-audit.md`.
 */
export const TIER_STORAGE_MAX = 59_605 * 8 * 1024 * 1024;

export interface TierNumbers {
  /** Simultaneous runs. */
  readonly runs: number;
  /** Storage, in bytes. */
  readonly storage_bytes: number;
}

/**
 * Each tier's simultaneous runs and storage, which the founder edits with no deploy (free 5
 * runs and 25 GB, paid 10 and 250 GB: founder, 2026-11-02). No number is in code: a record
 * without them refuses every run, never a default.
 */
export type Tiers = Readonly<Record<TierName, TierNumbers>>;

/** The sandbox's numbers (`sandbox/src/control.ts`). */
export interface SandboxCeilings {
  /**
   * How long one sandbox may run before it is paused and resumed (Hobby's 1 hour, Pro's 24):
   * the vendor plan's own ceiling, which raising needs the plan raised first, or E2B refuses.
   */
  readonly runtime_ceiling_s: number;
  /** How many sandboxes the E2B team may run at once (Hobby 20, Pro 100 and more with add-ons). */
  readonly concurrency_ceiling: number;
  /**
   * How long before the runtime ceiling a renewal looks for a moment its CLIs are quiet: the
   * founder's choice of how long a turn may run on before a renewal cuts into it.
   */
  readonly renewal_window_s: number;
}

/** The record, parsed exactly. */
export interface Control {
  readonly outside: "on" | "off";
  readonly ordinary: "all" | "canary" | "none";
  readonly canary: readonly string[];
  readonly inbound: boolean;
  /** The tier numbers; null when the record holds none, and every run is then refused. */
  readonly tiers: Tiers | null;
  /**
   * The sandbox's numbers (the founder's rule of 2026-11-03), which the sandbox
   * Worker reads at each start: null when the record holds none, and it then starts nothing.
   */
  readonly sandbox: SandboxCeilings | null;
  /**
   * Each source's vendor sign-in, on or off, by source id: a source the record
   * does not name takes its record's verdict on the vendor's terms for hosted use
   * (`sources/src/signin.ts`, `signinsOn`). Null when the record names none.
   */
  readonly signins: Readonly<Record<string, boolean>> | null;
}

/**
 * The record as read: there and well formed, with the text it was read from; kept (the
 * namespace could not be read, and the reader's keeper held the copy last read well formed,
 * first read at `read_at`, ms); missing;
 * malformed (an operator's bad write: not JSON, too long, or out of shape); or unreadable (an
 * outage, with no copy kept). The gates tell them apart; the last three are reported.
 */
export type ControlRead =
  | { readonly kind: "read"; readonly control: Control; readonly text: string }
  | { readonly kind: "kept"; readonly control: Control; readonly read_at: number }
  | { readonly kind: "missing" }
  | { readonly kind: "malformed" }
  | { readonly kind: "unreadable" };

/** What the record is read from: its namespace, as each Worker binds it (`CONTROL`). */
export type ControlStore = Pick<KVNamespace, "get">;

/**
 * The most bytes the record's text may hold: what each reader's kept copy is costed for (the
 * owner's database bound, `owner/guards/owner-bound.test.ts`), far past any record (a hundred
 * canary owners and every source's switch take a few KiB). A longer text is malformed.
 */
export const CONTROL_TEXT_MAX = 64 * 1024;

/** The record's text as a reader last read it well formed, and when it first read that text. */
export interface Kept {
  readonly text: string;
  /** Epoch ms. */
  readonly read_at: number;
}

/**
 * Where a reader keeps its last good copy of the record, which `readControl` writes at every
 * well-formed read and reads only when the namespace can't be read. Each reader hands its own:
 * the doors the isolate's memory and the location's cache (`doors/src/control.ts`), the
 * owner's and the sandbox's objects their own SQLite (`SqlKeeper`). Neither shares KV's
 * backend, as R2 or a second namespace would.
 */
export interface ControlKeeper {
  /** The copy kept, or null when none is. */
  kept(): Kept | null | Promise<Kept | null>;
  /**
   * `text`, read well formed at `at`, kept: written only when it is not the copy kept. Null, a
   * read that found no record or a malformed one, drops the copy, so the gates that read closed
   * stay closed through an outage after it (a kill flip with a typo).
   */
  keep(text: string | null, at: number): void | boolean | Promise<void>;
}

/**
 * A keeper in an object's own SQLite (the owner's and the sandbox's): one row of its table
 * `control_kept` (`only`, `text`, `read_at`), which each object's own migrations make.
 */
export class SqlKeeper implements ControlKeeper {
  readonly #sql: Pick<SqlStorage, "exec">;

  constructor(sql: Pick<SqlStorage, "exec">) {
    this.#sql = sql;
  }

  kept(): Kept | null {
    const row = this.#sql.exec<{ text: string; read_at: number }>(
      "SELECT text, read_at FROM control_kept").toArray()[0];
    return row === undefined ? null : { text: row.text, read_at: row.read_at };
  }

  /** Whether this write changed the stored copy. */
  keep(text: string | null, at: number): boolean {
    // Read first: an owner unit that runs any write is marked changed (`owner/src/gate.ts`).
    if ((this.kept()?.text ?? null) === text) {
      return false;
    }
    if (text === null) {
      this.#sql.exec("DELETE FROM control_kept");
      return true;
    }
    this.#sql.exec("INSERT INTO control_kept (only, text, read_at) VALUES (1, ?, ?) " +
                     "ON CONFLICT (only) DO UPDATE SET text = excluded.text, " +
                     "read_at = excluded.read_at", text, at);
    return true;
  }
}

/**
 * The acts a kept copy never admits, each by the words its refusal names it in: each opens
 * something that outlasts the outage, on a record that may have changed since the copy (a
 * switch just turned off), so it waits for the record read fresh. Writing the record's own
 * numbers into lasting state is the other such act: a read that is not fresh hands none
 * (`freshRecord`), so no reader rewrites what it keeps. Everything else runs on the copy.
 */
export const FRESH_ONLY = Object.freeze({
  /** An outside client's new authorization (`doors/src/authorize.ts`). */
  authorization: "a new authorization",
  /** A new owner's first admit, at its first sign-in (`doors/src/authorize.ts`). */
  admit: "a first sign-in",
  /** A new API key (`doors/src/app.ts`). */
  key: "a new API key",
  /** A vendor sign-in started under its source's switch (`owner/src/signins.ts`). */
  signin: "a vendor sign-in",
});
export type FreshAct = keyof typeof FRESH_ONLY;

/** The temporary refusal of `act` while the record can't be read fresh: a 503 to retry. */
export function staleRefusal(act: FreshAct): Refusal {
  return new Refusal("unavailable", `${FRESH_ONLY[act]} needs the platform's control record ` +
                     "read fresh, and it can't be read just now");
}

/** `act` refused when `read` is a kept copy (`staleRefusal`); any other read is the gates'. */
export function requireFresh(read: ControlRead, act: FreshAct): void {
  if (read.kind === "kept") {
    throw staleRefusal(act);
  }
}

const SANDBOX_KEYS: readonly string[] = Object.freeze([
  "sandbox_runtime_ceiling_s", "sandbox_concurrency_ceiling", "sandbox_renewal_window_s"]);
/** The record's keys: the gates always; the rest optional. */
const CONTROL_KEYS: readonly string[] = Object.freeze(["canary", "inbound", "ordinary",
                                                       "outside"]);
const CONTROL_OPTIONAL: readonly string[] = Object.freeze([
  "tiers", "sandbox_runtime_ceiling_s", "sandbox_concurrency_ceiling",
  "sandbox_renewal_window_s", "signins"]);

/** The tier numbers exactly, or `invalid`. */
export function parseTiers(value: unknown): Tiers {
  if (!keyed(value, ["free", "paid"])) {
    throw new Refusal("invalid", "the tier record names free and paid");
  }
  const numbers = (tier: unknown): TierNumbers => {
    if (!keyed(tier, ["runs", "storage_bytes"])) {
      throw new Refusal("invalid", "a tier is its runs and its storage_bytes");
    }
    const { runs, storage_bytes: bytes } = tier;
    if (!whole(runs, 1, RUNS_CEILING) || !whole(bytes, 1, TIER_STORAGE_MAX)) {
      throw new Refusal("invalid", `a tier's runs are 1 to ${RUNS_CEILING}, its storage whole ` +
                                   `bytes up to ${TIER_STORAGE_MAX}`);
    }
    return Object.freeze({ runs, storage_bytes: bytes });
  };
  return Object.freeze({ free: numbers(value["free"]), paid: numbers(value["paid"]) });
}

/** The sandbox's three numbers checked: positive whole numbers, the window under the ceiling. */
export function sandboxCeilings(runtime: unknown, concurrency: unknown,
                                window: unknown): SandboxCeilings {
  if (!whole(runtime, 1) || !whole(concurrency, 1) || !whole(window, 1) || window >= runtime) {
    throw new Error("the sandbox numbers are positive whole numbers, the renewal window " +
                    "shorter than the runtime ceiling");
  }
  return Object.freeze({ runtime_ceiling_s: runtime, concurrency_ceiling: concurrency,
                         renewal_window_s: window });
}

/** The record parsed exactly, or null when it is anything else. */
export function parseControl(value: unknown): Control | null {
  if (!keyed(value, CONTROL_KEYS, CONTROL_OPTIONAL)) {
    return null;
  }
  const { outside, ordinary, canary, inbound, tiers, signins } = value;
  if (signins !== undefined && (!object(signins) || !Object.entries(signins).every(([id, on]) =>
                                  SOURCE_ID.test(id) && typeof on === "boolean"))) {
    return null;
  }
  const sandbox = SANDBOX_KEYS.map((key) => value[key]);
  if ((outside !== "on" && outside !== "off")
      || (ordinary !== "all" && ordinary !== "canary" && ordinary !== "none")
      || typeof inbound !== "boolean" || !Array.isArray(canary) || canary.length > MAX_CANARY
      || !(sandbox.every((part) => part === undefined)
           || sandbox.every((part) => part !== undefined))) {
    return null;
  }
  try {
    return { outside, ordinary, inbound, canary: Object.freeze(canary.map(principal)),
             tiers: tiers === undefined ? null : parseTiers(tiers),
             signins: signins === undefined ? null
               : Object.freeze({ ...(signins as Record<string, boolean>) }),
             sandbox: sandbox[0] === undefined ? null
               : sandboxCeilings(sandbox[0], sandbox[1], sandbox[2]) };
  } catch {
    return null;
  }
}

/**
 * The record from its text, parsed exactly, or null when it is anything else: longer than
 * `CONTROL_TEXT_MAX`, not JSON, or out of shape. Every reader's parser, and the kit's.
 */
export function controlOf(text: string): Control | null {
  if (new TextEncoder().encode(text).byteLength > CONTROL_TEXT_MAX) {
    return null;
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return null;
  }
  return parseControl(value);
}

/**
 * The record from its namespace, as read, each well-formed read's text kept in `keeper` and a
 * missing or malformed one dropping it; when the namespace throws, the copy `keeper` holds. Never throws: an outage with no copy
 * is `unreadable`. A keeper that fails is reported and is no copy: a good read stays good.
 */
export async function readControl(store: ControlStore,
                                  keeper: ControlKeeper): Promise<ControlRead> {
  let text: string | null;
  try {
    text = await store.get(CONTROL_KEY, { type: "text" });
  } catch (error) {
    return kept(keeper, error);
  }
  const control = text === null ? null : controlOf(text);
  if (text !== null && control === null) {
    console.error("the control record is malformed: every gate is closed until it is fixed");
  }
  try {
    await keeper.keep(control === null ? null : text, Date.now());
  } catch (error) {
    console.error("the control record's last good copy could not be kept", error);
  }
  return text === null ? { kind: "missing" }
    : control !== null ? { kind: "read", control, text } : { kind: "malformed" };
}

/** The read an outage gives: the copy `keeper` holds, or `unreadable`. */
async function kept(keeper: ControlKeeper, outage: unknown): Promise<ControlRead> {
  let copy: Kept | null = null;
  try {
    copy = await keeper.kept();
  } catch (error) {
    console.error("the control record's last good copy could not be read", error);
  }
  const control = copy === null ? null : controlOf(copy.text);
  if (copy === null || control === null) {
    console.error("the control record is unreadable and no copy is kept: every gate is closed",
                  outage);
    return { kind: "unreadable" };
  }
  console.error(`the control record is unreadable: served from the copy first read at ${
    new Date(copy.read_at).toISOString()}; the acts that need it fresh are refused`, outage);
  return { kind: "kept", control, read_at: copy.read_at };
}

/** The record a read gives the gates: as read, or as kept; null when it gives none. */
export function gating(read: ControlRead): Control | null {
  return read.kind === "read" || read.kind === "kept" ? read.control : null;
}

/** Whether the maintenance gate admits `owner`: every owner, or a canary one. */
export function ordinaryAdmits(control: Control, owner: string): boolean {
  return control.ordinary === "all"
    || (control.ordinary === "canary" && control.canary.includes(owner));
}

/**
 * Whether the record admits an outside call for `owner` (an MCP tool call, an `/api` call, an
 * MCP authorization): only a record read well formed or kept, with `outside` on and the
 * maintenance gate admitting its owner. Missing, malformed or unreadable refuses it: the kill
 * switch is off unless it is provably on.
 */
export function outsideOpen(read: ControlRead, owner: string): boolean {
  const control = gating(read);
  return control !== null && control.outside === "on" && ordinaryAdmits(control, owner);
}

/**
 * Whether the record admits the owner's own call (`/app/api/*`, the owner's sign-in, a run's
 * or the owner's code-mode call): held by the maintenance gate alone. With no record at all it
 * is open, so a fresh deployment's owner can reach their account; a record that is malformed
 * or unreadable with no copy closes it, since a gate that cannot be read is never taken as open.
 */
export function ownerOpen(read: ControlRead, owner: string): boolean {
  const control = gating(read);
  return read.kind === "missing" || (control !== null && ordinaryAdmits(control, owner));
}

/**
 * Whether the record admits nobody, so neither gate above opens for any owner: its maintenance
 * gate admits none (`none`, or `canary` naming none), as the dark record's does. The sign-in
 * probe runs only behind such a record (`deploy/deploy.ts`).
 */
export function closedToAll(control: Control): boolean {
  return control.ordinary === "none"
    || (control.ordinary === "canary" && control.canary.length === 0);
}

/**
 * The hooks gate (`/hooks/inbound`), before any route is read: `unavailable` when the record
 * is malformed or unreadable, `off` when it is missing or its `inbound` is off, else the
 * record read or kept, by whose maintenance gate the event's owner is then held
 * (`ordinaryAdmits`).
 */
export function inboundGate(read: ControlRead): Control | "off" | "unavailable" {
  const control = gating(read);
  return read.kind === "malformed" || read.kind === "unreadable" ? "unavailable"
    : control === null || !control.inbound ? "off" : control;
}

/**
 * The record a read hands the owner's object to keep as its one copy (`Binding.control`, its
 * `SqlKeeper`), which its tier numbers and sign-in switches are read from: the text when it
 * read well formed; null when it found none or a malformed one (the object then drops its
 * copy, refuses every run for want of numbers and starts no sign-in); none (`undefined`) when
 * it was not read fresh, so the object keeps its own (`FRESH_ONLY`).
 */
export function freshRecord(read: ControlRead): string | null | undefined {
  return read.kind === "kept" || read.kind === "unreadable" ? undefined
    : read.kind === "read" ? read.text : null;
}
