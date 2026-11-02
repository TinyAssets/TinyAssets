/**
 * Runs in the owner's object (slice R; ADR-034 decision 4), in workerd, with a recording
 * fake behind the `SANDBOX` interface (`sandbox-fake.ts`): admission in one unit (each of its
 * refusals with its code), the run row, its grant and the sandbox's lease, the start with the
 * run's whole environment, the settle from the runner's end, the cancel through the sandbox,
 * no sandbox at all, simultaneous runs each on their own connection, a run's calls held to
 * its grant, and the `runs` lane. The doors' side is `doors/test/runs.test.ts`.
 */
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { verifyToken } from "../../doors/src/routing-mac";
import { bundledSources, runEnv } from "../../doors/src/sources";
import { PLATFORM_CALLER } from "../src/admission";
import { AGENT_DISABLED, AGENT_UNDEFINED, agentFile } from "../src/agent-file";
import { WriteGate } from "../src/gate";
import { AUTHORITY_CHANGED } from "../src/authority";
import type { Binding } from "../src/owner-do";
import { RUN_STARTS_NONE } from "../src/owner-do";
import { CONVERSATION_BUSY, NO_CONVERSATION, NO_RUN, RUN_ENDED, Runs, UNATTENDED_REFUSED }
  from "../src/runs";
import { SANDBOX_ABSENT, type SandboxStart } from "../src/sandbox";
import { PLATFORM_TIER_ONLY, TIER_RUNS, TIER_UNREAD } from "../src/tier";
import { gated, type Gated, OPENAI } from "./gateway-support";
import { MCP, MUSE, signedIn } from "./ledger-support";
import { ALL_SCOPES, defineAgent, ownerRun, recorder, TIERS } from "./runs-support";
import { drainDue, failure, sqlIn } from "./support";

/** The owner's run of Claude Code on its own sign-in: no key, no connection. */
const SIGNED_IN = Object.freeze({ engine: "claude-code", credential: "vendor_login_in_sandbox",
                                  scopes: [] });

/** Each lease's egress token, as the start that opened it got it. */
const TOKENS = new Map<string, string>();

/** The run's own binding on the egress host, as the binder builds it (`bindRun`). */
function runBinding(r: Gated, start: SandboxStart): Binding {
  if (start.tokens !== null) {
    TOKENS.set(start.lease.lease_id, start.tokens.egress_token);
  }
  const token = verifyToken(env.ROUTING_MAC_KEY, "egress",
                            TOKENS.get(start.lease.lease_id))!.hash;
  return r.bind({ sandbox: { kind: "egress", token, generation: 1 }, run: start.run });
}

/** Claude Code's stream-json events, as its record's matchers read them. */
const INIT = (session: string) => ({ type: "system", subtype: "init", session_id: session });
const RESULT = (text: string) => ({ type: "result", result: text, usage: { output_tokens: 3 } });
const FAILED = (text: string) => ({ type: "result", is_error: true, result: text });

function frames(...events: unknown[]) {
  return events.map((event, i) => ({ seq: i + 1, at: 1_800_000_000 + i, event }));
}

/** The owner's tier set by the platform, as its billing does. */
function tierSet(r: Gated, tier: "free" | "paid") {
  return r.stub.tierSet({ owner: r.name, caller: PLATFORM_CALLER, origin: null }, { tier });
}

/** Make every schedule due now and let the lane's job run. */
async function fireSchedules(r: Gated): Promise<void> {
  await r.sql("UPDATE run_schedules SET next_at = ? WHERE next_at IS NOT NULL",
              Date.now() / 1000 - 1);
  // Any door that changes a lane's rows re-derives the jobs.
  await r.stub.fileMkdir(r.bind(), { path: `/workspace/d-${crypto.randomUUID()}`,
                                     expected_etag: null });
  await drainDue(r.stub);
}

