/**
 * The owner's own agents as ledger identities (slice ID), in workerd, with the recording
 * sandbox: each `agent.json` an identity that follows its file, mail to one starting its run
 * through slice R's admission (its conversation the thread's, its input the mail, its result
 * the agent's reply in the thread, its authority the sender's), mail past the tier waiting
 * its turn, a revoked sender's mail starting nothing, two agents taking turns by mail, and
 * what bounds agents that mail each other: the tier and the owner's own limits, never a cap
 * of the platform's.
 */
import { describe, expect, it } from "vitest";

import { toolNamed } from "../../doors/src/tools";
import { AGENT_RETIRED, type Ledger, NO_AGENT } from "../src/ledger";
import type { Binding } from "../src/owner-do";
import type { RunReceipt } from "../src/runs";
import type { SandboxStart } from "../src/sandbox";
import { gated, type Gated } from "./gateway-support";
import { MCP, MUSE, rig, signedIn } from "./ledger-support";
import { ALL_SCOPES, defineAgent, recorder, runBinding, runnerBinding, startOf, TIERS }
  from "./runs-support";
import { QUEUE_ROWS } from "../src/run-rows";
import { drainDue, failure } from "./support";

/** Claude Code on the owner's own sign-in, which its record allows unattended. */
const SIGNED_IN = Object.freeze({ engine: "claude-code", credential: "vendor_login_in_sandbox",
                                  scopes: ALL_SCOPES });

const INIT = (session: string) => ({ type: "system", subtype: "init", session_id: session });
const RESULT = (text: string) => ({ type: "result", result: text, usage: { output_tokens: 3 } });

/** The ledger id of the owner's agent `name`, and when it was retired. */
async function identity(r: Gated, name: string):
    Promise<{ agent_id: string; retired_at: number | null }> {
  const found = (await r.stub.outsideAgents(r.bind())).owner_agents
    .find((agent) => agent.agent === name);
  if (found === undefined) {
    throw new Error(`${name} is no identity`);
  }
  return found;
}

let sent = 0;

/** The owner's mail in `room`, with the tier numbers the binder hands. */
function ownerMail(r: Gated, room: string, body: string, parent: string | null = null) {
  return r.stub.roomPost(r.bind({ tiers: TIERS }), {
    room_id: room, client_message_id: `m-${++sent}`, body, parent_id: parent });
}

/** The runner's end of `start`'s run: its session, its result, exit 0. */
function end(r: Gated, start: SandboxStart, result: string, session = `s-${start.run}`) {
  return r.stub.runEvents(runnerBinding(r, start), {
    frames: [{ seq: 1, at: 1, event: INIT(session) }, { seq: 2, at: 2, event: RESULT(result) }],
    end: { exit_code: 0 } });
}

/** The starts the sandbox has seen, newest last. */
async function starts(r: Gated): Promise<SandboxStart[]> {
  return (await recorder(r.stub)).starts;
}

/** A run's receipt, as the owner reads it. */
async function receipt(r: Gated, run: string): Promise<RunReceipt> {
  return await r.stub.agentStatus(r.bind(), { run_id: run }) as RunReceipt;
}

/** The newest start, its lease's tokens recorded. */
async function newest(r: Gated): Promise<SandboxStart> {
  return startOf(r.stub, (await starts(r)).at(-1)!.run);
}

const notices = async (r: Gated) =>
  (await r.stub.noticeList(r.bind())).notices.map((notice) => notice.detail);

/** Muse, admitted, its grant set by the owner, and its pull agent registered in `alpha`. */
async function muse(r: Gated, grant: { mode: "full" | "scoped";
                                       scopes: { universe: string; agent: string;
                                                 capability: string }[] }):
    Promise<{ client: Binding; agent: string }> {
  const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
  await r.stub.outsideAgents(client);
  const { revision } = (await r.stub.grantList(r.bind())).clients
    .find((entry) => entry.client === MUSE.client)!;
  await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", ...grant,
                                       expected_revision: revision, name: "Muse" });
  const { card_id, digest } = await r.stub.registerPropose(client, {
    command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
    recipe_id: null });
  const agent = await r.stub.registerAgent(r.session(), { card_id, digest });
  return { client: r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn(),
                            tiers: TIERS }), agent: agent.agent_id };
}

