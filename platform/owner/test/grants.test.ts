/**
 * P0-3 outside-client grants (decision A1), admitted by the object that holds them.
 *
 * Ported from `tests/test_outside_grants.py` and the M3 tests of
 * `tests/test_outside_wakes.py` on `p0-3-outside-grants` (be88c9116), where they are not
 * about the Python router's identity stamp, the box's recheck of it, open streams or the
 * API-key mint (the binder's, tasks 2): here every outside call is admitted in the
 * object, against its grant, before any door acts.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { GRANT_LIMITS, Grants } from "../src/grants";
import { apiKeyOrigin, clock, Ledger, type Origin } from "../src/ledger";
import type { OwnerDO } from "../src/owner-do";
import { Policy, POLICY_LIMITS, policyDigest } from "../src/policy";
import { DOT, MCP, MUSE, rig, type Rig, signedIn } from "./ledger-support";
import { failure } from "./support";

const CHATGPT: Origin = Object.freeze({ client: "1".repeat(64), family: "2".repeat(64) });
const CLAUDE: Origin = Object.freeze({ client: "3".repeat(64), family: "4".repeat(64) });
const READ = Object.freeze({ universe: "alpha", agent: "main", capability: "read_graph" });
const WRITE = Object.freeze({ ...READ, capability: "write_graph" });

async function clients(r: Rig) {
  return (await r.stub.grantList(r.bind(null))).clients;
}

/** The owner sets a client's grant, against the revision it stands at. */
async function grant(r: Rig, origin: Origin, mode: string, scopes: object[] = []) {
  const row = (await clients(r)).find((c) => c.client === origin.client);
  return r.stub.grantSet(r.session(null), {
    client: origin.client, source: row?.source ?? "mcp", mode, scopes: scopes as never,
    expected_revision: row?.revision ?? 0, name: "" });
}

/** The owner renames a client's grant: the same mode and scopes, against its revision. */
async function rename(r: Rig, origin: Origin, name: string) {
  const row = (await clients(r)).find((c) => c.client === origin.client)!;
  return r.stub.grantSet(r.session(null), {
    client: origin.client, source: row.source, mode: row.mode, scopes: row.scopes as never,
    expected_revision: row.revision, name });
}

async function kindsOf(r: Rig, kind: string) {
  return (await r.table("policy_receipts")).filter((row) => row["kind"] === kind);
}