describe("a run", () => {
  it("is admitted in one unit: its row, its grant, the sandbox's lease, and a start with " +
     "its whole environment", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const { run, start } = await ownerRun(r.stub, r.bind(), {
      engine: "claude-code", credential: "api_key", connection, scopes: ["read_files"] });
    expect(run).toMatchObject({ state: "running", agent: "main", engine: "claude-code",
                                credential: "api_key", initiator: "owner", requests: 0,
                                settled_at: null });
    const [lease] = await r.sql("SELECT lease_id, epoch FROM file_space");
    expect(await r.sql("SELECT run, lease_id, epoch, stamp, origin, scopes, connection " +
                         "FROM run_grants")).toEqual([{
      run: run.run_id, lease_id: lease!["lease_id"], epoch: lease!["epoch"], stamp: null,
      origin: null, scopes: "[\"read_files\"]", connection }]);
    expect(await r.sql("SELECT agent, source, session_id, last_run FROM conversations"))
      .toEqual([{ agent: "main", source: "claude-code", session_id: null,
                  last_run: run.run_id }]);
    // The start: the lease it opened, its two tokens and network, and the run's whole
    // environment, the record's own variables and its one connection's key.
    expect(start).toMatchObject({ run: run.run_id, agent: "main", engine: "claude-code",
                                  credential: "api_key", session_id: null,
                                  prompt: "do the thing", unattended: false,
                                  lease: { lease_id: lease!["lease_id"] } });
    expect(start.tokens).not.toBeNull();
    expect(start.network!.network.rules["egress.tinyassets.io"]).toBeDefined();
    const base = `https://egress.tinyassets.io/r/${run.run_id}/c/${connection}/`;
    expect(start.config.env).toMatchObject({
      ...runEnv(bundledSources(), "claude-code", "api_key").env, ANTHROPIC_BASE_URL: base });
    expect(start.config.env["ANTHROPIC_API_KEY"]).toMatch(/^ta_ph_c[0-9a-f]{24}_[0-9a-f]{32}$/);
    expect(start.urls).toEqual({ mcp: `https://egress.tinyassets.io/r/${run.run_id}/mcp`,
                                 events: `https://egress.tinyassets.io/r/${run.run_id}/events` });
    expect(JSON.stringify(start)).not.toContain("sk-good");
    // A second run joins the live lease: no new tokens, its own grant.
    const second = await ownerRun(r.stub, r.bind(), undefined, "main");
    expect(second.start.tokens).toBeNull();
    expect(second.start.network).toBeNull();
    expect(second.start.lease).toEqual(start.lease);
    expect((await r.sql("SELECT COUNT(*) AS n FROM run_grants"))[0]).toEqual({ n: 2 });
  });

  it("is the same run when its request is retried", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const ask = { message: "hi", client_send_id: "same-1" };
    const first = await r.stub.agentSubmit(r.bind({ tiers: TIERS }), ask);
    const again = await r.stub.agentSubmit(r.bind({ tiers: TIERS }), ask);
    expect(again.run_id).toBe(first.run_id);
    expect((await recorder(r.stub)).starts).toHaveLength(1);
  });

  it("continues a conversation of the caller's own, resuming its engine's session", async () => {
    const r = await gated();
    const { run, start } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    // While its run is live, the conversation takes no other.
    expect((await failure(() => ownerRun(r.stub, r.bind(), undefined, "main",
                                         { conversation_id: run.conversation_id })))
      .message).toBe(CONVERSATION_BUSY);
    await r.stub.runEvents(runBinding(r, start), { frames: frames(INIT("sess-1")),
                                                   end: { exit_code: 0 } });
    const next = await ownerRun(r.stub, r.bind(), undefined, "main",
                                { conversation_id: run.conversation_id });
    expect(next.start.session_id).toBe("sess-1");
    expect(next.start.conversation).toBe(run.conversation_id);
    // Another agent's, or no such conversation, is none of this one's.
    await defineAgent(r.stub, r.bind(), "other", SIGNED_IN);
    expect((await failure(() => ownerRun(r.stub, r.bind(), undefined, "other",
                                         { conversation_id: run.conversation_id })))
      .message).toBe(NO_CONVERSATION);
  });
});