describe("an agent's identity", () => {
  it("is made as its agent.json appears, follows the file, and is retired when it goes, " +
     "its mail kept and new mail refused", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const main = await identity(r, "main");
    expect(main).toMatchObject({ agent_id: expect.stringMatching(/^ta_[0-9a-f]{16}$/),
                                 retired_at: null });
    // Writing the file again keeps the identity as it is.
    await defineAgent(r.stub, r.bind(), "main", { ...SIGNED_IN, scopes: [] });
    expect(await identity(r, "main")).toEqual(main);
    // Mail to it starts its run; the run's result is its reply, in the owner's mailbox.
    const ping = await ownerMail(r, `dm:${main.agent_id}`, "ping");
    await drainDue(r.stub);
    await end(r, await newest(r), "pong");
    const page = await r.stub.mailboxRead(r.bind(), { limit: 10 });
    expect(page.messages.map((m) => [m.sender_id, m.parent_id, m.body]))
      .toEqual([[main.agent_id, ping.message_id, "pong"]]);
    // A second message to it, which no run takes yet, then the file goes.
    const late = await r.stub.roomPost(r.bind(), { room_id: `dm:${main.agent_id}`,
                                                    client_message_id: "late", body: "later",
                                                    parent_id: null });
    const dir = (await r.stub.fileStat(r.bind(), { path: "/workspace/agents/main" })).entry!;
    await r.stub.fileRemove(r.bind(), { path: "/workspace/agents/main", expected_etag: dir.etag,
                                        recursive: true });
    expect((await identity(r, "main")).retired_at).not.toBeNull();
    // Its history stays readable: the owner rereads the reply it sent.
    expect((await r.stub.mailboxRead(r.bind(), { limit: 10 })).messages.map((m) => m.body))
      .toEqual(["pong"]);
    // Its untaken mail failed and is no longer held; new mail to it is refused, fixed code.
    expect(await r.sql("SELECT state, unacked FROM ledger_recipients WHERE message_id = ?",
                       late.message_id)).toEqual([{ state: "failed", unacked: 0 }]);
    expect((await failure(() => ownerMail(r, `dm:${main.agent_id}`, "anyone?"))).message)
      .toBe(AGENT_RETIRED);
    await drainDue(r.stub);
    expect(await starts(r)).toHaveLength(1);
    // An outside caller sees the live agents only; the owner sees the retired one too.
    const { client } = await muse(r, { mode: "full", scopes: [] });
    expect((await r.stub.outsideAgents(client)).owner_agents).toEqual([]);
    // The file back: the same identity, live again.
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    expect(await identity(r, "main")).toEqual(main);
    expect((await r.stub.outsideAgents(client)).owner_agents.map((a) => a.agent))
      .toEqual(["main"]);
    // A renamed agent directory moves the file: the old name retired, the new one made.
    const moving = (await r.stub.fileStat(r.bind(), { path: "/workspace/agents/main" }))
      .entry!;
    await r.stub.fileRename(r.bind(), { from: "/workspace/agents/main",
                                        to: "/workspace/agents/helper",
                                        expected_etag: moving.etag, replace: false });
    expect((await identity(r, "main")).retired_at).not.toBeNull();
    expect((await identity(r, "helper")).retired_at).toBeNull();
    expect((await failure(() => ownerMail(r, "dm:ta_0123456789abcdef", "who?"))).message)
      .toBe(NO_AGENT);
  });
});

