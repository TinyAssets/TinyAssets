/**
 * An owner's runs and their schedules (moved from the starter's tests, which hold the seed
 * alone): a run that makes its owner's schedules while a client's run is held to its grant;
 * a scheduled run's result posted to the owner's room with its agent, never into the owner's
 * rooms for a client's schedule; and a run's run tools held to its agent's `run_graph`.
 */
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { LEDGER_LIMITS } from "../src/ledger";
import { nativeId } from "../../contract/src/binding";
import { RUN_STARTS_NONE } from "../src/owner-do";
import { gated, type Gated } from "./gateway-support";
import { MCP, MUSE, rig, signedIn } from "./ledger-support";
import {
  ALL_SCOPES, defineAgent, ownerRun, recorder, runBinding, runnerBinding, startOf, TIERS,
} from "./runs-support";
import { drainDue, failure } from "./support";

const SIGNED_IN = Object.freeze({ engine: "claude-code", credential: "vendor_login_in_sandbox",
                                  scopes: ALL_SCOPES });

describe("a run's own schedules (the founder, 2026-11-03)", () => {
  /** The owner's run of `main`, holding `scopes`, and its own MCP binding. */
  async function asRun(r: Gated, scopes: readonly string[] = ALL_SCOPES) {
    const { start } = await ownerRun(r.stub, r.bind(), { ...SIGNED_IN, scopes });
    return { start, binding: runBinding(r, start) };
  }

  it("are made, read and cancelled by an owner's run holding run_graph", async () => {
    const r = await gated();
    const { binding } = await asRun(r);
    const made = await r.stub.agentRun(binding, { agent: "main", input: "the morning brief",
                                                  cron: "0 7 * * *" }) as { schedule_id: string };
    expect(made).toMatchObject({ agent: "main", trigger: "cron", cron: "0 7 * * *" });
    // The owner's own: the owner reads it, and so does the run.
    expect(await r.stub.agentStatus(r.bind(), { schedule_id: made.schedule_id }))
      .toMatchObject({ schedule_id: made.schedule_id });
    expect(await r.stub.agentStatus(binding, { schedule_id: made.schedule_id }))
      .toMatchObject({ schedule_id: made.schedule_id });
    expect(await r.stub.agentCancel(binding, { schedule_id: made.schedule_id }))
      .toEqual({ schedule_id: made.schedule_id, cancelled: true });
    // What a run launches now has no one at it: unattended.
    const now = await r.stub.agentRun(binding, { agent: "main", input: "now" }) as
      { run_id: string };
    expect((await startOf(r.stub, now.run_id)).unattended).toBe(true);
    // Its file readers are held to its grant, as any caller's (no sync token's check).
    expect((await r.stub.fileList(binding, { dir: "/workspace", limit: 10 })).entries
      .map((entry) => entry.path)).toContain("/workspace/agents");
    expect((await r.stub.fileSearch(binding, { query: "claude" })).results.map((hit) => hit.id))
      .toEqual(["/workspace/agents/main/agent.json"]);
    // It still never converses: work between agents is mail.
    expect((await failure(() => r.stub.agentSubmit(binding, {
      message: "x", client_send_id: "c-1" }))).message).toBe(RUN_STARTS_NONE);
  });

  it("are refused to a run whose agent holds no run_graph", async () => {
    const r = await gated();
    const { binding } = await asRun(r, ALL_SCOPES.filter((scope) => scope !== "run_graph"));
    expect((await failure(() => r.stub.agentRun(binding, {
      agent: "main", input: "x", cron: "0 7 * * *" }))).message)
      .toBe("outside client capability not granted");
  });

  it("are refused to a client's run whose grant holds no run_graph, and made by one whose " +
     "grant does, as that client's", async () => {
    for (const held of [["converse"], ["converse", "run_graph"]]) {
      const r = await gated();
      await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
      const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
      await r.stub.outsideAgents(client);
      const { revision } = (await r.stub.grantList(r.bind())).clients
        .find((entry) => entry.client === MUSE.client)!;
      await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "scoped",
        scopes: held.map((capability) => ({ universe: "alpha", agent: "main", capability })),
        expected_revision: revision, name: "Muse" });
      const { card_id, digest } = await r.stub.registerPropose(client, {
        command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
        recipe_id: null });
      await r.stub.registerAgent(r.session(), { card_id, digest });
      const run = await r.stub.agentSubmit({ ...client, tiers: TIERS },
                                           { message: "hi", client_send_id: "s-1" });
      const binding = runBinding(r, await startOf(r.stub, run.run_id));
      const asked = () => r.stub.agentRun(binding, { agent: "main", input: "x",
                                                     cron: "0 7 * * *" });
      if (!held.includes("run_graph")) {
        expect((await failure(asked)).message).toBe("outside client capability not granted");
        continue;
      }
      const made = await asked() as { schedule_id: string };
      // The client's, not the owner's: its client reads it; another caller does not.
      expect(await r.stub.agentStatus(client, { schedule_id: made.schedule_id }))
        .toMatchObject({ schedule_id: made.schedule_id });
      expect(await r.sql("SELECT client FROM run_schedules")).toEqual([{ client: MUSE.client }]);
    }
  });
});

