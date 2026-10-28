/**
 * The platform gates at the doors (research §7, checklist 11): read from the directory's
 * `control` record on every outside call, fail-closed.
 *
 * - An outside call (an MCP tool call, an `/api` call, an MCP authorization) passes only
 *   when the record is there and well formed, `outside` is `on`, and the maintenance gate
 *   admits its owner. A missing, malformed or unreadable record refuses it: the kill
 *   switch is off unless it is provably on.
 * - The owner's own surfaces (`/app/api/*`, the owner's sign-in) are held only by the
 *   maintenance gate, and only when the record reads; with no record they stay open, so
 *   the owner can always reach their own account. The kill switch is for outside work.
 *
 * A flip reaches every location within KV's 60 s; no revoke depends on it. The switch
 * reaching the object's alarm (the doorbell) is task 7's.
 */
import type { Control } from "./directory";
import { readControl } from "./directory";
import type { DoorsEnv } from "./config";

export const OUTSIDE_OFF = "outside access is switched off";
export const MAINTENANCE = "TinyAssets is in maintenance";

function ordinaryAdmits(control: Control, owner: string): boolean {
  return control.ordinary === "all"
    || (control.ordinary === "canary" && control.canary.includes(owner));
}

/** Whether `control` admits an outside call for `owner`. */
export function outsideOpen(control: Control | null, owner: string): boolean {
  return control !== null && control.outside === "on" && ordinaryAdmits(control, owner);
}

/** Whether `control` admits the owner's own call. */
export function ownerOpen(control: Control | null, owner: string): boolean {
  return control === null || ordinaryAdmits(control, owner);
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
