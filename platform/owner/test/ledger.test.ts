/**
 * S30 message ledger core: attribution, idempotency, rooms, reads and receipts.
 *
 * Ported from `tests/test_message_ledger.py` and the ledger parts of
 * `tests/test_mailbox_door.py` (origin/main 8b415f569), test for test. Where the Python
 * went through the router's owner channel with an `origin`, this goes through the
 * object's RPC door with a {@link AccountBinding}. The Python's account erasure, policy
 * bootstrap and racing-open tests have no counterpart: the object is the account.
 */
import { describe, expect, it } from "vitest";

import { apiKeyOrigin, displayName, OWNER } from "../src/ledger";
import { policyDigest } from "../src/policy";
import { DOT, MCP, MUSE, rig } from "./ledger-support";
import { failure } from "./support";

describe("the sender is the verified identity", () => {
  it("attributes, chains, receipts the reply and moves recipient states", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    const first = await r.post(null, `dm:${muse}`, { body: '{"sender": "owner"}' });
    expect(first).toEqual({ message_id: first.message_id, recipients: [muse], held: false,
                            paused_reason: "" });
    const forged = await failure(() => r.stub.roomPost(r.bind(MUSE), {
      room_id: `dm:${muse}`, client_message_id: "x", body: "x", parent_id: null,
      sender: "owner",
    } as never));
    expect([forged.name, forged.message]).toEqual(["Invalid", "invalid ledger request"]);
    const [mail] = (await r.read(MUSE)).messages;
    expect([mail!.sender_kind, mail!.sender_id, mail!.chain_depth]).toEqual(["owner", "owner", 0]);
    expect([mail!.body, mail!.integrity]).toEqual(['{"sender": "owner"}', "ok"]);
    const reply = await r.post(MUSE, `dm:${muse}`, { body: "hi", cmid: "r1",
                                                     parent: first.message_id });
    expect(reply.recipients).toEqual([OWNER]);
    const [back] = (await r.read()).messages;
    expect([back!.sender_kind, back!.sender_id, back!.chain_depth]).toEqual(["outside", muse, 1]);
    // The owner reads the agent's reply with its recorded time to reply.
    expect(back!.reply_s).toBeGreaterThanOrEqual(0);
    const again = await r.post(null, `dm:${muse}`, { cmid: "c2", parent: reply.message_id });
    const states = Object.fromEntries((await r.table("ledger_recipients"))
      .map((row) => [row["message_id"], row["state"]]));
    expect(states).toEqual({ [first.message_id]: "replied", [reply.message_id]: "replied",
                             [again.message_id]: "queued" });
    const messages = await r.table("ledger_messages");
    expect(messages.find((m) => m["message_id"] === again.message_id)!["chain_depth"]).toBe(0);
    expect(new Set(messages.map((m) => m["chain_root"]))).toEqual(new Set([first.message_id]));
    const receipts = (await r.table("ledger_receipts")).map((row) => ({
      agent: row["agent_id"], kind: row["kind"], ref: row["ref"],
      detail: JSON.parse(row["detail_json"] as string) as Record<string, number | string>,
    }));
    expect(receipts.map((x) => [x.agent, x.kind])).toEqual([
      [muse, "read"], [muse, "reply"], [OWNER, "read"], [OWNER, "reply"]]);
    const { detail } = receipts[1]!;
    expect([detail["parent_id"], receipts[1]!.ref]).toEqual([first.message_id, reply.message_id]);
    expect(detail["read_s"]).toBeGreaterThanOrEqual(0);
    expect(detail["read_s"]).toBeLessThanOrEqual(detail["reply_s"] as number);
    expect(Object.keys(detail)).toEqual(["parent_id", "read_s", "reply_s"]);
  });

  it("returns the original on a resend and refuses another hash as a conflict", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    const sent = await r.post(null, `dm:${muse}`, { body: "one" });
    expect(await r.post(null, `dm:${muse}`, { body: "one" })).toEqual(sent);
    const dot = await r.register(DOT, "Dot");
    for (const [room, body, parent] of [[`dm:${muse}`, "two", null], [`dm:${dot}`, "one", null],
                                        [`dm:${muse}`, "one", sent.message_id]] as const) {
      const refused = await failure(() => r.post(null, room, { body, parent }));
      expect([refused.name, refused.message]).toEqual(["Refused", "conflict"]);
    }
    expect(await r.table("ledger_messages")).toHaveLength(1);
    expect((await r.read(MUSE)).messages.map((m) => m.body)).toEqual(["one"]);
    // The same client id from another sender is that sender's own message.
    expect((await r.post(MUSE, `dm:${muse}`, { body: "two" })).message_id)
      .not.toBe(sent.message_id);
  });

  it("keeps dm room and parent rules", async () => {
    const r = await rig();
    const [muse, dot] = [await r.register(MUSE), await r.register(DOT, "Dot")];
    const own = await r.post(MUSE, `dm:${muse}`, { cmid: "m1" });
    const toDot = await r.post(null, `dm:${dot}`, { cmid: "d1" });
    for (const [origin, room, parent, reason] of [
      [MUSE, `dm:${dot}`, null, "an outside agent posts only in its own dm room"],
      [null, "general", null, "room is not an enabled agent's dm"],
      [null, "dm:agent_0000000000000000", null, "room is not an enabled agent's dm"],
      [null, `dm:${muse}`, toDot.message_id, "parent is not a message to this sender in this room"],
      [MUSE, `dm:${muse}`, own.message_id, "parent is not a message to this sender in this room"],
      [MUSE, `dm:${muse}`, "msg_missing", "parent is not a message to this sender in this room"],
      // Each half of Muse's identity alone is not Muse.
      [{ client: MUSE.client, family: DOT.family }, `dm:${muse}`, null,
        "an outside agent posts only in its own dm room"],
      [{ client: DOT.client, family: MUSE.family }, `dm:${muse}`, null,
        "an outside agent posts only in its own dm room"],
    ] as const) {
      const refused = await failure(() => r.post(origin, room, { cmid: "z", parent }));
      expect([refused.name, refused.message]).toEqual(["Refused", reason]);
    }
  });

  it("refuses a disabled agent's room, post and read, and re-enables it as itself", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    await r.post(null, `dm:${muse}`);
    const disabled = await r.stub.disableAgent(r.session(null),
                                               { agent_id: muse, reason: "owner removed it" });
    expect([disabled.disabled_reason, disabled.revision]).toEqual(["owner removed it", 2]);
    for (const attempt of [() => r.post(null, `dm:${muse}`, { cmid: "c2" }),
                           () => r.post(MUSE, `dm:${muse}`, { cmid: "m1" }),
                           () => r.read(MUSE)]) {
      expect((await failure(attempt)).name).toBe("Refused");
    }
    expect(await r.register(MUSE)).toBe(muse);
    expect((await r.read(MUSE)).messages).toHaveLength(1);
  });

  it("validates fields and keeps a body verbatim", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    for (const [fields, reason] of [
      [{ body: "" }, "a message body is 1 to 16384 characters"],
      [{ body: "x".repeat(16385) }, "a message body is 1 to 16384 characters"],
      [{ body: "a\uD800b" }, "a message body is well-formed text"],
      [{ cmid: "" }, "client_message_id is 1 to 128 printable characters"],
      [{ cmid: "a\nb" }, "client_message_id is 1 to 128 printable characters"],
    ] as const) {
      const refused = await failure(() => r.post(null, `dm:${muse}`, fields));
      expect([refused.name, refused.message]).toEqual(["Invalid", reason]);
    }
    const body = '  { "b": 1,  "a": "café 🎲" }\n';
    await r.post(null, `dm:${muse}`, { body });
    expect((await r.read(MUSE)).messages[0]!.body).toBe(body);
  });
});