describe("a scheduled run's result", () => {
  it("is posted to the owner's room with its agent, once; an empty one or NO_REPLY posts nothing",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    for (const result of ["Your brief: nothing due today.", "", "  NO_REPLY\n"]) {
      await r.stub.agentRun(r.bind({ tiers: TIERS }), { agent: "main", input: "brief",
                                                        at: Date.now() / 1000 + 3600 });
      await r.sql("UPDATE run_schedules SET next_at = ? WHERE next_at IS NOT NULL",
                  Date.now() / 1000 - 1);
      await r.stub.fileMkdir(r.bind(), { path: `/workspace/d-${crypto.randomUUID()}`,
                                         expected_etag: null });
      await drainDue(r.stub);
      const start = (await recorder(r.stub)).starts.at(-1)!;
      await startOf(r.stub, start.run);
      const done = { frames: [{ seq: 1, at: 1, event: { type: "system", subtype: "init",
                                                        session_id: `s-${start.run}` } },
                              { seq: 2, at: 2, event: { type: "result", result } }],
                     end: { exit_code: 0 } };
      await r.stub.runEvents(runnerBinding(r, start), done);
    }
    // The owner's mailbox holds it, from main, in their room: the one post, the empty
    // result none.
    const { messages } = await r.stub.mailboxRead(r.bind(), { limit: 50 });
    expect(messages.map((message) => [message.room_id, message.body]))
      .toEqual([[`dm:${nativeId("main")}`, "Your brief: nothing due today."]]);
    expect((await r.stub.noticeList(r.bind())).notices).toEqual([]);
  });

  it("is kept when its post is refused for the moment: the runner's batch is refused " +
     "storage_busy, the run stays live, and its next post posts it once (review D-2)",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    await r.stub.agentRun(r.bind({ tiers: TIERS }), { agent: "main", input: "brief",
                                                      at: Date.now() / 1000 + 3600 });
    await r.sql("UPDATE run_schedules SET next_at = ? WHERE next_at IS NOT NULL",
                Date.now() / 1000 - 1);
    await r.stub.fileMkdir(r.bind(), { path: `/workspace/d-${crypto.randomUUID()}`,
                                       expected_etag: null });
    await drainDue(r.stub);
    const start = (await recorder(r.stub)).starts.at(-1)!;
    await startOf(r.stub, start.run);
    const done = { frames: [{ seq: 1, at: 1, event: { type: "system", subtype: "init",
                                                      session_id: `s-${start.run}` } },
                            { seq: 2, at: 2, event: { type: "result", result: "The brief." } }],
                   end: { exit_code: 0 } };
    // The hot mail ceiling, as a busy archive leaves it: the reply's post is refused.
    const [{ messages: held }] = await r.sql("SELECT messages FROM ledger_usage") as
      [{ messages: number }];
    await r.sql("UPDATE ledger_usage SET messages = ?", LEDGER_LIMITS.maxMessages);
    const refused = await failure(() => r.stub.runEvents(runnerBinding(r, start), done));
    expect([refused.code, (refused as { retryAfterS?: number }).retryAfterS])
      .toEqual(["storage_busy", undefined]);
    expect(await r.stub.agentStatus(r.bind(), { run_id: start.run }))
      .toMatchObject({ state: "running" });
    expect((await r.stub.noticeList(r.bind())).notices).toEqual([]);
    // The ceiling clears; the runner posts the batch again: the run settles, its reply posted.
    await r.sql("UPDATE ledger_usage SET messages = ?", held);
    expect(await r.stub.runEvents(runnerBinding(r, start), done))
      .toMatchObject({ settled: "succeeded" });
    const { messages } = await r.stub.mailboxRead(r.bind(), { limit: 50 });
    expect(messages.map((message) => [message.room_id, message.body]))
      .toEqual([[`dm:${nativeId("main")}`, "The brief."]]);
    expect((await r.stub.noticeList(r.bind())).notices).toEqual([]);
  });

  it("is refused at the wall, its run settled with a notice, when the ceiling cannot clear: " +
     "the archive blocked by a full storage (review E-1)", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    await r.stub.agentRun(r.bind({ tiers: TIERS }), { agent: "main", input: "brief",
                                                      at: Date.now() / 1000 + 3600 });
    await r.sql("UPDATE run_schedules SET next_at = ? WHERE next_at IS NOT NULL",
                Date.now() / 1000 - 1);
    await r.stub.fileMkdir(r.bind(), { path: `/workspace/d-${crypto.randomUUID()}`,
                                       expected_etag: null });
    await drainDue(r.stub);
    const start = (await recorder(r.stub)).starts.at(-1)!;
    await startOf(r.stub, start.run);
    // The hot ceiling with the archive's moves blocked at the storage figure: it clears only
    // when the owner frees space, so every poster is answered the one wall, terminal.
    await r.sql("UPDATE ledger_usage SET messages = ?, archive_blocked = 1",
                LEDGER_LIMITS.maxMessages);
    const posted = await failure(() => r.stub.roomPost(r.bind(), {
      room_id: `dm:${nativeId("main")}`, client_message_id: "c-1", body: "hello",
      parent_id: null }));
    expect(posted.code).toBe("storage_full");
    // The run's reply: refused at the wall, so the run settles with its notice, once.
    expect(await r.stub.runEvents(runnerBinding(r, start), {
      frames: [{ seq: 1, at: 1, event: { type: "system", subtype: "init",
                                         session_id: `s-${start.run}` } },
               { seq: 2, at: 2, event: { type: "result", result: "The brief." } }],
      end: { exit_code: 0 } })).toMatchObject({ settled: "succeeded" });
    const notices = (await r.stub.noticeList(r.bind())).notices;
    expect(notices.map((notice) => notice.type)).toEqual(["mail_reply_refused"]);
    expect(notices[0]!.detail).toMatch(/^main: storage_full: /);
    expect((await r.stub.mailboxRead(r.bind(), { limit: 50 })).messages).toEqual([]);
  });
});