describe("admission", () => {
  it("refuses an owner not admitted, and writes nothing", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    await r.stub.admissionSet({ owner: r.name, caller: PLATFORM_CALLER, origin: null },
                              { reason: "billing", suspended: true });
    expect((await failure(() => ownerRun(r.stub, r.bind()))).message)
      .toMatch(/^owner_suspended/);
    expect(await r.sql("SELECT id FROM runs")).toEqual([]);
  });

  it("refuses an agent with no agent.json, or one disabled", async () => {
    const r = await gated();
    expect((await failure(() => ownerRun(r.stub, r.bind()))).message).toBe(AGENT_UNDEFINED);
    expect((await failure(() => ownerRun(r.stub, r.bind(), { ...SIGNED_IN, enabled: false })))
      .message).toBe(AGENT_DISABLED);
    expect(await r.sql("SELECT id FROM runs")).toEqual([]);
  });

  it("holds the tier's simultaneous runs, free 5 and paid 10, from the control record's " +
     "numbers; the owner's tier is the platform's alone", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    for (let i = 0; i < 5; i++) {
      await ownerRun(r.stub, r.bind());
    }
    expect((await failure(() => ownerRun(r.stub, r.bind()))).message)
      .toBe(`${TIER_RUNS} (free: 5)`);
    // No client, request or owner sets the tier: only the platform.
    expect((await failure(() => r.stub.tierSet(r.bind(), { tier: "paid" }))).message)
      .toBe(PLATFORM_TIER_ONLY);
    expect(await tierSet(r, "paid")).toEqual({ tier: "paid" });
    for (let i = 0; i < 5; i++) {
      await ownerRun(r.stub, r.bind());
    }
    expect((await failure(() => ownerRun(r.stub, r.bind()))).message)
      .toBe(`${TIER_RUNS} (paid: 10)`);
    // The founder's edit of the record reaches the next run door at once.
    const edited = { ...TIERS, paid: { runs: 11, storage_bytes: 1 } };
    await r.stub.agentSubmit(r.bind({ tiers: edited }), { message: "x", client_send_id: "e-1" });
    expect((await r.sql("SELECT COUNT(*) AS n FROM runs WHERE state = 'running'"))[0])
      .toEqual({ n: 11 });
    // A record past the ceiling, or malformed, is refused, never guessed.
    expect((await failure(() => r.stub.agentSubmit(
      r.bind({ tiers: { ...TIERS, free: { runs: 1001, storage_bytes: 1 } } }),
      { message: "x", client_send_id: "e-2" }))).name).toBe("Invalid");
  });

  it("refuses a run when the control record held no tier numbers", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    expect((await failure(() => r.stub.agentSubmit(r.bind(), {
      message: "x", client_send_id: "t-1" }))).message).toBe(TIER_UNREAD);
  });

  it("refuses with no sandbox bound, and no run reaches running", async () => {
    // The plain object: production's, with no `SANDBOX` until slice S binds it.
    const owner = `owner-${crypto.randomUUID()}`;
    const stub = env.OWNER.getByName(owner);
    const bind: Binding = { owner, caller: owner, origin: null };
    await defineAgent(stub, bind, "main", SIGNED_IN);
    expect((await failure(() => stub.agentSubmit({ ...bind, tiers: TIERS }, {
      message: "x", client_send_id: "n-1" }))).message).toBe(SANDBOX_ABSENT);
    expect(await sqlIn(stub, "SELECT id FROM runs")).toEqual([]);
    expect(await sqlIn(stub, "SELECT lease_id FROM file_space")).toEqual([{ lease_id: null }]);
  });

  it("refuses an unattended run on a source whose record does not allow it", async () => {
    const r = await gated();
    // Codex on the owner's ChatGPT sign-in: its record refuses unattended hosted use.
    await defineAgent(r.stub, r.bind(), "coder", { engine: "codex",
                                                   credential: "vendor_login_in_sandbox",
                                                   scopes: [] });
    // Attended, it runs; on a schedule, it is refused, the refusal kept on the schedule.
    await ownerRun(r.stub, r.bind(), undefined, "coder");
    const schedule = await r.stub.agentRun(r.bind({ tiers: TIERS }), {
      agent: "coder", input: "nightly", at: Date.now() / 1000 + 3600 });
    await fireSchedules(r);
    expect(await r.sql("SELECT trigger FROM run_schedules")).toEqual([]);
    const notices = (await r.stub.noticeList(r.bind())).notices;
    expect(notices.map((notice) => notice.detail)).toContain(`coder: ${UNATTENDED_REFUSED}`);
    expect((await recorder(r.stub)).starts).toHaveLength(1);
    expect(schedule).toMatchObject({ agent: "coder", trigger: "at" });
  });

  it("refuses a client's run once the client's stamp no longer stands", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", { ...SIGNED_IN, scopes: ALL_SCOPES });
    const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    await r.stub.outsideAgents(client);  // admitted: a free grant
    const { revision } = (await r.stub.grantList(r.bind())).clients
      .find((entry) => entry.client === MUSE.client)!;
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "full",
                                         scopes: [], expected_revision: revision,
                                         name: "Muse" });
    await r.stub.agentRun({ ...client, tiers: TIERS }, { agent: "main", input: "later",
                                                         cron: "0 * * * *" });
    // The client's grant moves without the doors' own hook, as a stale stamp finds it.
    await r.sql("UPDATE outside_clients SET generation = generation + 1 WHERE client = ?",
                MUSE.client);
    await fireSchedules(r);
    expect(await r.sql("SELECT last_error FROM run_schedules"))
      .toEqual([{ last_error: AUTHORITY_CHANGED }]);
    expect((await recorder(r.stub)).starts).toEqual([]);
  });
});

