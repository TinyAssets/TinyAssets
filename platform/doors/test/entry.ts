/**
 * The test Worker: the doors, served with the fake upstream sign-in, and the real
 * `OwnerDO` beside them, so the config's OWNER (by script_name in production) is bound to
 * the real object in one script (vitest.config.ts).
 */
import type { DoorsEnv } from "../src/config";
import { serve } from "../src/doors";
import { FAKE_UPSTREAM } from "./fake-upstream";

export { OwnerDO } from "../../owner/src/owner-do";

export default {
  fetch: (request: Request, env: DoorsEnv, ctx: ExecutionContext): Promise<Response> =>
    serve(request, env, ctx, FAKE_UPSTREAM),
};