describe("the ledger's bounds on identities", () => {
  it("count the owner's live agents among the reserved mailboxes and its rows in the " +
     "registry, forgetting the oldest retired one first", async () => {
    const r = await rig();
    const seen = await r.inLedger({ maxAgents: 2, maxAgentRows: 4, maxAgentUnacked: 2,
                                    maxOwnerUnacked: 1, maxSenderUnacked: 1, maxMessages: 5 },
                                  (ledger: Ledger) => {
      const refused = (act: () => unknown) => {
        try {
          act();
          return "done";
        } catch (error) {
          return (error as Error).message;
        }
      };
      ledger.appear("a");
      ledger.appear("b");
      const full = refused(() => ledger.appear("c"));
      // Outside agents share the same reserved mailboxes.
      const outside = refused(() => ledger.register(MUSE, MCP, {
        command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
        recipe_id: null }));
      ledger.retire("a");
      ledger.appear("c");
      ledger.retire("b");
      ledger.appear("d");
      ledger.retire("c");
      ledger.appear("e");  // the registry's fourth row: the oldest retired, a, is forgotten
      const names = ledger.natives(true).map((n) => [n.agent, n.retired_at === null]);
      // An agent's mail to the owner is held to its share of the owner's mailbox.
      const d = ledger.natives(false).find((n) => n.agent === "d")!.agent_id;
      ledger.post({ native: "d" }, `dm:${d}`, "m1", "hello owner", null);
      const share = refused(() => ledger.post({ native: "d" }, `dm:${d}`, "m2", "again",
                                              null));
      return { full, outside, names, share };
    });
    expect(seen.full).toBe("ledger_agents_full: the owner's agents are at the ledger's 2 " +
                           "enabled agents, each a reserved mailbox");
    expect(seen.outside).toBe("the owner's enabled agents are at their limit");
    expect(seen.names).toEqual([["b", false], ["c", false], ["d", true], ["e", true]]);
    expect(seen.share).toBe("this agent's unacked mail to the owner is full");
  });
});

