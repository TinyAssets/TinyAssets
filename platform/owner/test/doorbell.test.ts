/**
 * The doorbell (slice W, tasks 7): each wake kind's request from its record, sent by the
 * gateway with the secret only it can open; the kill switch in the control record
 * (`CONTROL`); admission; a revoke never outlived; a slow send never replayed; a write that
 * rings only the agents following it whose grant reads it, and never an agent's own; and a
 * wake that leads its agent's digest to just the change.
 *
 * The object is production's but for a recorder on its GATEWAY's rings (`RungOwnerDO`):
 * each ring is recorded as the object asks it, then made by the real gateway, whose sends
 * reach the fake receiver (`gateway/test/fake-upstream.ts`, read back from
 * `https://status.test/doorbell`). Each test opens the kill switch for its own owner only
 * (the maintenance gate's canary list), and uses its own callback paths.
 */
import { createHmac } from "node:crypto";

import { runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { PLATFORM_CALLER } from "../src/admission";
import { WriteGate } from "../src/gate";
import { clock, type Origin, WAKE_LEASE_S } from "../src/ledger";
import type { OwnerDO } from "../src/owner-do";
import { checkKinds, wakeKinds } from "../../contract/src/wake-records";
import { SUBSCRIPTIONS_PER_AGENT } from "../../contract/src/limits";
import { subscriptionId } from "../../contract/src/wake-kinds";
import { CLOSED, Doorbell, type RingAnswer, retryAfterOf, runWakes } from "../src/wakes";
import raw from "../../contract/wake-kinds.json" with { type: "json" };
import { DOT, EVENT, ledgerOn, ledgerSubscription, MCP, MUSE, rig, type Rig, WHSEC }
  from "./ledger-support";
import { failure } from "./support";
import type { Rang, RungOwnerDO } from "./worker";

const TOKEN = "sk-ant-oat01-" + "t".repeat(40);
const KEY = Buffer.from(WHSEC.slice("whsec_".length), "base64");

type Rung = Rig & {
  rang: () => Promise<Rang[]>;
  /** A path of this test's own on the fake receiver. */
  path: string;
};

async function rung(): Promise<Rung> {
  const r = await rig(env.RUNG as unknown as DurableObjectNamespace<OwnerDO>);
  return { ...r, path: `t${crypto.randomUUID().slice(0, 8)}`,
           rang: () => runInDurableObject(r.stub, (i: OwnerDO) => [...(i as RungOwnerDO).rang]) };
}

/** What the fake receiver took on this test's paths, oldest first. */
async function received(r: Rung): Promise<{ url: string; redirect: string;
                                            headers: Record<string, string>; body: string }[]> {
  const all = await (await fetch("https://status.test/doorbell")).json() as
    { url: string; redirect: string; headers: Record<string, string>; body: string }[];
  return all.filter((request) => request.url.includes(r.path));
}

/** The control record: the kill switch on for `owners` only, or off. */
async function control(owners: readonly string[], outside: "on" | "off" = "on"): Promise<void> {
  await env.CONTROL.put("c:control", JSON.stringify({
    outside, ordinary: "canary", canary: owners, inbound: false,
  }));
}

/**
 * How long `rings` waits for its rings before it fails with the wake states: the slowest run
 * measured of the longest test that waits on it (a revoke landing mid-pass, 1.3 s with 16
 * busy loops on 4 cores; 0.11 to 0.16 s alone) with more than twice that again for margin,
 * so it fails with its diagnostic well inside the test's own 5 s timeout, never as a bare
 * timeout.
 */
const RINGS_BOUND_MS = 3_000;

/** Run the object's alarm until `n` rings were asked and no wake is due. */
async function rings(r: Rung, n: number): Promise<Rang[]> {
  const until = Date.now() + RINGS_BOUND_MS;
  while (Date.now() < until) {
    await runDurableObjectAlarm(r.stub);
    const asked = await r.rang();
    const open = await r.sql("SELECT 1 FROM ledger_wakes WHERE state IN ('pending', 'sending') " +
                             "AND due <= ?", clock());
    if (asked.length >= n && open.length === 0) {
      return asked;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`the wakes were not rung within ${RINGS_BOUND_MS} ms: ` + JSON.stringify([
    (await r.rang()).length,
    await r.sql("SELECT agent_id, state, due, marked, receipt_json FROM ledger_wakes"),
    await r.wakesAt(), clock()]));
}

function write(r: Rig, collection: string, rowId: string, caller = r.bind(null),
               isPrivate = false) {
  return r.stub.collectionWrite(caller, { collection, row_id: rowId, body: `body of ${rowId}`,
                                          expected_etag: "", private: isPrivate });
}

/**
 * An agent's client's MCP Events subscription, as the MCP door makes it: its id derived,
 * its callback sealed by the gateway (as `verifyDoorbell` seals it once verified).
 */
async function subscribed(r: Rung, from: Origin = MUSE, follows: { board?: boolean; rooms?: string[] }
                            = {}, path = "chat", ttlMs: number | null = null) {
  const url = `https://doorbell.test/${r.path}/${path}`;
  const args = { command_center_id: "alpha", board: follows.board ?? false,
                 rooms: follows.rooms ?? [] };
  const id = subscriptionId(from.client, url, EVENT, args);
  const sealed = await env.CONNECT.sealDoorbell(r.owner, id, 1,
                                                { record: "chatgpt_mcp_events", url, secret: WHSEC });
  await r.stub.wakeSubscribe(r.bind(from), {
    subscription_id: id, name: EVENT, arguments: args, blob: sealed.blob, kid: sealed.kid,
    version: 1, expires_at: ttlMs === null ? null : clock() + ttlMs / 1000 });
  return { id, url };
}

/** The owner's pasted token for an agent, as the owner surface's route makes it. */
async function pasted(r: Rung, agent: string, kind: string, url: string,
                      follows: { board?: boolean; rooms?: string[] } = {}) {
  const board = follows.board ?? false;
  const rooms = follows.rooms ?? [];
  const id = subscriptionId("owner", url, kind, { agent_id: agent, board, rooms });
  const sealed = await env.CONNECT.sealDoorbell(r.owner, id, 1,
                                                { record: kind, url, secret: TOKEN });
  return r.stub.agentDoorbell(r.session(null), {
    agent_id: agent, kind, subscription_id: id, board, rooms, blob: sealed.blob, kid: sealed.kid,
    version: 1 });
}

function signed(id: string, stamp: string, body: string): string {
  return "v1," + createHmac("sha256", KEY).update(`${id}.${stamp}.${body}`).digest("base64");
}

type Delivered = { eventId: string; name: string; timestamp: string; cursor: null;
                   data: { agent_id: string; changes: string[]; after: number; read: string[] } };

describe("each wake kind sends its own record's shape, its secret the gateway's alone", () => {
  it("ChatGPT MCP Events: a signed event, IDs and cursors only, nothing in the clear here",
     async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    const { id, url } = await subscribed(r);
    await r.post(null, `dm:${muse}`, { body: "a secret plan" });
    const [asked] = await rings(r, 1);
    const [wake] = await r.sql("SELECT wake_id, state, created_at FROM ledger_wakes");
    expect(wake!["state"]).toBe("sent");
    // The object never held the secret or the URL: only the gateway's blob.
    const stored = JSON.stringify(await r.sql("SELECT * FROM wake_callbacks"));
    expect([stored.includes(WHSEC), stored.includes(url), stored.includes(r.path)])
      .toEqual([false, false, false]);
    expect(asked!.request.headers).toEqual({ "Content-Type": "application/json",
                                              "X-MCP-Subscription-Id": id });
    const [sent] = await received(r);
    expect([sent!.url, sent!.redirect]).toEqual([url, "manual"]);
    expect(sent!.headers["webhook-id"]).toBe(wake!["wake_id"]);
    expect(sent!.headers["webhook-signature"])
      .toBe(signed(sent!.headers["webhook-id"]!, sent!.headers["webhook-timestamp"]!, sent!.body));
    expect(JSON.parse(sent!.body)).toEqual({
      eventId: wake!["wake_id"], name: EVENT, cursor: null,
      timestamp: new Date(Math.floor((wake!["created_at"] as number) * 1000)).toISOString(),
      data: { agent_id: muse, command_center_id: "alpha", changes: ["mail"], cursor: 0,
              after: 0, read: ["inbox_digest"] },
    });
    expect(sent!.body).not.toContain("secret plan");
  });

  it("Claude /fire: the owner's pasted token, sealed, sent as a bearer with its headers",
     async () => {
    const r = await rung();
    await control([r.owner]);
    const claude = await r.register(MUSE, "Claude", "routine_fire");
    const url = `https://api.anthropic.com/v1/claude_code/routines/trig_${r.path}/fire`;
    // Only the owner, in session, for the agent's own kind.
    expect((await failure(() => r.stub.agentDoorbell(r.bind(null), {
      agent_id: claude, kind: "claude_routine_fire", subscription_id: "sub_" + "0".repeat(32),
      board: true, rooms: [], blob: "b", kid: "k1", version: 1 }))).message)
      .toBe("interactive owner session required");
    expect((await failure(() => pasted(r, claude, "grok_webhook_routine",
                                       `https://doorbell.test/${r.path}/grok`))).message)
      .toBe("not an agent whose doorbell the owner sets");
    expect(await pasted(r, claude, "claude_routine_fire", url, { board: true }))
      .toMatchObject({ agent_id: claude });
    await write(r, "board", "t1");
    await rings(r, 1);
    const [sent] = await received(r);
    expect(sent!.headers).toMatchObject({
      "authorization": `Bearer ${TOKEN}`, "content-type": "application/json",
      "anthropic-beta": "experimental-cc-routine-2026-04-01", "anthropic-version": "2023-06-01",
    });
    expect(JSON.parse(sent!.body)).toEqual({
      text: `TinyAssets: new board for agent ${claude} in alpha. Read just the change: ` +
        "board_digest with after=0." });
  });

  it("Grok's webhook routine: the routine's key as a bearer, the wake as JSON", async () => {
    const r = await rung();
    await control([r.owner]);
    const grok = await r.register(MUSE, "Grok", "webhook");
    await pasted(r, grok, "grok_webhook_routine", `https://doorbell.test/${r.path}/grok`,
                 { rooms: ["room:app:lobby"] });
    await write(r, "room:app:den", "m0");  // not a room it follows: rings nothing
    expect(await r.table("ledger_wakes")).toEqual([]);
    await write(r, "room:app:lobby", "m1");
    await rings(r, 1);
    const [sent] = await received(r);
    expect(sent!.headers["authorization"]).toBe(`Bearer ${TOKEN}`);
    expect(JSON.parse(sent!.body)).toEqual({
      agent_id: grok, command_center_id: "alpha", changes: ["rooms"], cursor: 0, after: 1,
      read: ["inbox_digest"] });
  });

  it("Muse: no push, so no wake and no doorbell; it catches up on its own schedule",
     async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await r.register(MUSE, "Muse", "pull");
    await r.post(null, `dm:${muse}`);
    await write(r, "board", "t1");
    expect(await r.table("ledger_wakes")).toEqual([]);
    expect((await failure(() => pasted(r, muse, "grok_webhook_routine",
                                       `https://doorbell.test/${r.path}/x`))).message)
      .toBe("not an agent whose doorbell the owner sets");
    const record = wakeKinds().find((kind) => kind.id === "muse_recurring_task")!;
    expect([record.push, record.delivery_method]).toEqual([null, null]);
    expect(record.catch_up).toContain("inbox_digest and board_digest");
  });
});

