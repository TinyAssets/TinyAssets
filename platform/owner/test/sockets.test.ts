/**
 * The owner app's sockets (tasks 5, A6; pi audit F2, F10), in workerd: a binder-shaped
 * upgrade carrying a socket ticket attaches to its thread's `Conversation.watch()`, the view
 * first, then each commit's exact Chord ops, the live tee's appends between them; the
 * binder's shape, the ticket's single use, the caps and the pinned Session; a reattach from
 * a fresh view after an eviction; a slow app's catch-up; and each end of the credential and
 * the thread closing the socket with its code.
 */
import { runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { fauxAssistantMessage, fauxToolCall, type Message } from "@earendil-works/pi-ai";
import {
  type ConversationWatch, LiveDoc, ROOT_CONVERSATION_ID, type TaskGraphWatch,
} from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import { FORK_LOST } from "../src/agent";
import { LiveText } from "../src/live-tee";
import type { Binding } from "../src/owner-do";
import {
  BINDING_HEADER, CLOSE, liveOps, NOT_BINDER_SHAPE, OWNER_SOCKETS, OWNERS_SOCKET,
  SOCKET_SESSIONS_FULL, SOCKET_TICKET_S, SOCKET_URL, SOCKET_WINDOW, SOCKETS_FULL,
  THREAD_HEADER, THREAD_SOCKETS, THREAD_SOCKETS_FULL, TICKET_HEADER, TICKET_REFUSED,
} from "../src/sockets";
import { gated, until } from "./gateway-support";
import { MCP, MUSE, signedIn } from "./ledger-support";
import { failure } from "./support";
import {
  type AppClient, granted, mint, opened, shownText, upgrade,
} from "./sockets-support";
import { FauxOwnerDO, says } from "./worker";

const BG = BACKGROUND_CONTEXT;
const ROOT = ROOT_CONVERSATION_ID;
const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** The faux model: anything is echoed, a tool round answered `held`. */
function reply(messages: Message[]) {
  const last = messages.at(-1);
  if (last?.role === "toolResult") {
    return says("held");
  }
  const text = typeof last?.content === "string" ? last.content : "";
  return says(`echo: ${text}`);
}

/** The faux model calling `ta`'s `hold`, which holds while the probe's gate does. */
function holding(messages: Message[]) {
  return messages.at(-1)?.role === "toolResult" ? says("held")
    : fauxAssistantMessage([fauxToolCall("ta", { op: "call", name: "hold", arguments: {} })],
                           { stopReason: "toolUse" });
}

async function rig() {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub = env.AGENT.getByName(owner);
  const at = signedIn();
  const bind = (extra: Partial<Binding> = {}): Binding =>
    ({ owner, caller: owner, origin: null, ...extra });
  const { session: cookie } = await stub.sessionOpen(bind({ authenticated_at: at }));
  const session = (): Binding => ({ owner, caller: owner, origin: null, session: cookie });
  // A stub got again for each call, so one an abort broke is never reused.
  const fresh = () => env.AGENT.getByName(owner);
  const inside = <R>(act: (instance: FauxOwnerDO, state: DurableObjectState) => R | Promise<R>) =>
    runInDurableObject(fresh(), act);
  await inside((instance) => {
    instance.script = Array.from({ length: 400 }, () => reply);
  });
  let sends = 0;
  /** A message to the owner's thread with an agent, waited on; its thread. */
  const say = async (message: string, agent = "main") => {
    const answer = await fresh().agentSubmit(bind(), {
      message, client_send_id: `s-${++sends}`, agent_id: agent, wait_ms: 20_000 });
    return answer.thread_id;
  };
  /** The binder's upgrade for `thread`, with a fresh ticket of `grant`'s. */
  const shaped = async (thread: string, options: { grant?: string; expiresAt?: number;
                                                    extra?: Record<string, string>;
                                                    url?: string; binding?: Binding } = {}) =>
    upgrade(options.binding ?? bind(), thread,
            await mint(fresh(), granted(owner, options.grant, options.expiresAt), thread),
            options.extra, options.url);
  /** A socket on `thread`, opened by ticket as the binder forwards it. */
  const socket = async (thread: string, options: { grant?: string; expiresAt?: number;
                                                    acking?: boolean } = {}) =>
    opened(await fresh().fetch(await shaped(thread, options)), options.acking ?? true);
  const refusal = async (request: Request) => {
    const answer = await fresh().fetch(request);
    return [answer.status, answer.status === 101 ? "OPENED"
      : ((await answer.json()) as { error: string }).error];
  };
  return { owner, stub, fresh, bind, session, at, in: inside, say, shaped, socket, refusal };
}

type Rig = Awaited<ReturnType<typeof rig>>;

/** The entries' model text, as the client's view holds them. */
function said(client: AppClient): string[] {
  return client.conversation.entries.flatMap((entry) => (entry.model ?? []).map((message) =>
    (typeof message.content === "string" ? message.content
      : message.content.map((part) => (part.type === "text" ? part.text : "")).join(""))))
    .filter((text) => text !== "");
}

const closedWith = async (client: AppClient, kind: keyof typeof CLOSE) => {
  await client.until((c) => c.closed !== null, `the ${kind} close`);
  expect(client.closed).toEqual({ code: CLOSE[kind][0], reason: CLOSE[kind][1] });
};

/** A new instance of the class over the same storage and sockets: a simulated eviction. */
function next(state: DurableObjectState): FauxOwnerDO {
  const instance = new FauxOwnerDO(state, env as never);
  instance.script = Array.from({ length: 200 }, () => reply);
  return instance;
}

describe("an app socket on a thread's watch", () => {
  it("attaches with the view, then each commit's exact ops, a late joiner from the view " +
     "as it stands", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the first frame");
    expect(client.frames[0]).toMatchObject({ v: 1, type: "view", seq: 1 });
    expect(said(client)).toEqual(["one", "echo: one"]);
    await r.say("two");
    await client.until((c) => said(c).includes("echo: two"), "the turn's commits");
    expect(client.frames.slice(1).every((frame) => frame.type === "ops" && frame.v === 1))
      .toBe(true);
    expect(said(client)).toEqual(["one", "echo: one", "two", "echo: two"]);
    // The client's view is pi's own, op for op.
    const truth = await r.in(async (instance) => (await (await instance.agent.pi(thread))
      .conversation(ROOT, BG))!.viewState(BG).then((state) => {
      const value = state.value;
      state.dispose();
      return value;
    }));
    expect(client.view).toEqual(JSON.parse(JSON.stringify(truth)));
    // A late joiner starts from the current view; nothing is replayed.
    const late = await r.socket(thread);
    await late.until((c) => c.frames.length > 0, "the late joiner's view");
    expect(late.frames.map((frame) => frame.type)).toEqual(["view"]);
    expect(said(late)).toEqual(said(client));
  });

  it("refuses a direct upgrade not in the binder's shape, loudly", async () => {
    const r = await rig();
    const thread = await r.say("one");
    for (const request of [
      new Request(SOCKET_URL),
      await r.shaped(thread, { extra: { "x-agents-lifecycle-props": "e30" } }),
      await r.shaped(thread, { extra: { Cookie: "a=b" } }),
      await r.shaped(thread, { extra: { Authorization: "Bearer x" } }),
      await r.shaped(thread, { url: `${SOCKET_URL}?_pk=chosen` }),
      await r.shaped(thread, { url: `${SOCKET_URL}?__agents_transport=capnweb` }),
      await r.shaped(thread, { url: "https://owner.internal/other" }),
      new Request(SOCKET_URL, { headers: { [BINDING_HEADER]: "{", [THREAD_HEADER]: thread,
                                           [TICKET_HEADER]: "0".repeat(64),
                                           Upgrade: "websocket" } }),
      new Request(SOCKET_URL, { headers: { [BINDING_HEADER]: JSON.stringify(r.bind()),
                                           [THREAD_HEADER]: thread, Upgrade: "websocket" } }),
    ]) {
      expect(await r.refusal(request)).toEqual([400, NOT_BINDER_SHAPE]);
    }
    // In shape, but no owner's: refused as any door refuses it.
    expect((await r.refusal(await r.shaped(thread, {
      binding: { ...r.bind(), owner: "someone-else" } })))[0]).toBe(403);
    expect(await r.refusal(await r.shaped(thread, {
      binding: r.bind({ session: "c".repeat(43) }) })))
      .toEqual([400, "an app socket carries no session"]);
  });

  it("is the owner's own: an outside client has no socket, nor a ticket", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const outside: Binding = { owner: r.owner, caller: r.owner, origin: MUSE, source: MCP,
                               authenticated_at: r.at };
    expect(await r.refusal(await r.shaped(thread, { binding: outside })))
      .toEqual([403, OWNERS_SOCKET]);
    expect((await failure(() => r.stub.socketTicket(
      { ...outside, grant: "g", expires_at: r.at + 3600 }, { thread_id: thread }))).message)
      .toBe("only the owner may open an app socket");
  });
});

