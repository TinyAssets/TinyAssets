/**
 * Every owner a test names by `getByName` is admitted first, as the doors' sign-in callback
 * admits a new owner at sign-up (`admission.ts`, slice L): a fresh object is `pending` and
 * refuses every door until the platform's explicit admit. The admit is sent on the same
 * stub before the test's own first call, so the object takes it first (one client's calls
 * on one stub arrive in order, and the door queues its unit as it is called). A name the
 * object refuses to admit (an erased owner's tombstone, the platform's reserved caller) is
 * the test's to see: its refusal is dropped here.
 *
 * The admission tests reach objects by `get(idFromName(...))`, which this leaves alone, so
 * they see a pending object as production makes one.
 */
import { env } from "cloudflare:workers";

import { PLATFORM_CALLER } from "../src/admission";

type Admitting = { admissionSet(binding: unknown, request: unknown): Promise<unknown> };

/** The owner object's namespaces the tests bind (`vitest.config.ts`). */
const OWNER_NAMESPACES = ["OWNER", "AGENT", "GATED", "BARE", "BOUNDED", "RUNG", "SWITCHED",
                          "CACHED", "ARCHIVING", "BIGARCHIVING", "DIVERGING", "PURGING",
                          "SEEDED"];

const namespaces = env as unknown as Record<string, unknown>;
for (const name of OWNER_NAMESPACES) {
  const namespace = namespaces[name] as { getByName: (owner: string) => unknown };
  const getByName = namespace.getByName.bind(namespace);
  const admitted = new Set<string>();
  namespace.getByName = (owner: string) => {
    const stub = getByName(owner) as Admitting;
    if (!admitted.has(owner)) {
      admitted.add(owner);
      stub.admissionSet({ owner, caller: PLATFORM_CALLER, origin: null }, { admit: true })
        .catch(() => undefined);
    }
    return stub;
  };
}
