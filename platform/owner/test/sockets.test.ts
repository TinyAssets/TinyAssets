/**
 * The owner app's sockets (tasks 5, A6; pi audit F2, F10), in workerd: a binder-shaped
 * upgrade attaches to its thread's `Conversation.watch()`, the view first, then each
 * commit's exact Chord ops, the live tee's appends between them; the binder's shape, the
 * caps and the pinned Session; a reattach from a fresh view after an eviction; a slow app's
 * catch-up; and each end of authority closing the socket with its code.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { fauxAssistantMessage, fauxToolCall, type Message } from "@earendil-works/pi-ai";
import { LiveDoc, ROOT_CONVERSATION_ID } from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import type { Origin } from "../src/ledger";
import type { Binding } from "../src/owner-do";
import {
  CLOSE, liveOps, NOT_BINDER_SHAPE, OWNER_SOCKETS, SOCKET_SESSIONS_FULL, SOCKET_URL,
  SOCKET_WINDOW, SOCKETS_FULL, THREAD_SOCKETS, THREAD_SOCKETS_FULL,
} from "../src/sockets";
import { gated, until } from "./gateway-support";
import { MCP, MUSE, signedIn } from "./ledger-support";
import { type AppClient, opened, shownText, upgrade } from "./sockets-support";
import { FauxOwnerDO, says } from "./worker";

const BG = BACKGROUND_CONTEXT;
const ROOT = ROOT_CONVERSATION_ID;

/** The faux model: `hold please` holds in `ta`'s `hold`; anything else is echoed. */
function reply(messages: Message[]) {
  const last = messages.at(-1);
  if (last?.role === "toolResult") {
    return says("held");
  }
  const text = typeof last?.content === "string" ? last.content : "";
  return says(`echo: ${text}`);
}

async function rig() {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub = env.AGENT.getByName(owner);
  const at = signedIn();
  const bind = (origin: Origin | null = null, extra: Partial<Binding> = {}): Binding =>
    ({ owner, caller: owner, origin,
       ...(origin === null ? {} : { source: MCP, authenticated_at: at }), ...extra });
  const { session: cookie } = await stub.sessionOpen(bind(null, { authenticated_at: at }));
  const session = (): Binding => ({ owner, caller: owner, origin: null, session: cookie });
  // A stub got again for each call, so one an abort broke is never reused.
  const fresh = () => env.AGENT.getByName(owner);
  const inside = <R>(act: (instance: FauxOwnerDO, state: DurableObjectState) => R | Promise<R>) =>
    runInDurableObject(fresh(), act);
  await inside((instance) => {
    instance.script = Array.from({ length: 200 }, () => reply);
  });
  let sends = 0;
  /** A message to the caller's thread with an agent, waited on; its thread. */
  const say = async (message: string, origin: Origin | null = null, agent = "main") => {
    const answer = await fresh().agentSubmit(bind(origin), {
      message, client_send_id: `s-${++sends}`, agent_id: agent, wait_ms: 20_000 });
    return answer.thread_id;
  };
  /** A socket on `thread`, as the binder forwards it. */
  const socket = async (thread: string, origin: Origin | null = null, acking = true) =>
    opened(await fresh().fetch(upgrade(bind(origin), thread)), acking);
  const refusal = async (request: Request) => {
    const answer = await fresh().fetch(request);
    return [answer.status, ((await answer.json()) as { error: string }).error];
  };
  return { owner, stub, bind, session, at, in: inside, say, socket, refusal };
}