describe("mail to an agent", () => {
  it("from the owner starts a run in the thread's conversation, and the reply lands in the " +
     "thread", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const main = await identity(r, "main");
    const ping = await ownerMail(r, `dm:${main.agent_id}`, "what is 2 + 2?");
    expect(ping.recipients).toEqual([main.agent_id]);
    // The post rang the lane: the run starts on the mail, unattended, as the owner's.
    await drainDue(r.stub);
    const first = await newest(r);
    expect(first).toMatchObject({ agent: "main", prompt: "what is 2 + 2?", unattended: true,
                                  authority: "owner", session_id: null });
    const run = await receipt(r, first.run);
    expect(run).toMatchObject({ initiator: "mail", state: "running" });
    expect(await r.sql("SELECT thread FROM conversations WHERE id = ?", run.conversation_id))
      .toEqual([{ thread: ping.message_id }]);
    // The mail is the run's own now: read, and no longer held in the agent's mailbox.
    expect(await r.sql("SELECT state, unacked FROM ledger_recipients WHERE message_id = ?",
                       ping.message_id)).toEqual([{ state: "read", unacked: 0 }]);
    // Its result is the agent's reply in the thread.
    expect(await end(r, first, "4")).toEqual({ accepted: 2, settled: "succeeded" });
    const page = await r.stub.mailboxRead(r.bind(), { limit: 10 });
    expect(page.messages).toMatchObject([{ room_id: `dm:${main.agent_id}`,
                                           sender_kind: "native", sender_id: main.agent_id,
                                           parent_id: ping.message_id, chain_depth: 1,
                                           body: "4" }]);
    // The owner answers the reply: the same conversation, its engine's session resumed.
    await ownerMail(r, `dm:${main.agent_id}`, "and 3 + 3?", page.messages[0]!.message_id);
    await drainDue(r.stub);
    const second = await newest(r);
    expect(second.run).not.toBe(first.run);
    expect(second).toMatchObject({ prompt: "and 3 + 3?", session_id: `s-${first.run}` });
    expect((await receipt(r, second.run)).conversation_id).toBe(run.conversation_id);
    // A new thread is a new conversation.
    await ownerMail(r, `dm:${main.agent_id}`, "something else");
    await drainDue(r.stub);
    expect((await receipt(r, (await newest(r)).run)).conversation_id)
      .not.toBe(run.conversation_id);
  });

  it("from a client starts a run held to the intersection with that client's grant",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const main = await identity(r, "main");
    // Muse's grant holds converse and read_graph on main where it acts, and write_graph.
    const { client, agent } = await muse(r, { mode: "scoped", scopes: [
      { universe: "alpha", agent: "main", capability: "converse" },
      { universe: "alpha", agent: "main", capability: "read_graph" },
      { universe: "alpha", agent: "main", capability: "write_graph" }] });
    const room = `dm:${agent}+${main.agent_id}`;
    const hello = await r.stub.roomPost(client, { room_id: room, client_message_id: "c-1",
                                                  body: "hello main", parent_id: null });
    expect(hello.recipients).toEqual([main.agent_id]);
    await drainDue(r.stub);
    const start = await newest(r);
    expect(start).toMatchObject({ agent: "main", prompt: "hello main", authority: MUSE.client });
    expect(await r.stub.agentStatus(r.bind(), { run_id: start.run }))
      .toMatchObject({ initiator: "mail", state: "running" });
    // The run's grant: the agent's scopes, only where Muse's grant holds them too, under
    // Muse's stamp at its generation, acting as main in alpha.
    const [grant] = await r.sql("SELECT scopes, stamp, origin FROM run_grants WHERE run = ?",
                                start.run);
    expect(JSON.parse(grant!["scopes"] as string))
      .toEqual(["read_graph", "write_graph", "converse"]);
    expect(JSON.parse(grant!["stamp"] as string)).toMatchObject({
      client: MUSE.client, universe: "alpha", agent: "main" });
    expect(JSON.parse(grant!["origin"] as string)).toEqual(MUSE);
    // The run acts as main in the ledger, never as Muse: its own mailbox, not Muse's.
    const asRun = runBinding(r, start);
    expect((await r.stub.mailboxRead(asRun, { limit: 5 })).reader).toBe(main.agent_id);
    // Its result is main's reply to Muse, in their room, which Muse reads.
    await end(r, start, "hello Muse");
    expect((await r.stub.mailboxRead(client, { limit: 5 })).messages)
      .toMatchObject([{ room_id: room, sender_id: main.agent_id, body: "hello Muse",
                        parent_id: hello.message_id }]);
    // A client whose grant does not hold converse on the agent cannot start its run by mail.
    const { revision } = (await r.stub.grantList(r.bind())).clients
      .find((entry) => entry.client === MUSE.client)!;
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "scoped",
                                         scopes: [{ universe: "alpha", agent: "main",
                                                    capability: "write_graph" }],
                                         expected_revision: revision, name: "Muse" });
    expect((await failure(() => r.stub.roomPost(client, {
      room_id: room, client_message_id: "c-2", body: "again", parent_id: null }))).message)
      .toBe("outside client capability not granted");
    // Nor post in a room that is not its own.
    expect((await failure(() => r.stub.roomPost(client, {
      room_id: `dm:${main.agent_id}`, client_message_id: "c-3", body: "x",
      parent_id: null }))).message).toBe("an outside agent posts only in its own dm room");
  });

  it("past the tier's simultaneous runs waits its turn, never refused", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const main = await identity(r, "main");
    // Seven threads to one agent on the free tier's five runs.
    for (let i = 0; i < 7; i++) {
      await ownerMail(r, `dm:${main.agent_id}`, `job ${i}`);
    }
    await drainDue(r.stub);
    expect(await r.sql("SELECT state, COUNT(*) AS n FROM runs GROUP BY state ORDER BY state"))
      .toEqual([{ state: "running", n: 5 }, { state: "waiting", n: 2 }]);
    // Every mail was taken: the waiting runs keep theirs, none is left in the ledger.
    expect(await r.sql("SELECT DISTINCT state FROM ledger_recipients WHERE agent_id = ?",
                       main.agent_id)).toEqual([{ state: "read" }]);
    expect((await starts(r)).map((start) => start.prompt))
      .toEqual(["job 0", "job 1", "job 2", "job 3", "job 4"]);
    // A run settles: the next mail's run takes its slot.
    await end(r, (await starts(r))[0]!, "done 0");
    await drainDue(r.stub);
    expect((await starts(r)).map((start) => start.prompt).slice(5)).toEqual(["job 5"]);
    expect(await r.sql("SELECT COUNT(*) AS n FROM runs WHERE state IN ('queued', 'running')"))
      .toEqual([{ n: 5 }]);
    // With the inputs' queue at its ceiling too, a mail's run cannot wait: the mail stays in
    // the ledger, held, and the lane has no work for it (no poll) until the queue has room.
    await r.sql("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < ?) " +
                  "INSERT INTO run_inputs (run, prompt, bytes) SELECT printf('run_%032x', i), " +
                  "'x', 1 FROM n", QUEUE_ROWS - 1);
    const held = await ownerMail(r, `dm:${main.agent_id}`, "job 7");
    await drainDue(r.stub);
    expect(await r.sql("SELECT state, unacked FROM ledger_recipients WHERE message_id = ?",
                       held.message_id)).toEqual([{ state: "held_cap", unacked: 1 }]);
    const [job] = await r.sql("SELECT time FROM cf_agents_jobs WHERE id = " +
                                "'owner-schedule/runs'");
    expect(job!["time"]).toBeGreaterThan(Date.now() + 30_000);
    // Room in the queue: the held mail's run waits its turn like the rest.
    await r.sql("DELETE FROM run_inputs WHERE prompt = 'x'");
    await r.stub.fileMkdir(r.bind(), { path: "/workspace/d", expected_etag: null });
    await drainDue(r.stub);
    expect(await r.sql("SELECT state, unacked FROM ledger_recipients WHERE message_id = ?",
                       held.message_id)).toEqual([{ state: "read", unacked: 0 }]);
    expect(await r.sql("SELECT COUNT(*) AS n FROM runs WHERE state = 'waiting'"))
      .toEqual([{ n: 2 }]);
  });

  it("from a revoked sender starts nothing", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const main = await identity(r, "main");
    const { client, agent } = await muse(r, { mode: "full", scopes: [] });
    const posted = await r.stub.roomPost(client, {
      room_id: `dm:${agent}+${main.agent_id}`, client_message_id: "c-1", body: "run this",
      parent_id: null });
    // The owner revokes Muse before the lane delivers its mail.
    await r.stub.grantRevoke(r.bind(), { client: MUSE.client });
    await drainDue(r.stub);
    expect(await starts(r)).toEqual([]);
    expect(await r.sql("SELECT COUNT(*) AS n FROM runs")).toEqual([{ n: 0 }]);
    expect(await r.sql("SELECT state, unacked FROM ledger_recipients WHERE message_id = ?",
                       posted.message_id)).toEqual([{ state: "failed", unacked: 0 }]);
    expect(await notices(r)).toContain("main: outside client authority no longer stands");
    // Nothing is left for the lane.
    expect(await r.sql("SELECT id FROM cf_agents_jobs WHERE id = 'owner-schedule/runs'"))
      .toEqual([]);
  });
});