describe("the socket ticket", () => {
  it("is single-use, for its own thread, until it expires, and needs its token's grant",
     async () => {
    const r = await rig();
    const thread = await r.say("one");
    const other = await r.say("two", "other");
    const secret = await mint(r.stub, granted(r.owner), thread);
    expect(secret).toMatch(/^[0-9a-f]{64}$/);
    expect(await r.refusal(upgrade(r.bind(), thread, secret))).toEqual([101, "OPENED"]);
    expect(await r.refusal(upgrade(r.bind(), thread, secret))).toEqual([403, TICKET_REFUSED]);
    // Another thread's ticket opens nothing here, and is spent for its own.
    const theirs = await mint(r.stub, granted(r.owner), other);
    expect(await r.refusal(upgrade(r.bind(), thread, theirs))).toEqual([403, TICKET_REFUSED]);
    expect(await r.refusal(upgrade(r.bind(), other, theirs))).toEqual([403, TICKET_REFUSED]);
    // One past its life opens nothing.
    const stale = await mint(r.stub, granted(r.owner), thread);
    await r.in((_instance, state) => {
      state.storage.sql.exec("UPDATE socket_tickets SET expires_at = expires_at - ?",
                             SOCKET_TICKET_S + 1);
    });
    expect(await r.refusal(upgrade(r.bind(), thread, stale))).toEqual([403, TICKET_REFUSED]);
    expect(await r.refusal(upgrade(r.bind(), thread, "f".repeat(64))))
      .toEqual([403, TICKET_REFUSED]);
    // Only its hash is kept, and a ticket never outlives its token.
    const short = await r.stub.socketTicket(granted(r.owner, "g", Math.floor(Date.now() / 1000)
                                                    + 5), { thread_id: thread });
    expect(short.expires_at).toBeLessThanOrEqual(Math.floor(Date.now() / 1000) + 5);
    expect(await r.in((_instance, state) => state.storage.sql.exec<{ n: number }>(
      "SELECT COUNT(*) AS n FROM socket_tickets WHERE hash = ?", short.secret).one().n))
      .toBe(0);
    // A token already expired, a binding with no grant, and another's thread mint nothing.
    for (const binding of [granted(r.owner, "g", Math.floor(Date.now() / 1000) - 1),
                           r.bind()]) {
      expect((await failure(() => r.stub.socketTicket(binding, { thread_id: thread }))).name)
        .toMatch(/Refused|Invalid/);
    }
    expect((await failure(() => r.stub.socketTicket(granted(r.owner),
      { thread_id: "thread_" + "0".repeat(32) }))).message).toBe("no such thread");
  });
});

