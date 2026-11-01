/**
 * The owner app's live socket and conversation doors at the doors (tasks 5, A6; P12's
 * local part): a ticket from `/app/api/agent/socket-ticket` behind the owner surface's
 * token checks and maintenance gate, then the upgrade `/app/api/agent/socket` carrying it
 * (a browser's `WebSocket` sends no `Authorization`), forwarded by the binder to the
 * object's `fetch` as a fresh request, so an outside credential gets no ticket and a
 * client's forged identity header never survives the hop; the sign-out that closes exactly
 * its device's sockets, cookie or not; and `/app/api/agent/{reset,compact,fork,withdraw,
 * task-graph}`.
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

/** A socket ticket for `thread`, from the owner's token. */
async function ticketFor(token: string, thread: string): Promise<string> {
  const minted = await appPost("agent/socket-ticket", token, null, { thread_id: thread });
  expect(minted.status).toBe(200);
  const body = await minted.json<{ ticket: string; thread_id: string; expires_at: number }>();
  expect(body.thread_id).toBe(thread);
  return body.ticket;
}

/** The app's upgrade on the doors, as a browser's `WebSocket` sends it: the ticket in the URL. */
function socket(ticket: string | null, thread: string | null,
                headers: Record<string, string> = {}): Promise<Response> {
  const query = new URLSearchParams();
  if (thread !== null) {
    query.set("thread_id", thread);
  }
  if (ticket !== null) {
    query.set("ticket", ticket);
  }
  return call(new Request(url(`/app/api/agent/socket?${query}`), {
    headers: { Upgrade: "websocket", Origin: url(""), ...headers },
  }));
}

/** An open socket's frames, as they come, and its close. */
function frames(response: Response) {
  expect(response.status).toBe(101);
  const ws = response.webSocket!;
  const seen: Frame[] = [];
  let closed: number | null = null;
  ws.accept();
  ws.addEventListener("message", (event) => {
    const frame = JSON.parse(String(event.data)) as Frame;
    seen.push(frame);
    if (frame.seq !== undefined) {
      ws.send(JSON.stringify({ v: 1, type: "ack", seq: frame.seq }));
    }
  });
  ws.addEventListener("close", (event) => {
    closed = event.code;
  });
  return { frames: seen, ws, closed: () => closed };
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
  it("attaches by ticket through the binder: the view first, then the commits of a turn",
     async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const first = await said(app.access_token, "hi", "s-1");
    const open = frames(await socket(await ticketFor(app.access_token, first.thread_id),
                                     first.thread_id));
    await until(() => open.frames.length > 0, "the view");
    expect(open.frames[0]).toMatchObject({ v: 1, type: "view", seq: 1 });
    expect(open.frames[0]!.value!.entries.length).toBeGreaterThan(0);
    await said(app.access_token, "again", "s-2");
    await until(() => open.frames.some((frame) => frame.type === "ops"), "a commit's ops");
    open.ws.close(1000, "done");
  }, 60_000);

  it("takes no bearer: only a ticket opens a socket, once, for its own thread", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const first = await said(app.access_token, "hi", "s-1");
    const other = await (await appPost("agent/submit", app.access_token, null, {
      message: "x", client_send_id: "s-2", agent_id: "other", wait_ms: 10_000 }))
      .json<Answer>();
    // The token as a bearer, with no ticket, opens nothing.
    const bearer = await socket(null, first.thread_id,
                                { Authorization: `Bearer ${app.access_token}` });
    expect(bearer.status).toBe(400);
    expect((await socket(app.access_token, first.thread_id)).status).toBe(403);
    const ticket = await ticketFor(app.access_token, first.thread_id);
    expect((await socket(ticket, other.thread_id)).status).toBe(403);  // another thread
    expect((await socket(ticket, first.thread_id)).status).toBe(403);  // spent there
    const fresh = await ticketFor(app.access_token, first.thread_id);
    frames(await socket(fresh, first.thread_id)).ws.close(1000, "done");
    expect((await socket(fresh, first.thread_id)).status).toBe(403);  // used once
    const plain = await call(new Request(url(`/app/api/agent/socket?thread_id=${
      first.thread_id}&ticket=${fresh}`)));
    expect(plain.status).toBe(426);
  }, 60_000);

  it("gives an outside credential no ticket", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const first = await said(app.access_token, "hi", "s-1");
    const tokens = await connect(await registerClient(), { owner: who });
    const outside = await appPost("agent/socket-ticket", tokens.access_token, null,
                                  { thread_id: first.thread_id });
    expect([outside.status, await outside.json()]).toEqual([403, { error: OUTSIDE_CREDENTIAL }]);
  }, 60_000);

  it("never lets a client's identity header through, nor a ticket for another owner",
     async () => {
    const alice = owner();
    const bob = owner();
    const a = await signInApp({ owner: alice });
    const b = await signInApp({ owner: bob });
    const bobs = await said(b.access_token, "mine", "b-1");
    const hers = await said(a.access_token, "hers", "a-1");
    const forged = { "x-ta-binding": JSON.stringify({ owner: bob, caller: bob, origin: null }),
                     "x-ta-thread": bobs.thread_id };
    // Alice can get no ticket for Bob's thread.
    expect((await appPost("agent/socket-ticket", a.access_token, null,
                          { thread_id: bobs.thread_id })).status).toBe(403);
    // Her ticket, with Bob's binding forged in headers, watches her own thread only.
    const open = frames(await socket(await ticketFor(a.access_token, hers.thread_id),
                                     hers.thread_id, forged));
    await until(() => open.frames.length > 0, "her view");
    expect(JSON.stringify(open.frames[0]!.value)).toContain("hers");
    expect(JSON.stringify(open.frames[0]!.value)).not.toContain("mine");
    open.ws.close(1000, "done");
    // Her ticket's secret under Bob's name opens nothing: his object never minted it.
    const ticket = await ticketFor(a.access_token, hers.thread_id);
    const [, , secret] = ticket.split(".");
    const asBob = (await ticketFor(b.access_token, bobs.thread_id)).split(".");
    expect((await socket(`${asBob[0]}.${asBob[1]}.${secret}`, bobs.thread_id)).status)
      .toBe(403);
  }, 60_000);

  it("is behind the maintenance gate", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const first = await said(app.access_token, "hi", "s-1");
    const ticket = await ticketFor(app.access_token, first.thread_id);
    await setControl({ ...OPEN, ordinary: "none" });
    expect((await socket(ticket, first.thread_id)).status).toBe(503);
  }, 60_000);
});

