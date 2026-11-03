/**
 * The doors Worker (ADR-032 Decision 2): the binder, our authorization server, the
 * stateless MCP door, `/api` keys and the owner surface (`doors.ts`). Not deployed: its
 * config has no routes and placeholder KV ids. The upstream sign-in is unwired until
 * the AuthKit slice, so a sign-in fails loudly here.
 */
import type { DoorsEnv } from "./config";
import { serve } from "./doors";
import { ringQueue } from "./ring";
import { bundledSources } from "./sources";
import { UNWIRED } from "./upstream";

export default Object.freeze({
  fetch: (request: Request, env: DoorsEnv, ctx: ExecutionContext): Promise<Response> =>
    serve(request, env, ctx, UNWIRED, bundledSources()),
  // The sandbox line's doorbell (`ring.ts`): the ring queue, its one consumer.
  queue: (batch: MessageBatch<unknown>, env: DoorsEnv): Promise<void> => ringQueue(batch, env),
});