describe("the caps", () => {
  it("refuses past each cap with its code, at once", async () => {
    const r = await rig();
    const threads = [await r.say("a")];
    for (const agent of ["b1", "b2", "b3", "b4"]) {
      threads.push(await r.say("hi", agent));
    }
    const open: AppClient[] = [];
    for (let n = 0; n < THREAD_SOCKETS; n++) {
      open.push(await r.socket(threads[0]!));
    }
    expect(await r.refusal(await r.shaped(threads[0]!))).toEqual([403, THREAD_SOCKETS_FULL]);
    for (const thread of threads.slice(1, 4)) {
      for (let n = 0; n < THREAD_SOCKETS; n++) {
        open.push(await r.socket(thread));
      }
    }
    expect(open.length).toBe(OWNER_SOCKETS);
    expect(await r.refusal(await r.shaped(threads[4]!))).toEqual([403, SOCKETS_FULL]);
    // One closed frees its place.
    open[0]!.ws.close(1000, "done");
    await until(async () => (await r.fresh().fetch(await r.shaped(threads[4]!))).status === 101,
                "a freed place");
  });

  it("never opens more than THREAD_SOCKETS on one thread when upgrades race", async () => {
    const r = await rig();
    const thread = await r.say("a");
    const requests = await Promise.all(Array.from({ length: THREAD_SOCKETS + 4 }, () =>
      r.shaped(thread)));
    const answers = await Promise.all(requests.map((request) => r.fresh().fetch(request)));
    const open = answers.filter((answer) => answer.status === 101);
    expect(open.length).toBeLessThanOrEqual(THREAD_SOCKETS);
    expect(open.length).toBeGreaterThan(0);
  });

  it("never opens more than OWNER_SOCKETS across threads when upgrades race", async () => {
    const r = await rig();
    const threads: string[] = [];
    for (let n = 0; n < 6; n++) {
      threads.push(await r.say("hi", `ag${n}`));
    }
    const requests = await Promise.all(threads.flatMap((thread) =>
      Array.from({ length: THREAD_SOCKETS }, () => r.shaped(thread))));
    const answers = await Promise.all(requests.map((request) => r.fresh().fetch(request)));
    expect(answers.filter((answer) => answer.status === 101).length)
      .toBeLessThanOrEqual(OWNER_SOCKETS);
    expect(await r.in((_instance, state) => state.getWebSockets().length))
      .toBeLessThanOrEqual(OWNER_SOCKETS);
  });

  it("pins a watched Session open, counted against OPEN_MAX: the idle LRU never closes it, " +
     "and a watch past the bound is refused", async () => {
    const r = await rig();
    const watched = await r.say("one");
    const other = await r.say("two", "other");
    const client = await r.socket(watched);
    await client.until((c) => c.frames.length > 0, "the view");
    await r.in((instance) => {
      instance.timing.idleCloseMs = 0;
    });
    await r.say("again", "other");  // the host's activity sweeps idle Sessions
    await delay(50);
    expect(await r.in((instance) => [instance.agent.host.peek(watched) !== undefined,
                                     instance.agent.host.openThreads().includes(watched)]))
      .toEqual([true, true]);
    // With one Session allowed and the watched one pinned, another watch is refused.
    await r.in(async (instance) => {
      await instance.agent.host.close(other);
      instance.timing.openMax = 1;
    });
    expect(await r.refusal(await r.shaped(other))).toEqual([403, SOCKET_SESSIONS_FULL]);
    // The watch ended, its pin goes: the Session is idle again, and closes as any does.
    client.ws.close(1000, "done");
    await until(async () => (await r.fresh().fetch(await r.shaped(other))).status === 101,
                "the pin let go");
  });

  it("lets a reattach's pin go when its socket closes while the pin is in flight", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    const leaked = await r.in(async (_instance, state) => {
      const fresh = next(state);
      const host = fresh.agent.host;
      const pin = host.pin.bind(host);
      let release!: () => void;
      const gate = new Promise<void>((resolve) => { release = resolve; });
      let entered = false;
      (host as unknown as { pin: (t: string) => Promise<void> }).pin = async (t: string) => {
        entered = true;
        await gate;
        return pin(t);
      };
      const ws = state.getWebSockets()[0]!;
      const message = fresh.webSocketMessage(ws, JSON.stringify({ v: 1, type: "ack", seq: 0 }));
      for (let i = 0; i < 200 && !entered; i++) {
        await delay(5);
      }
      expect(entered).toBe(true);
      await fresh.webSocketClose(ws, 1000, "bye", true);  // the app goes while it pins
      release();
      await message;
      await delay(50);
      fresh.timing.idleCloseMs = 0;
      await delay(20);
      await fresh.agentSubmit(r.bind(), { message: "x", client_send_id: "o", agent_id: "other",
                                          wait_ms: 20_000 });
      await fresh.agentSubmit(r.bind(), { message: "y", client_send_id: "p", agent_id: "other",
                                          wait_ms: 20_000 });
      await delay(100);
      return fresh.agent.host.peek(thread) !== undefined;
    });
    expect(leaked, "the watched Session is still pinned open with no socket").toBe(false);
  }, 30_000);

  it("lets a reattach's pin go when the close comes after the attach", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    const kept = await r.in(async (_instance, state) => {
      const fresh = next(state);
      const ws = state.getWebSockets()[0]!;
      await fresh.webSocketMessage(ws, JSON.stringify({ v: 1, type: "ack", seq: 0 }));
      await delay(50);
      await fresh.webSocketClose(ws, 1000, "bye", true);
      await delay(50);
      fresh.timing.idleCloseMs = 0;
      await delay(20);
      await fresh.agentSubmit(r.bind(), { message: "x", client_send_id: "o", agent_id: "other",
                                          wait_ms: 20_000 });
      await fresh.agentSubmit(r.bind(), { message: "y", client_send_id: "p", agent_id: "other",
                                          wait_ms: 20_000 });
      await delay(100);
      return fresh.agent.host.peek(thread) !== undefined;
    });
    expect(kept).toBe(false);
  }, 30_000);

  it("stops a watch that resolves after its socket closed", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    const stopped = await r.in(async (_instance, state) => {
      const fresh = next(state);
      const watch = fresh.agent.watch.bind(fresh.agent);
      let release!: () => void;
      const gate = new Promise<void>((resolve) => { release = resolve; });
      let entered = false;
      let stops = 0;
      Object.assign(fresh.agent, { watch: async (t: string): Promise<ConversationWatch> => {
        entered = true;
        await gate;
        const made = await watch(t);
        const stop = made.stop.bind(made);
        return Object.assign(made, { stop: () => {
          stops++;
          return stop();
        } });
      } });
      const ws = state.getWebSockets()[0]!;
      const message = fresh.webSocketMessage(ws, JSON.stringify({ v: 1, type: "ack", seq: 0 }));
      for (let i = 0; i < 200 && !entered; i++) {
        await delay(5);
      }
      await fresh.webSocketClose(ws, 1000, "bye", true);
      release();
      await message;
      await delay(20);
      return stops;
    });
    expect(stopped).toBe(1);
  }, 30_000);
});

