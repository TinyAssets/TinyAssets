/**
 * Runs' authority and liveness (slice R's review, round 1), in workerd: one sandbox an
 * authority, each run's own secret and the runner's, a client's revoke settling its runs at
 * once, a client's schedules, the runs' rows' bound under churn, a cancel's revoke, each
 * run's own heartbeat, the tier numbers' hand-off, and the cases the reviewer's mutants
 * found untested. The rest of the runs' tests are `runs.test.ts`.
 */
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { socketRequest } from "../../doors/src/binder";
import { verifyToken } from "../../doors/src/routing-mac";
import { PLATFORM_CALLER } from "../src/admission";
import { AGENT_DISABLED } from "../src/agent-file";
import { AGENT_CHANGED, type Binding } from "../src/owner-do";
import { Files } from "../src/files";
import { AGENTS_OWNER_ONLY } from "../src/paths";
import { RUNS_KEPT } from "../src/run-rows";
import { EVENT_FRAMES_MAX, EVENTS_BUSY, EVENTS_IN_FLIGHT, FRAME_UNITS_MAX, PostGate }
  from "../src/run-events";
import { NO_CONVERSATION, RUN_ENDED, SIGNIN_REQUIRED } from "../src/runs";
import { SANDBOX_ABSENT } from "../src/sandbox";
import { TIER_RUNS, TIER_UNREAD } from "../src/tier";
import { gated, type Gated } from "./gateway-support";
import { MCP, MUSE, signedIn } from "./ledger-support";
import {
  ALL_SCOPES, defineAgent, hashed, ownerRun, recorder, runBinding, runnerBinding, runnerSandbox,
  sandboxOf, secretOf, startOf, syncOf, TIERS,
} from "./runs-support";
import { drainDue, failure, sqlIn } from "./support";

const SIGNED_IN = Object.freeze({ engine: "claude-code", credential: "vendor_login_in_sandbox",
                                  scopes: [] });
const text = (value: string) => new TextEncoder().encode(value);

/** Muse admitted on a full grant: the outside client whose runs these tests start. */
async function fullClient(r: Gated): Promise<Binding> {
  const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
  await r.stub.outsideAgents(client);
  const { revision } = (await r.stub.grantList(r.bind())).clients
    .find((entry) => entry.client === MUSE.client)!;
  await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "full",
                                       scopes: [], expected_revision: revision, name: "Muse" });
  return client;
}

/**
 * The owner's agent on its own key: what an outside client's run spends, its sandbox
 * holding no login of the owner's (a login is never copied between sandboxes).
 */
async function keyed(r: Gated, extra: Record<string, unknown> = {}) {
  return { engine: "claude-code", credential: "api_key", connection: await r.connect("sk-good"),
           scopes: ALL_SCOPES, ...extra };
}

/** Any change to a lane's rows re-derives the jobs; then the due ones run. */
async function laneRuns(r: Gated): Promise<void> {
  await r.stub.fileMkdir(r.bind(), { path: `/workspace/d-${crypto.randomUUID()}`,
                                     expected_etag: null });
  await drainDue(r.stub);
}

const notices = async (r: Gated) =>
  (await r.stub.noticeList(r.bind())).notices.map((notice) => notice.detail);

