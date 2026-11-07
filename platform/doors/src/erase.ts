/**
 * The owner's erasure at the doors (tasks 2 and 5, "Erasure's order"; slice L, after its
 * review). Two parts, neither resting on the other's request finishing:
 *
 * - **The door**, `POST /app/api/account/erase`: the owner's own token, its live session
 *   (the object checks it) and its typed words. It calls the object's `ownerErase`, which
 *   writes the object's mark first, durable before it answers; from then on every door of
 *   the owner refuses, and the object's erasure job drives the rest.
 * - **The outside record**, the erasure queue's consumer (`erasureQueue`): the object's job
 *   puts `{owner}` on the queue at its mark and again at each attempt until the doors
 *   confirm. For each, read the names from the object (`erasureOutside`, which answers only
 *   while the owner is marked, so a stray message erases nothing), then, each step safe to
 *   run again:
 *   1. write the directory's `o:<owner>` as erased, so no sign-in resolves the owner and the
 *      platform never admits the name again;
 *   2. delete the owner's `k:`, `h:` and `i:` entries;
 *   3. revoke every OAuth grant the owner holds, its outside clients' and its devices';
 *   4. delete the person's identity at the issuer (none while no issuer is configured; a
 *      foreign issuer's identity is that provider's slice's);
 *   5. confirm to the object (`erasureOutsideDone`), whose wipe waits for it.
 *   A failure retries the message; the object's job sends it again anyway, so a half-done
 *   erasure always finishes.
 */
import { principal } from "../../contract/src/text";

import { bindPlatformFor, type Bound } from "./binder";
import { type DoorsEnv, ISSUER_NOT_CONFIGURED, now } from "./config";
import { deleteKey, deleteRoute, putOwner } from "./directory";
import { revokeAll } from "./revoke";
import type { Upstream } from "./upstream";

/** The door: the object's mark, then the job; the owner's own session and words. */
export async function eraseOwner({ binding, stub }: Bound, request: unknown):
    Promise<{ erasing: true }> {
  const confirm = (request as { confirm?: unknown } | null)?.confirm as string;
  return stub.ownerErase(binding, { confirm });
}

/** One erasure's outside record, made whole, then confirmed to the object. */
export async function eraseOutside(env: DoorsEnv, owner: string, upstream: () => Upstream):
    Promise<"done" | "wiped"> {
  const { binding, stub } = bindPlatformFor(env, owner);
  const names = await stub.erasureOutside(binding);
  if (names === null) {
    return "wiped";
  }
  await putOwner(env, owner, { s: "erased", t: now() });
  for (const keyId of names.keys) {
    await deleteKey(env, keyId);
  }
  for (const route of names.routes) {
    await deleteRoute(env, route);
  }
  await revokeAll(env, owner);
  if (env.OWNER_ISSUER !== ISSUER_NOT_CONFIGURED && !owner.startsWith("iss-")) {
    await upstream().forget(owner);
  }
  await stub.erasureOutsideDone(binding);
  return "done";
}

/** The erasure queue's consumer: each `{owner}` message's outside record, retried on failure. */
export async function erasureQueue(batch: MessageBatch<unknown>, env: DoorsEnv,
                                   upstream: () => Upstream): Promise<void> {
  for (const message of batch.messages) {
    const body = message.body as { owner?: unknown } | null;
    let owner: string;
    try {
      if (typeof body !== "object" || body === null || Object.keys(body).join() !== "owner") {
        throw new Error("out of shape");
      }
      owner = principal(body.owner);
    } catch {
      console.error("an erasure message is out of shape; dropped");
      message.ack();
      continue;
    }
    try {
      await eraseOutside(env, owner, upstream);
      message.ack();
    } catch (error) {
      // A refusal (no erasure under way) is the object's word: dropped, never retried.
      if (error instanceof Error && error.name === "Refused") {
        console.error("an erasure message names no erasure under way; dropped");
        message.ack();
        continue;
      }
      console.error("an erasure's outside record failed; the queue tries again", error);
      message.retry({ delaySeconds: 60 });
    }
  }
}
