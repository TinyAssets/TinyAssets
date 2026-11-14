/**
 * The platform gates at the doors (research §7, checklist 11): the control record
 * (`contract/src/control.ts`) read on every outside and owner call, its gates applied, and a
 * closed gate thrown as the contract's `unavailable`, its message why (`OUTSIDE_OFF`,
 * `MAINTENANCE`), which each door answers as its protocol says (`refused.ts`). An outside
 * call needs the outside gate (`outsideOpen`, fail-closed); the owner's own surfaces the
 * maintenance gate (`ownerOpen`). The record an admitted call was read on rides its binding
 * (`binder.ts`, `withAccount`). A `code` call's script reads the same gates again at each tool
 * call, in the object (`owner/src/control-gate.ts`).
 */
import {
  type ControlRead, MAINTENANCE, OUTSIDE_OFF, outsideOpen, ownerOpen, readControl, tiersOf,
} from "../../contract/src/control";
import { Refusal } from "../../contract/src/refusal";

import type { DoorsEnv } from "./env";

/** The outside gate, read once: the record it admitted the call on, or its refusal thrown. */
export async function outsideRead(env: DoorsEnv, owner: string): Promise<ControlRead> {
  const read = await readControl(env.CONTROL);
  if (!outsideOpen(read, owner)) {
    throw new Refusal("unavailable", OUTSIDE_OFF);
  }
  return read;
}

/** The maintenance gate, read once: the record it admitted the call on, or its refusal thrown. */
export async function ownerRead(env: DoorsEnv, owner: string): Promise<ControlRead> {
  const read = await readControl(env.CONTROL);
  if (!ownerOpen(read, owner)) {
    throw new Refusal("unavailable", MAINTENANCE);
  }
  return read;
}

/**
 * The record's tier numbers for the cutover's import (`import.ts`), read as every account
 * door's binding reads them (`tiersOf`); null for none, so the files plane's quota refuses
 * rather than guesses (`tier.ts`). The import passes no gate: it runs dark, before `routes`.
 */
export async function importTiers(env: DoorsEnv): Promise<unknown> {
  return tiersOf(await readControl(env.CONTROL));
}