describe("one sandbox an authority (review H-1)", () => {
  it("never puts a client's run in the owner's sandbox, nor lets it act as the owner's run",
     async () => {
    const r = await gated();
    const file = await keyed(r);
    await defineAgent(r.stub, r.bind(), "main", file);
    const owners = await ownerRun(r.stub, r.bind(), undefined, "main");
    const client = await fullClient(r);
    const run = await r.stub.agentSubmit({ ...client, tiers: TIERS },
                                         { message: "hi", client_send_id: "x-1" });
    const start = await startOf(r.stub, run.run_id);
    // Its own sandbox: its own lease and tokens, never the owner's.
    expect(start.authority).toBe(MUSE.client);
    expect(owners.start.authority).toBe("owner");
    expect(start.lease.lease_id).not.toBe(owners.start.lease.lease_id);
    expect(start.tokens).not.toBeNull();
    expect((await r.sql("SELECT authority FROM sandbox_leases ORDER BY authority"))
      .map((row) => row["authority"])).toEqual([MUSE.client, "owner"].sort());
    // The client's process names the owner's run: under its own sandbox's token, with its
    // own secret, the owner's grant is never reached; nor can it settle the owner's run.
    expect((await failure(() => r.stub.fileStat({ ...runBinding(r, start),
                                                  run: owners.run.run_id },
                                                { path: "/workspace" }))).message)
      .toBe(RUN_ENDED);
    expect((await failure(() => r.stub.runEvents(
      { ...runnerBinding(r, start), run: owners.run.run_id },
      { frames: [{ seq: 1, at: 1, event: { type: "result", result: "forged" } }],
        end: { exit_code: 0 } }))).message).toBe(RUN_ENDED);
    // Even the owner's run's own secret, were it to leak, acts as it only from the owner's
    // sandbox: the run is held to the lease it runs under.
    expect((await failure(() => r.stub.fileStat(
      sandboxOf(r, start, { secret: hashed(secretOf(owners.start)) }, owners.run.run_id),
      { path: "/workspace" }))).message).toBe(RUN_ENDED);
    expect(await r.stub.fileStat(runBinding(r, owners.start), { path: "/workspace" }))
      .toMatchObject({ entry: { kind: "dir" } });
    expect(await r.stub.agentStatus(r.bind(), { run_id: owners.run.run_id }))
      .toMatchObject({ state: "running", result: null });
    // The tier counts across both sandboxes.
    expect(await r.sql("SELECT COUNT(*) AS n FROM runs WHERE state = 'running'"))
      .toEqual([{ n: 2 }]);
  });

  it("takes a run's call only with its own secret, and the runner's reports only with the " +
     "runner's", async () => {
    const r = await gated();
    const key = await r.connect("sk-good");
    const keyed = await ownerRun(r.stub, r.bind(), { engine: "claude-code",
                                                      credential: "api_key", connection: key,
                                                      scopes: ["read_files"] }, "keyed");
    const plan = await ownerRun(r.stub, r.bind(), { ...SIGNED_IN, scopes: ["read_files"] },
                                "plan");
    expect(plan.start.lease).toEqual(keyed.start.lease);
    await r.in((instance) => instance.sandbox.networkApplied({
      ...keyed.start.lease, generation: 1 }));
    // The run id alone, or another run's secret, authorizes nothing.
    for (const presented of [{}, { secret: hashed(secretOf(plan.start)) }]) {
      expect((await failure(() => r.stub.fileStat(
        sandboxOf(r, keyed.start, presented, keyed.run.run_id), { path: "/workspace" })))
        .message).toBe(RUN_ENDED);
    }
    // A subscription run's process naming the keyed run spends nothing on its key.
    const open = (run: string, secret: string) => r.stub.egressOpen(
      sandboxOf(r, keyed.start, { secret: hashed(secret) }),
      { run, connection: key, method: "GET", path: "/v1/models", bytes_hint: null,
        model: null, tier: null });
    expect(await open(keyed.run.run_id, secretOf(plan.start)))
      .toMatchObject({ ok: false, code: "egress_run_closed" });
    expect(await open(plan.run.run_id, secretOf(plan.start)))
      .toMatchObject({ ok: false, code: "egress_run_connection" });
    expect(await open(keyed.run.run_id, secretOf(keyed.start))).toMatchObject({ ok: true });
    // A CLI, holding its own run's secret but not the runner's, settles nothing and reports
    // no sign-in.
    const asCli = sandboxOf(r, keyed.start, { secret: hashed(secretOf(keyed.start)) },
                            keyed.run.run_id);
    expect((await failure(() => r.stub.runEvents(asCli, { frames: [], end: { exit_code: 0 } })))
      .message).toBe(RUN_ENDED);
    expect((await failure(() => r.stub.signinReport(
      sandboxOf(r, keyed.start, { secret: hashed(secretOf(keyed.start)) }),
      { signins: [{ engine: "claude-code", signed_in: false }] }))).message)
      .toBe("egress_lease_closed");
    expect((await failure(() => r.stub.runEvents(
      sandboxOf(r, keyed.start, { runner: "0".repeat(64) }, keyed.run.run_id),
      { frames: [], end: { exit_code: 0 } }))).message).toBe(RUN_ENDED);
    expect(await r.stub.agentStatus(r.bind(), { run_id: keyed.run.run_id }))
      .toMatchObject({ state: "running" });
    // The runner's secret settles it.
    await r.stub.runEvents(runnerBinding(r, keyed.start), { frames: [], end: { exit_code: 0 } });
    expect(await r.stub.agentStatus(r.bind(), { run_id: keyed.run.run_id }))
      .toMatchObject({ state: "succeeded" });
  });

  it("syncs a client's sandbox as that client, never into the owner's trees, and keeps its " +
     "own sign-ins on its own lease", async () => {
    const r = await gated();
    const file = await keyed(r);
    await defineAgent(r.stub, r.bind(), "main", file);
    const client = await fullClient(r);
    const run = await r.stub.agentSubmit({ ...client, tiers: TIERS },
                                         { message: "hi", client_send_id: "s-1" });
    const start = await startOf(r.stub, run.run_id);
    const commit = (path: string) => r.stub.fileSyncCommit(syncOf(r, start), {
      ...start.lease, ops: [{ op: "write", path, base: null, inline: text("s") }] });
    expect((await commit("/workspace/notes.txt")).outcomes)
      .toMatchObject([{ outcome: "committed" }]);
    expect((await r.stub.fileStat(r.bind(), { path: "/workspace/notes.txt" })).entry)
      .toMatchObject({ writer: `client:${MUSE.client}` });
    expect((await commit("/workspace/skills/x.md")).outcomes)
      .toMatchObject([{ outcome: "refused", reason: AGENTS_OWNER_ONLY }]);
    // Its own runner reports its own sandbox's sign-ins, kept on its lease alone.
    expect(await r.stub.signinReport(runnerSandbox(r, start), {
      signins: [{ engine: "claude-code", signed_in: true }] }))
      .toEqual({ recorded: 1, lapsed: [] });
    expect(await r.sql("SELECT authority, signins FROM sandbox_leases ORDER BY authority"))
      .toEqual([{ authority: MUSE.client, signins: "claude-code" }]);
    expect(await r.sql("SELECT engine FROM engine_signins")).toEqual([]);
    // Its grant moved under its live run (no doors' hook), its sync stops at once.
    await r.sql("UPDATE outside_clients SET generation = generation + 1 WHERE client = ?",
                MUSE.client);
    expect((await failure(() => commit("/workspace/moved.txt"))).message).toMatch(/fenced/);
    // Its grant revoked, its sandbox's sync is fenced: no live run holds its authority.
    await r.stub.grantRevoke(r.bind(), { client: MUSE.client });
    expect((await failure(() => commit("/workspace/later.txt"))).message)
      .toMatch(/fenced/);
  });

  it("opens a client's sandbox without ending the owner's own streams", async () => {
    const r = await gated();
    const key = await r.connect("sk-good");
    await defineAgent(r.stub, r.bind(), "main", { engine: "claude-code", credential: "api_key",
                                                  connection: key, scopes: ALL_SCOPES });
    const keyed = await ownerRun(r.stub, r.bind(), { engine: "claude-code",
                                                      credential: "api_key", connection: key,
                                                      scopes: [] }, "keyed");
    await r.in((instance) => instance.sandbox.networkApplied({
      ...keyed.start.lease, generation: 1 }));
    const opened = await r.stub.egressOpen(
      sandboxOf(r, keyed.start, { secret: hashed(secretOf(keyed.start)) }),
      { run: keyed.run.run_id, connection: key, method: "GET", path: "/v1/models",
        bytes_hint: null, model: null, tier: null });
    if (!opened.ok) {
      throw new Error(`refused ${opened.code}`);
    }
    const client = await fullClient(r);
    await r.stub.agentSubmit({ ...client, tiers: TIERS }, { message: "hi", client_send_id: "o-1" });
    const ended = await Promise.race([
      opened.ended.getReader().read().then(() => true, () => true),
      new Promise<boolean>((resolve) => setTimeout(() => resolve(false), 300))]);
    expect(ended).toBe(false);
  });

  it("never joins a lease whose renewal lapsed, its tokens with it: its runs are lost, a " +
     "fresh one opens", async () => {
    const r = await gated();
    const first = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    // Its heartbeat stopped: the lease's life, and its tokens', is over.
    await r.sql("UPDATE sandbox_leases SET lease_expires = 1");
    const second = await ownerRun(r.stub, r.bind(), undefined, "main");
    expect(second.start.tokens).not.toBeNull();
    expect(second.start.lease.lease_id).not.toBe(first.start.lease.lease_id);
    expect(await r.stub.agentStatus(r.bind(), { run_id: first.run.run_id }))
      .toMatchObject({ state: "unknown", code: "lost" });
  });
});