describe("a sign-out ends its own device's sockets", () => {
  for (const cookie of [false, true]) {
    it(`closes the signed-out device's socket ${cookie ? "with" : "without"} a session ` +
       "cookie, and leaves the other device's open", async () => {
      const who = owner();
      const phone = await signInApp({ owner: who });
      const laptop = await signInApp({ owner: who });
      const first = await said(phone.access_token, "hi", "s-1");
      const onPhone = frames(await socket(await ticketFor(phone.access_token, first.thread_id),
                                          first.thread_id));
      const onLaptop = frames(await socket(await ticketFor(laptop.access_token,
                                                           first.thread_id), first.thread_id));
      await until(() => onPhone.frames.length > 0 && onLaptop.frames.length > 0, "the views");
      const out = await appPost("session/close", phone.access_token,
                                cookie ? phone.session : null, {});
      expect(out.status).toBe(200);
      await until(() => onPhone.closed() !== null, "the phone's close");
      expect(onPhone.closed()).toBe(4440);
      // Its token is dead; the laptop keeps talking, and its socket keeps watching.
      expect((await appPost("agent/status", phone.access_token, null,
                            { operation_id: first.operation_id })).status).toBe(401);
      const before = onPhone.frames.length;
      await said(laptop.access_token, "secret after sign-out", "s-2");
      await until(() => JSON.stringify(onLaptop.frames).includes("secret after sign-out"),
                  "the laptop's commits");
      expect(onLaptop.closed()).toBeNull();
      expect(onPhone.frames.length).toBe(before);
    }, 60_000);
  }
});

describe("the conversation doors at /app/api/agent/*", () => {
  it("serves reset, compact, fork, withdraw and the task graph on the owner's own threads",
     async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const first = await said(app.access_token, "hi", "s-1");
    const open = frames(await socket(await ticketFor(app.access_token, first.thread_id),
                                     first.thread_id));
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
                        "agent/task-graph", "agent/socket-ticket"]) {
      expect((await appPost(path, tokens.access_token, null,
                            { thread_id: first.thread_id })).status).toBe(403);
    }
  }, 60_000);
});