describe("agents mailing each other", () => {
  it("take turns in one room, each reply the other's next run, each its own conversation",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "alice", SIGNED_IN);
    await defineAgent(r.stub, r.bind(), "bob", SIGNED_IN);
    const alice = await identity(r, "alice");
    const bob = await identity(r, "bob");
    const room = [alice.agent_id, bob.agent_id].sort().join("+");
    // The owner asks alice to start; her run mails bob in their room, as alice.
    await ownerMail(r, `dm:${alice.agent_id}`, "start a game with bob");
    await drainDue(r.stub);
    const kickoff = await newest(r);
    const opened = await r.stub.roomPost(runBinding(r, kickoff), {
      room_id: `dm:${room}`, client_message_id: "open", body: "ping 0", parent_id: null });
    expect(opened.recipients).toEqual([bob.agent_id]);
    await end(r, kickoff, "started");
    // Bob's run takes alice's mail; each reply then starts the other's next run.
    const turns: { agent: string; prompt: string; conversation: string; session: string | null;
                   depth: number }[] = [];
    for (let turn = 1; turn <= 4; turn++) {
      await drainDue(r.stub);
      const start = await newest(r);
      const run = await receipt(r, start.run);
      const [mail] = await r.sql("SELECT m.chain_depth FROM ledger_messages m JOIN " +
                                   "message_bodies b ON b.message_id = m.message_id WHERE " +
                                   "b.body = ?", start.prompt);
      turns.push({ agent: start.agent, prompt: start.prompt, conversation: run.conversation_id,
                   session: start.session_id, depth: mail!["chain_depth"] as number });
      // One run at a time in the room: the other agent's waits for this one's reply.
      expect(await r.sql("SELECT COUNT(*) AS n FROM runs WHERE state IN ('queued', " +
                           "'running', 'waiting')")).toEqual([{ n: 1 }]);
      await end(r, start, `${turn % 2 === 1 ? "pong" : "ping"} ${turn}`,
                `${start.agent}-session`);
    }
    expect(turns.map((t) => [t.agent, t.prompt, t.depth])).toEqual([
      ["bob", "ping 0", 1], ["alice", "pong 1", 2], ["bob", "ping 2", 3],
      ["alice", "pong 3", 4]]);
    // Each agent's turns are one conversation (the thread's, for that agent), resumed.
    expect(turns[0]!.conversation).toBe(turns[2]!.conversation);
    expect(turns[1]!.conversation).toBe(turns[3]!.conversation);
    expect(turns[0]!.conversation).not.toBe(turns[1]!.conversation);
    expect(turns.map((t) => t.session))
      .toEqual([null, null, "bob-session", "alice-session"]);
  });

  it("are bounded by the tier and the owner's own limits, and stop loudly at them",
     async () => {
    const r = await gated();
    const key = await r.connect("sk-good");
    const keyed = { engine: "claude-code", credential: "api_key", connection: key,
                    scopes: ALL_SCOPES };
    const names = ["a1", "a2", "a3", "a4", "a5", "a6"];
    for (const name of names) {
      await defineAgent(r.stub, r.bind(), name, keyed);
    }
    const ids = await Promise.all(names.map((name) => identity(r, name)));
    // Every agent mailed at once: never more runs live than the tier's five, the sixth
    // waiting its turn (past the inputs' queue too, its mail is held in the ledger, and a
    // full mailbox refuses more, loudly, to whoever sends it).
    for (const id of ids) {
      await ownerMail(r, `dm:${id.agent_id}`, "go");
    }
    await drainDue(r.stub);
    expect(await r.sql("SELECT state, COUNT(*) AS n FROM runs GROUP BY state ORDER BY state"))
      .toEqual([{ state: "running", n: 5 }, { state: "waiting", n: 1 }]);
    // Each settle gives the waiting run its turn.
    for (let i = 0; i < 6; i++) {
      await drainDue(r.stub);
      await end(r, (await starts(r))[i]!, "done");
    }
    // A loop between two agents on a key: a1 mails a2, and each reply is the other's next
    // run, every one admitted under the owner's own spending caps.
    const room = `dm:${[ids[0]!.agent_id, ids[1]!.agent_id].sort().join("+")}`;
    await ownerMail(r, `dm:${ids[0]!.agent_id}`, "play with a2");
    await drainDue(r.stub);
    const a1 = await newest(r);
    await r.stub.roomPost(runBinding(r, a1), { room_id: room, client_message_id: "serve",
                                               body: "ping", parent_id: null });
    await end(r, a1, "served");
    await drainDue(r.stub);
    const a2 = await newest(r);
    expect(a2).toMatchObject({ agent: "a2", prompt: "ping" });
    // The owner's daily cap is spent before a2 answers: a1's next turn is refused, its mail
    // fails, the owner is told, and the loop stops with no run.
    const { generation, policy } = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: generation, policy: {
      ...policy, models: { ...policy.models, daily_cost: 100 } } });
    const day = new Date().toISOString().slice(0, 10);
    await r.sql("INSERT INTO model_spend (period, source, cost) VALUES (?, 'anthropic', 100)",
                `d:${day}`);
    await end(r, a2, "pong");
    await drainDue(r.stub);
    expect((await newest(r)).run).toBe(a2.run);
    expect(await notices(r)).toContain("a1: owner_limit: daily_cost is spent");
    expect(await r.sql("SELECT r.state FROM ledger_recipients r JOIN ledger_messages m " +
                         "ON m.message_id = r.message_id WHERE m.room_id = ? AND " +
                         "m.sender_id = ?", room, ids[1]!.agent_id))
      .toEqual([{ state: "failed" }]);
    expect(await r.sql("SELECT id FROM cf_agents_jobs WHERE id = 'owner-schedule/runs'"))
      .toEqual([]);
  });
});

describe("the owner-mail schedules slice R stood in with", () => {
  it("are gone: no run on the owner's mail, no schedule held to it", async () => {
    const r = await gated();
    for (const extra of [{ on_mail: true }, { cron: "0 * * * *", if_mail: true }]) {
      expect((await failure(() => r.stub.agentRun(r.bind({ tiers: TIERS }), {
        agent: "main", input: "x", ...extra } as never))).message).toBe("invalid run request");
    }
    const properties = Object.keys(toolNamed("run_graph")!.input["properties"] as object);
    expect(properties.filter((name) => name.includes("mail"))).toEqual([]);
    expect((await r.sql("SELECT sql FROM sqlite_master WHERE name = 'run_schedules'"))[0]!["sql"])
      .not.toMatch(/mail/);
  });
});