describe("the runner's events post (review M-4)", () => {
  it("is held to its frame bounds at the object too", async () => {
    const r = await gated();
    const { start } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    const many = Array.from({ length: EVENT_FRAMES_MAX + 1 },
                            (_, i) => ({ seq: i + 1, at: 1, event: {} }));
    expect((await failure(() => r.stub.runEvents(runnerBinding(r, start), { frames: many })))
      .name).toBe("Invalid");
    const long = [{ seq: 1, at: 1, event: { text: "x".repeat(FRAME_UNITS_MAX) } }];
    expect((await failure(() => r.stub.runEvents(runnerBinding(r, start), { frames: long })))
      .name).toBe("Invalid");
    expect(await r.stub.runEvents(runnerBinding(r, start), {
      frames: many.slice(0, EVENT_FRAMES_MAX) })).toMatchObject({ accepted: EVENT_FRAMES_MAX });
  });
});

describe("a sandbox's own sign-ins (review round 2, H; ADR-031 decision 2)", () => {
  it("never run a client's run on the owner's sign-in: auto takes the owner's key, and an " +
     "agent naming the sign-in is started only where its own sandbox signed in", async () => {
    const r = await gated();
    const key = await r.connect("sk-good");
    // The owner signs in inside its own sandbox.
    const own = await ownerRun(r.stub, r.bind(), { engine: "claude-code", credential: "api_key",
                                                    connection: key, scopes: [] }, "keyed");
    await r.stub.signinReport(runnerSandbox(r, own.start), {
      signins: [{ engine: "claude-code", signed_in: true }] });
    await defineAgent(r.stub, r.bind(), "auto", { engine: "auto", scopes: ALL_SCOPES });
    await defineAgent(r.stub, r.bind(), "plan", { ...SIGNED_IN, scopes: ALL_SCOPES });
    // The owner's own auto run uses its plan; a client's, in the client's sandbox, its key.
    expect((await ownerRun(r.stub, r.bind(), undefined, "auto")).run)
      .toMatchObject({ credential: "vendor_login_in_sandbox" });
    const client = await fullClient(r);
    // A cold client sandbox: the named agent is admitted (its sign-in unknown, as the
    // owner's own path admits one), and the sandbox's start finds no login there.
    await r.in((instance) => {
      instance.recorder.answer = { started: false, code: SIGNIN_REQUIRED };
    });
    const cold = await r.stub.agentSubmit({ ...client, tiers: TIERS }, {
      message: "hi", client_send_id: "p-0", agent_id: "plan" });
    expect(cold).toMatchObject({ state: "failed", code: SIGNIN_REQUIRED,
                                 credential: "vendor_login_in_sandbox" });
    expect((await startOf(r.stub, cold.run_id)).authority).toBe(MUSE.client);
    expect(await notices(r)).toContain(`plan: sign in to claude-code inside ${MUSE.client}'s ` +
                                       "sandbox, through the vendor's own flow");
    expect(await r.sql("SELECT authority FROM sandbox_leases WHERE authority != 'owner'"))
      .toEqual([]);
    await r.in((instance) => {
      instance.recorder.answer = { started: true };
    });
    const viaAuto = await r.stub.agentSubmit({ ...client, tiers: TIERS }, {
      message: "hi", client_send_id: "a-1", agent_id: "auto" });
    expect(viaAuto).toMatchObject({ state: "running", credential: "api_key" });
    expect((await startOf(r.stub, viaAuto.run_id)).authority).toBe(MUSE.client);
    // Every client start on a sign-in is its own sandbox's: the owner's never reaches it.
    expect((await recorder(r.stub)).starts
      .filter((start) => start.authority !== "owner"
                         && start.credential === "vendor_login_in_sandbox")
      .map((start) => start.run)).toEqual([cold.run_id]);
  });

  it("run a client's run on that client's sandbox's own sign-in, while it holds one",
     async () => {
    const r = await gated();
    const key = await r.connect("sk-good");
    await defineAgent(r.stub, r.bind(), "auto", { engine: "auto", scopes: ALL_SCOPES });
    await defineAgent(r.stub, r.bind(), "plan", { ...SIGNED_IN, scopes: ALL_SCOPES });
    const client = await fullClient(r);
    const submit = (agent: string, id: string) => r.stub.agentSubmit(
      { ...client, tiers: TIERS }, { message: "hi", client_send_id: id, agent_id: agent });
    // The client's first run, on the owner's key, opens its sandbox.
    const first = await submit("auto", "a-1");
    expect(first).toMatchObject({ credential: "api_key" });
    const start = await startOf(r.stub, first.run_id);
    void key;
    // The owner signs in inside the client's sandbox (the vendor's own flow there); its
    // runner reports it.
    expect(await r.stub.signinReport(runnerSandbox(r, start), {
      signins: [{ engine: "claude-code", signed_in: true }] }))
      .toEqual({ recorded: 1, lapsed: [] });
    const planned = await submit("plan", "p-1");
    expect(planned).toMatchObject({ state: "running", credential: "vendor_login_in_sandbox" });
    expect((await startOf(r.stub, planned.run_id)).lease).toEqual(start.lease);
    // Auto prefers that plan to the key of its provider, in that sandbox.
    expect(await submit("auto", "a-2")).toMatchObject({ credential: "vendor_login_in_sandbox" });
    // Its sign-in lapses: a notice; auto takes the key again, and the named agent's start
    // finds no login there (never the owner's used).
    expect(await r.stub.signinReport(runnerSandbox(r, start), {
      signins: [{ engine: "claude-code", signed_in: false }] }))
      .toEqual({ recorded: 1, lapsed: ["claude-code"] });
    expect(await notices(r)).toContain(
      `claude-code: sign in again inside ${MUSE.client}'s sandbox`);
    expect(await submit("auto", "a-3")).toMatchObject({ credential: "api_key" });
    await r.in((instance) => {
      instance.recorder.answer = { started: false, code: SIGNIN_REQUIRED };
    });
    expect(await submit("plan", "p-2"))
      .toMatchObject({ state: "failed", code: SIGNIN_REQUIRED });
  });
});

