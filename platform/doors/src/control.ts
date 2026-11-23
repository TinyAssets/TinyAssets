/**
 * The platform gates at the doors (research §7, checklist 11): the control record
 * (`contract/src/control.ts`) read on every outside and owner call, its gates applied, and a
 * closed gate thrown as the contract's `unavailable`, its message why (`OUTSIDE_OFF`,
 * `MAINTENANCE`), which each door answers as its protocol says (`refused.ts`). An outside
 * call needs the outside gate (`outsideOpen`, fail-closed); the owner's own surfaces the
 * maintenance gate (`ownerOpen`). The record an admitted call was read on rides its binding
 * (`binder.ts`, `withAccount`). A `code` call's script reads the same gates again at each tool
 * call, in the object (`owner/src/control-gate.ts`).
 *
 * Every read here keeps its copy in the doors' keeper (`KEEPER`), which serves it when KV
 * can't be read.
 */
import {
  type ControlKeeper, type ControlRead, type Kept, MAINTENANCE, OUTSIDE_OFF, outsideOpen,
  ownerOpen, readControl,
} from "../../contract/src/control";
import { Refusal } from "../../contract/src/refusal";

import type { AccountBinding } from "../../contract/src/binding";
import { type Bound, withAccount } from "./binder";
import type { DoorsEnv } from "./env";

/**
 * The isolate's copy of the record last read well formed: the platform's one record, no
 * owner's, so every request the isolate serves shares it. `"none"`: the last read found no
 * record or a malformed one, so the location's copy is dropped too; null: not yet looked for.
 */
let held: Kept | "none" | null = null;

/**
 * The location's copy's cache key, on a host no request can name (`.invalid`): the cache is the
 * CDN's, so a key on one of the zone's hosts could be served to a visitor asking for it.
 */
const KEPT_AT = "https://control.invalid/c:control";
/** The header the location's copy carries its first read's time in, epoch ms. */
const READ_AT = "x-control-read-at";

/**
 * The doors' keeper: the isolate's memory, then the location's cache (the Cache API, the CDN's
 * and not KV's backend), written only when the text changes. A location that never read the
 * record, or whose cache evicted it, keeps none.
 */
const KEEPER: ControlKeeper = Object.freeze({
  async kept(): Promise<Kept | null> {
    if (held !== null) {
      return held === "none" ? null : held;
    }
    const hit = await caches.default.match(KEPT_AT);
    if (hit === undefined) {
      return null;
    }
    held = { text: await hit.text(), read_at: Number(hit.headers.get(READ_AT)) };
    return held;
  },
  async keep(text: string | null, at: number): Promise<void> {
    if (held === "none" ? text === null : held?.text === text) {
      return;
    }
    if (text === null) {
      held = "none";
      await caches.default.delete(KEPT_AT);
      return;
    }
    held = { text, read_at: at };
    await caches.default.put(KEPT_AT, new Response(text, { headers: {
      "Cache-Control": "max-age=31536000", [READ_AT]: String(at) } }));
  },
});

/** The control record as the doors read it, kept in their keeper. */
export function controlRead(env: DoorsEnv): Promise<ControlRead> {
  return readControl(env.CONTROL, KEEPER);
}

/** The outside gate, read once: the record it admitted the call on, or its refusal thrown. */
export async function outsideRead(env: DoorsEnv, owner: string): Promise<ControlRead> {
  const read = await controlRead(env);
  if (!outsideOpen(read, owner)) {
    throw new Refusal("unavailable", OUTSIDE_OFF);
  }
  return read;
}

/** An outside caller's binding past the outside gate, the record it was admitted on riding it. */
export async function outsideAccount(env: DoorsEnv,
                                     bound: Bound<AccountBinding>): Promise<Bound<AccountBinding>> {
  return withAccount(bound, await outsideRead(env, bound.binding.owner));
}

/** The maintenance gate, read once: the record it admitted the call on, or its refusal thrown. */
export async function ownerRead(env: DoorsEnv, owner: string): Promise<ControlRead> {
  const read = await controlRead(env);
  if (!ownerOpen(read, owner)) {
    throw new Refusal("unavailable", MAINTENANCE);
  }
  return read;
}
