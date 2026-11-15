/**
 * What the doors' recorders share (`*.record.ts`, run by `npm run record`, `ci/record.ts`):
 * an owner signed in to its app with its agent defined, and one answer's text with the ids a
 * run mints at random taken out, so base and head print the same lines for the same answer.
 */
import { env } from "cloudflare:workers";

import type { Binding } from "../../../contract/src/binding";
import { OPEN, signInApp } from "../support";

/** The agent every recorder's owner has: Claude Code, signed in inside its sandbox. */
export const SIGNED_IN = Object.freeze({ engine: "claude-code",
                                         credential: "vendor_login_in_sandbox", scopes: [] });

/** `text` with each id, token, digest and timestamp a run mints in its place. */
export function clean(text: string): string {
  return text
    .replace(/user_[0-9a-f]{32}/g, "<owner>")
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g, "<uuid>")
    .replace(/[A-Za-z0-9_-]{40,}/g, "<tok>")
    .replace(/[0-9a-f]{16,}/g, "<hex>")
    .replace(/\b\d{10,}\b/g, "<n>");
}

/**
 * Owner `who` signed in to its app, its agent `main` written as `SIGNED_IN` by the owner's
 * file doors in a live session, as the app writes it.
 */
export async function owned(who: string) {
  const app = await signInApp({ owner: who });
  const binding: Binding = { owner: who, caller: who, origin: null, tiers: OPEN.tiers };
  const stub = env.OWNER.getByName(who);
  const { session } = await stub.sessionOpen({
    ...binding, authenticated_at: Math.floor(Date.now() / 1000) - 5 });
  const live = { ...binding, session };
  for (const path of ["/workspace/agents", "/workspace/agents/main"]) {
    if ((await stub.fileStat(live, { path })).entry === null) {
      await stub.fileMkdir(live, { path, expected_etag: null });
    }
  }
  const path = "/workspace/agents/main/agent.json";
  const { entry } = await stub.fileStat(live, { path });
  const written = await stub.fileWrite(live, {
    path, expected_etag: entry?.etag ?? null,
    content: new TextEncoder().encode(JSON.stringify(SIGNED_IN)) });
  if (!("entry" in written)) {
    throw new Error("agent.json was not written");
  }
  return { who, app, binding };
}