describe("after an eviction", () => {
  it("reattaches from a fresh view after a (simulated) eviction; pi replays nothing",
     async () => {
    // workerd's own reset (`abort`) disconnects a hibernating socket, and a real hibernation
    // is not driven in tests; so the eviction is simulated as the runtime makes it: a new
    // instance of the class over the same storage and sockets, with nothing in memory, the
    // old one never run again. Its sockets reach it by their next frame, or by their thread's
    // Session opening there.
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    // The thread's Session opens in the new instance: its socket reattaches from its view.
    await r.in(async (_instance, state) => {
      const fresh = next(state);
      await fresh.agentSubmit(r.bind(), { message: "two", client_send_id: "two",
                                          wait_ms: 20_000 });
      await client.until((c) => said(c).includes("echo: two"), "the new instance's commits");
    });
    const views = client.frames.filter((frame) => frame.type === "view");
    expect(views.length).toBe(2);
    expect(views[1]!.seq).toBe(1);  // numbered afresh, from its new view
    expect(said(client)).toEqual(["one", "echo: one", "two", "echo: two"]);
    // A frame the client sends reaches another new instance: it reattaches there too.
    await r.in(async (_instance, state) => {
      const fresh = next(state);
      const before = client.frames.length;
      await fresh.webSocketMessage(state.getWebSockets()[0]!,
                                   JSON.stringify({ v: 1, type: "ack", seq: 0 }));
      await client.until((c) => c.frames.slice(before).some((frame) => frame.type === "view"),
                         "a view at the client's frame");
      // The reattached watch pins its Session there too: the idle LRU spares it.
      fresh.timing.idleCloseMs = 0;
      await delay(20);
      await fresh.agentSubmit(r.bind(), { message: "x", client_send_id: "o", agent_id: "other",
                                          wait_ms: 20_000 });
      await fresh.agentSubmit(r.bind(), { message: "y", client_send_id: "p", agent_id: "other",
                                          wait_ms: 20_000 });
      await delay(50);
      expect(fresh.agent.host.peek(thread)).toBeDefined();
    });
    expect(client.closed).toBeNull();
    expect(said(client)).toEqual(["one", "echo: one", "two", "echo: two"]);
  }, 20_000);

  it("closes a socket whose thread was dropped while it was evicted", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    // Its row marked dropping with no close told (the instance that would have told is gone).
    await r.in((_instance, state) => {
      state.storage.sql.exec("UPDATE agent_threads SET state = 'dropping' WHERE thread_id = ?",
                             thread);
    });
    client.send({ v: 1, type: "ack", seq: 1 });
    await closedWith(client, "dropped");
  });

  it("closes a socket whose frame finds the owner suspended for abuse", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    // Suspended without the door's close (as an eviction between would leave it).
    await r.in((_instance, state) => {
      state.storage.sql.exec("UPDATE owner_admission SET state = 'suspended', " +
                             "reasons = '[\"abuse\"]'");
    });
    client.send({ v: 1, type: "ack", seq: 1 });
    await closedWith(client, "suspended");
  });
});