describe("a client's sandbox's files (review round 2, M2)", () => {
  it("always hold its runs' agents' own definitions, and the rest only as the client reads",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", await keyed(r));
    await defineAgent(r.stub, r.bind(), "other", SIGNED_IN);
    await r.stub.fileMkdir(r.bind(), { path: "/workspace/skills", expected_etag: null });
    await r.stub.fileWrite(r.bind(), { path: "/workspace/skills/s.md", expected_etag: null,
                                       content: text("a skill") });
    await r.stub.fileWrite(r.bind(), { path: "/workspace/notes.txt", expected_etag: null,
                                       content: text("the owner's") });
    // A connector client holding converse alone, where its agent acts.
    const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    await r.stub.outsideAgents(client);
    const { revision } = (await r.stub.grantList(r.bind())).clients
      .find((entry) => entry.client === MUSE.client)!;
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "scoped",
                                         scopes: [{ universe: "alpha", agent: "main",
                                                    capability: "converse" }],
                                         expected_revision: revision, name: "Muse" });
    const { card_id, digest } = await r.stub.registerPropose(client, {
      command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
      recipe_id: null });
    await r.stub.registerAgent(r.session(), { card_id, digest });
    const run = await r.stub.agentSubmit({ ...client, tiers: TIERS },
                                         { message: "hi", client_send_id: "f-1" });
    const start = await startOf(r.stub, run.run_id);
    const { changes } = await r.stub.fileChanges(syncOf(r, start), { after: 0, limit: 100 });
    const paths = changes.map((change) => change.path);
    expect(paths).toContain("/workspace/agents/main/agent.json");
    expect(paths).toContain("/workspace/skills/s.md");
    expect(paths.filter((path) => path === "/workspace/notes.txt"
                                  || path.startsWith("/workspace/agents/other"))).toEqual([]);
    // It writes nothing back: the client holds no write_files.
    expect((await failure(() => r.stub.fileSyncCommit(syncOf(r, start), {
      ...start.lease, ops: [{ op: "write", path: "/workspace/x.txt", base: null,
                              inline: text("x") }] }))).message)
      .toBe("outside client capability not granted");
  });
});

