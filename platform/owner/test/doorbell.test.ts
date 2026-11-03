/**
 * The doorbell (slice W, tasks 7): each wake kind's request from its record, the kill
 * switch in the control record (`CONTROL`), admission, a revoke never outlived, a slow send
 * never replayed, and a wake that leads its agent's digest to just the change.
 *
 * The object is production's but for its transport (`RungOwnerDO`): each request is
 * recorded as it leaves, never sent. Each test opens the kill switch for its own owner
 * only (the maintenance gate's canary list).
 */
import { createHmac } from "node:crypto";

import { evictDurableObject, runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { PLATFORM_CALLER } from "../src/admission";
import { outsideAdmits } from "../src/control-gate";
import { WriteGate } from "../src/gate";
import { clock, type Origin, WAKE_LEASE_S } from "../src/ledger";
import type { Binding, OwnerDO } from "../src/owner-do";
import { checkKinds, wakeKinds } from "../src/wake-kinds";
import { CLOSED, Doorbell, runWakes, type Transport } from "../src/wakes";
import raw from "../wake-kinds.json" with { type: "json" };
import {
  CALLBACK, DOT, ledgerOn, MCP, MUSE, rig, type Rig, subscription, WHSEC,
} from "./ledger-support";
import { failure } from "./support";
import type { Rang, RungOwnerDO } from "./worker";

const FIRE = "https://api.anthropic.com/v1/claude_code/routines/trig_01ABCDEF/fire";
const GROK = "https://bots.example.net/hooks/routine-7";
const TOKEN = "sk-test-0123456789abcdef";

type Rung = Rig & { rang: () => Promise<Rang[]> };

async function rung(): Promise<Rung> {
  const r = await rig(env.RUNG as unknown as DurableObjectNamespace<OwnerDO>);
  return { ...r, rang: () => runInDurableObject(r.stub, (i: OwnerDO) =>
    [...(i as RungOwnerDO).doorbell.rang]) };
}

/** The directory's control record: the kill switch on for `owners` only, or off. */
async function control(owners: readonly string[], outside: "on" | "off" = "on"): Promise<void> {
  await env.CONTROL.put("c:control", JSON.stringify({
    outside, ordinary: "canary", canary: owners, inbound: false,
  }));
}

/** Run the object's alarm until no wake is pending or being sent, or `n` requests left. */
async function rings(r: Rung, n: number): Promise<Rang[]> {
  for (let tries = 0; tries < 300; tries++) {
    await runDurableObjectAlarm(r.stub);
    const sent = await r.rang();
    const open = await r.sql("SELECT 1 FROM ledger_wakes WHERE kind = 'wake' " +
                             "AND state IN ('pending', 'sending') AND due <= ?", clock());
    if (sent.length >= n && open.length === 0) {
      return sent;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error("the wakes were not rung");
}

function write(r: Rig, collection: string, rowId: string, caller: Binding = r.bind(null),
               isPrivate = false) {
  return r.stub.collectionWrite(caller, { collection, row_id: rowId, body: `body of ${rowId}`,
                                          expected_etag: "", private: isPrivate });
}

/** A ringing agent with no batch window, its delivery `method`. */
function agent(r: Rig, from: Origin, method: string): Promise<string> {
  return r.register(from, from === MUSE ? "Muse" : "Dot", method);
}

describe("each wake kind sends its own record's shape", () => {
  it("ChatGPT MCP Events: a Standard Webhooks signed event, IDs and cursors only", async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await agent(r, MUSE, "mcp_event");
    await r.subscribe(MUSE, muse);
    await r.post(null, `dm:${muse}`, { body: "a secret plan" });
    const [sent] = await rings(r, 1);
    const [wake] = await r.sql("SELECT wake_id, state FROM ledger_wakes WHERE kind = 'wake'");
    expect(wake!["state"]).toBe("sent");
    expect([sent!.url, sent!.method, sent!.redirect]).toEqual([CALLBACK, "POST", "manual"]);
    const { headers, body } = sent!;
    expect(Object.keys(headers).sort()).toEqual(["content-type", "webhook-id",
      "webhook-signature", "webhook-timestamp", "x-mcp-subscription-id"]);
    expect(headers["x-mcp-subscription-id"]).toBe("sub-1");
    expect(headers["webhook-id"]).toBe(wake!["wake_id"]);
    const key = Buffer.from(WHSEC.slice("whsec_".length), "base64");
    const signed = createHmac("sha256", key)
      .update(`${headers["webhook-id"]}.${headers["webhook-timestamp"]}.${body}`).digest("base64");
    expect(headers["webhook-signature"]).toBe(`v1,${signed}`);
    expect(JSON.parse(body)).toEqual({
      eventId: wake!["wake_id"], name: "tinyassets.changed",
      timestamp: new Date(Number(headers["webhook-timestamp"]) * 1000).toISOString(),
      cursor: null,
      data: { agent_id: muse, command_center_id: "alpha", changes: ["mail"], cursor: 0,
              after: 0, read: ["inbox_digest"] },
    });
    expect(body).not.toContain("secret plan");
  });

  it("Claude /fire: the owner's token as a bearer, the beta header, one line of text",
     async () => {
    const r = await rung();
    await control([r.owner]);
    const claude = await agent(r, MUSE, "routine_fire");
    // The client cannot hand it over; the owner does, in session, on the vendor's own path.
    expect((await failure(() => r.stub.wakeSubscribe(r.bind(MUSE), subscription(claude))))
      .message).toBe("not this caller's subscribing agent");
    expect((await failure(() => r.stub.agentDoorbell(r.bind(null),
      { agent_id: claude, url: FIRE, secret: TOKEN }))).message)
      .toBe("interactive owner session required");
    for (const url of ["https://api.anthropic.com.example.org/v1/claude_code/routines/t/fire",
                       "https://api.anthropic.com/v1/messages", "http://api.anthropic.com" +
                       "/v1/claude_code/routines/t/fire", "https://1.2.3.4/x"]) {
      expect((await failure(() => r.stub.agentDoorbell(r.session(null),
        { agent_id: claude, url, secret: TOKEN }))).name).not.toBe("Error");
    }
    expect(await r.stub.agentDoorbell(r.session(null), { agent_id: claude, url: FIRE,
                                                         secret: TOKEN }))
      .toEqual({ agent_id: claude, generation: 1 });
    await write(r, "board", "t1");
    const [sent] = await rings(r, 1);
    expect(sent!.url).toBe(FIRE);
    expect(sent!.headers).toEqual({
      "authorization": `Bearer ${TOKEN}`, "content-type": "application/json",
      "anthropic-beta": "experimental-cc-routine-2026-04-01", "anthropic-version": "2023-06-01",
    });
    const { text } = JSON.parse(sent!.body) as { text: string };
    expect(text).toBe(`TinyAssets: new board for agent ${claude} in alpha. Read just the ` +
                      "change: board_digest with after=0.");
  });

  it("Grok's webhook routine: the routine's key as a bearer, the wake as JSON", async () => {
    const r = await rung();
    await control([r.owner]);
    const grok = await agent(r, MUSE, "webhook");
    await r.stub.agentDoorbell(r.session(null), { agent_id: grok, url: GROK, secret: TOKEN });
    await write(r, "room:app:lobby", "m1");
    const [sent] = await rings(r, 1);
    expect([sent!.url, sent!.headers["authorization"]]).toEqual([GROK, `Bearer ${TOKEN}`]);
    expect(JSON.parse(sent!.body)).toEqual({
      agent_id: grok, command_center_id: "alpha", changes: ["rooms"], cursor: 0, after: 0,
      read: ["inbox_digest"],
    });
  });

  it("Muse: no push, so no wake and no doorbell; it catches up on its own schedule",
     async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await agent(r, MUSE, "pull");
    await r.post(null, `dm:${muse}`);
    await write(r, "board", "t1");
    expect(await r.table("ledger_wakes")).toEqual([]);
    expect((await failure(() => r.stub.agentDoorbell(r.session(null),
      { agent_id: muse, url: GROK, secret: TOKEN }))).message)
      .toBe("not an agent whose doorbell the owner sets");
    const record = wakeKinds().find((kind) => kind.delivery_method === "pull")!;
    expect([record.push, record.catch_up]).toEqual([null, expect.stringContaining(
      "inbox_digest and board_digest")]);
  });
});

describe("the records are data, checked whole", () => {
  it("cites every record, and refuses one that breaks the shape", () => {
    expect(wakeKinds().map((kind) => [kind.id, kind.delivery_method, kind.push?.callback]))
      .toEqual([["chatgpt_mcp_events", "mcp_event", "subscribe"],
                ["claude_routine_fire", "routine_fire", "owner"],
                ["grok_webhook_routine", "webhook", "owner"],
                ["muse_recurring_task", "pull", undefined]]);
    expect(wakeKinds().every((kind) => kind.citations.length > 0)).toBe(true);
    const [first, ...rest] = raw.kinds;
    const broken = (change: Record<string, unknown>) =>
      () => checkKinds({ ...raw, kinds: [{ ...first, push: { ...first!.push, ...change } },
                                         ...rest] });
    expect(broken({ body: { text: "{body}" } })).toThrow("unknown placeholder");
    expect(broken({ secret: "whsec_.*" })).toThrow("anchored");
    expect(broken({ auth: "basic" })).toThrow("bearer or standard_webhooks");
    expect(broken({ headers: { Authorization: "x" } })).toThrow("never the auth's own");
    expect(() => checkKinds({ ...raw, kinds: [first, first] })).toThrow("share a id");
    expect(() => checkKinds({ ...raw, kinds: [{ ...first, citations: [] }] }))
      .toThrow("cites no source");
  });
});

describe("the kill switch and admission", () => {
  it("claims nothing while the switch is off, or the maintenance gate leaves the owner out",
     async () => {
    const r = await rung();
    const muse = await agent(r, MUSE, "mcp_event");
    await r.subscribe(MUSE, muse);
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
    expect(await outsideAdmits(env.CONTROL, r.owner)).toBe(false);
    await control([r.owner]);
    expect(await outsideAdmits(env.CONTROL, r.owner)).toBe(true);
    await r.sql("DELETE FROM cf_agents_jobs");  // the hold lifts at its time; here, now
    await evictDurableObject(r.stub);
    await r.stub.outsideAgents(r.bind(null));  // a wake re-derives the schedule
    expect(await rings(r, 1)).toHaveLength(1);
  });

  it("claims nothing for an owner not admitted, though the switch is on", async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await agent(r, MUSE, "mcp_event");
    await r.subscribe(MUSE, muse);
    await r.stub.admissionSet({ owner: r.owner, caller: PLATFORM_CALLER, origin: null },
                              { reason: "billing", suspended: true });
    await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      const ledger = ledgerOn(state.storage);
      ledger.post(null, `dm:${muse}`, "c1", "hello", null, clock() - 10);
    });
    await evictDurableObject(r.stub);
    await r.stub.outsideAgents(r.bind(null));  // a wake re-derives the schedule
    expect(await r.wakesAt()).toBeNull();  // a suspended owner's open wakes arm nothing
    await runInDurableObject(r.stub, (i: OwnerDO) => i.alarm());
    expect((await r.table("ledger_wakes")).map((w) => [w["state"], w["claim_id"]]))
      .toEqual([["pending", null]]);
    expect(await r.rang()).toEqual([]);
  });
});