describe("the records are data, checked whole", () => {
  it("cites every record, and refuses one that breaks the shape", () => {
    expect(wakeKinds().map((kind) => [kind.id, kind.delivery_method, kind.push?.callback]))
      .toEqual([["chatgpt_mcp_events", "mcp_event", "subscribe"],
                ["claude_routine_fire", "routine_fire", "owner"],
                ["grok_webhook_routine", "webhook", "owner"],
                ["muse_recurring_task", null, undefined]]);
    expect(wakeKinds().every((kind) => kind.citations.length > 0)).toBe(true);
    const [first, ...rest] = raw.kinds;
    const broken = (change: Record<string, unknown>) =>
      () => checkKinds({ ...raw, kinds: [{ ...first, push: { ...first!.push, ...change } },
                                         ...rest] });
    expect(broken({ body: { text: "{body}" } })).toThrow("unknown placeholder");
    expect(broken({ secret: "whsec_.*" })).toThrow("anchored");
    expect(broken({ auth: "basic" })).toThrow("bearer or standard_webhooks");
    expect(broken({ headers: { Authorization: "x" } })).toThrow("never the auth's own");
    expect(broken({ auth: "bearer" })).toThrow("signed by webhook");
    expect(broken({ max_body_bytes: 12 })).toThrow("max_body_bytes");
    expect(() => checkKinds({ ...raw, kinds: [first, first] })).toThrow("share an id");
    expect(() => checkKinds({ ...raw, kinds: [{ ...first, citations: [] }] }))
      .toThrow("cites no source");
  });
});