describe("a client's sandbox's readable set (review round 3, M1)", () => {
  it("sends its sync agent back to the feed's start when it grows", async () => {
    const r = await gated();
    const file = await keyed(r);
    await defineAgent(r.stub, r.bind(), "main", file);
    await defineAgent(r.stub, r.bind(), "other", file);
    await r.stub.fileWrite(r.bind(), { path: "/workspace/agents/other/AGENTS.md",
                                       expected_etag: null, content: text("other's rules") });
    // A converse-only client, on both agents.
    const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    await r.stub.outsideAgents(client);
    const { revision } = (await r.stub.grantList(r.bind())).clients
      .find((entry) => entry.client === MUSE.client)!;
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "scoped",
      scopes: ["main", "other"].map((agent) => ({ universe: "alpha", agent,
                                                  capability: "converse" })),
      expected_revision: revision, name: "Muse" });
    const { card_id, digest } = await r.stub.registerPropose(client, {
      command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
      recipe_id: null });
    await r.stub.registerAgent(r.session(), { card_id, digest });
    const run = await r.stub.agentSubmit({ ...client, tiers: TIERS },
                                         { message: "hi", client_send_id: "m-1" });
    const start = await startOf(r.stub, run.run_id);
    const first = await r.stub.fileChanges(syncOf(r, start), { after: 0, limit: 1000 });
    expect(first.reset).toBe(false);
    expect(first.changes.some((change) => change.path.startsWith("/workspace/agents/other")))
      .toBe(false);
    // Read again with nothing changed: no reset.
    expect((await r.stub.fileChanges(syncOf(r, start), { after: first.cursor, limit: 1000 }))
      .reset).toBe(false);
    // A run of `other` starts in the same sandbox: the next read starts over, and from its
    // start the feed now holds `other`'s definition.
    const second = await r.stub.agentSubmit({ ...client, tiers: TIERS }, {
      message: "hi", client_send_id: "o-1", agent_id: "other" });
    expect((await startOf(r.stub, second.run_id)).lease).toEqual(start.lease);
    const next = await r.stub.fileChanges(syncOf(r, start), { after: first.cursor, limit: 1000 });
    expect(next.reset).toBe(true);
    const again = await r.stub.fileChanges(syncOf(r, start), { after: 0, limit: 1000 });
    expect(again.changes.map((change) => change.path))
      .toContain("/workspace/agents/other/AGENTS.md");
    expect(again.reset).toBe(false);
  });
});

describe("the runners' events posts (review round 3, M2)", () => {
  it("are held at most EVENTS_IN_FLIGHT at once; one more is refused to post again",
     () => {
    const gate = new PostGate();
    for (let i = 0; i < EVENTS_IN_FLIGHT; i++) {
      gate.enter();
    }
    expect(() => gate.enter()).toThrow(EVENTS_BUSY);
    gate.leave();
    gate.enter();
    expect(gate.held).toBe(EVENTS_IN_FLIGHT);
  });

  it("give back the room of a post refused for any other reason", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const ended = await ownerRun(r.stub, r.bind(), undefined, "main");
    const live = await ownerRun(r.stub, r.bind(), undefined, "main");
    await r.stub.runEvents(runnerBinding(r, ended.start), { frames: [], end: { exit_code: 0 } });
    for (let i = 0; i <= EVENTS_IN_FLIGHT; i++) {
      expect((await failure(() => r.stub.runEvents(runnerBinding(r, ended.start),
                                                   { frames: [] }))).message).toBe(RUN_ENDED);
    }
    expect(await r.stub.runEvents(runnerBinding(r, live.start), { frames: [] }))
      .toEqual({ accepted: 0, settled: null });
  });

  it("all land, however many runners post at once, each retrying a busy answer",
     async () => {
    const r = await gated();
    const tiers = { free: { runs: 20, storage_bytes: 1 }, paid: { runs: 20, storage_bytes: 1 } };
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const runs = [];
    for (let i = 0; i < EVENTS_IN_FLIGHT + 4; i++) {
      runs.push((await r.stub.agentSubmit(r.bind({ tiers }), { message: "x",
                                                               client_send_id: `p-${i}` })));
    }
    // Every runner's end posted in one turn, inside the object: each holds its post until
    // its settle's schedule sync is done, so the gate fills; a busy one posts again.
    const starts = await Promise.all(runs.map((run) => startOf(r.stub, run.run_id)));
    const bindings = starts.map((start) => runnerBinding(r, start));
    const body = { frames: [{ seq: 1, at: 1, event: { type: "result", result: "done" } }],
                   end: { exit_code: 0 } };
    const busy = await r.in(async (instance) => {
      let refused = 0;
      let pending = bindings;
      for (let round = 0; pending.length > 0; round++) {
        if (round > 20) {
          throw new Error("the busy posts never landed");
        }
        const answers = await Promise.allSettled(
          pending.map((binding) => instance.runEvents(binding, body)));
        const again = pending.filter((_, i) => answers[i]!.status === "rejected");
        for (const answer of answers) {
          if (answer.status === "rejected") {
            expect(String((answer.reason as Error).message)).toBe(EVENTS_BUSY);
            refused += 1;
          }
        }
        pending = again;
      }
      return refused;
    });
    expect(await r.sql("SELECT state FROM runs")).toEqual(
      Array(EVENTS_IN_FLIGHT + 4).fill({ state: "succeeded" }));
    expect(busy).toBe(4);
  });
});

