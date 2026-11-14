/**
 * The doors Worker (ADR-032 Decision 2): the binder, our authorization server, the
 * stateless MCP door, `/api` keys, `/hooks/inbound` and the owner surface (`doors.ts`).
 * Not deployed: its checked-in config has no routes and placeholder KV ids, and the deploy
 * kit (`platform/deploy/`) writes the deployed one. The upstream sign-in is the configured
 * owner issuer's (`oidc.ts`), or `UNWIRED` until the deploy configures it, so a sign-in
 * fails loudly.
 */
import { IMPORT_QUEUE } from "../../contract/src/import-message";
import { QUEUES } from "../../contract/src/queue-names";

import { NOT_CONFIGURED } from "./config";
import type { DoorsEnv } from "./env";
import { serve } from "./doors";
import { oidcUpstream, upstreamConfig } from "./oidc";
import { ringQueue } from "./ring";
import { erasureQueue } from "./erase";
import { importQueue } from "./import";
import { UNWIRED, type Upstream } from "./upstream";
import { bundledSources } from "../../sources/src/catalog";

/**
 * The owner's sign-in as the Worker is configured: `UNWIRED` while both the issuer and its
 * record are unset; the live one when both are set; a throw when one is set without the
 * other, or the secret is missing (a half-configured deploy fails loudly, never signs in).
 */
export function upstreamOf(env: DoorsEnv): Upstream {
  const issuer = env.OWNER_ISSUER === NOT_CONFIGURED;
  const record = env.UPSTREAM === NOT_CONFIGURED;
  if (issuer && record) {
    return UNWIRED;
  }
  if (issuer || record) {
    throw new Error("OWNER_ISSUER and UPSTREAM are configured together, or neither is");
  }
  return oidcUpstream(env.OWNER_ISSUER, upstreamConfig(env.UPSTREAM), env.WORKOS_API_KEY);
}

export default Object.freeze({
  fetch: (request: Request, env: DoorsEnv, ctx: ExecutionContext): Promise<Response> =>
    serve(request, env, ctx, () => upstreamOf(env), bundledSources()),
  // Three queues, each by its producer's name and to its one consumer: the sandbox line's
  // doorbell (`ring.ts`), the erasure's outside record (`erase.ts`) and the cutover's import
  // window (`import.ts`, by the window's own name, since only it names the window's
  // bindings). A batch of any other queue is no one's, and fails loudly.
  queue: async (batch: MessageBatch<unknown>, env: DoorsEnv): Promise<void> => {
    switch (batch.queue) {
      case QUEUES.RING:
        return ringQueue(batch, env);
      case QUEUES.ERASURES:
        return erasureQueue(batch, env, () => upstreamOf(env));
      case IMPORT_QUEUE:
        return importQueue(batch, env);
    }
    throw new Error(`the doors consume no queue ${JSON.stringify(batch.queue)}`);
  },
});