describe("a reader sees only its own mail", () => {
  it("withholds and flags a tampered or missing body", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    await r.post(null, `dm:${muse}`, { body: "pay the invoice" });
    await r.post(null, `dm:${muse}`, { body: "second", cmid: "c2" });
    await r.sql("UPDATE message_bodies SET body = 'pay the attacker' WHERE body = 'pay the invoice'");
    await r.sql("DELETE FROM message_bodies WHERE body = 'second'");
    expect((await r.read(MUSE)).messages.map((m) => [m.body, m.integrity]))
      .toEqual([[null, "tampered"], [null, "missing"]]);
  });

  it("isolates readers, keeps a cursor per reader and finds nothing new after an ack",
     async () => {
    const r = await rig();
    const [muse, dot] = [await r.register(MUSE), await r.register(DOT, "Dot")];
    await r.post(null, `dm:${muse}`, { body: "for muse" });
    await r.post(null, `dm:${dot}`, { body: "for dot", cmid: "c2" });
    const first = await r.read(MUSE);
    expect(first.messages.map((m) => m.body)).toEqual(["for muse"]);
    expect((await r.read(DOT)).messages.map((m) => m.body)).toEqual(["for dot"]);
    expect((await r.read()).messages).toEqual([]);  // the owner was sent nothing
    // Before an ack a reread returns the same message_ids, the dedupe key.
    expect((await r.read(MUSE)).messages.map((m) => m.message_id))
      .toEqual(first.messages.map((m) => m.message_id));
    const through = first.messages.at(-1)!.seq;
    expect((await r.ack(MUSE, through)).through).toBe(through);
    expect((await r.read(MUSE)).messages).toEqual([]);  // W3-R5: a retried session
    expect((await r.ack(MUSE, 0)).through).toBe(through);  // never moves back
    expect((await r.ack(MUSE, through)).through).toBe(through);  // a retry: a no-op
    const past = await failure(() => r.ack(MUSE, through + 10));
    expect(past.message).toBe("ack is past this reader's mail");
    expect((await r.read(DOT)).messages).toHaveLength(1);  // Muse's cursor is Muse's alone
    // Only an ack that moves the cursor leaves a receipt.
    expect((await r.table("ledger_receipts")).filter((x) => x["kind"] === "ack")).toHaveLength(1);
  });

  it("holds a message back whole, without a read receipt, until a read returns it",
     async () => {
    const r = await rig();
    await r.register(MUSE);
    const room = `dm:${(await r.stub.outsideAgents(r.bind(null))).agents[0]!.agent_id}`;
    const sent: string[] = [];
    for (const n of [0, 1, 2]) {
      sent.push((await r.post(null, room, { body: String(n).repeat(16000), cmid: `c${n}` }))
        .message_id);
    }
    const page = (budget: number) => r.read(MUSE, 10, { max_bytes: budget, pad_bytes: 200 });
    const unread = async () => [
      new Set((await r.table("ledger_receipts")).filter((x) => x["kind"] === "read")
        .map((x) => x["ref"])),
      Object.fromEntries((await r.table("ledger_recipients"))
        .map((x) => [x["message_id"], x["state"]])),
    ] as const;
    const first = await page(20000);  // one 16 kB body fits; a second would cross it
    expect([first.messages.map((m) => m.message_id), first.more]).toEqual([sent.slice(0, 1), true]);
    expect(first.messages[0]!.body).toHaveLength(16000);  // whole, never cut
    let [read, states] = await unread();
    expect(read).toEqual(new Set(sent.slice(0, 1)));
    expect(sent.map((m) => states[m])).toEqual(["read", "queued", "queued"]);
    const tiny = await page(1);  // over budget alone: still one whole message, the same one
    expect(tiny.messages.map((m) => m.message_id)).toEqual(sent.slice(0, 1));
    await r.ack(MUSE, first.messages[0]!.seq);
    const rest = await page(40000);
    expect([rest.messages.map((m) => m.message_id), rest.more]).toEqual([sent.slice(1), false]);
    [read, states] = await unread();
    expect(read).toEqual(new Set(sent));
    expect(new Set(Object.values(states))).toEqual(new Set(["read"]));
    for (const [budget, reason] of [[{ max_bytes: 0 }, "max_bytes is a positive integer"],
                                    [{ pad_bytes: -1 }, "pad_bytes is a non-negative integer"]]) {
      expect((await failure(() => r.read(MUSE, 10, budget as never))).message).toBe(reason);
    }
    for (const limit of [0, 101, 1.5]) {
      expect((await failure(() => r.read(MUSE, limit))).message).toBe("limit is 1 to 100");
    }
    const forged = await failure(() => r.stub.mailboxRead(r.bind(MUSE),
                                                          { limit: 10, sender: "owner" } as never));
    expect(forged.message).toBe("invalid ledger request");
  });

  it("bounds an ack by the reader's own mail and reveals no count", async () => {
    const r = await rig();
    const [muse, dot] = [await r.register(MUSE), await r.register(DOT, "Dot")];
    await r.post(null, `dm:${muse}`, { cmid: "m1" });
    for (const n of [0, 1, 2]) {  // later mail to another reader raises the ledger's seq
      await r.post(null, `dm:${dot}`, { cmid: `d${n}` });
    }
    const [seq] = (await r.read(MUSE)).messages.map((m) => m.seq);
    expect(seq).toBe(1);
    for (const through of [2, 4, 10_000]) {
      const refused = await failure(() => r.ack(MUSE, through));
      expect([refused.name, refused.message]).toEqual(["Refused", "ack is past this reader's mail"]);
    }
    expect((await r.ack(MUSE, 1)).through).toBe(1);
    expect((await r.read(DOT)).messages[0]!.seq).toBe(2);  // Dot's mail intact
    expect((await failure(() => r.ack(MUSE, -1))).message).toBe("through is a ledger seq");
  });

  it("pages whole bodies under a byte budget and loses none", async () => {
    // test_a_read_pages_whole_bodies_under_the_ceiling_and_loses_none: the door's
    // read_graph ceilings, less its headroom, as the ledger's max_bytes.
    for (const [budget, pages] of [[24576 - 1024, 5], [40000 - 1024, 3], [4096 - 1024, 5]]) {
      const r = await rig();
      const room = `dm:${await r.register(MUSE)}`;
      const sent: string[] = [];
      for (const n of [0, 1, 2, 3, 4]) {
        sent.push((await r.post(null, room, { body: String(n).repeat(16000), cmid: `c${n}` }))
          .message_id);
      }
      const seen: string[] = [];
      let [cursor, count] = [0, 0];
      for (;;) {
        if (cursor) {
          await r.ack(MUSE, cursor);
        }
        const page = await r.read(MUSE, 10, { max_bytes: budget! });
        if (!page.messages.length) {
          break;
        }
        count++;
        const size = new TextEncoder().encode(JSON.stringify(page.messages)).byteLength;
        expect(page.messages.length === 1 || size <= budget!).toBe(true);
        expect(page.messages.every((m) => m.body!.length === 16000)).toBe(true);
        expect(page.more).toBe(seen.length + page.messages.length < sent.length);
        seen.push(...page.messages.map((m) => m.message_id));
        cursor = page.messages.at(-1)!.seq;
      }
      expect([seen, count]).toEqual([sent, pages]);
    }
  });
});