describe("a client's revoke (review H-2)", () => {
  it("settles its runs at once, frees the tier, drops its schedules and disarms the lane",
     async () => {
    const r = await gated();
    const file = await keyed(r);
    await defineAgent(r.stub, r.bind(), "main", file);
    const client = await fullClient(r);
    const runs: string[] = [];
    for (let i = 0; i < 5; i++) {
      runs.push((await r.stub.agentSubmit({ ...client, tiers: TIERS },
                                          { message: "hi", client_send_id: `c-${i}` })).run_id);
    }
    await r.stub.agentRun({ ...client, tiers: TIERS }, { agent: "main", input: "later",
                                                         cron: "0 * * * *" });
    expect((await failure(() => ownerRun(r.stub, r.bind(), undefined, "main"))).message)
      .toBe(`${TIER_RUNS} (free: 5)`);
    await r.stub.grantRevoke(r.bind(), { client: MUSE.client });
    expect(await r.sql("SELECT state, code FROM runs WHERE client = ? ORDER BY seq",
                       MUSE.client)).toEqual(Array(5).fill({ state: "failed", code: "revoked" }));
    expect((await recorder(r.stub)).cancels.map((cancel) => cancel.run).sort())
      .toEqual([...runs].sort());
    expect(await r.sql("SELECT id FROM run_schedules")).toEqual([]);
    expect(await r.sql("SELECT authority FROM sandbox_leases")).toEqual([]);
    // Nothing left: the lane has no job.
    await laneRuns(r);
    expect(await r.sql("SELECT id FROM cf_agents_jobs WHERE id = 'owner-schedule/runs'"))
      .toEqual([]);
    // The five slots are free.
    expect((await ownerRun(r.stub, r.bind(), undefined, "main")).run.state).toBe("running");
  });
});

describe("a client's schedule (review H-3)", () => {
  it("starts its run at its time and on its cron, as that client", async () => {
    const r = await gated();
    const file = await keyed(r);
    await defineAgent(r.stub, r.bind(), "main", file);
    const client = await fullClient(r);
    for (const when of [{ at: Date.now() / 1000 + 3600 }, { cron: "0 * * * *" }]) {
      await r.stub.agentRun({ ...client, tiers: TIERS }, { agent: "main", input: "later",
                                                           ...when });
    }
    await r.sql("UPDATE run_schedules SET next_at = ? WHERE next_at IS NOT NULL",
                Date.now() / 1000 - 1);
    await laneRuns(r);
    expect(await r.sql("SELECT initiator, client, state FROM runs ORDER BY initiator"))
      .toEqual([{ initiator: "schedule", client: MUSE.client, state: "running" },
                { initiator: "schedule", client: MUSE.client, state: "running" }]);
    expect((await recorder(r.stub)).starts.map((start) => start.authority))
      .toEqual(Array(2).fill(MUSE.client));
    expect(await r.sql("SELECT last_error FROM run_schedules WHERE last_error IS NOT NULL"))
      .toEqual([]);
    // A fire its admission refuses keeps its fixed code and a notice, and writes nothing.
    const before = await r.sql("SELECT COUNT(*) AS n FROM conversations");
    await defineAgent(r.stub, r.bind(), "main", { ...file, enabled: false });
    await r.sql("UPDATE run_schedules SET next_at = ? WHERE trigger = 'cron'",
                Date.now() / 1000 - 1);
    await laneRuns(r);
    expect(await r.sql("SELECT last_error FROM run_schedules WHERE trigger = 'cron'"))
      .toEqual([{ last_error: AGENT_DISABLED }]);
    expect(await notices(r)).toContain(`main: ${AGENT_DISABLED}`);
    expect(await r.sql("SELECT COUNT(*) AS n FROM conversations")).toEqual(before);
  });
});

describe("the runs' rows (review M-1)", () => {
  it("stay within RUNS_KEPT and the live ones however inputs are queued and cancelled",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const { run } = await ownerRun(r.stub, r.bind(), undefined, "main");
    const owner = r.bind({ tiers: TIERS });
    await r.in(async (instance) => {
      for (let i = 0; i < 3400; i++) {
        const waiting = await instance.agentSubmit(owner, {
          message: "x", client_send_id: `w-${i}`, conversation_id: run.conversation_id });
        await instance.agentCancel(owner, { run_id: waiting.run_id });
      }
    });
    const [{ n }] = await r.sql("SELECT COUNT(*) AS n FROM runs") as [{ n: number }];
    expect(n).toBeLessThanOrEqual(RUNS_KEPT + 1);
    expect(await r.sql("SELECT state FROM runs WHERE id = ?", run.run_id))
      .toEqual([{ state: "running" }]);
  }, 300_000);
});

describe("a long run's receipt (review round 2, M1)", () => {
  it("outlives its settle however many other runs settled meanwhile", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    await defineAgent(r.stub, r.bind(), "other", SIGNED_IN);
    const long = await ownerRun(r.stub, r.bind(), undefined, "main");
    const busy = await ownerRun(r.stub, r.bind(), undefined, "other");
    const owner = r.bind({ tiers: TIERS });
    await r.in(async (instance) => {
      for (let i = 0; i < 2050; i++) {
        const waiting = await instance.agentSubmit(owner, {
          message: "x", client_send_id: `w-${i}`, agent_id: "other",
          conversation_id: busy.run.conversation_id });
        await instance.agentCancel(owner, { run_id: waiting.run_id });
        if (i % 200 === 0) {  // the runners keep both runs alive
          await instance.runEvents(runnerBinding(r, long.start), { frames: [] });
          await instance.runEvents(runnerBinding(r, busy.start), { frames: [] });
        }
      }
    });
    await r.stub.runEvents(runnerBinding(r, long.start), {
      frames: [{ seq: 1, at: 1, event: { type: "result", result: "the long answer" } }],
      end: { exit_code: 0 } });
    expect(await r.stub.agentStatus(r.bind(), { run_id: long.run.run_id }))
      .toMatchObject({ state: "succeeded", result: "the long answer" });
    const [{ n }] = await r.sql("SELECT COUNT(*) AS n FROM runs WHERE settled_at IS NOT NULL") as
      [{ n: number }];
    expect(n).toBe(RUNS_KEPT);
  }, 300_000);
});