describe("a revoke is never outlived", () => {
  it("lands mid-pass: the next agent's wake is suppressed and its callback already gone",
     async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await agent(r, MUSE, "mcp_event");
    const dot = await agent(r, DOT, "mcp_event");
    await r.subscribe(MUSE, muse);
    await r.subscribe(DOT, dot);
    const session = r.session(null);
    const woken = (sent: Rang) => (JSON.parse(sent.body) as { data: { agent_id: string } })
      .data.agent_id;
    // As the first wake leaves, the owner disables the other agent.
    await runInDurableObject(r.stub, (i: OwnerDO) => {
      const instance = i as RungOwnerDO;
      instance.doorbell.during = async () => {
        instance.doorbell.during = null;
        const first = woken(instance.doorbell.rang.at(-1)!);
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
      .filter((w) => w["kind"] === "wake").map((w) => [w["agent_id"], w["state"]]));
    expect(states).toEqual({ [sent[0]!]: "sent", [other]: "suppressed" });
    expect((await r.sql("SELECT * FROM wake_callbacks ORDER BY agent_id")).map((c) => c["agent_id"])).toEqual(sent);
  });

  it("lands between the mark and the byte: nothing is sent", async () => {
    const r = await rung();
    const t = clock();
    const out = await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      const ledger = ledgerOn(state.storage);
      const muse = ledger.register(MUSE, MCP, { command_center_id: "alpha", display_name: "Muse",
                                               delivery_method: "mcp_event", recipe_id: null })
        .agent_id;
      ledger.subscribe(MUSE, subscription(muse));
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
      const sent: Request[] = [];
      const transport: Transport = async (request) => {
        sent.push(request);
        return new Response(null, { status: 202 });
      };
      const doorbell = new Doorbell({ ledger, live: () => true, write, clock: () => t,
                                      gate: async () => true, transport });
      const accepted = await runWakes(ledger, doorbell, () => t, write);
      return { accepted, sent: sent.length,
               wake: state.storage.sql.exec("SELECT state, marked, receipt_json FROM " +
                                            "ledger_wakes WHERE kind = 'wake'").one() };
    });
    expect(out).toEqual({ accepted: 0, sent: 0, wake: {
      state: "failed", marked: 1, receipt_json: '{"reason":"not_sent","through":1}' } });
  });

  it("never erases a re-enabled agent's newer callback, which still rings", async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await agent(r, MUSE, "mcp_event");
    expect(await r.subscribe(MUSE, muse)).toBe(1);
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "paused" });
    expect(await r.sql("SELECT * FROM wake_callbacks ORDER BY agent_id")).toEqual([]);  // gone in the disable's own unit
    await agent(r, MUSE, "mcp_event");
    expect(await r.subscribe(MUSE, muse)).toBe(2);
    await r.post(null, `dm:${muse}`);
    await rings(r, 1);
    const revokes = (await r.table("ledger_wakes")).filter((w) => w["kind"] === "revoke");
    expect(revokes.map((w) => [w["state"], w["generation"]])).toEqual([["sent", 1]]);
    expect((await r.sql("SELECT * FROM wake_callbacks ORDER BY agent_id")).map((c) => c["generation"])).toEqual([2]);
    expect(await r.rang()).toHaveLength(1);
  });

  it("lets only the client that holds a callback unsubscribe it", async () => {
    const r = await rung();
    const muse = await agent(r, MUSE, "mcp_event");
    await r.subscribe(MUSE, muse);
    const end = { agent_id: muse, subscription_id: "sub-1" };
    expect(await r.stub.wakeUnsubscribe(r.bind(DOT), end)).toEqual({ unsubscribed: false });
    expect((await failure(() => r.stub.wakeUnsubscribe(r.bind(null), end))).message)
      .toBe("only the client that holds a subscription ends it");
    expect(await r.stub.wakeUnsubscribe(r.bind(MUSE), end)).toEqual({ unsubscribed: true });
    expect(await r.stub.wakeUnsubscribe(r.bind(MUSE), end)).toEqual({ unsubscribed: false });
    expect(await r.sql("SELECT * FROM wake_callbacks ORDER BY agent_id")).toEqual([]);
  });
});