describe("an outside client's grant", () => {
  it("makes two connectors two clients, each full by default, per owner", async () => {
    const r = await rig();
    for (const origin of [CHATGPT, CLAUDE]) {
      expect((await r.stub.outsideAgents(r.bind(origin))).agents).toEqual([]);
    }
    expect((await clients(r)).map((c) => [c.client, c.source, c.mode, c.scopes, c.generation]))
      .toEqual([[CHATGPT.client, "mcp", "full", [], 1], [CLAUDE.client, "mcp", "full", [], 1]]);
    expect(await clients(await rig())).toEqual([]);  // per owner, never shared
    expect((await failure(() => r.stub.grantList(r.bind(MUSE)))).message)
      .toBe("only the owner may read its outside clients");
  });

  it("keeps a scoped client to exactly its triples at its own mailbox", async () => {
    const r = await rig();
    const agent = await r.register(CHATGPT);
    await r.post(null, `dm:${agent}`);
    const denied = "outside client capability not granted";
    const calls = {
      read: () => r.read(CHATGPT),
      ack: () => r.ack(CHATGPT, 0),
      post: () => r.post(CHATGPT, `dm:${agent}`, { cmid: "m1" }),
    };
    const outcome = async () => Object.fromEntries(await Promise.all(Object.entries(calls)
      .map(async ([name, call]) => [name, await call().then(() => "ok",
                                                           (e: Error) => e.message)])));
    await grant(r, CHATGPT, "scoped", [READ]);
    expect(await outcome()).toEqual({ read: "ok", ack: denied, post: denied });
    await grant(r, CHATGPT, "scoped", [WRITE]);
    expect(await outcome()).toEqual({ read: denied, ack: "ok", post: "ok" });
    await grant(r, CHATGPT, "scoped", [{ ...READ, universe: "beta" }, { ...WRITE, agent: "x" }]);
    expect(await outcome()).toEqual({ read: denied, ack: denied, post: denied });
    await grant(r, CHATGPT, "full");
    expect(await outcome()).toEqual({ read: "ok", ack: "ok", post: "ok" });
  });

  it("lets a scoped client subscribe only with read_graph in that command center",
     async () => {
    const r = await rig();
    const agent = await r.register(CHATGPT, "Muse", "mcp_event");
    const subscribe = (center = "alpha") => r.stub.mailboxSubscriber(
      r.bind(CHATGPT), { command_center_id: center, agent_id: agent });
    await grant(r, CHATGPT, "scoped", [WRITE]);
    expect((await failure(() => subscribe())).message)
      .toBe("outside client capability not granted");
    await grant(r, CHATGPT, "scoped", [READ]);
    expect((await subscribe()).generation).toBe(1);
    expect((await failure(() => subscribe("beta"))).message)
      .toBe("outside client capability not granted");
  });

  it("refuses the next call after a revoke, and needs a sign-in made after it", async () => {
    const r = await rig();
    const as = (authenticated_at?: number) => r.stub.outsideAgents(
      r.bind(CHATGPT, authenticated_at === undefined ? {} : { authenticated_at }));
    await as(100);
    await grant(r, CHATGPT, "revoked");
    expect((await failure(() => as(2 ** 40))).message).toBe("outside client revoked");
    await grant(r, CHATGPT, "full");
    for (const stale of [100, undefined, signedIn() + 2]) {  // a clock a little ahead too
      expect((await failure(() => as(stale))).message)
        .toBe("a fresh sign-in is required after a revoke");
    }
    expect((await as(2 ** 40)).agents).toEqual([]);
    expect((await clients(r))[0]!.generation).toBe(3);
  });

  it("refuses an API key with no grant, never widening it, and records nothing", async () => {
    const r = await rig();
    const key = "0123456789abcdef";
    const keyed = { source: { kind: "api_key", ref: key } } as const;
    const refused = await failure(() => r.stub.outsideAgents(r.bind(apiKeyOrigin(key), keyed)));
    expect([refused.name, refused.message]).toEqual(["Refused", "outside client has no grant"]);
    expect(await clients(r)).toEqual([]);
    await r.keyed(key);
    expect((await r.stub.outsideAgents(r.bind(apiKeyOrigin(key), keyed))).agents).toEqual([]);
    const switched = await failure(() => r.stub.outsideAgents(r.bind(apiKeyOrigin(key))));
    expect(switched.message).toBe("outside client source changed");
    await r.stub.outsideAgents(r.bind(CHATGPT));
    const moved = await failure(() => r.stub.grantSet(r.session(null), {
      client: CHATGPT.client, source: "api_key", mode: "full", scopes: [],
      expected_revision: 1, name: "" }));
    expect(moved.message).toBe("outside client source changed");
  });

  it("changes only in the owner's session, against its revision, receipted", async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(CHATGPT));
    const change = { client: CHATGPT.client, source: "mcp", mode: "revoked", scopes: [],
                     expected_revision: 1, name: "" };
    for (const [binding, reason] of [
      [r.bind(CHATGPT), "only the owner may change an outside client's grant"],
      [r.bind(null), "interactive owner session required"],
      [r.bind(null, { session: "f".repeat(64) }), "interactive owner session required"],
    ] as const) {
      expect((await failure(() => r.stub.grantSet(binding, change))).message).toBe(reason);
    }
    for (const [extra, name, reason] of [
      [{ client: "x" }, "Invalid", "invalid outside client"],
      [{ source: "oauth" }, "Invalid", "invalid outside client source"],
      [{ mode: "admin" }, "Invalid", "invalid outside grant mode"],
      [{ mode: "scoped" }, "Invalid", "a scoped grant, and only a scoped one, names scopes"],
      [{ mode: "full", scopes: [READ] }, "Invalid",
        "a scoped grant, and only a scoped one, names scopes"],
      [{ mode: "scoped", scopes: [{ ...READ, universe: "*" }] }, "Invalid",
        "outside scopes require exact universe, agent and capability"],
      [{ mode: "scoped", scopes: [{ ...READ, extra: "x" }] }, "Invalid",
        "outside scopes require exact universe, agent and capability"],
      [{ mode: "scoped", scopes: Array.from({ length: GRANT_LIMITS.maxScopes + 1 }, () => READ) },
        "Invalid", "outside scopes require exact universe, agent and capability"],
      [{ expected_revision: -1 }, "Invalid", "expected outside grant revision required"],
      [{ name: "n".repeat(81) }, "Invalid", "invalid outside client name"],
      [{ expected_revision: 0 }, "Refused", "outside grant changed"],
    ] as const) {
      const refused = await failure(() => r.stub.grantSet(r.session(null),
                                                          { ...change, ...extra } as never));
      expect([refused.name, refused.message]).toEqual([name, reason]);
    }
    expect((await clients(r)).map((c) => [c.mode, c.generation])).toEqual([["full", 1]]);
    expect((await kindsOf(r, "refusal")).length).toBe(14);
    const named = await r.stub.grantSet(r.session(null), { ...change, mode: "scoped",
                                                           scopes: [READ], name: "ChatGPT" });
    expect(named).toEqual({ client: CHATGPT.client, mode: "scoped", scopes: [READ],
                            generation: 2, revision: 2 });
    await grant(r, CHATGPT, "full");  // an empty name keeps the one given
    expect((await clients(r))[0]!.name).toBe("ChatGPT");
    expect((await kindsOf(r, "outside_grant")).map((row) => [
      row["session_id"] !== null, row["rule"], row["decision"], row["generation"],
      row["request_digest"]])).toEqual([
      [true, CHATGPT.client, "scoped", 2, policyDigest({
        client: CHATGPT.client, mode: "scoped", scopes: [READ], name: "ChatGPT", revision: 2 })],
      [true, CHATGPT.client, "full", 3, policyDigest({
        client: CHATGPT.client, mode: "full", scopes: [], name: "ChatGPT", revision: 3 })]]);
  });

  it("is revoked by the owner's account without a session, as a key's revoke is",
     async () => {
    const r = await rig();
    const revoke = (client: string, from: Origin | null = null) =>
      r.stub.grantRevoke(r.bind(from), { client });
    expect(await revoke(CHATGPT.client)).toEqual({ client: CHATGPT.client, revoked: false,
                                                   generation: 0, revision: 0 });
    await r.stub.outsideAgents(r.bind(CHATGPT));
    expect((await failure(() => revoke(CHATGPT.client, CHATGPT))).message)
      .toBe("only the owner may revoke an outside client's grant");
    expect(await revoke(CHATGPT.client)).toEqual({ client: CHATGPT.client, revoked: true,
                                                   generation: 2, revision: 2 });
    expect(await revoke(CHATGPT.client)).toEqual({ client: CHATGPT.client, revoked: true,
                                                   generation: 2, revision: 2 });  // as it stands
    expect((await failure(() => revoke("nope"))).message).toBe("invalid outside client");
    const [row] = await clients(r);
    expect([row!.mode, row!.scopes, row!.revoked_at !== null]).toEqual(["revoked", [], true]);
    expect((await kindsOf(r, "outside_grant")).map((receipt) => [
      receipt["session_id"], receipt["decision"], receipt["generation"]]))
      .toEqual([[null, "revoked", 2]]);
  });

  it("never refuses a new client at the cap: the least recently seen free row goes (P4)",
     async () => {
    const r = await rig();
    const id = (n: number) => n.toString(16).padStart(64, "0");
    const out = await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      const grants = new Grants(state.storage, new Policy(state.storage));
      for (let n = 0; n < GRANT_LIMITS.maxFreeClients; n++) {
        grants.admit(id(n), "mcp", null, 1000 + n);
      }
      grants.revoke(id(0), 5000);  // the owner's fence: kept whatever comes
      const admitted = [grants.admit("e".repeat(64), "mcp", null, 6000).mode,
                        grants.admit("f".repeat(64), "mcp", null, 6001).mode];
      const kept = new Set(grants.list().map((c) => c.client));
      return { admitted, revokedKept: kept.has(id(0)), oldestFreeGone: !kept.has(id(1)),
               nextKept: kept.has(id(3)), rows: kept.size,
               revoked: (() => {
                 try {
                   grants.admit(id(0), "mcp", 2 ** 40, 6002);
                   return "admitted";
                 } catch (error) {
                   return (error as Error).message;
                 }
               })() };
    });
    expect(out).toEqual({ admitted: ["full", "full"], revokedKept: true, oldestFreeGone: true,
                          nextKept: true, rows: GRANT_LIMITS.maxFreeClients + 1,
                          revoked: "outside client revoked" });
    // An evicted free row is recorded again, the same, on the client's next call.
    await r.stub.outsideAgents(r.bind({ client: id(1), family: "f".repeat(64) }));
    const back = (await clients(r)).find((c) => c.client === id(1))!;
    expect([back.mode, back.generation, back.owner_set]).toEqual(["full", 1, 0]);
  });

  it("keeps a row an agent holds, and forgets only free rows", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    await r.stub.outsideAgents(r.bind(CHATGPT));
    const rows = async () => Object.fromEntries((await clients(r))
      .map((c) => [c.client, [c.owner_set, c.agents]]));
    expect(await rows()).toEqual({ [MUSE.client]: [0, 1], [CHATGPT.client]: [0, 0] });
    for (const client of [MUSE.client]) {
      expect((await failure(() => r.sql("DELETE FROM outside_clients WHERE client = ?",
                                        client))).message)
        .toMatch(/an outside client grant the owner set or an agent holds is never deleted/);
    }
    await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      const grants = new Grants(state.storage, new Policy(state.storage),
                                { ...GRANT_LIMITS, maxFreeClients: 1 });
      grants.admit(CLAUDE.client, "mcp", null, clock());  // CHATGPT's free row goes
    });
    expect(Object.keys(await rows()).sort()).toEqual([CLAUDE.client, MUSE.client].sort());
    await r.stub.deleteAgent(r.session(null), { agent_id: muse });
    expect(await rows()).toMatchObject({ [MUSE.client]: [0, 0] });  // free once its agent goes
    await r.sql("DELETE FROM outside_clients WHERE client = ?", MUSE.client);
    // A client recorded after an agent of it was (as a store change could leave it)
    // still counts that agent, so its row is held, not free.
    await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      const grants = new Grants(state.storage, new Policy(state.storage));
      new Ledger(state.storage, grants).register(DOT, MCP, {
        command_center_id: "alpha", display_name: "Dot", delivery_method: "pull",
        recipe_id: null });
      grants.admit(DOT.client, "mcp", null, clock());
    });
    expect((await rows())[DOT.client]).toEqual([0, 1]);
    const [usage] = await r.sql("SELECT owner_clients, free_clients FROM policy_usage");
    const all = await clients(r);
    expect(usage).toEqual({ owner_clients: all.filter((c) => c.owner_set === 1).length,
                            free_clients: all.filter((c) => c.owner_set === 0 && c.agents === 0)
                              .length });
  });

  it("bounds the rows the owner sets at the owner's own change, never at a revoke",
     async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(CHATGPT));
    await r.stub.outsideAgents(r.bind(CLAUDE));
    await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      const grants = new Grants(state.storage, new Policy(state.storage),
                                { ...GRANT_LIMITS, maxOwnerClients: 1 });
      const change = (client: string, expected: number) => ({
        client, source: "mcp", mode: "full", scopes: [], expected_revision: expected, name: "" });
      grants.set("session", change(CHATGPT.client, 1), clock());
      grants.set("session", change(CHATGPT.client, 2), clock());  // the same row again
      for (const client of [CLAUDE.client, DOT.client]) {
        expect(() => grants.set("session", change(client, client === DOT.client ? 0 : 1),
                                clock()))
          .toThrow("the owner's managed outside clients are at their limit");
      }
      expect(grants.revoke(CLAUDE.client, clock()).revoked).toBe(true);  // still lands
      expect(grants.admit(MUSE.client, "mcp", null, clock()).mode).toBe("full");
    });
    expect((await clients(r)).map((c) => [c.client, c.owner_set]).sort())
      .toEqual([[CHATGPT.client, 1], [CLAUDE.client, 1], [MUSE.client, 0]].sort());
    expect((await failure(() => r.sql("DELETE FROM outside_clients WHERE client = ?",
                                      CHATGPT.client))).message)
      .toMatch(/never deleted/);  // a grant the owner set, standing
  });

  it("rechecks saved work against the generation it was stamped with (standing)",
     async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(CHATGPT));
    const standing = (generation: unknown, signed: number | null = 100) =>
      runInDurableObject(r.stub, (_i: OwnerDO, state) => {
        try {
          return new Grants(state.storage, new Policy(state.storage))
            .standing(CHATGPT.client, generation, signed).mode;
        } catch (error) {
          return (error as Error).message;
        }
      });
    expect(await standing(1)).toBe("full");
    expect(await standing("1")).toBe("outside client authority changed");
    await grant(r, CHATGPT, "scoped", [READ]);
    expect(await standing(1)).toBe("outside client authority changed");
    expect(await standing(2)).toBe("scoped");
    await grant(r, CHATGPT, "revoked");
    expect(await standing(3)).toBe("outside client revoked");
    await grant(r, CHATGPT, "full");
    expect(await standing(4)).toBe("a fresh sign-in is required after a revoke");
    expect(await standing(4, 2 ** 40)).toBe("full");
    expect(await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      try {
        return new Grants(state.storage, new Policy(state.storage))
          .standing(DOT.client, 1, null).mode;
      } catch (error) {
        return (error as Error).message;
      }
    })).toBe("outside client has no grant");
    expect((await clients(r)).map((c) => c.client)).toEqual([CHATGPT.client]);  // a read
  });

  it("records a client's last call coarsely, so admission is a read most calls", async () => {
    const r = await rig();
    const seen = await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      const grants = new Grants(state.storage, new Policy(state.storage));
      const at = (now: number) => {
        grants.admit(CHATGPT.client, "mcp", null, now);
        return grants.list()[0]!.last_seen;
      };
      return [at(1000), at(1000 + GRANT_LIMITS.seenIntervalS - 1),
              at(1000 + GRANT_LIMITS.seenIntervalS)];
    });
    expect(seen).toEqual([1000, 1000, 1000 + GRANT_LIMITS.seenIntervalS]);
  });
});