describe("a run's cancel and its heartbeat (review M-2, M-3)", () => {
  it("revokes its grant at once: its calls fail, and its runner's end still settles it",
     async () => {
    const r = await gated();
    const key = await r.connect("sk-good");
    const keyed = await ownerRun(r.stub, r.bind(), { engine: "claude-code",
                                                      credential: "api_key", connection: key,
                                                      scopes: ["read_files"] }, "keyed");
    await r.in((instance) => instance.sandbox.networkApplied({
      ...keyed.start.lease, generation: 1 }));
    await r.stub.agentCancel(r.bind(), { run_id: keyed.run.run_id });
    expect((await failure(() => r.stub.fileStat(runBinding(r, keyed.start),
                                                { path: "/workspace" }))).message)
      .toBe(RUN_ENDED);
    expect(await r.stub.egressOpen(
      sandboxOf(r, keyed.start, { secret: hashed(secretOf(keyed.start)) }),
      { run: keyed.run.run_id, connection: key, method: "GET", path: "/v1/models",
        bytes_hint: null, model: null, tier: null }))
      .toMatchObject({ ok: false, code: "egress_run_closed" });
    await r.stub.runEvents(runnerBinding(r, keyed.start), { frames: [], end: { exit_code: null } });
    expect(await r.stub.agentStatus(r.bind(), { run_id: keyed.run.run_id }))
      .toMatchObject({ state: "failed", code: "cancelled" });
  });

  it("settles a run unknown when its own heartbeat lapses, though another keeps the lease",
     async () => {
    const r = await gated();
    const quiet = await ownerRun(r.stub, r.bind(), SIGNED_IN, "quiet");
    const busy = await ownerRun(r.stub, r.bind(), SIGNED_IN, "busy");
    expect(busy.start.lease).toEqual(quiet.start.lease);
    await r.sql("UPDATE runs SET beat_at = beat_at - 61");
    await r.stub.runEvents(runnerBinding(r, busy.start), { frames: [] });
    await laneRuns(r);
    expect(await r.stub.agentStatus(r.bind(), { run_id: quiet.run.run_id }))
      .toMatchObject({ state: "unknown", code: "lost" });
    expect(await r.stub.agentStatus(r.bind(), { run_id: busy.run.run_id }))
      .toMatchObject({ state: "running" });
    expect((await recorder(r.stub)).cancels.map((cancel) => cancel.run))
      .toEqual([quiet.run.run_id]);
  });

  it("settles every run unknown from the lane alone once their lease lapsed", async () => {
    const r = await gated();
    const { run } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    await r.sql("UPDATE sandbox_leases SET lease_expires = 1");
    await laneRuns(r);
    expect(await r.stub.agentStatus(r.bind(), { run_id: run.run_id }))
      .toMatchObject({ state: "unknown", code: "lost" });
    expect(await r.sql("SELECT lease_id FROM sandbox_leases")).toEqual([]);
  });
});

describe("the tier numbers (review L-1)", () => {
  it("are refused once the record drops them, the kept ones forgotten", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    await r.stub.agentSubmit(r.bind({ tiers: TIERS }), { message: "x", client_send_id: "t-1" });
    expect((await failure(() => r.stub.agentSubmit(r.bind({ tiers: null }), {
      message: "x", client_send_id: "t-2" }))).message).toBe(TIER_UNREAD);
    expect(await r.sql("SELECT tiers FROM platform_tiers")).toEqual([]);
    expect((await failure(() => r.stub.agentSubmit(r.bind(), {
      message: "x", client_send_id: "t-3" }))).message).toBe(TIER_UNREAD);
  });

  it("start a waiting run only within the owner's own tier's room", async () => {
    const r = await gated();
    const tiers = { free: { runs: 2, storage_bytes: 1 }, paid: { runs: 10, storage_bytes: 1 } };
    const owner = r.bind({ tiers });
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const a = await r.stub.agentSubmit(owner, { message: "a1", client_send_id: "a-1" });
    await r.stub.agentSubmit(owner, { message: "b1", client_send_id: "b-1" });
    const waiting = await r.stub.agentSubmit(owner, {
      message: "a2", client_send_id: "a-2", conversation_id: a.conversation_id });
    expect(waiting.state).toBe("waiting");
    await r.stub.runEvents(runnerBinding(r, await startOf(r.stub, a.run_id)),
                           { frames: [], end: { exit_code: 0 } });
    // Another run takes the slot first: the free tier's two are live, so it still waits.
    await r.stub.agentSubmit(owner, { message: "c1", client_send_id: "c-1" });
    await drainDue(r.stub);
    expect(await r.stub.agentStatus(r.bind(), { run_id: waiting.run_id }))
      .toMatchObject({ state: "waiting" });
    // Its turn has not come, so the lane waits for the live runs' leases, not now.
    const [job] = await r.sql("SELECT time FROM cf_agents_jobs WHERE id = 'owner-schedule/runs'");
    expect(job!["time"]).toBeGreaterThan(Date.now() + 30_000);
  });
});