describe("the kill switch and admission", () => {
  it("claims nothing while the switch is off, or the maintenance gate leaves the owner out",
     async () => {
    const r = await rung();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await subscribed(r);
    for (const [owners, outside] of [[[r.owner], "off"], [["someone-else"], "on"]] as const) {
      await control(owners, outside);
      await r.post(null, `dm:${muse}`, { cmid: `c-${outside}` });
      await runInDurableObject(r.stub, (i: OwnerDO) => i.alarm());
      expect((await r.table("ledger_wakes")).map((w) => [w["state"], w["claim_id"]]))
        .toEqual([["pending", null]]);
      expect(await r.rang()).toEqual([]);
      expect(await r.wakesAt()).toBeGreaterThan(clock() + 30);  // held, never a busy loop
    }
    await env.CONTROL.delete("c:control");  // missing: closed too
    await runInDurableObject(r.stub, (i: OwnerDO) => i.alarm());
    expect(await r.rang()).toEqual([]);
    await control([r.owner]);
    await r.sql("DELETE FROM cf_agents_jobs");  // the hold lifts at its time; here, now
    await write(r, "notes", "n1");  // a door re-derives the lane's job from the rows
    expect(await rings(r, 1)).toHaveLength(1);
  });

  it("claims nothing for an owner not admitted, though the switch is on", async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await subscribed(r);
    await r.stub.admissionSet({ owner: r.owner, caller: PLATFORM_CALLER, origin: null },
                              { reason: "billing", suspended: true });
    await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      ledgerOn(state.storage).post(null, `dm:${muse}`, "c1", "hello", null, clock() - 10);
    });
    await runInDurableObject(r.stub, (i: OwnerDO) => i.alarm());
    expect((await r.table("ledger_wakes")).map((w) => [w["state"], w["claim_id"]]))
      .toEqual([["pending", null]]);
    expect(await r.rang()).toEqual([]);
  });
});