describe("frames", () => {
  it("holds a slow app to pi's rule: at most the window unacknowledged, then one whole view",
     async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread, { acking: false });
    await client.until((c) => c.frames.length > 0, "the view");
    const notes = 150;
    await r.in(async (instance) => {
      const root = (await (await instance.agent.pi(thread)).conversation(ROOT, BG))!;
      for (let i = 0; i < notes; i++) {
        await root.submit({ type: "write", entry: { kind: "app.note", data: { i } } }, BG);
      }
    });
    await client.until((c) => c.frames.length >= SOCKET_WINDOW, "the window");
    await delay(100);
    expect(client.frames.length).toBe(SOCKET_WINDOW);
    client.acking(true);
    const kinds = (c: AppClient) => c.conversation.entries.filter((entry) =>
      entry.kind === "app.note").length;
    await client.until((c) => kinds(c) === notes, "every note");
    const later = client.frames.slice(SOCKET_WINDOW);
    expect(later.some((frame) => frame.type === "view")).toBe(true);  // pi's whole view
    expect(later.length).toBeLessThan(notes - SOCKET_WINDOW);
  });

  it("holds the window against an ack past what was sent", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread, { acking: false });
    await client.until((c) => c.frames.length > 0, "the view");
    client.send({ v: 1, type: "ack", seq: 1_000_000 });
    await delay(50);
    await r.in(async (instance) => {
      const root = (await (await instance.agent.pi(thread)).conversation(ROOT, BG))!;
      for (let i = 0; i < 40; i++) {
        await root.submit({ type: "write", entry: { kind: "app.note", data: { i } } }, BG);
      }
    });
    await delay(300);
    expect(client.frames.length).toBeLessThanOrEqual(SOCKET_WINDOW + 1);
  });

  it("closes a socket whose watch pi ends on its own (its Session gone): watch_lost",
     async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    await r.in(async (instance) => {
      await instance.agent.host.close(thread);
    });
    await closedWith(client, "lost");
  });

  it("answers a fault inside the accept with a fixed close, never its text", async () => {
    const r = await rig();
    const thread = await r.say("one");
    await r.in((instance) => {
      Object.assign(instance.agent, { watch: async () => {
        throw new Error("a secret internal fault at /src/owner-do.ts:123");
      } });
    });
    const client = await r.socket(thread);
    await closedWith(client, "lost");
    expect(JSON.stringify(client.frames)).not.toMatch(/secret|stack|owner-do/);
  });

  it("closes a socket whose frame is malformed, too long or of another version", async () => {
    const r = await rig();
    const thread = await r.say("one");
    for (const frame of ["not json", JSON.stringify({ v: 2, type: "ack", seq: 1 }),
                         JSON.stringify({ v: 1, type: "subscribe-everything" }),
                         "x".repeat(2000),
                         JSON.stringify({ v: 1, type: "ack", seq: 1, pad: "x".repeat(2000) })]) {
      const client = await r.socket(thread);
      await client.until((c) => c.frames.length > 0, "the view");
      client.send(frame);
      await closedWith(client, "frame");
    }
  });

  it("watches the thread's task graph over the socket, once, and stops it at the close",
     async () => {
    const r = await rig();
    const thread = await r.say("one");
    const stops = { n: 0 };
    await r.in((instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
      instance.script = Array.from({ length: 200 }, () => holding);
      const watchGraph = instance.agent.watchTaskGraph.bind(instance.agent);
      Object.assign(instance.agent, { watchTaskGraph: async (t: string):
          Promise<TaskGraphWatch> => {
        const made = await watchGraph(t);
        const stop = made.stop.bind(made);
        return Object.assign(made, { stop: () => {
          stops.n++;
          return stop();
        } });
      } });
    });
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    client.send({ v: 1, type: "task_graph" });
    await client.until((c) => c.graph !== undefined, "the graph");
    client.send({ v: 1, type: "task_graph" });
    void r.stub.agentSubmit(r.bind(), { message: "hold please", client_send_id: "h",
                                        wait_ms: 0 });
    await client.until((c) => Object.values((c.graph as { tasks: Record<string,
      { kind: string }> }).tasks).some((task) => task.kind === "pi.tool"),
                       "a tool task in the graph");
    expect(client.frames.filter((frame) => frame.type === "graph").length).toBe(1);
    client.ws.close(1000, "done");
    await until(() => r.in(() => stops.n === 1), "the graph's watch stopped");
  });
});