describe("the reviewer's untested cases (review M-6)", () => {
  it("refuses a run past the owner's own spend caps, its source's apart from another's",
     async () => {
    const r = await gated();
    const key = await r.connect("sk-good");
    await defineAgent(r.stub, r.bind(), "keyed", { engine: "claude-code", credential: "api_key",
                                                   connection: key, scopes: [] });
    const { generation, policy } = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: generation, policy: {
      ...policy, models: { ...policy.models, sources: { anthropic: { daily_cost: 100 } } } } });
    const day = new Date().toISOString().slice(0, 10);
    // Another source's spend is not this one's.
    await r.sql("INSERT INTO model_spend (period, source, cost) VALUES (?, 'openai', 500)",
                `d:${day}`);
    expect((await ownerRun(r.stub, r.bind(), undefined, "keyed")).run.state).toBe("running");
    await r.sql("INSERT INTO model_spend (period, source, cost) VALUES (?, 'anthropic', 100)",
                `d:${day}`);
    expect((await failure(() => ownerRun(r.stub, r.bind(), undefined, "keyed"))).message)
      .toBe("owner_limit: source_daily_cost is spent");
  });

  it("refuses a retry key that named another run, and a client's continuing the owner's " +
     "conversation", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", { ...SIGNED_IN, scopes: ALL_SCOPES });
    await defineAgent(r.stub, r.bind(), "other", SIGNED_IN);
    const first = await r.stub.agentSubmit(r.bind({ tiers: TIERS }),
                                           { message: "x", client_send_id: "same" });
    expect((await failure(() => r.stub.agentSubmit(r.bind({ tiers: TIERS }), {
      message: "x", client_send_id: "same", agent_id: "other" }))).message)
      .toBe("conflict: this request id named another run");
    const client = await fullClient(r);
    expect((await failure(() => r.stub.agentSubmit({ ...client, tiers: TIERS }, {
      message: "x", client_send_id: "c-1", conversation_id: first.conversation_id })))
      .message).toBe(NO_CONVERSATION);
  });

  it("refuses an admission whose agent.json changed after it was read", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const refused = await r.in(async (instance, state) => {
      const open = Files.prototype.open;
      // The file changes between its read and the admission's unit (its etag moves).
      Files.prototype.open = function (this: Files, path: unknown) {
        const content = open.call(this, path);
        state.storage.sql.exec("UPDATE file_entries SET mode = 1 - mode WHERE name = " +
                                 "'agent.json'");
        return content;
      } as typeof open;
      try {
        await instance.agentSubmit(r.bind({ tiers: TIERS }),
                                   { message: "x", client_send_id: "e-1" });
        return "admitted";
      } catch (error) {
        return (error as Error).message;
      } finally {
        Files.prototype.open = open;
      }
    });
    expect(refused).toBe(AGENT_CHANGED);
    expect(await r.sql("SELECT id FROM runs")).toEqual([]);
  });

  it("ends every live run in the sandbox when the owner is suspended", async () => {
    const r = await gated();
    const { run, start } = await ownerRun(r.stub, r.bind(), { ...SIGNED_IN,
                                                              scopes: ["read_files"] });
    await r.stub.admissionSet({ owner: r.name, caller: PLATFORM_CALLER, origin: null },
                              { reason: "billing", suspended: true });
    expect((await recorder(r.stub)).cancels.map((cancel) => cancel.run)).toEqual([run.run_id]);
    expect((await failure(() => r.stub.fileStat(runBinding(r, start),
                                                { path: "/workspace" }))).message)
      .not.toBe("allowed");
  });

  it("opens an app socket for the owner alone, whatever ticket a client holds", async () => {
    const r = await gated();
    const { run } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    const minted = await r.stub.socketTicket(
      r.bind({ grant: "grant-1", expires_at: Math.floor(Date.now() / 1000) + 600 }),
      { conversation_id: run.conversation_id });
    const hash = verifyToken(env.ROUTING_MAC_KEY, "socket", minted.ticket)!.hash;
    // An admitted client's own binding, as the binder would build it: refused, the ticket
    // left unspent.
    const client = await fullClient(r);
    const refused = await r.stub.fetch(socketRequest(
      { binding: client, stub: r.stub as never }, run.conversation_id, hash));
    expect(refused.status).not.toBe(101);
    expect(await r.sql("SELECT COUNT(*) AS n FROM socket_tickets")).toEqual([{ n: 1 }]);
  });

  it("refuses a cancel loudly when no sandbox is bound to stop the run", async () => {
    // The plain object: production's, with no `SANDBOX` until slice S binds it.
    const owner = `owner-${crypto.randomUUID()}`;
    const stub = env.OWNER.getByName(owner);
    const bind: Binding = { owner, caller: owner, origin: null };
    await defineAgent(stub, bind, "main", SIGNED_IN);
    const run = `run_${"a".repeat(32)}`;
    await sqlIn(stub, "INSERT INTO runs (id, agent, conversation, source, credential, state, " +
                  "initiator, unattended, created_at, lease_id, epoch, beat_at) VALUES (?, " +
                  "'main', ?, 'claude-code', 'vendor_login_in_sandbox', 'running', 'owner', 0, " +
                  "1, ?, 1, 1)", run, `cv_${"a".repeat(32)}`, "a".repeat(32));
    await sqlIn(stub, "INSERT INTO run_grants (run, secret, scopes, created_at) VALUES " +
                  "(?, ?, '[]', 1)", run, "b".repeat(64));
    expect((await failure(() => stub.agentCancel(bind, { run_id: run }))).message)
      .toBe(SANDBOX_ABSENT);
    expect(await sqlIn(stub, "SELECT cancel_requested FROM runs")).toEqual([
      { cancel_requested: 0 }]);
  });
});
