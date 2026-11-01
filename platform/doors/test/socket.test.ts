/**
 * The owner app's live socket and conversation doors at the doors (tasks 5, A6; P12's
 * local part): `/app/api/agent/socket` behind the owner surface's token check and
 * maintenance gate, forwarded by the binder to the object's `fetch` as a fresh request, so
 * an outside credential is refused `403` and a client's forged identity header never
 * survives the hop; and `/app/api/agent/{reset,compact,fork,withdraw,task-graph}`.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { OUTSIDE_CREDENTIAL } from "../src/app";
import {
  appPost, call, connect, OPEN, registerClient, serveFirstPartyDocument, setControl,
  signInApp, url,
} from "./support";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

type Answer = { operation_id: string; thread_id: string; status: string; reason?: string };
type Frame = { v: number; type: string; seq?: number; value?: { entries: unknown[] } };

let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

/** The app's upgrade on the doors, with its token and any other headers it sends. */
function socket(token: string, thread: string | null, headers: Record<string, string> = {}):
    Promise<Response> {
  return call(new Request(url(`/app/api/agent/socket${thread === null ? ""
    : `?thread_id=${thread}`}`), {
    headers: { Upgrade: "websocket", Authorization: `Bearer ${token}`, Origin: url(""),
               ...headers },
  }));
}

/** An open socket's frames, as they come. */
function frames(response: Response): { frames: Frame[]; ws: WebSocket } {
  expect(response.status).toBe(101);
  const ws = response.webSocket!;
  const seen: Frame[] = [];
  ws.accept();
  ws.addEventListener("message", (event) => {
    const frame = JSON.parse(String(event.data)) as Frame;
    seen.push(frame);
    if (frame.seq !== undefined) {
      ws.send(JSON.stringify({ v: 1, type: "ack", seq: frame.seq }));
    }
  });
  return { frames: seen, ws };
}

async function until(check: () => boolean, what: string): Promise<void> {
  for (let i = 0; i < 800; i++) {
    if (check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timed out waiting for ${what}`);
}

async function said(token: string, message: string, id: string): Promise<Answer> {
  return (await appPost("agent/submit", token, null,
                        { message, client_send_id: id, wait_ms: 10_000 })).json<Answer>();
}

describe("the owner app's socket at the doors", () => {
  it("attaches through the binder: the view first, then the commits of a turn", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const first = await said(app.access_token, "hi", "s-1");
    const open = frames(await socket(app.access_token, first.thread_id));
    await until(() => open.frames.length > 0, "the view");
    expect(open.frames[0]).toMatchObject({ v: 1, type: "view", seq: 1 });
    const entries = open.frames[0]!.value!.entries.length;
    await said(app.access_token, "again", "s-2");
    await until(() => open.frames.some((frame) => frame.type === "ops"), "a commit's ops");
    expect(entries).toBeGreaterThan(0);
    open.ws.close(1000, "done");
  }, 60_000);

  it("refuses an outside credential, a missing thread and a request not an upgrade",
     async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const first = await said(app.access_token, "hi", "s-1");
    const tokens = await connect(await registerClient(), { owner: who });
    const outside = await socket(tokens.access_token, first.thread_id);
    expect([outside.status, await outside.json()]).toEqual([403, { error: OUTSIDE_CREDENTIAL }]);
    expect((await socket(app.access_token, null)).status).toBe(400);
    const plain = await call(new Request(url(`/app/api/agent/socket?thread_id=${
      first.thread_id}`), { headers: { Authorization: `Bearer ${app.access_token}` } }));
    expect(plain.status).toBe(426);
  }, 60_000);

  it("never lets a client's identity header through: the binder's identity is the token's",
     async () => {
    const alice = owner();
    const bob = owner();
    const a = await signInApp({ owner: alice });
    const b = await signInApp({ owner: bob });
    const bobs = await said(b.access_token, "mine", "b-1");
    const forged = { "x-ta-binding": JSON.stringify({ owner: bob, caller: bob, origin: null }),
                     "x-ta-thread": bobs.thread_id };
    // Alice's token with Bob's binding forged: bound as Alice, Bob's thread is no thread of hers.
    const refused = await socket(a.access_token, bobs.thread_id, forged);
    expect([refused.status, await refused.json()]).toEqual([403, { error: "no such thread" }]);
    // With her own thread, the forged header changes nothing: she watches her own.
    const hers = await said(a.access_token, "hers", "a-1");
    const open = frames(await socket(a.access_token, hers.thread_id, forged));
    await until(() => open.frames.length > 0, "her view");
    expect(JSON.stringify(open.frames[0]!.value)).toContain("hers");
    expect(JSON.stringify(open.frames[0]!.value)).not.toContain("mine");
    open.ws.close(1000, "done");
  }, 60_000);

  it("is behind the maintenance gate", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const first = await said(app.access_token, "hi", "s-1");
    await setControl({ ...OPEN, ordinary: "none" });
    expect((await socket(app.access_token, first.thread_id)).status).toBe(503);
  }, 60_000);
});

describe("the conversation doors at /app/api/agent/*", () => {
  it("serves reset, compact, fork, withdraw and the task graph on the owner's own threads",
     async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const first = await said(app.access_token, "hi", "s-1");
    const open = frames(await socket(app.access_token, first.thread_id));
    await until(() => open.frames.length > 0, "the view");
    const entry = (open.frames[0]!.value!.entries[0] as { id: number }).id;
    open.ws.close(1000, "done");
    const post = async (path: string, body: unknown) => {
      const response = await appPost(path, app.access_token, null, body);
      return [response.status, await response.json()];
    };
    const [status, forked] = await post("agent/fork", {
      thread_id: first.thread_id, entry_id: entry, message: "again", client_send_id: "f-1",
      wait_ms: 10_000 }) as [number, Answer];
    expect([status, forked.status]).toEqual([200, "unanswered"]);
    expect(forked.thread_id).not.toBe(first.thread_id);
    expect(await post("agent/reset", { thread_id: first.thread_id }))
      .toEqual([200, { thread_id: first.thread_id, reset: true }]);
    expect((await post("agent/compact", { thread_id: first.thread_id }))[0]).toBe(200);
    expect(await post("agent/task-graph", { thread_id: first.thread_id }))
      .toEqual([200, { thread_id: first.thread_id, graph: expect.anything() }]);
    expect(await post("agent/withdraw", { operation_id: first.operation_id }))
      .toEqual([200, { operation_id: first.operation_id, withdrawn: false }]);
    // An outside credential reaches none of them.
    const tokens = await connect(await registerClient(), { owner: who });
    for (const path of ["agent/reset", "agent/compact", "agent/fork", "agent/withdraw",
                        "agent/task-graph"]) {
      expect((await appPost(path, tokens.access_token, null,
                            { thread_id: first.thread_id })).status).toBe(403);
    }
  }, 60_000);
});