describe("a run's settle", () => {
  it("is succeeded, failed or unknown, from the runner's end, never replayed", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    // Succeeded: its session id and result kept, its grant gone.
    const ok = await ownerRun(r.stub, r.bind());
    expect(await r.stub.runEvents(runBinding(r, ok.start), {
      frames: frames(INIT("sess-ok"), RESULT("all done")), end: { exit_code: 0 } }))
      .toEqual({ accepted: 2, settled: "succeeded" });
    expect(await r.stub.agentStatus(r.bind(), { run_id: ok.run.run_id }))
      .toMatchObject({ state: "succeeded", result: "all done", code: null, exit_code: 0 });
    expect(await r.sql("SELECT session_id FROM conversations WHERE id = ?",
                       ok.run.conversation_id)).toEqual([{ session_id: "sess-ok" }]);
    expect(await r.sql("SELECT run FROM run_grants WHERE run = ?", ok.run.run_id)).toEqual([]);
    // A frame past its settle is refused: the run is no longer live.
    expect((await failure(() => r.stub.runEvents(runBinding(r, ok.start),
                                                 { frames: frames(RESULT("again")) })))
      .message).toBe(RUN_ENDED);
    // Failed: the exit code, or an error event though it exited 0.
    const exit = await ownerRun(r.stub, r.bind());
    await r.stub.runEvents(runBinding(r, exit.start), { frames: [], end: { exit_code: 2 } });
    expect(await r.stub.agentStatus(r.bind(), { run_id: exit.run.run_id }))
      .toMatchObject({ state: "failed", code: "exit", exit_code: 2 });
    const error = await ownerRun(r.stub, r.bind());
    await r.stub.runEvents(runBinding(r, error.start), { frames: frames(FAILED("rate")),
                                                         end: { exit_code: 0 } });
    expect(await r.stub.agentStatus(r.bind(), { run_id: error.run.run_id }))
      .toMatchObject({ state: "failed", code: "engine_error", error: "rate" });
    // Unknown: its runner lost (no heartbeat within the lease's life).
    const lost = await ownerRun(r.stub, r.bind());
    await r.sql("UPDATE file_space SET lease_expires = 1");
    await ownerRun(r.stub, r.bind());
    expect(await r.stub.agentStatus(r.bind(), { run_id: lost.run.run_id }))
      .toMatchObject({ state: "unknown", code: "lost" });
  });

  it("is unknown when the sandbox's start never answered, failed when it refused", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    await r.in((instance) => { instance.recorder.answer = "throw"; });
    const lost = await r.stub.agentSubmit(r.bind({ tiers: TIERS }),
                                          { message: "x", client_send_id: "s-1" });
    expect(lost).toMatchObject({ state: "unknown", code: "start_lost" });
    // In case it did start, it is cancelled too.
    expect((await recorder(r.stub)).cancels.map((cancel) => cancel.run)).toEqual([lost.run_id]);
    await r.in((instance) => { instance.recorder.answer = { started: false, code: "e2b_down" }; });
    const refused = await r.stub.agentSubmit(r.bind({ tiers: TIERS }),
                                             { message: "x", client_send_id: "s-2" });
    expect(refused).toMatchObject({ state: "failed", code: "e2b_down" });
    // Neither holds the lease: the last live run's settle gave it back.
    expect(await r.sql("SELECT lease_id FROM file_space")).toEqual([{ lease_id: null }]);
  });
});