describe("outside agents register from a verified identity", () => {
  it("is one enabled agent per outside identity", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    const twice = await failure(() => r.register(MUSE, "Muse", "pull",
                                                 { command_center_id: "beta" }));
    expect([twice.name, twice.message])
      .toEqual(["Refused", "this outside identity is already an enabled agent"]);
    expect((await r.stub.outsideAgents(r.bind(null))).agents.map((a) => a.agent_id))
      .toEqual([muse]);
    await r.post(null, `dm:${muse}`);
    expect((await r.read(MUSE)).messages).toHaveLength(1);  // not locked out
    expect(await r.register(MUSE)).toBe(muse);  // its own command center again
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "moved" });
    const moved = await r.registered(MUSE, "Muse", "pull", { command_center_id: "beta" });
    expect(moved.agent_id).not.toBe(muse);
    expect(moved.command_center_id).toBe("beta");
  });

  it("needs the owner's live session and a verified source", async () => {
    const r = await rig();
    const request = { command_center_id: "alpha", display_name: "Muse",
                      delivery_method: "pull", recipe_id: null };
    for (const [binding, name, reason] of [
      [r.bind(MUSE, { source: MCP }), "Refused", "interactive owner session required"],
      [r.bind(MUSE, { source: MCP, session: "x".repeat(43) }), "Refused",
        "interactive owner session required"],
      [r.bind(null, { source: MCP, session: r.cookie }), "Refused",
        "an agent registers only from its verified outside identity"],
      [{ ...r.bind(MUSE, { session: r.cookie }), source: undefined } as never, "Invalid",
        "source is api_key or mcp"],
      [r.bind(MUSE, { session: r.cookie, source: { kind: "oauth", ref: "x" } as never }),
        "Invalid", "source is api_key or mcp"],
    ] as const) {
      const refused = await failure(() => r.stub.registerAgent(binding, request));
      expect([refused.name, refused.message]).toEqual([name, reason]);
    }
    const disable = await failure(() => r.stub.disableAgent(r.bind(null),
                                                            { agent_id: "x", reason: "y" }));
    expect(disable.message).toBe("interactive owner session required");
    const missing = await failure(() => r.stub.disableAgent(r.session(null),
                                                            { agent_id: "agent_x", reason: "y" }));
    expect([missing.name, missing.message]).toEqual(["Refused", "agent not found"]);
    expect(await r.table("outside_agents")).toEqual([]);
  });

  it("takes an API-key agent's identity from its key, and only for pull", async () => {
    const r = await rig();
    const key = "0123456789abcdef";
    const origin = apiKeyOrigin(key);
    const source = { kind: "api_key", ref: key } as const;
    const request = { command_center_id: "alpha", display_name: "Night Agent",
                      delivery_method: "pull", recipe_id: null };
    await r.keyed(key);
    for (const [binding, fields, reason] of [
      [r.session(MUSE, source), {}, "the origin is not this API key's"],
      [r.session(apiKeyOrigin("fedcba9876543210"), source), {}, "the origin is not this API key's"],
      [r.session(origin, source), { delivery_method: "mcp_event" },
        "an API-key agent reads by pull; mcp_event is for MCP clients"],
    ] as const) {
      const refused = await failure(() => r.stub.registerAgent(binding, { ...request, ...fields }));
      expect([refused.name, refused.message]).toEqual(["Refused", reason]);
    }
    const badKey = await failure(() => r.stub.registerAgent(
      r.session(origin, { kind: "api_key", ref: "not-a-key" }), request));
    expect([badKey.name, badKey.message]).toEqual(["Invalid", "an api_key source_ref is the key id"]);
    const agent = await r.stub.registerAgent(r.session(origin, source), request);
    expect([agent.source, agent.source_ref, agent.delivery_method, agent.outside_client])
      .toEqual(["api_key", key, "pull", origin.client]);
  });

  it("admits pull and mcp_event delivery only, with W3's defaults and bounds", async () => {
    const r = await rig();
    for (const method of ["routine_fire", "webhook", "a2a"]) {
      const refused = await failure(() => r.register(MUSE, "Muse", method));
      expect([refused.name, refused.message])
        .toEqual(["Refused", "only pull and mcp_event delivery are available yet"]);
    }
    expect((await failure(() => r.register(MUSE, "Muse", "carrier pigeon"))).message)
      .toBe("unknown delivery method");
    const agent = await r.registered(MUSE, "Muse", "mcp_event");
    expect([agent.max_wakes_per_hour, agent.batch_window_s, agent.revision]).toEqual([12, 60, 1]);
    const limits = async (extra: object) => (await r.stub.policyWakeLimits(r.session(null), {
      agent_id: agent.agent_id, max_wakes_per_hour: 12, batch_window_s: 60,
      expected_generation: (await r.stub.policyRead(r.bind(null))).generation, ...extra })).agent;
    for (const [extra, reason] of [
      [{ max_wakes_per_hour: 0 }, "max_wakes_per_hour is 1 to 60"],
      [{ max_wakes_per_hour: 61 }, "max_wakes_per_hour is 1 to 60"],
      [{ batch_window_s: -1 }, "batch_window_s is 0 to 3600"],
      [{ batch_window_s: 1.5 }, "batch_window_s is 0 to 3600"],
      [{ agent_id: "agent_x" }, "agent not found"],
    ] as const) {
      expect((await failure(() => limits(extra))).message).toBe(reason);
    }
    const tuned = await limits({ max_wakes_per_hour: 2, batch_window_s: 0 });
    expect([tuned.max_wakes_per_hour, tuned.batch_window_s, tuned.revision]).toEqual([2, 0, 2]);
    // A re-registration keeps the owner's policy; it cannot carry its own.
    const again = await r.registered(MUSE, "Muse", "mcp_event");
    expect([again.max_wakes_per_hour, again.batch_window_s, again.revision]).toEqual([2, 0, 3]);
    const carried = await failure(() => r.stub.registerAgent(r.session(MUSE), {
      command_center_id: "alpha", display_name: "Muse", delivery_method: "mcp_event",
      recipe_id: null, max_wakes_per_hour: 60 } as never));
    expect(carried.message).toBe("invalid ledger request");
  });

  it("lets an outside caller disable its own agent only; the owner disables any",
     async () => {
    const r = await rig();
    const [muse, dot] = [await r.register(MUSE), await r.register(DOT, "Dot")];
    const refused = await failure(() => r.stub.disableAgent(r.session(MUSE),
                                                            { agent_id: dot, reason: "x" }));
    expect([refused.name, refused.message])
      .toEqual(["Refused", "an outside caller disables only its own agent"]);
    expect((await r.stub.disableAgent(r.session(MUSE), { agent_id: muse, reason: "done" }))
      .disabled_reason).toBe("done");
    expect((await r.stub.disableAgent(r.session(null), { agent_id: dot, reason: "owner" }))
      .disabled_reason).toBe("owner");
  });

  it("lets only the owner, in its session, set wake limits or delete an agent", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    const policy = { agent_id: muse, max_wakes_per_hour: 60, batch_window_s: 0,
                     expected_generation: 1 };
    for (const [binding, reason] of [
      [r.session(MUSE), "only the owner may change its agents' policy"],
      [r.bind(null), "interactive owner session required"],
      [r.bind(MUSE), "interactive owner session required"],
    ] as const) {
      for (const call of [() => r.stub.policyWakeLimits(binding, policy),
                          () => r.stub.deleteAgent(binding, { agent_id: muse })]) {
        expect((await failure(call)).message).toBe(reason);
      }
    }
    const [agent] = (await r.stub.outsideAgents(r.bind(null))).agents;
    expect([agent!.max_wakes_per_hour, agent!.revision]).toEqual([12, 1]);
  });

  it("keeps a display name to letters, digits, spaces, dots and dashes, and no secret",
     () => {
    expect(displayName("  Night <Agent>! v1.2-b ")).toBe("Night Agent v1.2-b");
    expect(displayName("Café_Muse")).toBe("Café_Muse");
    for (const secret of ["Bearer abc", "sk-live", "ta_key_x", "https://x", "token=1", "eyJhbG"]) {
      expect(displayName(secret)).toBe("");
    }
    expect(displayName("x".repeat(81))).toBe("");
    expect(displayName(7)).toBe("");
  });
});

