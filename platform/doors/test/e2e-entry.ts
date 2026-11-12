/**
 * The owner app's browser tests' Worker (`app.e2e.mjs`): the doors' test entry (the fake
 * upstream sign-in, the bundled records and the test OAuth source, the real `OwnerDO`, its
 * SANDBOX the owner's fake sandbox Worker), plus one door for the harness alone, on an
 * origin the doors never serve: `https://e2e.internal/starts/<owner>`, the starts that
 * owner's object asked of the fake, so the harness can play the runner and post a run's
 * events; and
 * `/signins/<owner>`, the sign-ins it asked and the codes the owner pasted back; and
 * `/starter/<owner>`, its starter seed as the owner reads it: the version reconciled, its
 * agents, whether the starter's own files are there, and each failure it was told.
 */
import type { DoorsEnv } from "../src/env";
import worker, { OwnerDO } from "./entry";

export { OwnerDO };

const HARNESS = "https://e2e.internal";
/** The starter's files the harness looks for: the profile, the map and main's definition. */
const SEEDED = Object.freeze(["/workspace/USER.md", "/workspace/index.md",
                              "/workspace/agents/main/agent.json"]);

export default {
  async fetch(request: Request, env: DoorsEnv, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const log = (env as unknown as Cloudflare.Env).SANDBOX_LOG;
    if (url.origin === HARNESS && url.pathname.startsWith("/starts/")) {
      return Response.json((await log.recorded(url.pathname.slice("/starts/".length))).starts);
    }
    if (url.origin === HARNESS && url.pathname.startsWith("/signins/")) {
      const { signins, codes } = await log.recorded(url.pathname.slice("/signins/".length));
      return Response.json({ signins, codes });
    }
    if (url.origin === HARNESS && url.pathname.startsWith("/starter/")) {
      const name = url.pathname.slice("/starter/".length);
      const owner = (env.OWNER as unknown as DurableObjectNamespace<OwnerDO>).getByName(name);
      const binding = { owner: name, caller: name, origin: null };
      const files: Record<string, boolean> = {};
      for (const path of SEEDED) {
        files[path] = (await owner.fileStat(binding, { path })).entry !== null;
      }
      return Response.json({
        version: (await owner.starterReceipts(binding)).version,
        agents: (await owner.outsideAgents(binding)).owner_agents.map((agent) => agent.agent),
        files,
        failed: (await owner.noticeList(binding)).notices
          .filter((notice) => notice.type === "starter_failed").map((notice) => notice.detail),
      });
    }
    return worker.fetch(request, env, ctx);
  },
};