describe("a changed or revoked grant ends its agents' wakes (P0-3's M3)", () => {
  it("suppresses the wake and revokes the callback in the change's own transaction",
     async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    await r.subscribe(MUSE, muse);
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    await r.stub.grantRevoke(r.bind(null), { client: MUSE.client });
    const rows = await r.table("ledger_wakes");
    expect(rows.map((w) => [w["kind"], w["state"]]))
      .toEqual([["wake", "suppressed"], ["revoke", "pending"]]);
    expect(JSON.parse(rows[0]!["receipt_json"] as string)).toEqual({ reason: "grant" });
    expect(await r.tick(clock() + 1)).toBe(0);
    expect([r.sender.sent, r.sender.revoked, r.sender.callbacks.get(muse)])
      .toEqual([[], [[muse, 1]], new Set()]);
    expect((await failure(() => r.subscribe(MUSE, muse))).message)
      .toBe("outside client revoked");  // it cannot subscribe again while revoked
  });

  it("makes a rescoped agent subscribe again under its new grant, and then rings it",
     async () => {
    const r = await rig();
    const dot = await r.register(DOT, "Dot", "mcp_event", { batch_window_s: 0 });
    await r.subscribe(DOT, dot);
    await r.post(null, `dm:${dot}`, { cmid: "c1" });
    await grant(r, DOT, "scoped", [READ]);
    expect(await r.tick(clock() + 1)).toBe(0);
    expect([r.sender.sent, r.sender.revoked]).toEqual([[], [[dot, 1]]]);
    expect(await r.subscribe(DOT, dot)).toBe(2);
    await r.post(null, `dm:${dot}`, { cmid: "c2" });
    expect(await r.tick(clock() + 2)).toBe(1);
    expect(r.sender.sent.map((target) => target.agent_id)).toEqual([dot]);
  });

  it("is rechecked at the claim, so a revoke made around the object still ends the wake",
     async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    await r.subscribe(MUSE, muse);
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    // no suppression with it
    await r.sql("UPDATE outside_clients SET mode = 'revoked', owner_set = 1");
    expect(await r.tick(clock() + 1)).toBe(0);
    expect(r.sender.sent).toEqual([]);
    const rows = await r.table("ledger_wakes");
    expect(rows.map((w) => [w["kind"], w["state"]]))
      .toEqual([["wake", "suppressed"], ["revoke", "pending"]]);
    expect(JSON.parse(rows[0]!["receipt_json"] as string)).toEqual({ reason: "grant" });
    await r.tick(clock() + 2);
    expect(r.sender.revoked).toEqual([[muse, 1]]);
  });
});