describe("a write rings only who follows it and may read it, and never on an agent's own", () => {
  it("rings a board follower on the owner's write, but no one on an outside client's",
     async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    const dot = await r.register(DOT, "Dot", "mcp_event");
    await subscribed(r, MUSE, { board: true });
    await subscribed(r, DOT, { board: true }, "dot");
    // An agent's write, or a run's, rings no agent: so two agents never wake each other
    // without end (OpenAI's feedback-loop check; slice ID's rule).
    for (let round = 0; round < 3; round++) {
      await write(r, "board", `m${round}`, r.bind(MUSE));
      await write(r, "board", `d${round}`, r.bind(DOT));
    }
    expect(await r.table("ledger_wakes")).toEqual([]);
    await write(r, "board", "o1");
    const asked = await rings(r, 2);
    const woken = asked.map((ring) => (JSON.parse(ring.request.body) as Delivered).data.agent_id);
    expect(woken.sort()).toEqual([muse, dot].sort());
  });

  it("never rings an agent whose grant cannot read the change", async () => {
    const r = await rung();
    await control([r.owner]);
    await r.register(DOT, "Dot", "mcp_event");
    await subscribed(r, DOT, { board: true }, "dot");
    const scope = (capability: string) => ({ universe: "alpha", agent: "main", capability });
    await r.stub.grantSet(r.session(null), {
      client: DOT.client, source: "mcp", mode: "scoped", scopes: [scope("write_graph")],
      expected_revision: (await r.stub.grantList(r.bind(null))).clients
        .find((c: { client: string }) => c.client === DOT.client)!.revision, name: "",
    } as never);
    await write(r, "board", "o1");
    expect(await r.table("ledger_wakes")).toEqual([]);
    expect(await r.rang()).toEqual([]);
  });

  it("names the change and the cursor its digest is after, and nothing else", async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await subscribed(r, MUSE, { board: true, rooms: ["room:app:lobby"] });
    await write(r, "board", "t0");
    const [first] = (await rings(r, 1)).map((ring) => JSON.parse(ring.request.body) as Delivered);
    expect([first!.data.changes, first!.data.read, first!.data.after])
      .toEqual([["board"], ["board_digest"], 0]);
    const board = await r.stub.boardDigest(r.bind(MUSE), { limit: 50, after: first!.data.after });
    expect(board.rows.map((row) => row.row_id)).toEqual(["t0"]);
    await write(r, "board", "secret", r.bind(null), true);  // a private row rings no one
    expect(await r.sql("SELECT 1 FROM ledger_wakes WHERE state = 'pending'")).toEqual([]);
    // Mail and the room it follows, in one wake: just those, after the cursor it names.
    await r.stub.policyWakeLimits(r.session(null), {
      agent_id: muse, max_wakes_per_hour: null, batch_window_s: 3600,
      expected_generation: (await r.stub.policyRead(r.bind(null))).generation });
    const before = (await r.sql("SELECT change FROM collection_clock"))[0]!["change"];
    await r.post(null, `dm:${muse}`, { body: "new mail" });
    await write(r, "room:app:lobby", "m1");
    await write(r, "room:app:den", "m2");  // not followed
    await r.sql("UPDATE ledger_wakes SET due = ? WHERE state = 'pending'", clock() - 1);
    await write(r, "notes", "n1");  // a door re-derives the lane's job from the rows
    const last = JSON.parse((await rings(r, 2))[1]!.request.body) as Delivered;
    expect([last.data.changes, last.data.read, last.data.after])
      .toEqual([["mail", "rooms"], ["inbox_digest"], before]);
    const inbox = await r.stub.inboxDigest(r.bind(MUSE), { limit: 50, after: last.data.after });
    expect(inbox.mail.messages.map((m) => m.body)).toEqual(["new mail"]);
    expect(inbox.rooms.rows.map((row) => row.row_id)).toEqual(["m1", "m2"]);
  });
});

