/**
 * The owner app's browser tests' Worker (`app.e2e.mjs`): the doors' test entry (the fake
 * upstream sign-in, the bundled records and the test OAuth source, the real `OwnerDO` with
 * a recording sandbox), plus one door for the harness alone, on an origin the doors never
 * serve: `https://e2e.internal/starts/<owner>`, the starts that owner's object asked of its
 * recording sandbox, so the harness can play the runner and post a run's events.
 */
import type { DoorsEnv } from "../src/config";
import type { SandboxStart } from "../../owner/src/sandbox";
import worker, { OwnerDO as TestOwnerDO } from "./entry";

export class OwnerDO extends TestOwnerDO {
  /** What the recording sandbox was asked to start, in order. */
  e2eStarts(): SandboxStart[] {
    return this.recorder.starts;
  }
}

const HARNESS = "https://e2e.internal";

export default {
  async fetch(request: Request, env: DoorsEnv, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    if (url.origin === HARNESS && url.pathname.startsWith("/starts/")) {
      const owner = (env.OWNER as unknown as DurableObjectNamespace<OwnerDO>)
        .getByName(url.pathname.slice("/starts/".length));
      return Response.json(await owner.e2eStarts());
    }
    return worker.fetch(request, env, ctx);
  },
};