describe("the live tee in the view's vocabulary", () => {
  const block = (type: "text" | "thinking", text: string) => {
    const kept = new LiveText();
    kept.append(text);
    return { type, text: kept };
  };

  it("keeps a growable buffer: appends, tails and prefixes", () => {
    const text = new LiveText();
    for (let i = 0; i < 1000; i++) {
      text.append(`d${i} `);
    }
    expect(text.length).toBe([...Array(1000).keys()].map((i) => `d${i} `).join("").length);
    expect(text.from(text.length - 5)).toBe("d999 ".slice(0, 5));
    expect(text.startsWith("d0 d1 ")).toBe(true);
    expect(text.startsWith("d0 d2")).toBe(false);
  });

  it("follows a commit with the tail of the tee's text it does not hold yet", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    const session = (client.conversation.docs["pi.provider"] as { sessionId: string }).sessionId;
    await r.in(async (instance) => {
      const tee = instance.parts.tee;
      tee.publish(session, { type: "start" });
      tee.publish(session, { type: "text_start", contentIndex: 0 });
      tee.publish(session, { type: "text_delta", contentIndex: 0, delta: "abcdef" });
      // pi commits a partial behind the stream: "abc".
      await (await instance.agent.pi(thread)).commit(async (tx) => {
        (await tx.doc(LiveDoc, ROOT)).generation = { attempt: 1, message: {
          role: "assistant", content: [{ type: "text", text: "abc" }] } as never };
      }, BG);
    });
    await client.until((c) => c.frames.at(-1)?.type === "live", "the tail after the commit");
    expect(client.frames.at(-2)!.type).toBe("ops");
    expect(client.frames.at(-1)!.ops).toEqual([
      ["a", ["docs", "pi.live", "generation", "message", "content", 0, "text"], "def"]]);
    expect([shownText(client.view), shownText(client.display)]).toEqual(["abc", "abcdef"]);
  });

  it("keeps text only for a conversation listened to, from the start it heard", async () => {
    const r = await rig();
    await r.in((instance) => {
      const tee = instance.parts.tee;
      tee.publish("unheard", { type: "start" });
      tee.publish("unheard", { type: "text_delta", contentIndex: 0, delta: "abc" });
      expect(tee.blocks("unheard")).toBeUndefined();
      const off = tee.subscribe("late", () => undefined);
      tee.publish("late", { type: "text_delta", contentIndex: 0, delta: "mid-stream" });
      expect(tee.blocks("late")).toBeUndefined();  // its start came before its listener
      tee.publish("late", { type: "start" });
      tee.publish("late", { type: "text_delta", contentIndex: 0, delta: "abc" });
      expect(tee.blocks("late")?.get(0)?.text.from(0)).toBe("abc");
      off();
      expect(tee.blocks("late")).toBeUndefined();  // no one listens: its text goes
    });
  });

  it("lays the tee's text over the committed partial as appends by content index", () => {
    const view = (text: string | null) => ({
      conversation: {}, entries: [],
      docs: { "pi.live": { generation: { attempt: 1, ...(text === null ? {}
        : { message: { role: "assistant", content: [{ type: "text", text }] } }) } } },
    }) as never;
    const shown = new Map<number, number>();
    const path = ["docs", "pi.live", "generation", "message", "content", 0, "text"];
    expect(liveOps(view("ab"), new Map([[0, block("text", "abcd")]]), shown))
      .toEqual([["a", path, "cd"]]);
    expect(liveOps(view("ab"), new Map([[0, block("text", "abcdef")]]), shown))
      .toEqual([["a", path, "ef"]]);
    // A commit resets what is shown; its tail beyond the commit is sent again.
    expect(liveOps(view("abcde"), new Map([[0, block("text", "abcdef")]]), new Map()))
      .toEqual([["a", path, "f"]]);
    // Text the tee's does not extend (another attempt) is the commits' alone.
    expect(liveOps(view("xy"), new Map([[0, block("text", "abc")]]), new Map())).toEqual([]);
    // A block no commit holds yet, the next one, is spliced in whole.
    expect(liveOps(view("ab"), new Map([[0, block("text", "ab")],
                                        [1, block("thinking", "hm")]]), new Map()))
      .toEqual([["p", ["docs", "pi.live", "generation", "message", "content"], 1, 0,
                 [{ type: "thinking", thinking: "hm" }]]]);
    expect(liveOps(view(null), new Map([[0, block("text", "a")]]), new Map())).toEqual([]);
  });

  it("sends a turn's tee deltas as pi.live appends between its commits, on the gateway",
     async () => {
    const r = await gated();
    await r.connect("sk-good");
    const first = await r.stub.agentSubmit(r.bind(), { message: "cmd:echo",
                                                       client_send_id: "a", wait_ms: 20_000 });
    const secret = await mint(r.stub, granted(r.name), first.thread_id);
    const client = opened(await r.stub.fetch(upgrade(r.bind(), first.thread_id, secret)));
    await client.until((c) => c.frames.length > 0, "the view");
    const committed: string[] = [];
    const displayed: string[] = [];
    const watching = setInterval(() => {
      committed.push(shownText(client.view));
      displayed.push(shownText(client.display));
    }, 20);
    const answer = await r.stub.agentSubmit(r.bind(), { message: "cmd:drip",
                                                        client_send_id: "b", wait_ms: 20_000 });
    clearInterval(watching);
    expect(answer.status).toBe("done");
    const live = client.frames.filter((frame) => frame.type === "live");
    expect(live.length).toBeGreaterThan(0);
    for (const frame of live) {
      for (const op of frame.ops as unknown[][]) {
        expect(op[0] === "a" || op[0] === "p").toBe(true);
        expect((op[1] as unknown[]).slice(0, 5))
          .toEqual(["docs", "pi.live", "generation", "message", "content"]);
      }
    }
    // Between commits the display ran ahead of the committed partial, never apart from it.
    expect(displayed.some((text, i) => text.length > committed[i]!.length)).toBe(true);
    expect(displayed.every((text, i) => text.startsWith(committed[i]!))).toBe(true);
  }, 20_000);
});