/** The entries' model text, as the client's view holds them. */
function said(client: AppClient): string[] {
  return client.conversation.entries.flatMap((entry) => (entry.model ?? []).map((message) =>
    (typeof message.content === "string" ? message.content
      : message.content.map((part) => (part.type === "text" ? part.text : "")).join(""))))
    .filter((text) => text !== "");
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
    const shaped = (extra: Record<string, string> = {}, url?: string) =>
      upgrade(r.bind(), thread, extra, url);
    for (const request of [
      new Request(SOCKET_URL),
      shaped({ "x-agents-lifecycle-props": "e30" }),
      shaped({ Cookie: "a=b" }),
      shaped({}, `${SOCKET_URL}?_pk=chosen`),
      shaped({}, `${SOCKET_URL}?__agents_transport=capnweb`),
      shaped({}, "https://owner.internal/other"),
      new Request(SOCKET_URL, { headers: { "x-ta-binding": "{", "x-ta-thread": thread,
                                           Upgrade: "websocket" } }),
    ]) {
      expect(await r.refusal(request)).toEqual([400, NOT_BINDER_SHAPE]);
    }
    // In shape, but no owner's: refused as any door refuses it.
    expect((await r.refusal(upgrade({ ...r.bind(), owner: "someone-else" }, thread)))[0])
      .toBe(403);
    expect(await r.refusal(upgrade(r.bind(null, { session: "c".repeat(43) }), thread)))
      .toEqual([400, "an app socket carries no session"]);
    expect(await r.refusal(upgrade(r.bind(), "thread_" + "0".repeat(32))))
      .toEqual([403, "no such thread"]);
  });

  it("refuses another caller's thread: a client's is not the owner's, nor another's",
     async () => {
    const r = await rig();
    const owners = await r.say("one");
    const dots = await r.say("mine", MUSE);
    expect(await r.refusal(upgrade(r.bind(MUSE), owners))).toEqual([403, "no such thread"]);
    expect(await r.refusal(upgrade(r.bind(), dots))).toEqual([403, "no such thread"]);
    const theirs = await r.socket(dots, MUSE);
    await theirs.until((c) => c.frames.length > 0, "the client's own view");
  });

  it("refuses past each cap with its code, at once", async () => {
    const r = await rig();
    const threads = [await r.say("a")];
    for (const agent of ["b1", "b2", "b3", "b4"]) {
      threads.push(await r.say("hi", null, agent));
    }
    const open: AppClient[] = [];
    for (let n = 0; n < THREAD_SOCKETS; n++) {
      open.push(await r.socket(threads[0]!));
    }
    expect(await r.refusal(upgrade(r.bind(), threads[0]!)))
      .toEqual([403, THREAD_SOCKETS_FULL]);
    for (const thread of threads.slice(1, 4)) {
      for (let n = 0; n < THREAD_SOCKETS; n++) {
        open.push(await r.socket(thread));
      }
    }
    expect(open.length).toBe(OWNER_SOCKETS);
    expect(await r.refusal(upgrade(r.bind(), threads[4]!))).toEqual([403, SOCKETS_FULL]);
    // One closed frees its place.
    open[0]!.ws.close(1000, "done");
    await until(async () => (await r.stub.fetch(upgrade(r.bind(), threads[4]!))).status === 101,
                "a freed place");
  });

  it("pins a watched Session open, counted against OPEN_MAX: the idle LRU never closes it, " +
     "and a watch past the bound is refused", async () => {
    const r = await rig();
    const watched = await r.say("one");
    const other = await r.say("two", null, "other");
    const client = await r.socket(watched);
    await client.until((c) => c.frames.length > 0, "the view");
    await r.in((instance) => {
      instance.timing.idleCloseMs = 0;
    });
    await r.say("again", null, "other");  // the host's activity sweeps idle Sessions
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(await r.in((instance) => [instance.agent.host.peek(watched) !== undefined,
                                     instance.agent.host.openThreads().includes(watched)]))
      .toEqual([true, true]);
    // With one Session allowed and the watched one pinned, another watch is refused.
    await r.in(async (instance) => {
      await instance.agent.host.close(other);
      instance.timing.openMax = 1;
    });
    expect(await r.refusal(upgrade(r.bind(), other))).toEqual([403, SOCKET_SESSIONS_FULL]);
    // The watch ended, its pin goes: the Session is idle again, and closes as any does.
    client.ws.close(1000, "done");
    await until(async () => (await r.stub.fetch(upgrade(r.bind(), other))).status === 101,
                "the pin let go");
  });

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
    const next = (state: DurableObjectState) => {
      const fresh = new FauxOwnerDO(state, env as never);
      fresh.script = Array.from({ length: 200 }, () => reply);
      return fresh;
    };
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
      await new Promise((resolve) => setTimeout(resolve, 20));
      await fresh.agentSubmit(r.bind(), { message: "x", client_send_id: "o", agent_id: "other",
                                          wait_ms: 20_000 });
      await fresh.agentSubmit(r.bind(), { message: "y", client_send_id: "p", agent_id: "other",
                                          wait_ms: 20_000 });
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(fresh.agent.host.peek(thread)).toBeDefined();
    });
    expect(client.closed).toBeNull();
    expect(said(client)).toEqual(["one", "echo: one", "two", "echo: two"]);
  }, 20_000);

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
    await client.until((c) => c.closed !== null, "the close");
    expect(client.closed).toEqual({ code: CLOSE.suspended[0], reason: CLOSE.suspended[1] });
  });

  it("holds a slow app to pi's rule: at most the window unacknowledged, then one whole view",
     async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread, null, false);
    await client.until((c) => c.frames.length > 0, "the view");
    const notes = 150;
    await r.in(async (instance) => {
      const root = (await (await instance.agent.pi(thread)).conversation(ROOT, BG))!;
      for (let i = 0; i < notes; i++) {
        await root.submit({ type: "write", entry: { kind: "app.note", data: { i } } }, BG);
      }
    });
    await client.until((c) => c.frames.length >= SOCKET_WINDOW, "the window");
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(client.frames.length).toBe(SOCKET_WINDOW);
    client.acking(true);
    const kinds = (c: AppClient) => c.conversation.entries.filter((entry) =>
      entry.kind === "app.note").length;
    await client.until((c) => kinds(c) === notes, "every note");
    const later = client.frames.slice(SOCKET_WINDOW);
    expect(later.some((frame) => frame.type === "view")).toBe(true);  // pi's whole view
    expect(later.length).toBeLessThan(notes - SOCKET_WINDOW);
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
    await client.until((c) => c.closed !== null, "the close");
    expect(client.closed).toEqual({ code: CLOSE.lost[0], reason: CLOSE.lost[1] });
  });

  it("closes a socket whose frame is malformed or of another version", async () => {
    const r = await rig();
    const thread = await r.say("one");
    for (const frame of ["not json", JSON.stringify({ v: 2, type: "ack", seq: 1 }),
                         JSON.stringify({ v: 1, type: "subscribe-everything" }),
                         "x".repeat(2000)]) {
      const client = await r.socket(thread);
      await client.until((c) => c.frames.length > 0, "the view");
      client.send(frame);
      await client.until((c) => c.closed !== null, "the close");
      expect(client.closed!.code).toBe(CLOSE.frame[0]);
    }
  });

  it("watches the thread's task graph over the socket", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    await r.in((instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
      instance.script = Array.from({ length: 200 }, () => (messages: Message[]) =>
        messages.at(-1)?.role === "toolResult" ? says("held")
          : fauxAssistantMessage([fauxToolCall("ta", { op: "call", name: "hold",
                                                       arguments: {} })],
                                 { stopReason: "toolUse" }));
    });
    client.send({ v: 1, type: "task_graph" });
    await client.until((c) => c.graph !== undefined, "the graph");
    void r.stub.agentSubmit(r.bind(), { message: "hold please", client_send_id: "h",
                                        wait_ms: 0 });
    await client.until((c) => Object.values((c.graph as { tasks: Record<string,
      { kind: string }> }).tasks).some((task) => task.kind === "pi.tool"),
                       "a tool task in the graph");
  });
});