describe("the ledger's binding", () => {
  it("answers only the object's owner's own accounts, with a well-formed origin", async () => {
    const r = await rig();
    const visitor = await failure(() => r.stub.outsideAgents({ owner: r.owner, caller: "bob",
                                                               origin: null }));
    expect([visitor.name, visitor.message])
      .toEqual(["Refused", "the account answers its owner's own callers only"]);
    for (const origin of [{ client: "a", family: "b" }, { client: "a".repeat(64) },
                          { ...MUSE, extra: "x" }, "owner"]) {
      const refused = await failure(() => r.stub.outsideAgents(r.bind(origin as never)));
      expect([refused.name, refused.message]).toEqual(["Invalid", "invalid outside origin"]);
    }
    const bare = await failure(() => r.stub.outsideAgents({ owner: r.owner, caller: r.owner } as never));
    expect(bare.message).toBe("invalid outside origin");
    expect((await r.stub.outsideAgents(r.bind(MUSE))).agents).toEqual([]);
  });

  it("lists every agent to the owner, and an outside caller only its own", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "moved" });
    const moved = await r.register(MUSE, "Muse", "pull", { command_center_id: "beta" });
    const dot = await r.register(DOT, "Dot");
    const ids = async (from: typeof MUSE | null) =>
      new Set((await r.stub.outsideAgents(r.bind(from))).agents.map((a) => a.agent_id));
    expect(await ids(null)).toEqual(new Set([muse, moved, dot]));
    expect(await ids(MUSE)).toEqual(new Set([muse, moved]));
    expect(await ids(DOT)).toEqual(new Set([dot]));
    expect(await ids({ client: MUSE.client, family: DOT.family })).toEqual(new Set());
  });

  it("migrates the ledger tables with zeroed counters", async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(null));
    const tables = (await r.sql("SELECT name FROM sqlite_master WHERE type = 'table'"))
      .map((t) => t["name"]);
    for (const table of ["outside_agents", "ledger_messages", "ledger_recipients",
                         "ledger_receipts", "ledger_readers", "ledger_wakes", "ledger_rings",
                         "message_bodies", "ledger_usage"]) {
      expect(tables).toContain(table);
    }
    expect(await r.table("ledger_usage")).toEqual([
      { only: 1, agents: 0, enabled_agents: 0, messages: 0, body_bytes: 0, settled_wakes: 0,
        tombstones: 0 }]);
  });
});