describe("a run's cancel", () => {
  it("reaches the sandbox, and the run settles from the runner's end as cancelled",
     async () => {
    const r = await gated();
    const { run, start } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    const cancelled = await r.stub.agentCancel(r.bind(), { run_id: run.run_id });
    expect(cancelled).toMatchObject({ run_id: run.run_id, state: "running" });
    expect((await recorder(r.stub)).cancels).toEqual([{ run: run.run_id,
                                                        lease_id: start.lease.lease_id }]);
    await r.stub.runEvents(runBinding(r, start), { frames: [], end: { exit_code: null } });
    expect(await r.stub.agentStatus(r.bind(), { run_id: run.run_id }))
      .toMatchObject({ state: "failed", code: "cancelled" });
    // A settled run cancels nothing more.
    await r.stub.agentCancel(r.bind(), { run_id: run.run_id });
    expect((await recorder(r.stub)).cancels).toHaveLength(1);
  });
});

describe("simultaneous runs", () => {
  it("each carry their own connection, and egress refuses the other's", async () => {
    const r = await gated();
    const a = await r.connect("sk-good");
    const b = await r.connect("sk-good-2", OPENAI, "B");
    const onA = await ownerRun(r.stub, r.bind(), { engine: "pi", credential: "api_key",
                                                    connection: a, scopes: [] }, "on-a");
    const onB = await ownerRun(r.stub, r.bind(), { engine: "pi", credential: "api_key",
                                                    connection: b, scopes: [] }, "on-b");
    expect(onB.start.lease).toEqual(onA.start.lease);
    expect(await r.sql("SELECT run, connection FROM run_grants ORDER BY run")).toEqual(
      [{ run: onA.run.run_id, connection: a }, { run: onB.run.run_id, connection: b }]
        .sort((x, y) => (x.run < y.run ? -1 : 1)));
    // Each run's environment names its own connection alone.
    expect(JSON.stringify(onA.start.config.env)).not.toContain(b);
    expect(JSON.stringify(onB.start.config.env)).not.toContain(a);
    await r.in((instance) => instance.sandbox.networkApplied({
      ...onA.start.lease, generation: 1 }));
    const egress = r.bind({ sandbox: { kind: "egress", generation: 1, token: verifyToken(
      env.ROUTING_MAC_KEY, "egress", onA.start.tokens!.egress_token)!.hash } });
    const call = (run: string, connection: string) => r.stub.egressOpen(egress, {
      run, connection, method: "GET", path: "/v1/models", bytes_hint: null, model: null,
      tier: null });
    expect(await call(onA.run.run_id, b)).toMatchObject({ ok: false,
                                                          code: "egress_run_connection" });
    expect(await call(onB.run.run_id, a)).toMatchObject({ ok: false,
                                                          code: "egress_run_connection" });
    expect(await call(onA.run.run_id, a)).toMatchObject({ ok: true });
    expect(await call(onB.run.run_id, b)).toMatchObject({ ok: true });
    // A run settled spends no more, though its sandbox's lease is live for the other.
    await r.stub.runEvents(runBinding(r, onA.start), { frames: [], end: { exit_code: 0 } });
    expect(await call(onA.run.run_id, a)).toMatchObject({ ok: false, code: "egress_run_closed" });
    expect(await call(onB.run.run_id, b)).toMatchObject({ ok: true });
  });
});