describe("every door admits an outside client against its grant (review L1, L4)", () => {
  const BOARD = Object.freeze({ collection: "board", row_id: "t" });

  /** Each collection door, as `binding` calls it, and what it answered. */
  async function collectionDoors(r: Rig, binding: ReturnType<Rig["bind"]>) {
    const calls: Record<string, () => Promise<unknown>> = {
      write: () => r.stub.collectionWrite(binding, { ...BOARD, body: "x", expected_etag: "",
                                                      private: false }),
      pull: () => r.stub.collectionPull(binding, { collection: "board", after: 0, limit: 5 }),
      get: () => r.stub.collectionGet(binding, BOARD),
      history: () => r.stub.collectionHistory(binding, BOARD),
      delete: () => r.stub.collectionDelete(binding, { ...BOARD, expected_etag: "nope" }),
      clear: () => r.stub.collectionClear(binding, { collection: "scratch", writer: null }),
    };
    return Object.fromEntries(await Promise.all(Object.entries(calls).map(async ([door, call]) =>
      [door, await call().then(() => "ok", (error: Error) => error.message)])));
  }

  it("holds each collection door to the client's grant and scopes", async () => {
    const r = await rig();
    await r.register(CHATGPT);
    const all = (answer: string) => ({ write: answer, pull: answer, get: answer,
                                       history: answer, delete: answer, clear: answer });
    expect(await collectionDoors(r, r.bind(CHATGPT))).toEqual(all("ok"));  // full: the owner's
    const denied = "outside client capability not granted";
    await grant(r, CHATGPT, "scoped", [READ]);
    expect(await collectionDoors(r, r.bind(CHATGPT))).toEqual({
      write: denied, pull: "ok", get: "ok", history: "ok", delete: denied, clear: denied });
    await grant(r, CHATGPT, "scoped", [WRITE]);
    expect(await collectionDoors(r, r.bind(CHATGPT))).toEqual({
      write: "ok", pull: denied, get: denied, history: denied, delete: "ok", clear: "ok" });
    await grant(r, CHATGPT, "scoped", [{ ...READ, universe: "beta" }, { ...WRITE, agent: "x" }]);
    expect(await collectionDoors(r, r.bind(CHATGPT))).toEqual(all(denied));
    await grant(r, CHATGPT, "revoked");
    expect(await collectionDoors(r, r.bind(CHATGPT))).toEqual(all("outside client revoked"));
    const key = "0123456789abcdef";
    expect(await collectionDoors(r, r.bind(apiKeyOrigin(key), {
      source: { kind: "api_key", ref: key } }))).toEqual(all("outside client has no grant"));
  });

  it("still serves a visiting owner as itself, but never through an outside client",
     async () => {
    const r = await rig();
    const visitor = { owner: r.owner, caller: "bob", origin: null };
    expect((await r.stub.collectionWrite(visitor, { ...BOARD, body: "hi", expected_etag: "",
                                                    private: false }) as { row: { writer: string } })
      .row.writer).toBe("bob");
    const through = await failure(() => r.stub.collectionPull(
      { owner: r.owner, caller: "bob", origin: CHATGPT, source: MCP },
      { collection: "board", after: 0, limit: 1 }));
    expect(through.message).toBe("the account answers its owner's own callers only");
    expect((await failure(() => r.stub.outsideAgents(visitor))).message)
      .toBe("the account answers its owner's own callers only");
  });

  it("lets a scoped client propose only with propose_policy where its agent is",
     async () => {
    const r = await rig();
    await r.register(CHATGPT);
    const propose = (request_id: string) => r.stub.policyPropose(r.bind(CHATGPT), {
      change: { type: "policy_proposal" }, request_id });
    await grant(r, CHATGPT, "scoped", [{ ...READ, universe: "u" }]);  // P6
    expect((await failure(() => propose("card-x"))).message)
      .toBe("outside client capability not granted");
    await grant(r, CHATGPT, "scoped", [{ ...READ, capability: "propose_policy" }]);
    expect((await propose("card-y")).generation).toBe(1);
    await grant(r, CHATGPT, "revoked");
    expect((await failure(() => propose("card-z"))).message).toBe("outside client revoked");
    for (const [binding, message] of [
      [{ ...r.bind(null), owner: "someone-else" }, "this object belongs to another owner"],
      [{ ...r.bind(null), caller: "bob" }, "the account answers its owner's own callers only"],
      [{ owner: r.owner, caller: r.owner } as never, "invalid outside origin"],
    ] as const) {
      expect((await failure(() => r.stub.policyPropose(binding, { change: {} }))).message)
        .toBe(message);
    }
    expect((await r.table("policy_proposals")).map((row) => [row["request_id"],
                                                             row["proposer"]]))
      .toEqual([["card-y", CHATGPT.client]]);
  });

  it("holds one client to its share of the pending proposals, never the owner", async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(CHATGPT));
    await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      const policy = new Policy(state.storage, { ...POLICY_LIMITS, pendingProposals: 6,
                                                 pendingPerClient: 2 });
      const now = clock();
      policy.propose({ n: 1 }, "a", CHATGPT.client, now);
      policy.propose({ n: 2 }, "b", CHATGPT.client, now);
      expect(() => policy.propose({ n: 3 }, "c", CHATGPT.client, now))
        .toThrow("this client's pending policy proposals are at their limit");
      policy.propose({ n: 1 }, "a", CHATGPT.client, now);  // the same card again: a no-op
      policy.propose({ n: 4 }, "d", CLAUDE.client, now);
      for (const card of ["e", "f", "h"]) {  // the owner past a client's share
        policy.propose({ card }, card, "owner", now);
      }
      expect(() => policy.propose({ n: 6 }, "i", "owner", now))
        .toThrow("too many pending policy proposals");
      // An applied card frees its proposer's share.
      const { digest } = policy.held("a", now)!;
      state.storage.sql.exec("UPDATE policy_proposals SET applied_at = ? WHERE request_id = ?",
                             now, "a");
      expect(digest).toMatch(/^sha256:/);
      policy.propose({ n: 7 }, "g", CHATGPT.client, now);
    });
    expect((await r.table("proposal_shares")).map((row) => [row["proposer"], row["pending"]])
      .sort()).toEqual([[CHATGPT.client, 2], [CLAUDE.client, 1], ["owner", 3]].sort());
  });
});

