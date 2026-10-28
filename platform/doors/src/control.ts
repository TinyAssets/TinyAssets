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
 * A flip reaches every location within KV's 60 s; no revoke depends on it. The switch
 * reaching the object's alarm (the doorbell) is task 7's.
 */
import type { DoorsEnv } from "./config";
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
  return outsideOpen(await readControl(env), owner) ? null : refusal(OUTSIDE_OFF);
}

export async function ownerGate(env: DoorsEnv, owner: string): Promise<Response | null> {
  return ownerOpen(await readControl(env), owner) ? null : refusal(MAINTENANCE);
}

function refusal(message: string): Response {
  return Response.json({ error: "unavailable", message },
                       { status: 503, headers: { "Retry-After": "60" } });
}
