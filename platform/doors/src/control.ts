/**
 * The platform gates at the doors (research §7, checklist 11): read from the directory's
 * `control` record on every outside call, fail-closed.
 *
 * - An outside call (an MCP tool call, an `/api` call, an MCP authorization) passes only
 *   when the record reads well formed, `outside` is `on`, and the maintenance gate
 *   admits its owner. A missing, malformed or unreadable record refuses it: the kill
 *   switch is off unless it is provably on.
 * - The owner's own surfaces (`/app/api/*`, the owner's sign-in) are held by the
 *   maintenance gate. With no record at all they stay open, so a fresh deployment's owner
 *   can reach their account (research checklist 11); a record that is malformed or
 *   unreadable closes them, as the Python's `router_traffic.admits` did, since a gate
 *   that cannot be read is never taken as open.
 *
 * A flip reaches every location within KV's 60 s; no revoke depends on it. A `code` call's
 * script reads the same gates again at each tool call, in the object (`control-gate.ts`).
 * The owner object's `wakes` lane reads it too, once a pass, before it claims a wake (slice W).
 */
import type { DoorsEnv } from "./config";
import { bundledSources } from "../../sources/src/catalog";
import { signinsOn } from "../../sources/src/signin";
import { type Control, type ControlRead, readControl } from "./directory";

export const OUTSIDE_OFF = "outside access is switched off";
export const MAINTENANCE = "TinyAssets is in maintenance";

function ordinaryAdmits(control: Control, owner: string): boolean {
  return control.ordinary === "all"
    || (control.ordinary === "canary" && control.canary.includes(owner));
}

/** Whether the record admits an outside call for `owner`. */
export function outsideOpen(read: ControlRead, owner: string): boolean {
  return read.kind === "read" && read.control.outside === "on"
    && ordinaryAdmits(read.control, owner);
}

/** Whether the record admits the owner's own call. */
export function ownerOpen(read: ControlRead, owner: string): boolean {
  return read.kind === "missing" || (read.kind === "read" && ordinaryAdmits(read.control, owner));
}

/** Refused, as a 503 naming why; or null when the gate admits the call. */
export async function outsideGate(env: DoorsEnv, owner: string): Promise<Response | null> {
  const read = await outsideRead(env, owner);
  return read instanceof Response ? read : null;
}

export async function ownerGate(env: DoorsEnv, owner: string): Promise<Response | null> {
  const read = await ownerRead(env, owner);
  return read instanceof Response ? read : null;
}

/**
 * The record's tier numbers, as the binder hands them to a run door; null for none (the
 * object then forgets the numbers it kept, and refuses: `tier.ts`).
 */
function tiersOf(read: ControlRead): unknown {
  return read.kind === "read" && read.control.tiers !== null ? read.control.tiers : null;
}

/**
 * The served records whose vendor sign-in is switched on (the record's `signins`, else each
 * record's cited verdict), as the binder hands them to the object with every call: the
 * object refuses a sign-in, a pasted code and a run on a vendor's login for any other.
 */
function signinsOf(read: ControlRead): string[] {
  return signinsOn(bundledSources(), read.kind === "read" ? read.control.signins : null);
}

/** What a gate admitted: the record's tier numbers and the switched-on sign-ins. */
export interface Gated {
  readonly tiers: unknown;
  readonly signins: readonly string[];
}

/** The outside gate, read once: its refusal, or what the binding carries. */
export async function outsideRead(env: DoorsEnv, owner: string): Promise<Response | Gated> {
  const read = await readControl(env.CONTROL);
  return outsideOpen(read, owner) ? { tiers: tiersOf(read), signins: signinsOf(read) }
    : refusal(OUTSIDE_OFF);
}

/** The maintenance gate, read once: its refusal, or what the binding carries. */
export async function ownerRead(env: DoorsEnv, owner: string): Promise<Response | Gated> {
  const read = await readControl(env.CONTROL);
  return ownerOpen(read, owner) ? { tiers: tiersOf(read), signins: signinsOf(read) }
    : refusal(MAINTENANCE);
}

/**
 * The record's tier numbers for the cutover's import (`import.ts`), read as every account
 * door's binding reads them; null for none, so the files plane's quota refuses rather than
 * guesses (`tier.ts`). The import passes no gate: it runs dark, before `routes`.
 */
export async function importTiers(env: DoorsEnv): Promise<unknown> {
  return tiersOf(await readControl(env.CONTROL));
}

function refusal(message: string): Response {
  return Response.json({ error: "unavailable", message },
                       { status: 503, headers: { "Retry-After": "60" } });
}