describe("a send's answer", () => {
  it("marks a slow send unknown and never sends it again", async () => {
    const r = await rung();
    const t = clock();
    const out = await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      const ledger = ledgerOn(state.storage);
      const muse = ledger.register(MUSE, MCP, { command_center_id: "alpha", display_name: "Muse",
                                               delivery_method: "mcp_event", recipe_id: null })
        .agent_id;
      ledger.subscribe(MUSE, subscription(muse));
      ledger.post(null, `dm:${muse}`, "c1", "hello", null, t - 100);
      const write = new WriteGate(state.storage).write;
      let sent = 0;
      const doorbell = new Doorbell({ ledger, live: () => true, write, clock: () => t,
                                      gate: async () => true,
                                      transport: () => { sent++; return new Promise(() => {}); } });
      const first = await runWakes(ledger, doorbell, () => t, write, 16, 30);
      const late = state.storage.sql.exec("SELECT state, marked FROM ledger_wakes").one();
      const again = await runWakes(ledger, doorbell, () => t + WAKE_LEASE_S, write, 16, 30);
      const after = state.storage.sql.exec("SELECT state, marked FROM ledger_wakes").one();
      return { first, late, again, after, sent };
    });
    expect(out).toEqual({ first: 0, late: { state: "sending", marked: 1 }, again: 0,
                          after: { state: "unknown", marked: 1 }, sent: 1 });
  });

  it("waits out a 429, erases a gone callback, and never follows a redirect", async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await agent(r, MUSE, "mcp_event");
    await r.subscribe(MUSE, muse);
    await runInDurableObject(r.stub, (i: OwnerDO) => {
      const { doorbell } = i as RungOwnerDO;
      doorbell.answers.push(302, 429, 410);
      doorbell.retryAfter.push(null, "120");
    });
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    await rings(r, 1);
    await r.post(null, `dm:${muse}`, { cmid: "c2" });
    await rings(r, 2);
    const [limited] = await r.sql("SELECT receipt_json FROM ledger_wakes " +
                                  "WHERE state = 'rate_limited'");
    expect(JSON.parse(limited!["receipt_json"] as string))
      .toEqual({ status: 429, retry_after: 120, through: 2 });
    await r.sql("UPDATE ledger_wakes SET due = ? WHERE state = 'rate_limited'", clock() - 1);
    await write(r, "notes", "n1");  // a door re-derives the lane's job from the rows
    await rings(r, 3);
    expect((await r.table("ledger_wakes")).map((w) => [w["state"],
      JSON.parse(w["receipt_json"] as string)]))
      .toEqual([["failed", { status: 302, through: 1 }],
                ["failed", { status: 410, reason: "gone", through: 2 }]]);
    expect(await r.sql("SELECT * FROM wake_callbacks ORDER BY agent_id")).toEqual([]);
  });
});

