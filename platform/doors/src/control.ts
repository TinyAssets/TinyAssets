/**
 * The platform gates at the doors (research §7, checklist 11): the control record
 * (`contract/src/control.ts`) read on every outside and owner call, its gates applied, and a
 * closed gate answered as a 503 naming why. An outside call needs the outside gate
 * (`outsideOpen`, fail-closed); the owner's own surfaces the maintenance gate (`ownerOpen`).
 * The record an admitted call was read on rides its binding (`binder.ts`, `withTiers`). A
 * `code` call's script reads the same gates again at each tool call, in the object
 * (`owner/src/control-gate.ts`).
 */
import {
  type ControlRead, MAINTENANCE, OUTSIDE_OFF, outsideOpen, ownerOpen, readControl, tiersOf,
} from "../../contract/src/control";

import type { DoorsEnv } from "./config";

/** The outside gate, read once: its refusal, or the record it admitted the call on. */
export async function outsideRead(env: DoorsEnv, owner: string): Promise<Response | ControlRead> {
  const read = await readControl(env.CONTROL);
  return outsideOpen(read, owner) ? read : refusal(OUTSIDE_OFF);
}

/** The maintenance gate, read once: its refusal, or the record it admitted the call on. */
export async function ownerRead(env: DoorsEnv, owner: string): Promise<Response | ControlRead> {
  const read = await readControl(env.CONTROL);
  return ownerOpen(read, owner) ? read : refusal(MAINTENANCE);
}

/** The maintenance gate's refusal, or null when it admits the call. */
export async function ownerGate(env: DoorsEnv, owner: string): Promise<Response | null> {
  const read = await ownerRead(env, owner);
  return read instanceof Response ? read : null;
}

/**
 * The record's tier numbers for the cutover's import (`import.ts`), read as every account
 * door's binding reads them (`tiersOf`); null for none, so the files plane's quota refuses
 * rather than guesses (`tier.ts`). The import passes no gate: it runs dark, before `routes`.
 */
export async function importTiers(env: DoorsEnv): Promise<unknown> {
  return tiersOf(await readControl(env.CONTROL));
}

function refusal(message: string): Response {
  return Response.json({ error: "unavailable", message },
                       { status: 503, headers: { "Retry-After": "60" } });
}