describe("only the owner's own act lifts a revoke (slice 3's L1, slice 5's review M1)", () => {
  const DAY = 86_400;
  const MONTH = 30 * DAY;
  /** Revoke `origin` at `at` around the doors, as an old revoke would stand. */
  const revokedAt = (r: Rig, origin: Origin, at: number) =>
    runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      new Grants(state.storage, new Policy(state.storage)).revoke(origin.client, at);
    });
  const forgetRevoked = (r: Rig, clients: string[], binding = r.session(null)) =>
    r.stub.grantForgetRevoked(binding, { clients });
  const fence = async (r: Rig) =>
    (await r.sql("SELECT mcp_through FROM grant_fences"))[0]!["mcp_through"];

  it("forgets an API key's revoke at the binder's word that the key is deleted", async () => {
    const r = await rig();
    const { origin, source } = await r.keyed("0123456789abcdef");
    const forget = () => r.stub.grantForget(r.bind(null), { client: origin.client });
    expect((await failure(forget)).message)
      .toBe("only a revoked grant is forgotten; revoke it first");
    await r.stub.grantRevoke(r.bind(null), { client: origin.client });
    const [before] = await r.sql("SELECT owner_clients FROM policy_usage");
    expect(await forget()).toEqual({ client: origin.client, forgotten: true });
    expect(await forget()).toEqual({ client: origin.client, forgotten: false });
    const [after] = await r.sql("SELECT owner_clients FROM policy_usage");
    expect([before!["owner_clients"], after!["owner_clients"]]).toEqual([1, 0]);
    const [receipt] = (await kindsOf(r, "outside_grant")).slice(-1);
    expect([receipt!["decision"], receipt!["rule"]]).toEqual(["forgotten", origin.client]);
    // A key with no row is refused, never recorded: forgetting it reopens nothing.
    expect((await failure(() => r.stub.outsideAgents(r.bind(origin, { source })))).message)
      .toBe("outside client has no grant");
    expect(await fence(r)).toBe(0);  // an API key's forget moves no MCP fence
    expect((await failure(() => r.stub.grantForget(r.bind(MUSE), { client: origin.client })))
      .message).toBe("only the owner may forget an outside client's grant");
  });

  it("never lets the binder's forget lift an MCP revoke, however old (probe 1)", async () => {
    const r = await rig();
    const t = clock();
    const signedBefore = Math.floor(t - MONTH - 3600);  // a sign-in from before the revoke
    await r.stub.outsideAgents(r.bind(CHATGPT, { authenticated_at: signedBefore }));
    await revokedAt(r, CHATGPT, t - MONTH - 60);
    expect((await failure(() => r.stub.grantForget(r.bind(null), { client: CHATGPT.client })))
      .message).toBe("only the owner, in its session, forgets an MCP client's revoke");
    expect((await clients(r)).map((c) => [c.client, c.mode])).toEqual([[CHATGPT.client, "revoked"]]);
    expect((await failure(() => r.stub.outsideAgents(
      r.bind(CHATGPT, { authenticated_at: signedBefore })))).message).toBe("outside client revoked");
  });

  it("lifts an MCP revoke only in the owner's session, and the fence still stands",
     async () => {
    const r = await rig();
    const t = clock();
    const signedBefore = Math.floor(t - 3 * DAY);
    await r.stub.outsideAgents(r.bind(CHATGPT, { authenticated_at: signedBefore }));
    await r.stub.outsideAgents(r.bind(CLAUDE, { authenticated_at: signedBefore }));
    await revokedAt(r, CHATGPT, t - 2 * DAY);
    await revokedAt(r, CLAUDE, t - DAY);
    const refused = await failure(() => forgetRevoked(r, [CHATGPT.client], r.bind(null)));
    expect(refused.message).toBe("interactive owner session required");
    expect((await kindsOf(r, "refusal")).at(-1)).toMatchObject({ decision: "deny" });
    expect(await forgetRevoked(r, [CHATGPT.client, CLAUDE.client, DOT.client]))
      .toEqual({ forgotten: [CHATGPT.client, CLAUDE.client], mcp_fence: t - DAY });
    expect(await clients(r)).toEqual([]);
    const [receipt] = (await kindsOf(r, "outside_grant")).slice(-1);
    expect([receipt!["decision"], receipt!["session_id"] !== null]).toEqual(["forgotten", true]);
    // The pre-revoke credential, and any client whose sign-in is unknown or older than the
    // latest forgotten revoke, is never recorded; a sign-in after it is, as `full`.
    for (const binding of [r.bind(CHATGPT, { authenticated_at: signedBefore }), r.bind(CHATGPT),
                           r.bind(DOT, { authenticated_at: Math.floor(t - 2 * DAY) }),
                           // within the revoke's clock margin: counted as before it
                           r.bind(DOT, { authenticated_at: Math.ceil(t - DAY) + 2 })]) {
      expect((await failure(() => r.stub.outsideAgents(binding))).message)
        .toBe("a fresh sign-in is required after a revoke");
    }
    expect(await clients(r)).toEqual([]);
    await r.stub.outsideAgents(r.bind(CHATGPT, { authenticated_at: Math.floor(t) }));
    expect((await clients(r)).map((c) => [c.client, c.mode, c.generation, c.owner_set]))
      .toEqual([[CHATGPT.client, "full", 1, 0]]);
    expect((await failure(() => forgetRevoked(r, [CHATGPT.client]))).message)
      .toBe("only a revoked grant is forgotten; revoke it first");
    expect((await failure(() => forgetRevoked(r, []))).message)
      .toBe("forget names 1 to 100 distinct outside clients");
  });

  it("never forgets a revoked row an agent or stamped work holds", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    await revokedAt(r, MUSE, clock() - DAY);
    expect((await failure(() => forgetRevoked(r, [MUSE.client]))).message)
      .toBe("an agent or scheduled work still holds this client's grant");
    await expect(r.sql("DELETE FROM outside_clients WHERE client = ?", MUSE.client))
      .rejects.toThrow(/never deleted/);
    await r.stub.deleteAgent(r.session(null), { agent_id: muse });
    await r.sql("UPDATE outside_clients SET held = 1 WHERE client = ?", MUSE.client);
    expect((await failure(() => forgetRevoked(r, [MUSE.client]))).message)
      .toBe("an agent or scheduled work still holds this client's grant");
    await r.sql("UPDATE outside_clients SET held = 0 WHERE client = ?", MUSE.client);
    expect((await forgetRevoked(r, [MUSE.client])).forgotten).toEqual([MUSE.client]);
  });

  it("refuses another client's grant at the bound, and never forgets a revoke for it " +
     "(probe 2)", async () => {
    const r = await rig();
    const t = clock();
    const signedBefore = Math.floor(t - 40 * DAY);
    await r.stub.outsideAgents(r.bind(CHATGPT, { authenticated_at: signedBefore }));
    await r.stub.outsideAgents(r.bind(CLAUDE));
    const out = await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      const grants = new Grants(state.storage, new Policy(state.storage),
                                { ...GRANT_LIMITS, maxOwnerClients: 1 });
      grants.revoke(CHATGPT.client, t - 31 * DAY);
      try {
        grants.set("session", { client: CLAUDE.client, source: "mcp", mode: "full", scopes: [],
                                expected_revision: 1, name: "" }, t);
        return "set";
      } catch (error) {
        return (error as Error).message;
      }
    });
    expect(out).toBe("the owner's managed outside clients are at their limit; forget " +
                     "revoked clients to make room");
    expect((await clients(r)).find((c) => c.client === CHATGPT.client)!.mode).toBe("revoked");
    expect((await failure(() => r.stub.outsideAgents(
      r.bind(CHATGPT, { authenticated_at: signedBefore })))).message).toBe("outside client revoked");
    expect(await fence(r)).toBe(0);
  });

  it("keeps the fence at the newest revoke forgotten, and never lowers it (review L1)",
     async () => {
    const r = await rig();
    const t = Math.floor(clock());
    for (const origin of [CHATGPT, CLAUDE]) {
      await r.stub.outsideAgents(r.bind(origin, { authenticated_at: t - 10 * DAY }));
    }
    await revokedAt(r, CHATGPT, t - 2 * DAY);
    await revokedAt(r, CLAUDE, t - DAY);
    // Newer first: forgetting the older revoke leaves the fence where the newer put it.
    expect((await forgetRevoked(r, [CLAUDE.client])).mcp_fence).toBe(t - DAY);
    expect((await forgetRevoked(r, [CHATGPT.client])).mcp_fence).toBe(t - DAY);
    await expect(r.sql("UPDATE grant_fences SET mcp_through = ?", t - 2 * DAY))
      .rejects.toThrow(/only ever rises/);
    await expect(r.sql("DELETE FROM grant_fences")).rejects.toThrow(/only ever rises/);
    expect(await fence(r)).toBe(t - DAY);
    // The margin's edge is before the fence; a second past it is after.
    const edge = t - DAY + GRANT_LIMITS.revokeClockMarginS;
    expect((await failure(() => r.stub.outsideAgents(r.bind(DOT, { authenticated_at: edge }))))
      .message).toBe("a fresh sign-in is required after a revoke");
    await r.stub.outsideAgents(r.bind(DOT, { authenticated_at: edge + 1 }));
    expect((await clients(r)).map((c) => c.client)).toEqual([DOT.client]);
  });

  it("forgets at most 100 distinct clients at once (review L3)", async () => {
    const r = await rig();
    const many = Array.from({ length: 101 }, (_, i) => i.toString(16).padStart(64, "0"));
    const refusal = "forget names 1 to 100 distinct outside clients";
    expect((await failure(() => forgetRevoked(r, many))).message).toBe(refusal);
    expect(await forgetRevoked(r, many.slice(0, 100))).toEqual({ forgotten: [], mcp_fence: 0 });
    expect((await failure(() => forgetRevoked(r, [DOT.client, DOT.client]))).message)
      .toBe(refusal);
  });

  it("moves the fence on any delete of a revoked MCP row, even around the module",
     async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(CHATGPT));
    await r.stub.outsideAgents(r.bind(CLAUDE));
    await revokedAt(r, CHATGPT, 5000);
    await r.sql("DELETE FROM outside_clients WHERE client = ?", CHATGPT.client);
    expect(await fence(r)).toBe(5000);
    await r.sql("DELETE FROM outside_clients WHERE client = ?", CLAUDE.client);  // a free row
    expect(await fence(r)).toBe(5000);
    await grant(r, DOT, "full");
    await expect(r.sql("DELETE FROM outside_clients WHERE client = ?", DOT.client))
      .rejects.toThrow(/never deleted/);  // a standing grant the owner set
  });
});