describe("a wake leads its agent's digest to just the change", () => {
  it("names the change and the cursor its digest is after", async () => {
    const r = await rung();
    await control([r.owner]);
    const muse = await agent(r, MUSE, "mcp_event");
    const dot = await agent(r, DOT, "mcp_event");
    await r.subscribe(MUSE, muse);
    await r.subscribe(DOT, dot);
    await write(r, "board", "t0");
    const data = (sent: Rang) => (JSON.parse(sent.body) as {
      data: { agent_id: string; changes: string[]; after: number; read: string[] } }).data;
    const first = (await rings(r, 2)).map(data);
    expect(first.map((d) => [d.agent_id, d.changes, d.read, d.after]).sort()).toEqual(
      [[muse, ["board"], ["board_digest"], 0], [dot, ["board"], ["board_digest"], 0]].sort());
    // Muse writes the next row itself: only Dot is woken, and its digest is just that row.
    await write(r, "board", "t1", r.bind(MUSE));
    const [, , next] = await rings(r, 3);
    const woken = data(next!);
    expect([woken.agent_id, woken.after]).toEqual([dot, 1]);
    const digest = await r.stub.boardDigest(r.bind(DOT), { limit: 50, after: woken.after });
    expect(digest.rows.map((row) => row.row_id)).toEqual(["t1"]);
    await write(r, "board", "mine", r.bind(null), true);  // a private row rings no one
    expect(await r.sql("SELECT 1 FROM ledger_wakes WHERE state = 'pending'")).toEqual([]);
    expect(await r.rang()).toHaveLength(3);
    // Mail and a room in one wake: the inbox digest after its cursor holds just those.
    await r.stub.policyWakeLimits(r.session(null), {
      agent_id: muse, max_wakes_per_hour: null, batch_window_s: 3600,
      expected_generation: (await r.stub.policyRead(r.bind(null))).generation });
    await r.post(null, `dm:${muse}`, { body: "new mail" });
    await write(r, "room:app:lobby", "m1");
    await r.sql("UPDATE ledger_wakes SET due = ? WHERE agent_id = ? AND state = 'pending'",
                clock() - 1, muse);
    await write(r, "notes", "n1");  // a door re-derives the lane's job from the rows
    await rings(r, 5);
    const mine = (await r.rang()).map(data).filter((d) => d.agent_id === muse).at(-1)!;
    expect([mine.changes, mine.read]).toEqual([["mail", "rooms"], ["inbox_digest"]]);
    const inbox = await r.stub.inboxDigest(r.bind(MUSE), { limit: 50, after: mine.after });
    expect(inbox.mail.messages.map((m) => m.body)).toEqual(["new mail"]);
    expect(inbox.rooms.rows.map((row) => row.row_id)).toEqual(["m1"]);
  });
});

it("answers closed from the pass itself when the switch is closed", async () => {
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
      ledger, live: () => true, write, clock: () => t, gate,
      transport: async () => new Response(null, { status: 202 }) });
    return [await runWakes(ledger, doorbell(async () => false), () => t, write),
            await runWakes(ledger, doorbell(async () => { throw new Error("KV down"); }),
                           () => t, write),
            await runWakes(ledger, doorbell(() => new Promise(() => {})), () => t, write, 16, 30)];
  });
  expect(out).toEqual([CLOSED, CLOSED, CLOSED]);
});