describe("a refused widening's receipt", () => {
  it("is left for each refused register, disable or delete, and never holds the cookie",
     async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    const request = { command_center_id: "alpha", display_name: "Dot",
                      delivery_method: "pull", recipe_id: null };
    for (const call of [
      () => r.stub.registerAgent(r.bind(DOT), request),  // no session
      () => r.stub.registerAgent(r.session(DOT), { ...request, delivery_method: "a2a" }),
      () => r.stub.disableAgent(r.session(DOT), { agent_id: muse, reason: "x" }),
      () => r.stub.deleteAgent(r.session(MUSE), { agent_id: muse }),
      () => r.stub.deleteAgent(r.session(null), { agent_id: "agent_x" }),
      () => r.stub.deleteAgent(r.session(null), { agent_id: muse, extra: 1 } as never),
    ]) {
      await failure(call);
    }
    // A call that never reached the account (another caller, a malformed origin) is no
    // owner's widening, and leaves nothing.
    await failure(() => r.stub.registerAgent({ owner: r.owner, caller: "bob", origin: DOT,
                                               source: MCP, session: r.cookie }, request));
    await failure(() => r.stub.disableAgent(r.bind({ client: "a" } as never,
                                                   { session: r.cookie }),
                                            { agent_id: muse, reason: "x" }));
    const refusals = (await r.table("policy_receipts"))
      .filter((row) => row["kind"] === "refusal");
    expect(refusals).toHaveLength(6);
    expect(refusals.map((row) => [row["rule"], row["decision"], row["outcome"],
                                  row["session_id"]]))
      .toEqual(Array(6).fill(["interactive_owner_session", "deny", "refused", null]));
    expect(refusals[0]!["request_digest"]).toBe(policyDigest({
      action: "OUTSIDE_AGENTS.register", request: { request, origin: DOT } }));
    expect(refusals[3]!["request_digest"]).toBe(policyDigest({
      action: "OUTSIDE_AGENTS.delete", request: { request: { agent_id: muse },
                                                  origin: MUSE } }));
    expect(JSON.stringify(refusals)).not.toContain(r.cookie);
    expect((await r.stub.outsideAgents(r.bind(null))).agents.map((a) => a.agent_id))
      .toEqual([muse]);
  });

  it("is kept even when the refused change wrote first and rolled back", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    const stale = await failure(() => r.stub.policyWakeLimits(r.session(null), {
      agent_id: muse, max_wakes_per_hour: 1, batch_window_s: 0, expected_generation: 7 }));
    expect(stale.message).toBe("policy generation changed");
    const [agent] = (await r.stub.outsideAgents(r.bind(null))).agents;
    expect([agent!.max_wakes_per_hour, agent!.revision]).toEqual([12, 1]);
    expect((await r.table("policy_receipts")).map((row) => row["kind"])).toEqual(["refusal"]);
  });
});