describe("a scoped grant's capabilities (slice 5's review N9)", () => {
  it("names only the capabilities a door checks", async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(CHATGPT));
    const refused = await failure(() => grant(r, CHATGPT, "scoped",
                                              [{ ...READ, capability: "read_everything" }]));
    expect([refused.name, refused.message]).toEqual(["Invalid", "a scope's capability is one " +
      "of read_graph, write_graph, propose_policy, read_files, write_files, converse, " +
      "run_graph"]);
    for (const capability of ["read_graph", "write_graph", "propose_policy", "read_files",
                              "write_files", "converse", "run_graph"]) {
      expect((await grant(r, CHATGPT, "scoped", [{ ...READ, capability }])).mode).toBe("scoped");
    }
  });
});

describe("the grant row's own rule (slice 3's review N4)", () => {
  it("refuses a row the owner never set that is anything but a fresh full grant", async () => {
    const r = await rig();
    for (const values of [
      "'mcp', 'scoped', '[]', 1, NULL, ''", "'api_key', 'full', '[]', 1, NULL, ''",
      "'mcp', 'full', '[]', 2, NULL, ''", "'mcp', 'full', '[]', 1, 5, ''",
      "'mcp', 'full', '[1]', 1, NULL, ''", "'mcp', 'full', '[]', 1, NULL, 'x'",
    ]) {
      await expect(r.sql("INSERT INTO outside_clients (client, source, mode, scopes, " +
                           `generation, revoked_at, name, first_seen, last_seen) VALUES (?, ${
                             values}, 1, 1)`, "9".repeat(64)))
        .rejects.toThrow(/CHECK constraint failed/);
    }
    await r.sql("INSERT INTO outside_clients (client, source, mode, scopes, generation, " +
                  "revoked_at, name, first_seen, last_seen) VALUES (?, 'mcp', 'full', '[]', 1, " +
                  "NULL, '', 1, 1)", "9".repeat(64));
    expect((await clients(r)).map((c) => c.client)).toEqual(["9".repeat(64)]);
  });
});