describe("the live tee in the view's vocabulary", () => {
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

  it("lays the tee's text over the committed partial as appends by content index", () => {
    const view = (text: string | null) => ({
      conversation: {}, entries: [],
      docs: { "pi.live": { generation: { attempt: 1, ...(text === null ? {}
        : { message: { role: "assistant", content: [{ type: "text", text }] } }) } } },
    }) as never;
    const shown = new Map<number, number>();
    const path = ["docs", "pi.live", "generation", "message", "content", 0, "text"];
    expect(liveOps(view("ab"), new Map([[0, { type: "text", text: "abcd" }]]), shown))
      .toEqual([["a", path, "cd"]]);
    expect(liveOps(view("ab"), new Map([[0, { type: "text", text: "abcdef" }]]), shown))
      .toEqual([["a", path, "ef"]]);
    // A commit resets what is shown; its tail beyond the commit is sent again.
    expect(liveOps(view("abcde"), new Map([[0, { type: "text", text: "abcdef" }]]), new Map()))
      .toEqual([["a", path, "f"]]);
    // Text the tee's does not extend (another attempt) is the commits' alone.
    expect(liveOps(view("xy"), new Map([[0, { type: "text", text: "abc" }]]), new Map()))
      .toEqual([]);
    // A block no commit holds yet, the next one, is spliced in whole.
    expect(liveOps(view("ab"), new Map([[0, { type: "text", text: "ab" }],
                                        [1, { type: "thinking", text: "hm" }]]), new Map()))
      .toEqual([["p", ["docs", "pi.live", "generation", "message", "content"], 1, 0,
                 [{ type: "thinking", thinking: "hm" }]]]);
    expect(liveOps(view(null), new Map([[0, { type: "text", text: "a" }]]), new Map()))
      .toEqual([]);
  });

  it("sends a turn's tee deltas as pi.live appends between its commits, on the gateway",
     async () => {
    const r = await gated();
    await r.connect("sk-good");
    const first = await r.stub.agentSubmit(r.bind(), { message: "cmd:echo",
                                                       client_send_id: "a", wait_ms: 20_000 });
    const client = opened(await r.stub.fetch(upgrade(r.bind(), first.thread_id)));
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

describe("a socket's lifetime is its authority's", () => {
  /** A client's thread with a socket on it, the client granted full. */
  async function clientSocket(r: Awaited<ReturnType<typeof rig>>) {
    const thread = await r.say("mine", MUSE);
    const client = await r.socket(thread, MUSE);
    await client.until((c) => c.frames.length > 0, "the view");
    return { thread, client };
  }

  const closedWith = async (client: AppClient, kind: keyof typeof CLOSE) => {
    await client.until((c) => c.closed !== null, `the ${kind} close`);
    expect(client.closed).toEqual({ code: CLOSE[kind][0], reason: CLOSE[kind][1] });
  };

  it("closes on a revoke", async () => {
    const r = await rig();
    const { client } = await clientSocket(r);
    await r.stub.grantRevoke(r.bind(), { client: MUSE.client });
    await closedWith(client, "authority");
  });

  it("closes on a scope change (the generation moves), and keeps a rename open", async () => {
    const r = await rig();
    const { client, thread } = await clientSocket(r);
    const grant = async () => (await r.stub.grantList(r.bind())).clients
      .find((row) => row.client === MUSE.client)!;
    const held = await grant();
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: held.mode,
                                         scopes: held.scopes, name: "Renamed",
                                         expected_revision: held.revision });
    client.send({ v: 1, type: "ack", seq: 1 });
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(client.closed).toBeNull();
    expect(await r.in((instance) => instance.parts.threads.get(thread) !== null)).toBe(true);
    const renamed = await grant();
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "scoped",
                                         scopes: [{ universe: "alpha", agent: "main",
                                                    capability: "converse" }],
                                         name: "Renamed", expected_revision: renamed.revision });
    await closedWith(client, "authority");
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
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(client.closed).toBeNull();
    const other = await r.socket(thread);  // a read: attaching is open too
    await other.until((c) => c.frames.length > 0, "a view while billing is suspended");
    await r.stub.admissionSet(platform, { reason: "abuse", suspended: true });
    await closedWith(client, "suspended");
    await closedWith(other, "suspended");
    expect((await r.refusal(upgrade(r.bind(), thread)))[0]).toBe(403);
  });

  it("closes on the owner's erasure", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    await r.in(async (instance) => {
      await instance.erasure.erase();
    });
    await closedWith(client, "erased");
  });

  it("closes the owner's sockets as its session closes", async () => {
    const r = await rig();
    const thread = await r.say("one");
    const client = await r.socket(thread);
    await client.until((c) => c.frames.length > 0, "the view");
    const { client: theirs } = await clientSocket(r);
    await r.stub.sessionClose(r.session());
    await closedWith(client, "session");
    theirs.send({ v: 1, type: "ack", seq: 1 });
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(theirs.closed).toBeNull();  // an outside client's rides no owner session
  });

  it("refuses a frame once its authority moved, before acting on it", async () => {
    const r = await rig();
    const { client } = await clientSocket(r);
    // The grant's generation moved without the door's close (as an eviction between
    // would leave it): the next frame finds it.
    await r.in((_instance, state) => {
      state.storage.sql.exec("UPDATE outside_clients SET generation = generation + 1, " +
                             "owner_set = 1 WHERE client = ?", MUSE.client);
    });
    client.send({ v: 1, type: "ack", seq: 1 });
    await closedWith(client, "authority");
  });
});