describe("a client's run doors", () => {
  it("hold a scoped client to converse and run_graph where its agent acts", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", { ...SIGNED_IN, scopes: ALL_SCOPES });
    const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    await r.stub.outsideAgents(client);
    const { revision } = (await r.stub.grantList(r.bind())).clients
      .find((entry) => entry.client === MUSE.client)!;
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "scoped",
                                         scopes: [{ universe: "alpha", agent: "main",
                                                    capability: "read_graph" }],
                                         expected_revision: revision, name: "Muse" });
    const { card_id, digest } = await r.stub.registerPropose(client, {
      command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
      recipe_id: null });
    await r.stub.registerAgent(r.session(), { card_id, digest });
    expect((await failure(() => r.stub.agentSubmit({ ...client, tiers: TIERS }, {
      message: "hi", client_send_id: "n-1" }))).message)
      .toBe("outside client capability not granted");
    expect((await failure(() => r.stub.agentRun({ ...client, tiers: TIERS }, {
      agent: "main", input: "x" }))).message).toBe("outside client capability not granted");
    expect((await recorder(r.stub)).starts).toEqual([]);
  });
});

describe("the admission unit", () => {
  it("refuses an owner not admitted itself, whatever reached it (the lane's race)",
     async () => {
    const r = await gated();
    const refused = await r.in((_instance, state) => {
      const gate = new WriteGate(state.storage);
      const never = () => {
        throw new Error("admission went past the owner's admission");
      };
      const runs = new Runs(gate.storage, {
        unadmitted: () => "owner_suspended: the owner's account is suspended (billing)",
        sandboxBound: () => true, sources: never, usable: never, egressible: never,
        standing: never, tier: never, caps: never, spent: never,
        lease: { live: never, open: never, release: never, renew: never },
        ended: never, leaseEnded: never });
      return gate.write(() => runs.admit({
        initiator: "schedule", stamp: null, origin: null, agent: "main",
        file: agentFile(JSON.stringify(SIGNED_IN)), conversation: null, request: null,
        tiers: TIERS, unattended: true }, Date.now() / 1000, never))
        .then(() => "admitted", (error: Error) => error.message);
    });
    expect(refused).toBe("owner_suspended: the owner's account is suspended (billing)");
    expect(await r.sql("SELECT id FROM runs")).toEqual([]);
  });
});