describe("a grant's revision apart from its authority (S4-S5 review round 2, L1)", () => {
  it("moves the revision on every set and revoke, the generation only with the mode or " +
     "scopes", async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(CHATGPT));
    const set = (mode: string, scopes: object[], name: string, expected: number) =>
      r.stub.grantSet(r.session(null), { client: CHATGPT.client, source: "mcp", mode,
                                         scopes: scopes as never, expected_revision: expected,
                                         name });
    expect(await set("full", [], "ChatGPT", 1)).toEqual({
      client: CHATGPT.client, mode: "full", scopes: [], generation: 1, revision: 2 });
    expect(await set("scoped", [READ, WRITE], "", 2))
      .toMatchObject({ generation: 2, revision: 3 });
    // The same triples, reordered and repeated, hold the same: a rename.
    expect(await set("scoped", [WRITE, READ, WRITE], "Chat", 3))
      .toMatchObject({ generation: 2, revision: 4 });
    expect(await set("scoped", [READ], "", 4)).toMatchObject({ generation: 3, revision: 5 });
    expect(await set("full", [], "", 5)).toMatchObject({ generation: 4, revision: 6 });
    expect((await r.stub.grantRevoke(r.bind(null), { client: CHATGPT.client })).generation)
      .toBe(5);
    expect((await clients(r)).map((c) => [c.generation, c.revision])).toEqual([[5, 7]]);
    expect((await r.stub.grantRevoke(r.bind(null), { client: CHATGPT.client })).revision)
      .toBe(7);  // as it stands, so the owner's next set names it
    expect(await set("revoked", [], "Old", 7)).toMatchObject({ generation: 5, revision: 8 });
    expect((await clients(r)).map((c) => [c.name, c.mode, c.generation, c.revision]))
      .toEqual([["Old", "revoked", 5, 8]]);
    const receipts = await kindsOf(r, "outside_grant");
    expect(receipts.map((row) => row["generation"])).toEqual([1, 2, 2, 3, 4, 5, 5]);
    expect(receipts[0]!["request_digest"]).toBe(policyDigest({
      client: CHATGPT.client, mode: "full", scopes: [], name: "ChatGPT", revision: 2 }));
    expect(new Set(receipts.map((row) => row["request_digest"])).size).toBe(7);
  });

  it("keeps a revoked grant's fence when it is set revoked again, and times a new revoke",
     async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(CHATGPT));
    expect(await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      const grants = new Grants(state.storage, new Policy(state.storage));
      const set = (mode: string, expected: number, name: string, now: number) =>
        grants.set("session", { client: CHATGPT.client, source: "mcp", mode, scopes: [],
                                expected_revision: expected, name }, now);
      const fence = () => grants.list()[0]!.revoked_at;
      const out = [set("revoked", 1, "", 100).moved, fence()];
      out.push(set("revoked", 2, "Old", 200).moved, fence());
      out.push(set("full", 3, "", 300).moved, fence());
      out.push(set("revoked", 4, "", 400).moved, fence());
      return out;
    })).toEqual([true, 100, false, 100, true, 100, true, 400]);
  });

  it("refuses a stale revision on every set, a rename included", async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(CHATGPT));
    const set = (change: object, expected: number) => r.stub.grantSet(r.session(null), {
      client: CHATGPT.client, source: "mcp", mode: "full", scopes: [], name: "",
      expected_revision: expected, ...change });
    await set({ name: "ChatGPT" }, 1);
    const changes = [{ name: "Again" }, { mode: "scoped", scopes: [READ] }, {},
                     { mode: "revoked" }];
    for (const [change, expected] of [...changes.map((one) => [one, 1] as const),
                                      [{ name: "Ahead" }, 3] as const]) {
      const refused = await failure(() => set(change, expected));
      expect([refused.name, refused.message]).toEqual(["Refused", "outside grant changed"]);
    }
    await r.stub.grantRevoke(r.bind(null), { client: CHATGPT.client });
    expect((await failure(() => set({ mode: "revoked", name: "Old" }, 2))).message)
      .toBe("outside grant changed");
    expect((await clients(r)).map((c) => [c.name, c.mode, c.generation, c.revision]))
      .toEqual([["ChatGPT", "revoked", 2, 3]]);
    expect(await set({ mode: "revoked", name: "Old" }, 3)).toMatchObject({ revision: 4 });
  });

  it("keeps a stamp made before a rename standing, and refuses one made before a scope " +
     "change", async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(CHATGPT));
    const standing = (generation: number) => runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      try {
        return new Grants(state.storage, new Policy(state.storage))
          .standing(CHATGPT.client, generation, 100).mode;
      } catch (error) {
        return (error as Error).message;
      }
    });
    await grant(r, CHATGPT, "scoped", [READ]);
    expect(await standing(2)).toBe("scoped");
    await rename(r, CHATGPT, "ChatGPT");
    expect(await standing(2)).toBe("scoped");
    await grant(r, CHATGPT, "scoped", [WRITE]);
    expect(await standing(2)).toBe("outside client authority changed");
    expect(await standing(3)).toBe("scoped");
  });

  it("leaves a renamed client's wakes and callback as they were, and ends them on a scope " +
     "change", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    await r.subscribe(MUSE, muse);
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    await rename(r, MUSE, "Muse");
    expect((await r.table("ledger_wakes")).map((w) => [w["kind"], w["state"]]))
      .toEqual([["wake", "pending"]]);
    expect(await r.tick(clock() + 1)).toBe(1);
    expect([r.sender.sent.map((target) => target.agent_id), r.sender.revoked])
      .toEqual([[muse], []]);
    await r.post(null, `dm:${muse}`, { cmid: "c2" });
    await grant(r, MUSE, "scoped", [READ]);
    expect((await r.table("ledger_wakes")).map((w) => [w["kind"], w["state"]]).slice(1))
      .toEqual([["wake", "suppressed"], ["revoke", "pending"]]);
  });
});
