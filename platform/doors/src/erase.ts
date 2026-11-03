/**
 * The owner's erasure at the doors (tasks 2 and 5, "Erasure's order"; slice L): the door
 * the outside record unblocks. `POST /app/api/account/erase`, the owner's own token and
 * its live session (the object checks the session), confirmed by the owner's typed words.
 *
 * Order, each step safe to run again:
 * 1. What the directory holds for the owner, as its object knows it (`erasureNames`): its
 *    keys' ids and its inbound routes. Asked first, in the session and with the owner's
 *    confirmation, so a refusal there (no session, no confirmation, an erasure already
 *    under way) erases nothing.
 * 2. **The outside record**: `o:<owner>` written `erased`. From here no sign-in resolves
 *    the owner (the callback refuses it, `authorize.ts`) and the platform never admits the
 *    name again, so a stale `getByName` of a wiped object whose tombstone a crash lost
 *    makes a pending object that refuses everything (`owner/src/admission.ts`): the
 *    erasure's crash window, closed.
 * 3. The owner's directory entries deleted (`k:`, `h:`, `i:`), so no key or inbound route
 *    resolves the owner again, and every OAuth grant the owner holds revoked (its outside
 *    clients' and its own devices').
 * 4. The object's erasure (`ownerErase`): its mark, every run ended, the wipe and its
 *    tombstone; it resumes on its own after a failure.
 * 5. The person's identity deleted at the issuer (`Upstream.forget`). A failure here is
 *    reported in the answer, and the next sign-in by that identity tries again, refused.
 *
 * A failure before step 4 leaves the owner's account as it was, with what step 3 deleted
 * gone: the owner may sign in again and repeat the erasure (step 2's record makes the
 * callback refuse the sign-in, so step 2 is written only once steps 1 checked the session;
 * the founder's admin path clears a record an erasure left half done).
 */
import type { Bound } from "./binder";
import { now, type DoorsEnv } from "./config";
import { deleteKey, deleteRoute, putOwner } from "./directory";
import { revokeAll } from "./revoke";
import type { Upstream } from "./upstream";

export interface Erased {
  readonly erasing: true;
  readonly erased: boolean;
  readonly identity_deleted: boolean;
  readonly keys: number;
  readonly routes: number;
  readonly grants: number;
}

/** Erase the bound owner: the outside record first, the object last but the identity. */
export async function eraseOwner(env: DoorsEnv, { binding, stub }: Bound, request: unknown,
                                 upstream: Upstream): Promise<Erased> {
  const confirm = (request as { confirm?: unknown } | null)?.confirm as string;
  const names = await stub.erasureNames(binding, { confirm });
  await putOwner(env, binding.owner, { s: "erased", t: now() });
  for (const keyId of names.keys) {
    await deleteKey(env, keyId);
  }
  for (const route of names.routes) {
    await deleteRoute(env, route);
  }
  const grants = await revokeAll(env, binding.owner);
  const done = await stub.ownerErase(binding, { confirm });
  let identity = false;
  if (!binding.owner.startsWith("iss-")) {  // a foreign issuer's identity is its slice's
    try {
      await upstream.forget(binding.owner);
      identity = true;
    } catch {
      console.error("an erased owner's identity deletion failed; its next sign-in retries");
    }
  }
  return { erasing: true, erased: done.erased, identity_deleted: identity,
           keys: names.keys.length, routes: names.routes.length, grants };
}