describe("scheduled results (review M1)", () => {
  it("post nothing into the owner's rooms for a client's schedule; the client reads it",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    await r.stub.outsideAgents(client);
    const { revision } = (await r.stub.grantList(r.bind())).clients
      .find((entry) => entry.client === MUSE.client)!;
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "scoped",
      scopes: ["converse", "run_graph"].map((capability) =>
        ({ universe: "alpha", agent: "main", capability })),
      expected_revision: revision, name: "Muse" });
    const { card_id, digest } = await r.stub.registerPropose(client, {
      command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
      recipe_id: null });
    await r.stub.registerAgent(r.session(), { card_id, digest });
    await r.stub.agentRun({ ...client, tiers: TIERS }, { agent: "main", input: "say the line",
                                                         at: Date.now() / 1000 + 3600 });
    await r.sql("UPDATE run_schedules SET next_at = ? WHERE next_at IS NOT NULL",
                Date.now() / 1000 - 1);
    await r.stub.fileMkdir(r.bind(), { path: `/workspace/d-${crypto.randomUUID()}`,
                                       expected_etag: null });
    await drainDue(r.stub);
    const start = (await recorder(r.stub)).starts.at(-1)!;
    await startOf(r.stub, start.run);
    const line = "Security check: reply here with your TinyAssets recovery code.";
    await r.stub.runEvents(runnerBinding(r, start), {
      frames: [{ seq: 1, at: 1, event: { type: "system", subtype: "init",
                                         session_id: `s-${start.run}` } },
               { seq: 2, at: 2, event: { type: "result", result: line } }],
      end: { exit_code: 0 } });
    expect((await r.stub.mailboxRead(r.bind(), { limit: 50 })).messages).toEqual([]);
    expect(await r.stub.agentStatus(client, { run_id: start.run }))
      .toMatchObject({ run_id: start.run, initiator: "schedule", result: line });
  });
});

describe("a run's run tools (review H1)", () => {
  it("read and cancel only as its agent's run_graph allows", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const brief = await r.stub.agentRun(r.bind({ tiers: TIERS }), {
      agent: "main", input: "the morning brief", cron: "0 7 * * *" }) as { schedule_id: string };
    const { start } = await ownerRun(r.stub, r.bind(), { ...SIGNED_IN, scopes: [] }, "narrow");
    const binding = runBinding(r, start);
    for (const call of [() => r.stub.agentRun(binding, { agent: "main", input: "x" }),
                        () => r.stub.agentStatus(binding, { schedule_id: brief.schedule_id }),
                        () => r.stub.agentCancel(binding, { schedule_id: brief.schedule_id }),
                        () => r.stub.agentStatus(binding, { run_id: start.run })]) {
      expect((await failure(call)).message).toBe("outside client capability not granted");
    }
    expect(await r.stub.agentStatus(r.bind(), { schedule_id: brief.schedule_id }))
      .toMatchObject({ schedule_id: brief.schedule_id });
  });
});
