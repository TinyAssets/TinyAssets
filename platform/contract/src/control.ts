/**
 * The platform's control record (research §7, checklist 11): one record, `c:control`, in a
 * KV namespace of its own (`CONTROL`), which the founder edits with no deploy. The doors read
 * it on every outside and owner call, and hand its tier numbers and switched-on sign-ins in
 * the binding (`doors/src/binder.ts`, `withTiers`); the owner's object at a code-mode script's
 * tool calls and its lanes' passes (`owner/src/control-gate.ts`); the sandbox Worker once per start and once per look
 * (`sandbox/src/control.ts`); and a first deploy writes it closed (`deploy/deploy.ts`). Each
 * reads it here, by this module's key, parse and gates, and nowhere else.
 *
 * It holds:
 * - **the gates:** `outside`, the outside kill switch (the Python's `TINYASSETS_OUTSIDE_DENY`
 *   and durable switch); `ordinary`, the maintenance gate (all owners, the `canary` owners
 *   only, or none); `inbound`, the hooks gate (`/hooks/inbound` serves nothing unless it is
 *   on);
 * - **the tier numbers** (`tiers`, ADR-034 decision 8);
 * - **each source's vendor sign-in switch** (`signins`);
 * - **the sandbox's platform numbers** and **its scratch retention**.
 *
 * Every field but the gates is optional, and a record any part of which is malformed is
 * malformed whole: never a guess. A flip reaches every location within KV's 60 s; no revoke
 * depends on it.
 */

// By file name, so the deploy kit runs it in Node as it is (`deploy/deploy.ts`).
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

/**
 * The most live runs any tier record may give: what the owner-bound guard costs the owner's
 * database for. A frozen bound, which the owner's schema spells (the frozen-bounds guard holds
 * them equal). `owner/src/tier.ts` spells it again as text for the sandbox template's tag
 * (`sandbox/template/build.py`), held to this one by the template guard, until fix A6 splits
 * that build.
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
   * The sandbox's numbers (slice S; the founder's rule of 2026-11-03), which the sandbox
   * Worker reads at each start: null when the record holds none, and it then starts nothing.
   */
  readonly sandbox: SandboxCeilings | null;
  /**
   * How many days an agent's sandbox scratch may go unused before it is deleted
   * (`scratch_retention_days`; `sandbox/runner/scratch.ts`): a chosen retention, refusing
   * nothing, which the sandbox Worker hands each run's runner. Null when the record names
   * none: then only the disk's own pressure lets scratch go.
   */
  readonly scratch_retention_days: number | null;
  /**
   * Each source's vendor sign-in, on or off (slice SIGNIN), by source id: a source the record
   * does not name takes its record's verdict on the vendor's terms for hosted use
   * (`sources/src/signin.ts`, `signinsOn`). Null when the record names none.
   */
  readonly signins: Readonly<Record<string, boolean>> | null;
}

/**
 * The record as read: there and well formed, missing, malformed (an operator's bad write),
 * or unreadable (an outage, or a value that is not JSON). The gates tell them apart; the
 * last two are reported.
 */
export type ControlRead =
  | { readonly kind: "read"; readonly control: Control }
  | { readonly kind: "missing" }
  | { readonly kind: "malformed" }
  | { readonly kind: "unreadable" };

/** What the record is read from: its namespace, as each Worker binds it (`CONTROL`). */
export type ControlStore = Pick<KVNamespace, "get">;

const SANDBOX_KEYS: readonly string[] = Object.freeze([
  "sandbox_runtime_ceiling_s", "sandbox_concurrency_ceiling", "sandbox_renewal_window_s"]);
/** The record's keys: the gates always; the rest optional. */
const CONTROL_KEYS: readonly string[] = Object.freeze(["canary", "inbound", "ordinary",
                                                       "outside"]);
const CONTROL_OPTIONAL: readonly string[] = Object.freeze([
  "tiers", "sandbox_runtime_ceiling_s", "sandbox_concurrency_ceiling",
  "sandbox_renewal_window_s", "scratch_retention_days", "signins"]);

function plain(value: unknown, keys: string): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    && Object.keys(value).sort().join() === keys;
}

function positive(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 1;
}

