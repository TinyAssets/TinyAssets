/**
 * The doors Worker (ADR-032 Decision 2): the binder, our authorization server, the
 * stateless MCP door, `/api` keys, `/hooks/inbound` and the owner surface (`doors.ts`).
 * Its checked-in config is not deployable (no routes, placeholder KV ids): the deploy kit
 * (`platform/deploy/`) writes the deployed one. The upstream sign-in is the configured
 * owner issuer's (`upstream.ts` `upstreamOf`), or `UNWIRED` until the deploy configures it,
 * so a sign-in fails loudly; the source records are the bundled ones.
 */
import { QUEUES } from "../../contract/src/queue-names";

import { canaryClock } from "./canary-clock";
import type { DoorsEnv } from "./env";
import { serve } from "./doors";
import { ringQueue } from "./ring";
import { erasureQueue } from "./erase";
import { importQueue } from "./import";
import { operatorQueue } from "./operator";
import { bundledSources } from "../../sources/src/catalog";

export default Object.freeze({
  fetch: (request: Request, env: DoorsEnv, ctx: ExecutionContext): Promise<Response> =>
    serve(request, env, ctx, bundledSources()),
  // Four queues, each by its producer's name and to its one consumer: the sandbox line's
  // doorbell (`ring.ts`), the erasure's outside record (`erase.ts`), the operator channel
  // (`operator.ts`) and the cutover's import window (`import/`). A batch of any other queue is
  // no one's, and fails loudly.
  queue: async (batch: MessageBatch<unknown>, env: DoorsEnv): Promise<void> => {
    switch (batch.queue) {
      case QUEUES.RING:
        return ringQueue(batch, env);
      case QUEUES.ERASURES:
        return erasureQueue(batch, env);
      case QUEUES.OPERATOR:
        return operatorQueue(batch, env);
      case QUEUES.IMPORTS:
        return importQueue(batch, env);
    }
    throw new Error(`the doors consume no queue ${JSON.stringify(batch.queue)}`);
  },
  // The platform canary's clock, the config's one schedule (`canary-clock.ts`).
  scheduled: (controller: ScheduledController, env: DoorsEnv): Promise<void> =>
    canaryClock(env, controller.scheduledTime),
});