describe("the policy store's counters", () => {
  it("equal a recount of their rows after any sequence", async () => {
    const r = await rig();
    await r.stub.sessionOpen(r.bind(null, { authenticated_at: Math.floor(Date.now() / 1000) }));
    await r.stub.sessionClose(r.bind(null, { session: r.cookie }));
    for (const from of [MUSE, DOT]) {
      await r.stub.outsideAgents(r.bind(from));
    }
    await r.stub.grantRevoke(r.bind(null), { client: DOT.client });
    const change = { type: "policy_proposal", expected_generation: 1, policy: {} };
    await r.stub.policyPropose(r.bind(MUSE), { change, request_id: "a" });
    await r.stub.policyPropose(r.bind(null), { change: { ...change, n: 2 }, request_id: "b" });
    await r.stub.policyPropose(r.bind(null), { change });
    await failure(() => r.stub.policyWrite(r.bind(null), { expected_generation: 1,
                                                          policy: {} as never }));
    await r.sql("UPDATE policy_proposals SET applied_at = 5 WHERE request_id = 'a'");
    const [usage] = await r.sql(
      "SELECT sessions, clients, pending_proposals, applied_proposals, owner_receipts, " +
        "agent_receipts, refusal_receipts FROM policy_usage");
    const [actual] = await r.sql(
      "SELECT (SELECT COUNT(*) FROM owner_sessions) AS sessions, " +
        "(SELECT COUNT(*) FROM outside_clients) AS clients, " +
        "(SELECT COUNT(*) FROM policy_proposals WHERE applied_at IS NULL) AS pending_proposals, " +
        "(SELECT COUNT(*) FROM policy_proposals WHERE applied_at IS NOT NULL) " +
        "AS applied_proposals, " +
        "(SELECT COUNT(*) FROM policy_receipts WHERE bucket = 'owner') AS owner_receipts, " +
        "(SELECT COUNT(*) FROM policy_receipts WHERE bucket = 'agent') AS agent_receipts, " +
        "(SELECT COUNT(*) FROM policy_receipts WHERE bucket = 'refusal') AS refusal_receipts");
    expect(usage).toEqual(actual);
    expect(usage).toEqual({ sessions: 1, clients: 2, pending_proposals: 1, applied_proposals: 1,
                            owner_receipts: 1, agent_receipts: 3, refusal_receipts: 1 });
    await r.sql("DELETE FROM policy_proposals");
    const [emptied] = await r.sql(
      "SELECT pending_proposals, applied_proposals FROM policy_usage");
    expect(emptied).toEqual({ pending_proposals: 0, applied_proposals: 0 });
  });
});