describe("a run's own calls", () => {
  it("are held to its grant's scopes, and never the owner itself", async () => {
    const r = await gated();
    const reading = await ownerRun(r.stub, r.bind(), { ...SIGNED_IN, scopes: ["read_files"] },
                                   "reader");
    const bare = await ownerRun(r.stub, r.bind(), SIGNED_IN, "bare");
    const asReader = runBinding(r, reading.start);
    expect(await r.stub.fileStat(asReader, { path: "/workspace" }))
      .toMatchObject({ entry: { kind: "dir" } });
    expect((await failure(() => r.stub.fileStat(runBinding(r, bare.start),
                                                { path: "/workspace" }))).message)
      .toBe("outside client capability not granted");
    // Never the owner's own act, and never a run door.
    expect((await failure(() => r.stub.policyRead(asReader))).message)
      .toBe("only the owner may read its policy");
    expect((await failure(() => r.stub.agentSubmit(asReader, {
      message: "x", client_send_id: "inner" }))).message).toBe(RUN_STARTS_NONE);
    // Another run's id under this lease is refused, as is one settled.
    expect((await failure(() => r.stub.fileStat({ ...asReader, run: `run_${"f".repeat(32)}` },
                                                { path: "/workspace" }))).message)
      .toBe(RUN_ENDED);
  });

  it("of a client's run recheck its stamp at every call: a grant moved ends the run",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", { ...SIGNED_IN, scopes: ALL_SCOPES });
    const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    await r.stub.outsideAgents(client);
    const { revision } = (await r.stub.grantList(r.bind())).clients
      .find((entry) => entry.client === MUSE.client)!;
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "full",
                                         scopes: [], expected_revision: revision,
                                         name: "Muse" });
    const run = await r.stub.agentSubmit({ ...client, tiers: TIERS },
                                         { message: "hi", client_send_id: "g-1" });
    const asRun = runBinding(r, (await recorder(r.stub)).starts.at(-1)!);
    expect(await r.stub.fileStat(asRun, { path: "/workspace" }))
      .toMatchObject({ entry: { kind: "dir" } });
    // The client's grant moves without the doors' own hook: the run's next call finds it.
    await r.sql("UPDATE outside_clients SET generation = generation + 1 WHERE client = ?",
                MUSE.client);
    expect((await failure(() => r.stub.fileStat(asRun, { path: "/workspace" }))).message)
      .toBe(AUTHORITY_CHANGED);
    expect((await recorder(r.stub)).cancels.map((cancel) => cancel.run)).toEqual([run.run_id]);
    expect((await failure(() => r.stub.fileStat(asRun, { path: "/workspace" }))).message)
      .toBe(RUN_ENDED);
  });

  it("of a client's run hold only what that client holds, and its grant's change ends " +
     "the run", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", { ...SIGNED_IN, scopes: ALL_SCOPES });
    const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    await r.stub.outsideAgents(client);
    // A scoped grant holding converse and read_graph alone, where its agent acts.
    const { revision } = (await r.stub.grantList(r.bind())).clients
      .find((entry) => entry.client === MUSE.client)!;
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "scoped",
                                         scopes: [{ universe: "alpha", agent: "main",
                                                    capability: "converse" },
                                                  { universe: "alpha", agent: "main",
                                                    capability: "read_graph" }],
                                         expected_revision: revision, name: "Muse" });
    const { card_id, digest } = await r.stub.registerPropose(client, {
      command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
      recipe_id: null });
    await r.stub.registerAgent(r.session(), { card_id, digest });
    const run = await r.stub.agentSubmit({ ...client, tiers: TIERS },
                                         { message: "hi", client_send_id: "c-1" });
    expect(run).toMatchObject({ state: "running", initiator: "client" });
    expect(await r.sql("SELECT scopes FROM run_grants")).toEqual([
      { scopes: "[\"read_graph\",\"converse\"]" }]);
    // The client reads its own runs only; the owner reads every one.
    expect(await r.stub.agentStatus(client, { run_id: run.run_id }))
      .toMatchObject({ run_id: run.run_id });
    const start = (await recorder(r.stub)).starts.at(-1)!;
    const asRun = runBinding(r, start);
    expect(await r.stub.mailboxRead(asRun, { limit: 1 })).toMatchObject({ messages: [] });
    expect((await failure(() => r.stub.fileStat(asRun, { path: "/workspace" }))).message)
      .toBe("outside client capability not granted");
    // The owner revokes the client: its run's grant goes at once, and its CLI is stopped.
    await r.stub.grantRevoke(r.bind(), { client: MUSE.client });
    expect((await failure(() => r.stub.mailboxRead(asRun, { limit: 1 }))).message)
      .toBe(RUN_ENDED);
    expect((await recorder(r.stub)).cancels.map((cancel) => cancel.run)).toEqual([run.run_id]);
    // Another owner's client never reads this one's runs.
    expect((await failure(() => r.stub.agentStatus(
      r.bind({ origin: { client: "e".repeat(64), family: "f".repeat(64) }, source: MCP,
               authenticated_at: signedIn() }), { run_id: run.run_id }))).message)
      .toBe(NO_RUN);
  });
});