describe("a revoke is never outlived", () => {
  it("lands mid-pass: the next agent's wake is suppressed and its subscription already gone",
     async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    const dot = await r.register(DOT, "Dot", "mcp_event");
    await subscribed(r, MUSE, { board: true });
    await subscribed(r, DOT, { board: true }, "dot");
    const session = r.session(null);
    const woken = (ring: Rang) => (JSON.parse(ring.request.body) as Delivered).data.agent_id;
    await runInDurableObject(r.stub, (i: OwnerDO) => {
      const instance = i as RungOwnerDO;
      instance.during = async () => {
        instance.during = null;
        const first = woken(instance.rang.at(-1)!);
        await instance.disableAgent(session, { agent_id: first === muse ? dot : muse,
                                               reason: "revoked" });
      };
    });
    await write(r, "board", "t1");  // both ring, in one pass
    await rings(r, 1);
    const sent = (await r.rang()).map(woken);
    expect(sent).toHaveLength(1);
    const other = sent[0] === muse ? dot : muse;
    const states = Object.fromEntries((await r.table("ledger_wakes"))
      .map((w) => [w["agent_id"], w["state"]]));
    expect(states).toEqual({ [sent[0]!]: "sent", [other]: "suppressed" });
    expect((await r.sql("SELECT agent_id FROM wake_callbacks")).map((c) => c["agent_id"]))
      .toEqual(sent);
  });

  it("lands between the mark and the byte: nothing is sent", async () => {
    const r = await rung();
    const t = clock();
    const out = await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      const ledger = ledgerOn(state.storage);
      const muse = ledger.register(MUSE, MCP, { command_center_id: "alpha", display_name: "Muse",
                                               delivery_method: "mcp_event", recipe_id: null })
        .agent_id;
      ledger.subscribe(MUSE, ledgerSubscription(), t);
      ledger.post(null, `dm:${muse}`, "c1", "hello", null, t - 100);
      const gate = new WriteGate(state.storage).write;
      let units = 0;
      // The pass's second unit is the mark; the revoke lands right after it.
      const write = (async (unit: () => unknown) => {
        const done = await gate(unit);
        if (++units === 2) {
          await gate(() => ledger.disable(muse, "revoked"));
        }
        return done;
      }) as typeof gate;
      let sent = 0;
      const doorbell = new Doorbell({
        ledger, live: () => true, reads: () => true, touched: () => false,
        notice: async () => undefined, write, clock: () => t, gate: async () => true,
        ring: async () => {
          sent++;
          return { state: "answered", status: 202, retry_after: null };
        } });
      const accepted = await runWakes(ledger, doorbell, () => t, write);
      return { accepted, sent,
               wake: state.storage.sql.exec("SELECT state, marked, receipt_json FROM " +
                                            "ledger_wakes").one() };
    });
    expect(out).toEqual({ accepted: 0, sent: 0, wake: {
      state: "failed", marked: 1, receipt_json: '{"reason":"not_sent","through":1}' } });
  });

  it("keeps the owner's pasted token through a grant change, and erases the client's own",
     async () => {
    const r = await rung();
    const claude = await r.register(MUSE, "Claude", "routine_fire");
    await pasted(r, claude, "claude_routine_fire",
                 `https://api.anthropic.com/v1/claude_code/routines/trig_${r.path}/fire`);
    await r.stub.grantSet(r.session(null), {
      client: MUSE.client, source: "mcp", mode: "scoped",
      scopes: [{ universe: "alpha", agent: "main", capability: "read_graph" }],
      expected_revision: (await r.stub.grantList(r.bind(null))).clients
        .find((c: { client: string }) => c.client === MUSE.client)!.revision, name: "",
    } as never);
    expect((await r.sql("SELECT principal FROM wake_callbacks")).map((c) => c["principal"]))
      .toEqual(["owner"]);
  });

  it("lets only the caller that holds a subscription end it, a refresh replacing its secret",
     async () => {
    const r = await rung();
    await r.register(MUSE, "Muse", "mcp_event");
    const { id } = await subscribed(r);
    await subscribed(r);  // a refresh: the same id, the old secret kept to sign beside it
    const [row] = await r.sql("SELECT previous_blob IS NOT NULL AS rotating, " +
                              "previous_until - refreshed_at AS grace FROM wake_callbacks");
    expect(row).toEqual({ rotating: 1, grace: 300 });
    const end = { subscription_id: id };
    expect(await r.stub.wakeUnsubscribe(r.bind(DOT), end)).toEqual({ unsubscribed: false });
    expect((await failure(() => r.stub.wakeUnsubscribe(r.bind(null), end))).message)
      .toBe("only the client that holds a subscription ends it");
    expect(await r.stub.wakeUnsubscribe(r.bind(MUSE), end)).toEqual({ unsubscribed: true });
    expect(await r.stub.wakeUnsubscribe(r.bind(MUSE), end)).toEqual({ unsubscribed: false });
    expect(await r.sql("SELECT * FROM wake_callbacks")).toEqual([]);
  });

  it("stops at a subscription's granted expiry, and keeps it across a restart", async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await subscribed(r, MUSE, {}, "short", 60_000);
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    await rings(r, 1);
    await r.sql("UPDATE wake_callbacks SET expires_at = ?", clock() - 1);
    await r.post(null, `dm:${muse}`, { cmid: "c2" });
    await rings(r, 1);
    expect((await r.table("ledger_wakes")).map((w) => w["state"])).toEqual(["sent", "suppressed"]);
    expect(await r.rang()).toHaveLength(1);
  });

  it("holds each agent to its own subscriptions, so no client fills another's room",
     async () => {
    const r = await rung();
    const t = clock();
    const out = await r.inLedger({}, (ledger) => {
      ledger.register(MUSE, MCP, { command_center_id: "alpha", display_name: "Muse",
                                   delivery_method: "mcp_event", recipe_id: null });
      const claude = ledger.register(DOT, MCP, { command_center_id: "alpha",
                                                 display_name: "Claude",
                                                 delivery_method: "routine_fire",
                                                 recipe_id: null }).agent_id;
      const id = (n: number) => "sub_" + n.toString(16).padStart(32, "0");
      const attempt = (act: () => unknown) => {
        try {
          act();
          return "stored";
        } catch (error) {
          return (error as Error).message;
        }
      };
      for (let n = 0; n < SUBSCRIPTIONS_PER_AGENT; n++) {
        ledger.subscribe(MUSE, { ...ledgerSubscription(), subscription_id: id(n),
                                 expires_at: n === 0 ? t + 60 : null }, t);
      }
      const full = attempt(() => ledger.subscribe(
        MUSE, { ...ledgerSubscription(), subscription_id: "sub_" + "f".repeat(32) }, t));
      // A refresh at the bound is the same subscription, never counted again.
      const refreshed = attempt(() => ledger.subscribe(
        MUSE, { ...ledgerSubscription(), subscription_id: id(1) }, t));
      // The owner's own paste, for its own agent, always has room.
      const owners = attempt(() => ledger.subscribe(null, {
        ...ledgerSubscription(), subscription_id: "sub_" + "e".repeat(32), agent_id: claude,
        kind: "claude_routine_fire" }, t));
      // A lapsed one holds no place: the next subscribe lets it go.
      const lapsed = attempt(() => ledger.subscribe(
        MUSE, { ...ledgerSubscription(), subscription_id: "sub_" + "d".repeat(32) }, t + 61));
      return { full, refreshed, owners, lapsed };
    });
    expect(out).toEqual({ full: expect.stringContaining(
      `subscription_limit: ${SUBSCRIPTIONS_PER_AGENT}`), refreshed: "stored", owners: "stored",
      lapsed: "stored" });
    expect(SUBSCRIPTIONS_PER_AGENT).toBe(16);
  });

  it("does not ring an owner-pasted doorbell whose client's grant cannot read", async () => {
    // The pasted token outlives a grant change (it is the owner's), so the grant check at
    // the write and at the send is all that holds it.
    const r = await rung();
    await control([r.owner]);
    const grok = await r.register(DOT, "Grok", "webhook");
    await r.stub.grantSet(r.session(null), {
      client: DOT.client, source: "mcp", mode: "scoped",
      scopes: [{ universe: "alpha", agent: "main", capability: "write_graph" }],
      expected_revision: (await r.stub.grantList(r.bind(null))).clients
        .find((c: { client: string }) => c.client === DOT.client)!.revision, name: "",
    } as never);
    await pasted(r, grok, "grok_webhook_routine", `https://bots.example.net/${r.path}`,
                 { board: true });
    expect(await r.sql("SELECT principal FROM wake_callbacks")).toEqual([{ principal: "owner" }]);
    await write(r, "board", "o1");
    for (let n = 0; n < 5; n++) {
      await runDurableObjectAlarm(r.stub);
    }
    expect(await r.rang()).toEqual([]);
  });
});

