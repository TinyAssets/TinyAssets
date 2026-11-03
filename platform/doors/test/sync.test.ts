/**
 * The in-VM sync agent's doors on the egress host (slice S), in workerd through the real
 * owner object (its `SANDBOX` a recording fake, `entry.ts`): by the lease's sync token with
 * the egress token E2B's transform sets beside it (review M-5: the sync token alone, taken
 * out of the VM, reaches nothing), never the egress token alone or none; the feed, a file's
 * bytes, a listing, the upload of a file past an inline write and the fenced commit; and a
 * stale or forged token reaching no object.
 */
import { env, runInDurableObject } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

import { mintToken } from "../src/routing-mac";
import type { Binding } from "../../owner/src/owner-do";
import type { SandboxStart } from "../../owner/src/sandbox";
import { defineAgent } from "../../owner/test/runs-support";
import { EGRESS_ORIGIN } from "../src/config";
import { SYNC_HEADER } from "../src/egress";
import { GENERATION_HEADER, SANDBOX_HEADER } from "../../owner/src/network";
import type { OwnerDO } from "./entry";
import { appPost, call, OPEN, serveFirstPartyDocument, setControl, signInApp } from "./support";

let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

const SIGNED_IN = Object.freeze({ engine: "claude-code", credential: "vendor_login_in_sandbox",
                                  scopes: [] });

async function leased() {
  const who = `user_${crypto.randomUUID().replace(/-/g, "")}`;
  const app = await signInApp({ owner: who });
  const binding: Binding = { owner: who, caller: who, origin: null, tiers: OPEN.tiers };
  await defineAgent(env.OWNER.getByName(who), binding, "main", SIGNED_IN);
  await env.OWNER.getByName(who).fileWrite(binding, {
    path: "/workspace/notes.md", expected_etag: null, content: new TextEncoder().encode("hi") });
  await appPost("agent/submit", app.access_token, null, { message: "go", client_send_id: "s-1" });
  const [start] = await runInDurableObject(env.OWNER.getByName(who), (instance: unknown) =>
    (instance as OwnerDO).recorder.starts) as SandboxStart[];
  return { who, start: start!, sync: start!.tokens!.sync_token,
           // What every HTTPS call out of the sandbox carries: E2B's transform's headers.
           inside: { [SANDBOX_HEADER]: start!.tokens!.egress_token,
                     [GENERATION_HEADER]: String(start!.network!.generation) } };
}

async function sha256(bytes: Uint8Array): Promise<string> {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function door(name: string, body: unknown, headers: Record<string, string>): Promise<Response> {
  return call(new Request(`${EGRESS_ORIGIN}/sync/${name}`, {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", ...headers } }));
}

describe("the sync doors", () => {
  it("serve the feed, a file's bytes, a listing and the fenced commit, by the sync token",
     async () => {
    const { start, sync, inside } = await leased();
    const as = { [SYNC_HEADER]: sync, ...inside };
    const feed = await (await door("changes", { after: 0, limit: 100 }, as)).json() as {
      changes: { path: string }[] };
    expect(feed.changes.map((change) => change.path)).toContain("/workspace/notes.md");
    const read = await (await door("read", { path: "/workspace/notes.md" }, as)).json() as {
      inline: string; entry: { version: number } };
    expect(atob(read.inline)).toBe("hi");
    const listed = await (await door("list", { dir: "/workspace", limit: 10 }, as)).json() as {
      entries: { path: string }[] };
    expect(listed.entries.map((entry) => entry.path)).toContain("/workspace/notes.md");
    const committed = await (await door("commit", {
      lease_id: start.lease.lease_id, epoch: start.lease.epoch,
      ops: [{ op: "write", path: "/workspace/out.md", base: null, inline: btoa("made") }] }, as))
      .json() as { outcomes: { outcome: string }[] };
    expect(committed.outcomes.map((outcome) => outcome.outcome)).toEqual(["committed"]);
    const missing = await door("read", { path: "/workspace/none.md" }, as);
    expect(missing.status).toBe(404);
    // Another lease's id is fenced.
    const fenced = await door("commit", { lease_id: "0".repeat(32), epoch: 1,
                                          ops: [{ op: "remove", path: "/workspace/out.md",
                                                  base: null }] }, as);
    expect(fenced.status).toBe(403);
  });

  it("begin an upload of a file past an inline write, fenced by the lease", async () => {
    const { start, sync, inside } = await leased();
    const as = { [SYNC_HEADER]: sync, ...inside };
    const bytes = new TextEncoder().encode("x".repeat(5000));
    const hash = await sha256(bytes);
    const begun = await (await door("upload_begin", { lease_id: start.lease.lease_id,
                                                      epoch: start.lease.epoch,
                                                      size: bytes.byteLength, chunks: [hash] },
                                    as)).json() as { intent_id: string; missing: string[] };
    expect(begun.missing).toEqual([hash]);
    expect(begun.intent_id).toMatch(/^[0-9a-f]{32}$/);
    // The bytes cross as base64, decoded at the door (the object's own tests send them).
    const bad = await door("upload_chunks", { lease_id: start.lease.lease_id,
                                              epoch: start.lease.epoch,
                                              intent_id: begun.intent_id,
                                              chunks: [{ sha256: hash, bytes: "%%" }] }, as);
    expect(bad.status).toBeGreaterThanOrEqual(400);
    const fenced = await door("upload_begin", { lease_id: "0".repeat(32), epoch: 1,
                                                size: bytes.byteLength, chunks: [hash] }, as);
    expect(fenced.status).toBe(403);
  });

  it("refuse the sync token outside the sandbox: without its lease's egress token", async () => {
    const { sync } = await leased();
    const other = await leased();
    const alone = await door("changes", { after: 0, limit: 10 }, { [SYNC_HEADER]: sync });
    expect(alone.status).toBe(401);
    expect(await alone.json()).toMatchObject({ error: { code: "egress_token_missing" } });
    // Another owner's sandbox's egress token: a different owner, refused at the door.
    const elsewhere = await door("changes", { after: 0, limit: 10 },
                                 { [SYNC_HEADER]: sync, ...other.inside });
    expect(elsewhere.status).toBe(401);
  });

  it("refuse no token, the egress token, and a forged one, before any object", async () => {
    const { who, start } = await leased();
    for (const headers of [{}, { [SYNC_HEADER]: start.tokens!.egress_token,
                                 [SANDBOX_HEADER]: start.tokens!.egress_token,
                                 [GENERATION_HEADER]: String(start.network!.generation) },
                           { [SYNC_HEADER]: mintToken(Buffer.alloc(32, 7).toString("base64url"), "sync", who,
                                                      Math.floor(Date.now() / 1000) + 60).token }]) {
      const refused = await door("changes", { after: 0, limit: 10 }, headers);
      expect(refused.status).toBe(401);
    }
    const browser = await door("changes", { after: 0, limit: 10 },
                               { [SYNC_HEADER]: start.tokens!.sync_token,
                                 Origin: "https://evil.test" });
    expect(browser.status).toBe(404);
  });
});