describe("the runs lane", () => {
  it("starts a due schedule's run only when it has work, and nothing on an empty poll",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    // No schedule: the lane has no job, and nothing starts.
    await drainDue(r.stub);
    expect(await r.sql("SELECT id FROM cf_agents_jobs WHERE id = 'owner-schedule/runs'"))
      .toEqual([]);
    expect((await recorder(r.stub)).starts).toEqual([]);
    // A cron that runs only on the owner's mail: due, with none, it starts nothing.
    await r.stub.agentRun(r.bind({ tiers: TIERS }), { agent: "main", input: "triage",
                                                      cron: "0 * * * *", if_mail: true });
    await fireSchedules(r);
    expect((await recorder(r.stub)).starts).toEqual([]);
    expect((await r.sql("SELECT next_at FROM run_schedules"))[0]!["next_at"])
      .toBeGreaterThan(Date.now() / 1000);
    // A plain cron is its own work: due, it starts one run, unattended.
    await r.stub.agentRun(r.bind({ tiers: TIERS }), { agent: "main", input: "report",
                                                      cron: "30 * * * *" });
    await r.sql("UPDATE run_schedules SET next_at = NULL WHERE if_mail = 1");
    await fireSchedules(r);
    const starts = (await recorder(r.stub)).starts;
    expect(starts.map((start) => [start.prompt, start.unattended])).toEqual([["report", true]]);
    expect(await r.stub.agentStatus(r.bind(), { run_id: starts[0]!.run }))
      .toMatchObject({ initiator: "schedule" });
  });

  it("runs an agent on the owner's mail when mail comes, the doorbell rung by the post",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const schedule = await r.stub.agentRun(r.bind({ tiers: TIERS }), {
      agent: "main", input: "read your mail", on_mail: true });
    expect(schedule).toMatchObject({ trigger: "mail", next_at: null });
    // An outside agent posts to the owner: the post rings the lane.
    const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    const { card_id, digest } = await r.stub.registerPropose(client, {
      command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
      recipe_id: null });
    const agent = await r.stub.registerAgent(r.session(), { card_id, digest });
    await r.stub.roomPost(client, { room_id: `dm:${agent.agent_id}`, client_message_id: "m-1",
                                    body: "hello", parent_id: null });
    expect((await r.sql("SELECT next_at FROM run_schedules"))[0]!["next_at"]).not.toBeNull();
    await drainDue(r.stub);
    const starts = (await recorder(r.stub)).starts;
    expect(starts.map((start) => [start.prompt, start.unattended]))
      .toEqual([["read your mail", true]]);
    expect(await r.stub.agentStatus(r.bind(), { run_id: starts[0]!.run }))
      .toMatchObject({ initiator: "mail" });
    expect(await r.sql("SELECT next_at FROM run_schedules")).toEqual([{ next_at: null }]);
  });

  it("cancels a schedule of the caller's", async () => {
    const r = await gated();
    const schedule = await r.stub.agentRun(r.bind(), {
      agent: "main", input: "x", cron: "0 * * * *" }) as { schedule_id: string };
    expect(await r.stub.agentCancel(r.bind(), { schedule_id: schedule.schedule_id }))
      .toEqual({ schedule_id: schedule.schedule_id, cancelled: true });
    expect(await r.sql("SELECT id FROM run_schedules")).toEqual([]);
  });
});