describe("a send's answer", () => {
  it("reads a Retry-After of zero, or a past date, as none, so the wake backs off", async () => {
    const now = clock();
    expect(retryAfterOf("30", now)).toBe(30);
    expect(retryAfterOf(new Date((now + 90) * 1000).toUTCString(), now)).toBeGreaterThan(80);
    for (const unread of ["0", new Date((now - 90) * 1000).toUTCString(), "soon", null]) {
      expect(retryAfterOf(unread, now)).toBeNull();
    }
    const r = await rung();
    await control([r.owner]);
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await subscribed(r, MUSE, {}, "status-429?retry=0");
    await r.post(null, `dm:${muse}`);
    await rings(r, 1);
    const [limited] = await r.sql("SELECT due - ? AS wait, receipt_json FROM ledger_wakes",
                                  clock());
    expect(JSON.parse(limited!["receipt_json"] as string)).toMatchObject(
      { status: 429, retry_after: WAKE_LEASE_S });
    expect(limited!["wait"] as number).toBeGreaterThan(WAKE_LEASE_S - 5);
  });

  it("settles each subscription on its own: a retry rings only the one a 429 left owed",
     async () => {
    const r = await rung();
    const t = clock();
    const out = await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      const ledger = ledgerOn(state.storage);
      const muse = ledger.register(MUSE, MCP, { command_center_id: "alpha", display_name: "Muse",
                                               delivery_method: "mcp_event", recipe_id: null })
        .agent_id;
      const [taken, limited, gone] = ["a", "b", "c"].map((n) => "sub_" + n.repeat(32));
      for (const id of [taken!, limited!, gone!]) {
        ledger.subscribe(MUSE, { ...ledgerSubscription(), subscription_id: id }, t);
      }
      ledger.post(null, `dm:${muse}`, "c1", "hello", null, t - 100);
      const write = new WriteGate(state.storage).write;
      const answers: Record<string, number> = { [taken!]: 202, [limited!]: 429, [gone!]: 410 };
      const asked: string[] = [];
      const doorbell = new Doorbell({
        ledger, live: () => true, reads: () => true, touched: () => false,
        notice: async () => undefined, write, clock: () => t, gate: async () => true,
        ring: async (id) => {
          asked.push(id);
          return { state: "answered", status: answers[id]!,
                   retry_after: answers[id] === 429 ? "30" : null };
        } });
      const read = () => ({
        wake: state.storage.sql.exec("SELECT state, receipt_json FROM ledger_wakes").one(),
        mail: state.storage.sql.exec("SELECT state FROM ledger_recipients").toArray(),
        status: state.storage.sql.exec("SELECT subscription_id, status FROM wake_callbacks " +
                                       "ORDER BY subscription_id").toArray(),
      });
      await runWakes(ledger, doorbell, () => t, write);
      const first = { asked: [...asked].sort(), ...read() };
      asked.length = 0;
      answers[limited!] = 202;
      await runWakes(ledger, doorbell, () => t + 31, write);
      return { first, second: { asked: [...asked], ...read() }, ids: [taken, limited, gone] };
    });
    const [taken, limited, gone] = out.ids;
    expect(out.first).toMatchObject({
      asked: [taken, limited, gone],
      wake: { state: "rate_limited" },
      mail: [{ state: "woken" }],  // one subscription took it
      status: [{ subscription_id: taken, status: 202 }, { subscription_id: limited, status: 429 },
               { subscription_id: gone, status: 410 }],
    });
    expect(JSON.parse(out.first.wake["receipt_json"] as string))
      .toMatchObject({ accepted: true, retry_after: 30 });
    expect(out.second).toMatchObject({ asked: [limited], wake: { state: "sent" } });
  });

  it("tells the owner once a pasted token dies, and again once a new one dies", async () => {
    const r = await rung();
    await control([r.owner]);
    const claude = await r.register(MUSE, "Claude", "routine_fire");
    const fire = `https://api.anthropic.com/v1/claude_code/routines/trig_status_401_${r.path}/fire`;
    await pasted(r, claude, "claude_routine_fire", fire);
    const told = async () => (await r.stub.noticeList(r.session(null))).notices
      .filter((n: { type: string }) => n.type === "doorbell_token_refused").length;
    await r.post(null, `dm:${claude}`, { cmid: "c1" });
    await rings(r, 1);
    await r.post(null, `dm:${claude}`, { cmid: "c2" });
    await rings(r, 2);
    expect(await told()).toBe(1);
    await pasted(r, claude, "claude_routine_fire", fire);  // pasted again: a new token
    await r.post(null, `dm:${claude}`, { cmid: "c3" });
    await rings(r, 3);
    expect(await told()).toBe(2);
  });

  it("marks a slow send unknown and never sends it again", async () => {
    const r = await rung();
    const t = clock();
    const out = await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      const ledger = ledgerOn(state.storage);
      const muse = ledger.register(MUSE, MCP, { command_center_id: "alpha", display_name: "Muse",
                                               delivery_method: "mcp_event", recipe_id: null })
        .agent_id;
      ledger.subscribe(MUSE, ledgerSubscription(), t);
      ledger.post(null, `dm:${muse}`, "c1", "hello", null, t - 100);
      const write = new WriteGate(state.storage).write;
      let sent = 0;
      const doorbell = new Doorbell({
        ledger, live: () => true, reads: () => true, touched: () => false,
        notice: async () => undefined, write, clock: () => t, gate: async () => true,
        ring: () => {
          sent++;
          return new Promise<RingAnswer>(() => undefined);
        } });
      const first = await runWakes(ledger, doorbell, () => t, write, 16, 30);
      const late = state.storage.sql.exec("SELECT state, marked FROM ledger_wakes").one();
      const again = await runWakes(ledger, doorbell, () => t + WAKE_LEASE_S, write, 16, 30);
      const after = state.storage.sql.exec("SELECT state, marked FROM ledger_wakes").one();
      return { first, late, again, after, sent };
    });
    expect(out).toEqual({ first: 0, late: { state: "sending", marked: 1 }, again: 0,
                          after: { state: "unknown", marked: 1 }, sent: 1 });
  });

  it("waits out a Retry-After of days as sent, keeps a subscription on 410, never redirects",
     async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    for (const [n, path] of ["status-302", "status-429?retry=259200", "status-410"].entries()) {
      await r.sql("DELETE FROM wake_callbacks");
      await subscribed(r, MUSE, {}, path);
      await r.post(null, `dm:${muse}`, { cmid: path });
      await r.sql("UPDATE ledger_wakes SET due = ? WHERE state != 'sent'", clock() - 1);
      await write(r, "notes", path);
      await rings(r, n + 1);
      if (n === 1) {  // the 429 waits exactly the three days it asked, never cut to one
        const [limited] = await r.sql("SELECT state, due - ? AS wait, receipt_json FROM " +
                                      "ledger_wakes WHERE state = 'rate_limited'", clock());
        expect(JSON.parse(limited!["receipt_json"] as string))
          .toMatchObject({ status: 429, retry_after: 259200 });
        expect(limited!["wait"] as number).toBeGreaterThan(259200 - 60);
      }
    }
    const receipts = (await r.table("ledger_wakes")).map((w) =>
      JSON.parse(w["receipt_json"] as string));
    expect(receipts).toMatchObject([{ status: 302 }, { status: 410 }]);
    expect(await r.sql("SELECT * FROM wake_callbacks")).toHaveLength(1);  // 410 keeps it
    expect((await received(r)).every((request) => request.redirect === "manual")).toBe(true);
  });

  it("tells the owner when a pasted token is refused as dead, and keeps it", async () => {
    const r = await rung();
    await control([r.owner]);
    const claude = await r.register(MUSE, "Claude", "routine_fire");
    await pasted(r, claude, "claude_routine_fire",
                 `https://api.anthropic.com/v1/claude_code/routines/trig_status_401_${r.path}/fire`);
    await r.post(null, `dm:${claude}`);
    await rings(r, 1);
    const notices = (await r.stub.noticeList(r.session(null))).notices;
    expect(notices.map((n: { type: string }) => n.type)).toContain("doorbell_token_refused");
    expect(await r.sql("SELECT * FROM wake_callbacks")).toHaveLength(1);
  });
});

it("answers closed from the pass itself when the switch is closed, fails or never answers",
   async () => {
  const r = await rung();
  const t = clock();
  const out = await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
    const ledger = ledgerOn(state.storage);
    const muse = ledger.register(MUSE, MCP, { command_center_id: "alpha", display_name: "Muse",
                                             delivery_method: "mcp_event", recipe_id: null })
      .agent_id;
    ledger.post(null, `dm:${muse}`, "c1", "hello", null, t - 100);
    const write = new WriteGate(state.storage).write;
    const doorbell = (gate: () => Promise<boolean>) => new Doorbell({
      ledger, live: () => true, reads: () => true, touched: () => false,
      notice: async () => undefined, write, clock: () => t, gate,
      ring: async () => ({ state: "answered", status: 202, retry_after: null }) });
    return [await runWakes(ledger, doorbell(async () => false), () => t, write),
            await runWakes(ledger, doorbell(async () => { throw new Error("KV down"); }),
                           () => t, write),
            await runWakes(ledger, doorbell(() => new Promise(() => {})), () => t, write, 16, 30)];
  });
  expect(out).toEqual([CLOSED, CLOSED, CLOSED]);
});