describe("a socket's lifetime is its credential's and its thread's", () => {
  it("closes exactly the signed-out grant's sockets, and refuses its tickets", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const phone = await r.socket(thread, { grant: "phone" });
    const laptop = await r.socket(thread, { grant: "laptop" });
    await phone.until((c) => c.frames.length > 0, "the phone's view");
    await laptop.until((c) => c.frames.length > 0, "the laptop's view");
    const spare = await mint(r.stub, granted(r.owner, "phone"), thread);
    expect(await r.stub.socketsEnd({ ...r.bind(), grant: "phone" }))
      .toEqual({ grant: "phone", ended: true });
    await closedWith(phone, "signedOut");
    expect(await r.refusal(upgrade(r.bind(), thread, spare))).toEqual([403, TICKET_REFUSED]);
    await r.say("after");
    await laptop.until((c) => said(c).includes("echo: after"), "the laptop's commits");
    expect(laptop.closed).toBeNull();
  });

  it("closes a socket an attach raced its grant's sign-out with", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const request = await r.shaped(thread, { grant: "racing" });
    await r.in(async (instance) => {
      const pin = instance.agent.host.pin.bind(instance.agent.host);
      let release!: () => void;
      const gate = new Promise<void>((resolve) => { release = resolve; });
      Object.assign(instance.agent.host, { pin: async (t: string) => {
        await gate;
        return pin(t);
      } });
      const upgrading = instance.fetch(request);
      await delay(20);
      await instance.socketsEnd({ ...r.bind(), grant: "racing" });
      release();
      const client = opened(await upgrading);
      await closedWith(client, "signedOut");
      Object.assign(instance.agent.host, { pin });
    });
  });

  it("closes at its token's expiry by the expiry job, the next one's job set", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const soon = Math.floor(Date.now() / 1000) + 2;
    const byJob = await r.socket(thread, { grant: "a", expiresAt: soon });
    const later = await r.socket(thread, { grant: "c" });
    await byJob.until((c) => c.frames.length > 0, "the view");
    expect(await r.in((_instance, state) => state.storage.sql.exec<{ time: number }>(
      "SELECT time FROM cf_agents_jobs WHERE id = 'socket-expiry'").one().time))
      .toBe(soon * 1000);
    await delay(2200);
    await runDurableObjectAlarm(r.fresh());  // the alarm, if it has not fired on its own
    await closedWith(byJob, "expired");
    later.send({ v: 1, type: "ack", seq: 1 });
    await delay(100);
    expect(later.closed).toBeNull();
    expect(await r.in((_instance, state) => state.storage.sql.exec<{ time: number }>(
      "SELECT time FROM cf_agents_jobs WHERE id = 'socket-expiry'").one().time))
      .toBeGreaterThan(soon * 1000);
  }, 15_000);

  it("closes at a frame past its token's expiry, its job lost", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread, { expiresAt: Math.floor(Date.now() / 1000) + 2 });
    await client.until((c) => c.frames.length > 0, "the view");
    await r.in((_instance, state) => {
      state.storage.sql.exec("DELETE FROM cf_agents_jobs WHERE id = 'socket-expiry'");
    });
    await delay(2200);
    client.send({ v: 1, type: "ack", seq: 1 });
    await closedWith(client, "expired");
  }, 15_000);

  it("closes every socket as every session closes (account revocation)", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    await r.stub.sessionClose(r.session());  // one session's close is the sign-out's doors'
    await delay(100);
    expect(client.closed).toBeNull();
    await r.stub.sessionCloseAll(r.bind());
    await closedWith(client, "signedOut");
  });

  it("closes on the thread's drop", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    await r.in(async (instance) => {
      const marked = await instance.parts.write(() => instance.parts.threads.abandon(thread));
      await instance.agent.drop(marked!);
    });
    await closedWith(client, "dropped");
  });

  it("closes on an abuse suspension, and keeps a billing one's reads open", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    const platform: Binding = { owner: r.owner, caller: "platform", origin: null };
    await r.stub.admissionSet(platform, { reason: "billing", suspended: true });
    client.send({ v: 1, type: "ack", seq: 1 });
    await delay(100);
    expect(client.closed).toBeNull();
    const other = await r.socket(thread);  // a read: attaching is open too
    await other.until((c) => c.frames.length > 0, "a view while billing is suspended");
    await r.stub.admissionSet(platform, { reason: "abuse", suspended: true });
    await closedWith(client, "suspended");
    await closedWith(other, "suspended");
  });

  it("closes on the owner's erasure", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    const request = await r.shaped(thread);
    await r.in(async (instance) => {
      await instance.erasure.erase();
    });
    await closedWith(client, "erased");
    expect((await r.refusal(request))[0]).toBe(403);
  });
});

describe("a fork lost between its row and its history", () => {
  it("is dropped, as fork_lost says, rather than left ready and counted", async () => {
    const r = await rig();
    await r.say("one");
    const made = await r.in(async (instance) =>
      instance.agent.thread("main", null, { kind: "fork", need: ["converse"] }));
    const first = await failure(() => r.stub.agentSubmit(r.bind(), {
      message: "go on", client_send_id: "g", thread_id: made.thread_id, wait_ms: 1_000 }));
    expect(first.message).toBe(FORK_LOST);
    await until(async () => (await r.in((instance) =>
      instance.parts.threads.row(made.thread_id))) === null, "the lost fork's row forgotten");
  }, 30_000);
});

void ({} as Rig);