/** The tier numbers exactly, or `invalid`. */
export function parseTiers(value: unknown): Tiers {
  if (!plain(value, "free,paid")) {
    throw new Refusal("invalid", "the tier record names free and paid");
  }
  const numbers = (tier: unknown): TierNumbers => {
    if (!plain(tier, "runs,storage_bytes")) {
      throw new Refusal("invalid", "a tier is its runs and its storage_bytes");
    }
    const { runs, storage_bytes: bytes } = tier;
    if (!positive(runs) || runs > RUNS_CEILING || !positive(bytes) || bytes > TIER_STORAGE_MAX) {
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
  if (!positive(runtime) || !positive(concurrency) || !positive(window) || window >= runtime) {
    throw new Error("the sandbox numbers are positive whole numbers, the renewal window " +
                    "shorter than the runtime ceiling");
  }
  return Object.freeze({ runtime_ceiling_s: runtime, concurrency_ceiling: concurrency,
                         renewal_window_s: window });
}

/** The record parsed exactly, or null when it is anything else. */
export function parseControl(value: unknown): Control | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const keys = Object.keys(value);
  if (!CONTROL_KEYS.every((key) => keys.includes(key))
      || !keys.every((key) => CONTROL_KEYS.includes(key) || CONTROL_OPTIONAL.includes(key))) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const { outside, ordinary, canary, inbound, tiers, signins } = record;
  if (signins !== undefined && (typeof signins !== "object" || signins === null
                                || Array.isArray(signins)
                                || !Object.entries(signins).every(([id, on]) =>
                                  SOURCE_ID.test(id) && typeof on === "boolean"))) {
    return null;
  }
  const sandbox = SANDBOX_KEYS.map((key) => record[key]);
  const retention = record["scratch_retention_days"];
  if ((retention !== undefined && !positive(retention))
      || (outside !== "on" && outside !== "off")
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
               : sandboxCeilings(sandbox[0], sandbox[1], sandbox[2]),
             scratch_retention_days: retention === undefined ? null : retention };
  } catch {
    return null;
  }
}

/** The record from its namespace, as read. Never throws: an outage is `unreadable`. */
export async function readControl(store: ControlStore): Promise<ControlRead> {
  let value: unknown;
  try {
    value = await store.get(CONTROL_KEY, { type: "json" });
  } catch (error) {
    console.error("the control record is unreadable", error);
    return { kind: "unreadable" };
  }
  if (value === null) {
    return { kind: "missing" };
  }
  const control = parseControl(value);
  if (control === null) {
    console.error("the control record is malformed");
    return { kind: "malformed" };
  }
  return { kind: "read", control };
}

/** Whether the maintenance gate admits `owner`: every owner, or a canary one. */
export function ordinaryAdmits(control: Control, owner: string): boolean {
  return control.ordinary === "all"
    || (control.ordinary === "canary" && control.canary.includes(owner));
}

/**
 * Whether the record admits an outside call for `owner` (an MCP tool call, an `/api` call, an
 * MCP authorization): only a record that reads well formed, with `outside` on and the
 * maintenance gate admitting its owner. Missing, malformed or unreadable refuses it: the kill
 * switch is off unless it is provably on.
 */
export function outsideOpen(read: ControlRead, owner: string): boolean {
  return read.kind === "read" && read.control.outside === "on"
    && ordinaryAdmits(read.control, owner);
}

/**
 * Whether the record admits the owner's own call (`/app/api/*`, the owner's sign-in, a run's
 * or the owner's code-mode call): held by the maintenance gate alone. With no record at all it
 * is open, so a fresh deployment's owner can reach their account (research checklist 11); a
 * record that is malformed or unreadable closes it, as the Python's `router_traffic.admits`
 * did, since a gate that cannot be read is never taken as open.
 */
export function ownerOpen(read: ControlRead, owner: string): boolean {
  return read.kind === "missing" || (read.kind === "read" && ordinaryAdmits(read.control, owner));
}

/**
 * The hooks gate (`/hooks/inbound`), before any route is read: `unavailable` when the record
 * is malformed or unreadable, `off` when it is missing or its `inbound` is off, else the
 * record, by whose maintenance gate the event's owner is then held (`ordinaryAdmits`).
 */
export function inboundGate(read: ControlRead): Control | "off" | "unavailable" {
  return read.kind === "malformed" || read.kind === "unreadable" ? "unavailable"
    : read.kind === "missing" || !read.control.inbound ? "off" : read.control;
}

/** The record's tier numbers: null when it holds none, or when it was not read well formed. */
export function tiersOf(read: ControlRead): Tiers | null {
  return read.kind === "read" ? read.control.tiers : null;
}
