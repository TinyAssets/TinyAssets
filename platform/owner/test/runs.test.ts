/**
 * Runs in the owner's object (ADR-034 decision 4), in workerd, with a recording
 * fake sandbox Worker behind `SANDBOX` (`ci/fake-sandbox.ts`): admission in one unit (each of its
 * refusals with its code), the run row, its grant and the sandbox's lease, the start with the
 * run's whole environment, the settle from the runner's end, the cancel through the sandbox,
 * no sandbox at all, simultaneous runs each on their own connection, a run's calls held to
 * its grant, and the `runs` lane. The doors' side is `doors/test/runs.test.ts`.
 */
import { runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { afterEach, describe, expect, it, onTestFinished, vi } from "vitest";

import { verifyToken } from "../../contract/src/routing-mac";
import { egressTokenOf } from "../../contract/src/egress";
import { waitingPage } from "../../contract/src/identity";
import { bundledSources } from "../../sources/src/catalog";
import { runEnv } from "../../sources/src/run-env";
import { servedRecord } from "../../sources/src/served";
import { type Binding, PLATFORM_CALLER } from "../../contract/src/binding";
import { AGENT_DISABLED, AGENT_UNDEFINED } from "../../contract/src/agent-definition";
import { AUTHORITY_CHANGED } from "../src/authority";
import { RUN_NO_MAILBOX } from "../src/ledger";
import { RUN_STARTS_NONE, SANDBOX_ONLY } from "../src/owner-do";
import { INLINE_BYTES, QUEUE_BYTES, QUEUE_ROWS, SLOT_WAIT_MS } from "../../contract/src/limits";
import { slotBusy } from "../../contract/src/slots";
import { clock } from "../src/clock";
import { periods } from "../src/money";
import { NO_CONVERSATION, NO_RUN, NO_SOURCE, QUEUE_FULL, RUN_ENDED, SESSION_REFUSED,
         SIGNIN_LAPSED, TIER_RUNS, UNATTENDED_REFUSED } from "../src/runs";
import { PLATFORM_TIER_ONLY } from "../src/admission";
import { TIER_UNREAD } from "../src/files";
import { catalog, gated, type Gated, OPENAI } from "./gateway-support";
import { MCP, MUSE, signedIn } from "./ledger-support";
import {
  ALL_SCOPES, defineAgent, hashed, inSession, later, OPEN_RECORD, openRecord, ownerFile, ownerRun,
  recorder, runBinding, runnerBinding, runnerSandbox, scripted, startOf, TIERS, whileOut,
} from "./runs-support";
import { RANKING } from "../../contract/src/paths";
import type { ActionRule } from "../../contract/src/policy-document";
import type { Tiers } from "../../contract/src/control";
import { ADMITTED_FILE_BYTES, ownerSlots } from "../src/holds";
import { secretOf, until } from "../../ci/workerd";
import { drainDue, failure, gateFor, laneAt, spaceOf } from "./support";
import { LANE_CALL_MS, LANE_RETRY_S } from "../src/schedule";
import { SANDBOX_WAITING, type SandboxStart } from "../../contract/src/sandbox";

afterEach(() => {
  vi.useRealTimers();
});

/** The owner's run of Claude Code on its own sign-in: no key, no connection. */
const SIGNED_IN = Object.freeze({ engine: "claude-code", credential: "vendor_login_in_sandbox",
                                  scopes: [] });

/** The model Claude Code's record says a run on its plan is given (`cli.login.model`). */
const PLAN_MODEL = "claude-opus-5-5";

/** The runner's report of Claude Code's sign-in, under the sandbox's live lease. */
function claudeSignin(r: Gated, start: SandboxStart, signedIn: boolean) {
  return r.stub.signinReport(runnerSandbox(r, start), {
    signins: [{ engine: "claude-code", signed_in: signedIn }] });
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
    const [lease] = await r.sql("SELECT authority, lease_id, epoch FROM sandbox_leases");
    expect(lease!["authority"]).toBe("owner");
    // The lease keeps its egress token's hash, never the token's secret.
    const token = egressTokenOf(start.network!.network)!;
    const leases = JSON.stringify(await r.sql("SELECT * FROM sandbox_leases"));
    expect(leases).not.toContain(token.split(".")[2]);
    expect(leases).toContain(verifyToken(env.ROUTING_KEYRING, "egress", token)!.hash);
    expect(await r.sql("SELECT lease_id, epoch FROM runs WHERE id = ?", run.run_id))
      .toEqual([{ lease_id: lease!["lease_id"], epoch: lease!["epoch"] }]);
    // Its grant: the run's own secret's hash, never the secret, which only its URLs carry.
    const secret = secretOf(start);
    expect(await r.sql("SELECT run, secret, stamp, origin, scopes, connection " +
                         "FROM run_grants")).toEqual([{
      run: run.run_id, secret: hashed(secret), stamp: null, origin: null,
      scopes: "[\"read_files\"]", connection }]);
    expect(await r.sql("SELECT agent, source, session_id, last_run FROM conversations"))
      .toEqual([{ agent: "main", source: "claude-code", session_id: null,
                  last_run: run.run_id }]);
    // The start: the lease it opened, its two tokens and network, and the run's whole
    // environment, the record's own variables and its one connection's key.
    expect(start).toMatchObject({ run: run.run_id, agent: "main", engine: "claude-code",
                                  credential: "api_key", session_id: null, authority: "owner",
                                  prompt: "do the thing", unattended: false,
                                  lease: { lease_id: lease!["lease_id"] } });
    expect(start.tokens).not.toBeNull();
    expect(start.network!.network.rules["egress.tinyassets.io"]).toBeDefined();
    // Claude Code takes the run's secret as a header (its record's `run_secret_env`), so its
    // base URL carries the run alone.
    const base = `https://egress.tinyassets.io/r/${run.run_id}/c/${connection}/`;
    expect(start.config.env).toMatchObject({
      ...runEnv(bundledSources(), "claude-code", "api_key").env, ANTHROPIC_BASE_URL: base,
      ANTHROPIC_CUSTOM_HEADERS: `x-ta-run: ${secret}` });
    expect(start.config.env["ANTHROPIC_API_KEY"]).toMatch(/^ta_ph_c[0-9a-f]{24}_[0-9a-f]{32}$/);
    expect(start.tokens!.runner_token).toMatch(/^[0-9a-f]{64}$/);
    expect(start.urls).toEqual({ mcp: `https://egress.tinyassets.io/r/${run.run_id}/${secret}/mcp`,
                                 mcp_bare: `https://egress.tinyassets.io/r/${run.run_id}/mcp`,
                                 events: `https://egress.tinyassets.io/r/${run.run_id}/events`,
                                 signin: "https://egress.tinyassets.io/signin" });
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
    const first = await r.stub.agentSubmit(r.bind(), ask);
    const again = await r.stub.agentSubmit(r.bind(), ask);
    expect(again.run_id).toBe(first.run_id);
    expect((await recorder(r.stub)).starts).toHaveLength(1);
  });

  it("continues a conversation of the caller's own, resuming its engine's session", async () => {
    const r = await gated();
    const { run, start } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    // While its run is live, an input to the conversation waits its turn, never refused.
    const waiting = await r.stub.agentSubmit(r.bind(), {
      message: "and then", client_send_id: "then-1", conversation_id: run.conversation_id });
    expect(waiting).toMatchObject({ state: "waiting", conversation_id: run.conversation_id,
                                    started_at: null, settled_at: null });
    expect((await recorder(r.stub)).starts).toHaveLength(1);
    await r.stub.runEvents(runnerBinding(r, start), { frames: frames(INIT("sess-1")),
                                                   end: { exit_code: 0 } });
    await drainDue(r.stub);
    const next = await startOf(r.stub, waiting.run_id);
    expect(next.session_id).toBe("sess-1");
    expect(next.conversation).toBe(run.conversation_id);
    expect(next.prompt).toBe("and then");
    await r.stub.runEvents(runnerBinding(r, next), { frames: [], end: { exit_code: 0 } });
    const after = await ownerRun(r.stub, r.bind(), undefined, "main",
                                 { conversation_id: run.conversation_id });
    expect(after.start.session_id).toBe("sess-1");
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
    // An agent of the owner's own beside the seed's `main`.
    expect((await failure(() => ownerRun(r.stub, r.bind(), undefined, "helper"))).message)
      .toBe(AGENT_UNDEFINED);
    expect((await failure(() => ownerRun(r.stub, r.bind(), { ...SIGNED_IN, enabled: false },
                                         "helper"))).message).toBe(AGENT_DISABLED);
    expect(await r.sql("SELECT id FROM runs")).toEqual([]);
  });

  it("starts the agent's own MCP servers its egress lets out, and tells the owner of each " +
     "left out, by the policy the run was admitted under", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    await ownerFile(r.stub, r.bind(), "/workspace/agents/main/mcp.json", JSON.stringify({
      mcpServers: { docs: { url: "https://docs.example.com/mcp" },
                    other: { url: "https://other.example.com/mcp" },
                    local: { command: "uvx", args: ["some-mcp"], env: { MODE: "ro" } } } }));
    // Open mode, the default: every one.
    expect(Object.keys((await ownerRun(r.stub, r.bind())).start.mcp_servers))
      .toEqual(["docs", "other", "local"]);
    // An allowlist naming one of the two hosts: the other is left out, and the owner told.
    const { generation, policy } = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: generation, policy: {
      ...policy, egress: { mode: "allowlist",
                           destinations: [{ host: "docs.example.com", port: 443 }] } } });
    const { start } = await ownerRun(r.stub, r.bind());
    expect(start.mcp_servers).toEqual({
      docs: { url: "https://docs.example.com/mcp" },
      local: { command: "uvx", args: ["some-mcp"], env: { MODE: "ro" } } });
    const told = (await r.stub.noticeList(r.bind())).notices
      .filter((notice) => notice.type === "mcp_server_dropped");
    expect(told.map((notice) => notice.detail)).toEqual([
      "main: its MCP server other (other.example.com) is left out of its runs: its host is " +
      "not on your egress allowlist"]);
  });

  it("starts a run with its agent's own hooks as its hooks.json has them, none without one, " +
     "and refuses one that does not read", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const path = "/workspace/agents/main/hooks.json";
    await ownerFile(r.stub, r.bind(), path, null);
    expect(Object.values((await ownerRun(r.stub, r.bind())).start.hooks).flat()).toEqual([]);
    const guard = { match: { kinds: ["shell"] }, run: ["./hooks/guard.sh"], timeout_s: 30 };
    await ownerFile(r.stub, r.bind(), path, JSON.stringify({ hooks: { pre_tool: [guard] } }));
    const { start } = await ownerRun(r.stub, r.bind());
    expect(start.hooks).toEqual({ pre_tool: [guard], post_tool: [], prompt: [],
                                  session_start: [], stop: [], session_end: [], pre_compact: [] });
    await ownerFile(r.stub, r.bind(), path, JSON.stringify({ hooks: { pre_tool: [
      { run: ["./guard.sh"], allow: true }] } }));
    expect((await failure(() => ownerRun(r.stub, r.bind()))).message)
      .toMatch(/^hooks\.json: pre_tool\[0\] is its run, and optionally match and timeout_s/);
  });

  describe("the commands its own definition starts, under the owner's shell rules", () => {
    const guard = { match: { kinds: ["shell"] }, run: ["./hooks/guard.sh"] };
    const note = { run: ["./hooks/note.sh"] };
    const local = { command: "uvx", args: ["some-mcp"], env: {} };
    const docs = { url: "https://docs.example.com/mcp" };

    /** The owner whose `main` has two hooks and two servers, one by its command. */
    async function defined() {
      const r = await gated();
      await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
      await ownerFile(r.stub, r.bind(), "/workspace/agents/main/hooks.json", JSON.stringify({
        hooks: { pre_tool: [guard], session_start: [note] } }));
      await ownerFile(r.stub, r.bind(), "/workspace/agents/main/mcp.json",
                      JSON.stringify({ mcpServers: { docs, local } }));
      const ruled = async (actions: readonly ActionRule[]) => {
        const { generation, policy } = await r.stub.policyRead(r.bind());
        await r.stub.policyWrite(r.session(), { expected_generation: generation,
                                                policy: { ...policy, actions } });
      };
      const told = async (type: string) => (await r.stub.noticeList(r.bind())).notices
        .filter((notice) => notice.type === type).map((notice) => notice.detail);
      return { r, ruled, told };
    }

    it("leaves out each one a never rule holds for its agent, the owner told once of each, " +
       "and starts every one a rule holds for no other", async () => {
      const { r, ruled, told } = await defined();
      await ruled([{ act: "workspace.shell", do: "never", agent: "other" },
                   { act: "workspace.files", do: "never" }]);
      const free = (await ownerRun(r.stub, r.bind())).start;
      expect([free.mcp_servers, free.hooks.pre_tool, free.hooks.session_start])
        .toEqual([{ docs, local }, [guard], [note]]);
      await ruled([{ act: "workspace.shell", do: "never", agent: "main" }]);
      for (let n = 0; n < 2; n++) {
        const { start } = await ownerRun(r.stub, r.bind());
        expect(start.mcp_servers).toEqual({ docs });
        expect(Object.values(start.hooks).flat()).toEqual([]);
      }
      const why = "is left out of this run: the owner's rule never lets an agent do this " +
        "(workspace.shell)";
      expect((await told("definition_command_dropped")).sort()).toEqual([
        `main: its MCP server local (uvx) ${why}`, `main: its pre_tool hook ./hooks/guard.sh ${
          why}`, `main: its session_start hook ./hooks/note.sh ${why}`]);
      expect((await r.stub.policyReceipts(r.bind())).actions.map((receipt) =>
        [receipt.act, receipt.outcome, receipt.agent]))
        .toEqual(Array(6).fill(["workspace.shell", "denied", "main"]));
    });

    it("holds each one an ask rule names for the owner's yes, which resumes its conversation " +
       "on a run that starts it, once", async () => {
      const { r, ruled, told } = await defined();
      await ruled([{ act: "workspace.shell", do: "ask" }]);
      const { run, start } = await ownerRun(r.stub, r.bind());
      expect([start.mcp_servers, start.hooks.pre_tool, start.hooks.session_start])
        .toEqual([{ docs }, [], []]);
      const waits = /^main: (.*) is left out of this run: it waits for your answer at (\S+)$/;
      const dropped = (await told("definition_command_dropped")).map((detail) =>
        waits.exec(detail)!.slice(1));
      expect(dropped.map(([what]) => what).sort()).toEqual(["its MCP server local (uvx)",
        "its pre_tool hook ./hooks/guard.sh", "its session_start hook ./hooks/note.sh"]);
      const page = dropped.find(([what]) => what!.includes("pre_tool"))![1]!;
      expect((await told("action_request")).sort()).toEqual(dropped.map(([what, at]) =>
        `main asks to workspace.shell at ${what!.includes("MCP") ? "mcp.json" : "hooks.json"}: ` +
        `allow or deny it at ${at}`).sort());
      const shown = (await r.stub.actionRequests(r.bind())).cards
        .find((each) => waitingPage(each.card_id) === page)!;
      const card = shown.card_id;
      expect(shown).toMatchObject({
        act: "workspace.shell", door: "hooks.json", agent: "main",
        conversation: run.conversation_id,
        request: JSON.stringify({ hook: "pre_tool[0]", match: { kinds: ["shell"] },
                                  run: ["./hooks/guard.sh"] }) });
      // The yes while the run is live: its resume waits behind it, and so does an input sent
      // after, whose admission, written waiting, uses no yes.
      const approved = await r.stub.actionApprove(r.session(), { card_id: card,
                                                                 digest: shown.digest });
      const after = await r.stub.agentSubmit(r.bind(), {
        message: "and then", client_send_id: "then-1", conversation_id: run.conversation_id });
      expect(after.state).toBe("waiting");
      await r.stub.runEvents(runnerBinding(r, start), { frames: frames(INIT("sess-1")),
                                                     end: { exit_code: 0 } });
      await drainDue(r.stub);
      const resumed = await startOf(r.stub, approved.run!);
      expect([resumed.conversation, resumed.hooks.pre_tool, resumed.hooks.session_start,
              resumed.mcp_servers]).toEqual([run.conversation_id, [guard], [], { docs }]);
      // Once: the conversation's next run asks again.
      await r.stub.runEvents(runnerBinding(r, resumed), { frames: [], end: { exit_code: 0 } });
      await drainDue(r.stub);
      expect((await startOf(r.stub, after.run_id)).hooks.pre_tool).toEqual([]);
    });
  });

  it("refuses a run whose mcp.json names the run's own door, or is no file", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const path = "/workspace/agents/main/mcp.json";
    await ownerFile(r.stub, r.bind(), path, JSON.stringify({
      mcpServers: { tinyassets: { url: "https://docs.example.com/mcp" } } }));
    expect((await failure(() => ownerRun(r.stub, r.bind()))).message)
      .toMatch(/^mcp\.json: server "tinyassets": .* not tinyassets, the run's own door$/);
    await ownerFile(r.stub, r.bind(), path, null);
    await r.stub.fileMkdir(await inSession(r.stub, r.bind()), { path, expected_etag: null });
    expect((await failure(() => ownerRun(r.stub, r.bind()))).message)
      .toBe(`${path} is not a file`);
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
    expect(await r.stub.tierRead(r.bind())).toEqual({ tier: "free" });
    expect(await tierSet(r, "paid")).toEqual({ tier: "paid" });
    // The owner reads its tier (its app shows it); an outside client never does.
    expect(await r.stub.tierRead(r.bind())).toEqual({ tier: "paid" });
    expect((await failure(() => r.stub.tierRead(
      r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() })))).message)
      .toBe("only the owner may read its tier");
    for (let i = 0; i < 5; i++) {
      await ownerRun(r.stub, r.bind());
    }
    expect((await failure(() => ownerRun(r.stub, r.bind()))).message)
      .toBe(`${TIER_RUNS} (paid: 10)`);
    // The founder's edit of the record reaches the next run door at once.
    const edited = { ...TIERS, paid: { runs: 11, storage_bytes: 1 } };
    await r.stub.agentSubmit(r.bind({ control: openRecord(edited) }),
                             { message: "x", client_send_id: "e-1" });
    expect((await r.sql("SELECT COUNT(*) AS n FROM runs WHERE state = 'running'"))[0])
      .toEqual({ n: 11 });
    // A record past the ceiling, or malformed, is refused, never guessed.
    expect((await failure(() => r.stub.agentSubmit(
      r.bind({ control: openRecord({ ...TIERS, free: { runs: 1001, storage_bytes: 1 } }) }),
      { message: "x", client_send_id: "e-2" }))).code).toBe("invalid");
  });

  it("refuses a run when the control record held no tier numbers", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    expect((await failure(() => r.stub.agentSubmit(r.bind({ control: openRecord() }), {
      message: "x", client_send_id: "t-1" }))).message).toBe(TIER_UNREAD);
  });

  it("refuses an unattended run on a source whose record does not allow it", async () => {
    const r = await gated();
    // Codex on the owner's ChatGPT sign-in: its record refuses unattended hosted use.
    await defineAgent(r.stub, r.bind(), "coder", { engine: "codex",
                                                   credential: "vendor_login_in_sandbox",
                                                   scopes: [] });
    // Attended, it runs; on a schedule, it is refused, the refusal kept on the schedule.
    await ownerRun(r.stub, r.bind(), undefined, "coder");
    const schedule = await r.stub.agentRun(r.bind(), {
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
    await r.stub.agentRun({ ...client, control: OPEN_RECORD }, { agent: "main", input: "later",
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

describe("a run's sandbox report", () => {
  it("makes files the sync cannot name (not UTF-8) the owner's notice, once a frame",
     async () => {
    const r = await gated();
    const { start } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    const said = { type: "ta.sync_unnamed", paths: ["/workspace/f\\xffo"], count: 1,
                   message: "the runner's words, for the run's log" };
    await r.stub.runEvents(runnerBinding(r, start), { frames: frames(said) });
    // The same frame posted again (a retry) is no second notice.
    await r.stub.runEvents(runnerBinding(r, start), { frames: frames(said) });
    const notices = (await r.stub.noticeList(r.bind())).notices
      .filter((each) => each.type === "sync_unnamed");
    expect(notices.map((each) => each.detail)).toEqual([
      "main: 1 file or folder in its sandbox has a name that is not UTF-8, so it does not " +
      "sync to your files: \"/workspace/f\\xffo\". Rename it to sync it."]);
  });

  it("words the notice itself: an event's text, and a path not as the sync escapes one, " +
     "never reach it", async () => {
    const r = await gated();
    const { start } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    const forged = { type: "ta.sync_unnamed", count: 7,
                     paths: ["/workspace/a\\xff", "https://evil.example/paste-your-key",
                             "/workspace/b\nYour sign-in expired", 42],
                     message: "Your sign-in expired: paste your API key at https://evil.example" };
    await r.stub.runEvents(runnerBinding(r, start), { frames: frames(forged) });
    const [notice] = (await r.stub.noticeList(r.bind())).notices
      .filter((each) => each.type === "sync_unnamed");
    expect(notice!.detail).toBe(
      "main: 7 files or folders in its sandbox have names that are not UTF-8, so they do not " +
      "sync to your files: \"/workspace/a\\xff\", and 6 more. Rename them to sync them.");
    expect(notice!.detail).not.toMatch(/evil|expired/);
  });
});

describe("a run's settle", () => {
  it("is succeeded, failed or unknown, from the runner's end, never replayed", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    // Succeeded: its session id and result kept, its grant gone.
    const ok = await ownerRun(r.stub, r.bind());
    expect(await r.stub.runEvents(runnerBinding(r, ok.start), {
      frames: frames(INIT("sess-ok"), RESULT("all done")), end: { exit_code: 0 } }))
      .toEqual({ accepted: 2, settled: "succeeded" });
    expect(await r.stub.agentStatus(r.bind(), { run_id: ok.run.run_id }))
      .toMatchObject({ state: "succeeded", result: "all done", code: null, exit_code: 0 });
    expect(await r.sql("SELECT session_id FROM conversations WHERE id = ?",
                       ok.run.conversation_id)).toEqual([{ session_id: "sess-ok" }]);
    expect(await r.sql("SELECT run FROM run_grants WHERE run = ?", ok.run.run_id)).toEqual([]);
    // A frame past its settle is refused: the run is no longer live.
    expect((await failure(() => r.stub.runEvents(runnerBinding(r, ok.start),
                                                 { frames: frames(RESULT("again")) })))
      .message).toBe(RUN_ENDED);
    // Failed: the exit code, or an error event though it exited 0.
    const exit = await ownerRun(r.stub, r.bind());
    await r.stub.runEvents(runnerBinding(r, exit.start), { frames: [], end: { exit_code: 2 } });
    expect(await r.stub.agentStatus(r.bind(), { run_id: exit.run.run_id }))
      .toMatchObject({ state: "failed", code: "exit", exit_code: 2 });
    const error = await ownerRun(r.stub, r.bind());
    await r.stub.runEvents(runnerBinding(r, error.start), { frames: frames(FAILED("rate")),
                                                         end: { exit_code: 0 } });
    expect(await r.stub.agentStatus(r.bind(), { run_id: error.run.run_id }))
      .toMatchObject({ state: "failed", code: "engine_error", error: "rate" });
    // Unknown: its runner lost (no heartbeat within the lease's life).
    const lost = await ownerRun(r.stub, r.bind());
    later();
    await ownerRun(r.stub, r.bind());
    expect(await r.stub.agentStatus(r.bind(), { run_id: lost.run.run_id }))
      .toMatchObject({ state: "unknown", code: "lost" });
  });

  it("fails on a call the engine gave up on, never one it retried itself and recovered",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-pi");
    await defineAgent(r.stub, r.bind(), "main", { engine: "pi", credential: "api_key",
                                                  connection, scopes: [] });
    // pi's `--mode json` stream: each attempt's message ends, then pi says it retries.
    const attempt = (said: string) => [
      { type: "message_end", message: { role: "assistant", content: [], stopReason: "error",
                                        errorMessage: said } },
      { type: "agent_end", messages: [], willRetry: true },
      { type: "auto_retry_start", attempt: 1, maxAttempts: 3, delayMs: 2000,
        errorMessage: said }];
    const recovered = await ownerRun(r.stub, r.bind());
    await r.stub.runEvents(runnerBinding(r, recovered.start), {
      frames: frames({ type: "session", id: "sess-pi" }, ...attempt("529 overloaded")) });
    const rest = [
      { type: "message_end", message: { role: "assistant", stopReason: "stop",
                                        content: [{ type: "text", text: "done" }] } },
      { type: "auto_retry_end", success: true, attempt: 1 },
      { type: "agent_end", messages: [], willRetry: false }];
    expect(await r.stub.runEvents(runnerBinding(r, recovered.start), {
      frames: rest.map((event, i) => ({ seq: 5 + i, at: 1_800_000_005 + i, event })),
      end: { exit_code: 0 } })).toEqual({ accepted: 3, settled: "succeeded" });
    expect(await r.stub.agentStatus(r.bind(), { run_id: recovered.run.run_id }))
      .toMatchObject({ state: "succeeded", result: "done", code: null, error: null });
    const spent = await ownerRun(r.stub, r.bind());
    await r.stub.runEvents(runnerBinding(r, spent.start), {
      frames: frames(...attempt("529 overloaded"),
                     { type: "message_end", message: { role: "assistant", content: [],
                       stopReason: "error", errorMessage: "529 still overloaded" } },
                     { type: "agent_end", messages: [], willRetry: false },
                     { type: "auto_retry_end", success: false, attempt: 3,
                       finalError: "529 still overloaded" }),
      end: { exit_code: 0 } });
    expect(await r.stub.agentStatus(r.bind(), { run_id: spent.run.run_id }))
      .toMatchObject({ state: "failed", code: "engine_error", error: "529 still overloaded" });
    // The object's own refusal of a session id is no error of the engine's to withdraw.
    const refused = await ownerRun(r.stub, r.bind());
    await r.stub.runEvents(runnerBinding(r, refused.start), {
      frames: frames({ type: "session", id: "has space" }, ...attempt("529 overloaded")),
      end: { exit_code: 0 } });
    expect(await r.stub.agentStatus(r.bind(), { run_id: refused.run.run_id }))
      .toMatchObject({ state: "failed", error: SESSION_REFUSED });
  });

  it("keeps the run, its result and its session id and no frame as a row; a session id " +
     "outside its shape is refused and never kept", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const ok = await ownerRun(r.stub, r.bind());
    await r.stub.runEvents(runnerBinding(r, ok.start), {
      frames: frames(INIT("sess-ok"), { type: "assistant", message: { content: [] } },
                     RESULT("done")), end: { exit_code: 0 } });
    const stored = JSON.stringify(await r.sql("SELECT * FROM runs"));
    expect(stored).toContain("\"result\":\"done\"");
    expect(stored).not.toContain("assistant");
    const bad = await ownerRun(r.stub, r.bind());
    await r.stub.runEvents(runnerBinding(r, bad.start), {
      frames: frames(INIT("has a space"), RESULT("done")), end: { exit_code: 0 } });
    expect(await r.stub.agentStatus(r.bind(), { run_id: bad.run.run_id }))
      .toMatchObject({ error: SESSION_REFUSED });
    expect(await r.sql("SELECT session_id FROM conversations WHERE id = ?",
                       bad.run.conversation_id)).toEqual([{ session_id: null }]);
  });

  it("is unknown when the sandbox's start never answered, failed when it answered out of " +
     "shape or refused, each logged as itself", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    // The object runs in this isolate: its log lines are seen here.
    const logged = vi.spyOn(console, "error");
    onTestFinished(() => logged.mockRestore());
    const lines = () => logged.mock.calls.map((call) => String(call[0]));
    const submit = (id: string) => r.stub.agentSubmit(r.bind(),
                                                      { message: "x", client_send_id: id });
    await scripted(r.stub, { answer: "throw" });
    const lost = await submit("s-1");
    expect(lost).toMatchObject({ state: "unknown", code: "start_lost" });
    // Its cause is logged with it.
    expect(logged.mock.calls.find((call) => String(call[0]).includes("did not answer"))?.[1])
      .toMatchObject({ message: "the sandbox did not answer" });
    // A code that is none of the contract's, answered or thrown as a refusal, is an answer
    // out of shape: the sandbox Worker's bug, failed with its cause, never read as a lost start.
    await scripted(r.stub, { answer: { started: false, code: "e2b_down" } as never });
    const malformed = await submit("s-3");
    expect(malformed).toMatchObject({ state: "failed", code: "start_malformed" });
    await gateFor(env.SANDBOX_GATE, r.name).arm("start", { refuse: {
      code: "e2b_quota", message: "another version's refusal" } });
    const refusedOutOfShape = await submit("s-5");
    expect(refusedOutOfShape).toMatchObject({ state: "failed", code: "start_malformed" });
    const stopped = "a sandbox Worker bug: it failed, and is stopped in case it launched";
    expect(lines().filter((line) => line.includes("out of the contract's shape"))).toEqual([
      "the sandbox answered a run's start out of the contract's shape (started false, code " +
        `e2b_down, keys started code), ${stopped}`,
      "the sandbox answered a run's start out of the contract's shape (a refusal of code " +
        `e2b_quota), ${stopped}`]);
    // In case any did start, each is cancelled too.
    expect((await recorder(r.stub)).cancels.map((cancel) => cancel.run))
      .toEqual([lost.run_id, malformed.run_id, refusedOutOfShape.run_id]);
    await scripted(r.stub, { answer: { started: false, code: "sandbox_unconfigured" } });
    expect(await submit("s-2")).toMatchObject({ state: "failed", code: "sandbox_unconfigured" });
    // A refusal the sandbox Worker throws comes before it launches anything: failed with its
    // code, and nothing to cancel.
    await gateFor(env.SANDBOX_GATE, r.name).arm("start", { refuse: {
      code: "sandbox_runner_mismatch", message: "the sandbox's runner is not its digest" } });
    expect(await submit("s-4"))
      .toMatchObject({ state: "failed", code: "sandbox_runner_mismatch" });
    expect(lines()).toContain(
      "the sandbox refused a run's start (sandbox_runner_mismatch): nothing started");
    expect((await recorder(r.stub)).cancels).toHaveLength(3);
    // None holds the lease: the last live run's settle gave it back.
    expect(await r.sql("SELECT lease_id FROM sandbox_leases")).toEqual([]);
    // A start with no answer, or one out of shape, is never sent again: it may have launched.
    expect((await recorder(r.stub)).starts.filter((start) => start.run === lost.run_id))
      .toHaveLength(1);
    expect((await gateFor(env.SANDBOX_GATE, r.name).calls())
      .filter((call) => call.method === "start"
              && (call.args as [string, SandboxStart])[1].run === refusedOutOfShape.run_id))
      .toHaveLength(1);
  });

  it("sends a start refused for the moment again, the same start, while the run holds its " +
     "grant; one whose wait ends past the bound is failed by the refusal's own code, and one " +
     "whose run was cancelled, as cancelled", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const gate = gateFor(env.SANDBOX_GATE, r.name);
    const submit = (id: string) => r.stub.agentSubmit(r.bind(),
                                                      { message: "x", client_send_id: id });
    const overloaded = (retryAfterS: number) => ({ refuse: {
      code: "object_overloaded", message: "this account is busy", retryAfterS } });
    const sent = async (run: string) => (await gate.calls())
      .filter((call) => call.method === "start"
              && (call.args as [string, SandboxStart])[1].run === run)
      .map((call) => (call.args as [string, SandboxStart])[1]);
    await gate.arm("start", overloaded(0), 2);
    const again = await submit("t-1");
    expect(again).toMatchObject({ state: "running", code: null });
    // Sent three times, each the same start (its tokens and secret with it), and launched once.
    const starts = await sent(again.run_id);
    expect(starts).toHaveLength(3);
    expect(starts[1]).toEqual(starts[0]);
    expect(starts[2]).toEqual(starts[0]);
    expect((await recorder(r.stub)).starts.filter((start) => start.run === again.run_id))
      .toHaveLength(1);
    // A wait past `LANE_CALL_MS`: the refusal stands at once, and nothing is cancelled.
    await gate.arm("start", overloaded(60));
    const past = await submit("t-2");
    expect(past).toMatchObject({ state: "failed", code: "object_overloaded" });
    expect(await sent(past.run_id)).toHaveLength(1);
    expect((await recorder(r.stub)).cancels).toEqual([]);
    // Cancelled while its start waits: never sent again.
    await gate.arm("start", overloaded(1));
    const waiting = submit("t-3");
    let run = "";
    await until(async () => {
      run = (await gate.calls()).filter((call) => call.method === "start")
        .map((call) => (call.args as [string, SandboxStart])[1].run).at(-1) ?? "";
      return run !== past.run_id && run !== again.run_id && run !== "";
    }, "the refused start");
    await r.stub.agentCancel(r.bind(), { run_id: run });
    expect(await waiting).toMatchObject({ state: "failed", code: "cancelled" });
    expect(await sent(run)).toHaveLength(1);
    expect((await recorder(r.stub)).starts.filter((start) => start.run === run)).toEqual([]);
  });
});

describe("a run's cancel", () => {
  it("reaches the sandbox, and the run settles from the runner's end as cancelled",
     async () => {
    const r = await gated();
    const { run, start } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    const cancelled = await r.stub.agentCancel(r.bind(), { run_id: run.run_id });
    expect(cancelled).toMatchObject({ run_id: run.run_id, state: "running" });
    expect((await recorder(r.stub)).cancels).toEqual([{ run: run.run_id }]);
    await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: null } });
    expect(await r.stub.agentStatus(r.bind(), { run_id: run.run_id }))
      .toMatchObject({ state: "failed", code: "cancelled" });
    // A settled run cancels nothing more.
    await r.stub.agentCancel(r.bind(), { run_id: run.run_id });
    expect((await recorder(r.stub)).cancels).toHaveLength(1);
  });

  it("asked while its start is out, stops the run its start then starts, and settles one its " +
     "start answers with no sandbox or a refusal as cancelled, with no wait", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const submit = (id: string) => r.stub.agentSubmit(r.bind({ control: OPEN_RECORD }),
                                                      { message: "x", client_send_id: id });
    const cancels = async (run: string) => (await recorder(r.stub)).cancels
      .filter((cancel) => cancel.run === run).length;
    let run = "";
    const started = await whileOut(r.name, "start", () => submit("o-1"), async () => {
      run = (await recorder(r.stub)).starts.at(-1)!.run;
      await r.stub.agentCancel(r.bind(), { run_id: run });
      // Its cancel reached the sandbox before the start's answer came, so maybe before its
      // launch, which then stops nothing.
      expect(await cancels(run)).toBe(1);
    });
    // Started for a run that no longer stands: stopped again, now that it launched, and
    // settled from its runner's end.
    expect(started).toMatchObject({ run_id: run, state: "running" });
    expect(await cancels(run)).toBe(2);
    await r.stub.runEvents(runnerBinding(r, await startOf(r.stub, run)),
                           { frames: [], end: { exit_code: null } });
    expect(await r.stub.agentStatus(r.bind(), { run_id: run }))
      .toMatchObject({ state: "failed", code: "cancelled" });
    // No room for its sandbox, or a refusal: a cancelled run waits for none, and settles as
    // the cancel the owner asked, whatever its start answered after.
    for (const [code, id] of [[SANDBOX_WAITING, "o-2"], ["sandbox_unconfigured", "o-3"]] as const) {
      await scripted(r.stub, { answer: { started: false, code } });
      let refused = "";
      const answer = await whileOut(r.name, "start", () => submit(id), async () => {
        refused = (await recorder(r.stub)).starts.at(-1)!.run;
        await r.stub.agentCancel(r.bind(), { run_id: refused });
      });
      expect(answer).toMatchObject({ run_id: refused, state: "failed", code: "cancelled" });
      expect(await r.sql("SELECT run FROM run_inputs")).toEqual([]);
      expect(await r.sql("SELECT id FROM runs WHERE sandbox_wait = 1")).toEqual([]);
      // Nothing launched: only the cancel's own reached the sandbox.
      expect(await cancels(refused)).toBe(1);
    }
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
    const egress = (start: SandboxStart) => r.bind({ sandbox: {
      kind: "egress", generation: 1, secret: hashed(secretOf(start)), token: verifyToken(
        env.ROUTING_KEYRING, "egress", egressTokenOf(onA.start.network!.network)!)!.hash } });
    const call = (run: string, connection: string,
                  as = run === onA.run.run_id ? onA.start : onB.start) =>
      r.stub.egressOpen(egress(as), {
        run, connection, method: "GET", path: "/v1/models", bytes_hint: null });
    // A run id alone authorizes nothing: one run's process naming the other's id, with its
    // own secret, spends on neither.
    expect(await call(onB.run.run_id, b, onA.start))
      .toMatchObject({ ok: false, code: "egress_run_closed" });
    expect(await call(onA.run.run_id, b)).toMatchObject({ ok: false,
                                                          code: "egress_run_connection" });
    expect(await call(onB.run.run_id, a)).toMatchObject({ ok: false,
                                                          code: "egress_run_connection" });
    expect(await call(onA.run.run_id, a)).toMatchObject({ ok: true });
    expect(await call(onB.run.run_id, b)).toMatchObject({ ok: true });
    // A run settled spends no more, though its sandbox's lease is live for the other.
    await r.stub.runEvents(runnerBinding(r, onA.start), { frames: [], end: { exit_code: 0 } });
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
    expect(await failure(() => r.stub.agentSubmit({ ...client, control: OPEN_RECORD }, {
      message: "hi", client_send_id: "n-1" })))
      .toMatchObject({ code: "not_granted", message: "outside client capability not granted" });
    expect((await failure(() => r.stub.agentRun({ ...client, control: OPEN_RECORD }, {
      agent: "main", input: "x" }))).message).toBe("outside client capability not granted");
    expect((await recorder(r.stub)).starts).toEqual([]);
  });
});

describe("a conversation's inputs", () => {
  /** An input to `conversation`, by the owner. */
  const submit = (r: Gated, conversation: string, message: string, id: string) =>
    r.stub.agentSubmit(r.bind(), { message, client_send_id: id,
                                                  conversation_id: conversation });
  /** The prompts the sandbox was started on, in order. */
  const prompts = async (r: Gated) => (await recorder(r.stub)).starts.map((s) => s.prompt);

  it("two in a row while its run is live both run, in order, each as the one before settles",
     async () => {
    const r = await gated();
    const { run, start } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    const second = await submit(r, run.conversation_id, "second", "in-2");
    const third = await submit(r, run.conversation_id, "third", "in-3");
    expect([second.state, third.state]).toEqual(["waiting", "waiting"]);
    expect(await r.sql("SELECT prompt, bytes FROM run_inputs ORDER BY prompt"))
      .toEqual([{ prompt: "second", bytes: 6 }, { prompt: "third", bytes: 5 }]);
    // A retry of a waiting input is the same run, never a second one.
    expect((await submit(r, run.conversation_id, "second", "in-2")).run_id)
      .toBe(second.run_id);
    // Another conversation's run is not held up behind them.
    const other = await ownerRun(r.stub, r.bind());
    expect(other.run.state).toBe("running");
    expect(await prompts(r)).toEqual(["do the thing", "do the thing"]);
    // The first settles: the second starts, the third still waits; one more that comes as
    // the lane has yet to start them waits behind them.
    await r.stub.runEvents(runnerBinding(r, start), { frames: frames(INIT("sess-1")),
                                                   end: { exit_code: 0 } });
    const fourth = await submit(r, run.conversation_id, "fourth", "in-4");
    expect(fourth.state).toBe("waiting");
    await drainDue(r.stub);
    expect(await prompts(r)).toEqual(["do the thing", "do the thing", "second"]);
    expect(await r.stub.agentStatus(r.bind(), { run_id: second.run_id }))
      .toMatchObject({ state: "running", engine: "claude-code" });
    expect(await r.stub.agentStatus(r.bind(), { run_id: third.run_id }))
      .toMatchObject({ state: "waiting" });
    // The second settles: the third starts, resuming the same session.
    await r.stub.runEvents(runnerBinding(r, await startOf(r.stub, second.run_id)),
                           { frames: [], end: { exit_code: 0 } });
    await drainDue(r.stub);
    expect(await prompts(r)).toEqual(["do the thing", "do the thing", "second", "third"]);
    expect((await startOf(r.stub, third.run_id)).session_id).toBe("sess-1");
    await r.stub.runEvents(runnerBinding(r, await startOf(r.stub, third.run_id)),
                           { frames: [], end: { exit_code: 0 } });
    await drainDue(r.stub);
    expect(await prompts(r))
      .toEqual(["do the thing", "do the thing", "second", "third", "fourth"]);
    expect((await startOf(r.stub, fourth.run_id)).session_id).toBe("sess-1");
    expect(await r.sql("SELECT run FROM run_inputs")).toEqual([]);
  });

  it("wait only within the queue's ceiling, rows and bytes", async () => {
    const r = await gated();
    const { run } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    // The ceiling's rows, as other conversations' inputs: one more is refused.
    await r.sql("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n " +
                  `WHERE i < ${QUEUE_ROWS}) INSERT INTO run_inputs (run, prompt, bytes) ` +
                  "SELECT 'run_' || printf('%032x', i), 'x', 1 FROM n");
    expect((await failure(() => submit(r, run.conversation_id, "more", "q-1"))).message)
      .toBe(QUEUE_FULL);
    // One fewer, and it waits.
    await r.sql("DELETE FROM run_inputs WHERE run = ?", `run_${"0".repeat(31)}1`);
    expect((await submit(r, run.conversation_id, "more", "q-2")).state).toBe("waiting");
    // The ceiling's bytes, less than this input's: refused.
    await r.sql("DELETE FROM run_inputs WHERE prompt = 'x'");
    const held = QUEUE_BYTES - 4 - 3;
    await r.sql("INSERT INTO run_inputs (run, prompt, bytes) VALUES (?, " +
                  "CAST(zeroblob(?) AS TEXT), ?)", `run_${"f".repeat(32)}`, held, held);
    expect((await failure(() => submit(r, run.conversation_id, "four", "q-3"))).message)
      .toBe(QUEUE_FULL);
    expect((await submit(r, run.conversation_id, "two", "q-4")).state).toBe("waiting");
  });

  it("one waiting is cancelled at once, never started; one whose turn is refused settles " +
     "failed, the owner told", async () => {
    const r = await gated();
    const { run, start } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    const cancelled = await submit(r, run.conversation_id, "never", "c-1");
    const refused = await submit(r, run.conversation_id, "refused", "c-2");
    expect(await r.stub.agentCancel(r.bind(), { run_id: cancelled.run_id }))
      .toMatchObject({ state: "failed", code: "cancelled", started_at: null });
    expect((await recorder(r.stub)).cancels).toEqual([]);
    expect(await r.sql("SELECT run FROM run_inputs")).toEqual([{ run: refused.run_id }]);
    // The agent is disabled before the next one's turn: it settles failed, never started.
    await defineAgent(r.stub, r.bind(), "main", { ...SIGNED_IN, enabled: false });
    await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: 0 } });
    await drainDue(r.stub);
    expect(await r.stub.agentStatus(r.bind(), { run_id: refused.run_id }))
      .toMatchObject({ state: "failed", code: "agent_disabled", error: AGENT_DISABLED,
                       started_at: null });
    expect((await recorder(r.stub)).starts).toHaveLength(1);
    expect((await r.stub.noticeList(r.bind())).notices.map((notice) => notice.detail))
      .toContain(`main: ${AGENT_DISABLED}`);
    expect(await r.sql("SELECT run FROM run_inputs")).toEqual([]);
  });
});

describe("a source outside the sandbox's egress scope", () => {
  it("is neither offered nor started, though the owner's connection is usable", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const keyed = { engine: "claude-code", credential: "api_key", connection, scopes: [] };
    // Active, with its models (`Connections.usable`), but with no egress scope stored: the
    // sandbox could never reach its provider (`Connections.egressible`, `RunHost.reachable`).
    await r.sql("UPDATE connections SET egress = NULL WHERE id = ?", connection);
    expect(await r.sql("SELECT status FROM connections WHERE id = ?", connection))
      .toEqual([{ status: "active" }]);
    // `auto` is not offered it, and an agent that names it is refused: no run, no start.
    await defineAgent(r.stub, r.bind(), "helper", { engine: "auto", scopes: [] });
    expect((await failure(() => ownerRun(r.stub, r.bind(), undefined, "helper"))).message)
      .toMatch(/^no_source/);
    expect((await failure(() => ownerRun(r.stub, r.bind(), keyed))).message)
      .toMatch(/^no_source/);
    expect(await r.sql("SELECT id FROM runs")).toEqual([]);
    expect((await recorder(r.stub)).starts).toEqual([]);
  });
});

describe("auto and the owner's sign-ins", () => {
  /** An agent on `auto`, the owner's strongest connected source. */
  const AUTO_AGENT = Object.freeze({ engine: "auto", scopes: [] });

  it("runs an owner whose only source is a Claude subscription", async () => {
    const r = await gated();
    // Nothing reported yet: auto has no source.
    await defineAgent(r.stub, r.bind(), "helper", AUTO_AGENT);
    expect((await failure(() => ownerRun(r.stub, r.bind(), undefined, "helper"))).message)
      .toMatch(/^no_source/);
    // The owner's own Claude Code run opens the sandbox; its runner reports the sign-in:
    // presence only, its time, never a credential. A run naming the engine on its sign-in
    // is given its plan's model too.
    const { run, start } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    expect(run.model).toBe(PLAN_MODEL);
    expect(start.model).toBe(PLAN_MODEL);
    expect(await claudeSignin(r, start, true)).toEqual({ recorded: 1, lapsed: [] });
    const [row] = await r.sql("SELECT * FROM engine_signins");
    expect(row).toMatchObject({ engine: "claude-code", signed_in: 1, models: "[]" });
    expect(Object.keys(row!).sort())
      .toEqual(["changed_at", "checked_at", "engine", "models", "signed_in"]);
    // The plan runs the model its record says it does, and the receipt says which.
    const auto = await ownerRun(r.stub, r.bind(), undefined, "helper");
    expect(auto.run).toMatchObject({ state: "running", engine: "claude-code",
                                     credential: "vendor_login_in_sandbox", model: PLAN_MODEL });
    expect(auto.start).toMatchObject({ engine: "claude-code",
                                       credential: "vendor_login_in_sandbox", model: PLAN_MODEL });
    expect(await r.sql("SELECT connection FROM run_grants WHERE run = ?", auto.run.run_id))
      .toEqual([{ connection: null }]);
    // An agent that names the engine and a model runs that model on the sign-in.
    expect((await ownerRun(r.stub, r.bind(), { ...SIGNED_IN, model: "claude-sonnet-5-5" },
                           "named")).start)
      .toMatchObject({ credential: "vendor_login_in_sandbox", model: "claude-sonnet-5-5" });
  });

  it("ranks a subscription against another provider's key by tier, on the owner's ranking " +
     "file where it has one", async () => {
    const r = await gated();
    const [base] = await catalog(OPENAI);
    // Each key's models, as its setup check and the gateway's catalog list them.
    const keyOn = async (display: string, id: string) => {
      const models = [{ ...base!, id }];
      await r.gateway.arm("models", { answer: { models } });
      return r.connect("sk-good", OPENAI, display, models);
    };
    await keyOn("Weaker", "gpt-5.1-codex");
    await defineAgent(r.stub, r.bind(), "helper", AUTO_AGENT);
    // A run of `helper`, settled at once, so the tier's simultaneous runs never refuse one.
    const helper = async () => {
      const { run, start } = await ownerRun(r.stub, r.bind(), undefined, "helper");
      await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: 0 } });
      return run;
    };
    const keyed = await ownerRun(r.stub, r.bind(), undefined, "helper");
    expect(keyed.run).toMatchObject({ credential: "api_key", model: "gpt-5.1-codex" });
    // Signed in: Claude's plan (`claude-opus-5*`, the top tier) ranks above the key.
    await claudeSignin(r, keyed.start, true);
    const plan = { engine: "claude-code", credential: "vendor_login_in_sandbox",
                   model: PLAN_MODEL };
    expect(await helper()).toMatchObject(plan);
    // That provider's key on a model ranked above the plan's in the top tier: still the plan,
    // which the owner pays for already.
    const stronger = await keyOn("Stronger", "gpt-6.1-sol");
    expect(await helper()).toMatchObject(plan);
    // The owner's own ranking, whole in place of the platform's: OpenAI's a tier above, in a
    // file past the inline bytes, so read from its chunks.
    const own = (tiers: unknown, about = "") => ownerFile(r.stub, r.bind(), RANKING,
                                                          JSON.stringify({ version: 2, about,
                                                                           tiers }));
    await own([[{ provider: "*", model: "gpt-6*" }], [{ provider: "*", model: "claude-*" }]],
              "x".repeat(INLINE_BYTES));
    const strongest = await ownerRun(r.stub, r.bind(), undefined, "helper");
    expect(strongest.run).toMatchObject({ credential: "api_key", model: "gpt-6.1-sol" });
    expect(await r.sql("SELECT connection FROM run_grants WHERE run = ?",
                       strongest.run.run_id)).toEqual([{ connection: stronger }]);
    // One that ranks none of them: the plan first among the unknown, and the owner told
    // once, not once a run.
    await own([[{ provider: "*", model: "none-*" }]]);
    expect(await helper()).toMatchObject(plan);
    expect(await helper()).toMatchObject(plan);
    expect((await r.stub.noticeList(r.bind())).notices
      .filter((notice) => notice.type === "model_unranked").map((notice) => notice.detail))
      .toEqual([`${PLAN_MODEL} (claude-code's sign-in): no entry of the model ranking ranks ` +
                `it, so it ranked below every ranked model; rank it in ${RANKING}`]);
    // A file that is no ranking refuses the run, never a silent fall back to the platform's.
    for (const [text, message] of [
      ["{\"version\": 1}", `${RANKING}: a model ranking holds only version 2`],
      ["not json", `${RANKING} is not JSON text`],
      [JSON.stringify({ version: 2, about: "x".repeat(ADMITTED_FILE_BYTES),
                        tiers: [[{ provider: "*", model: "*" }]] }),
       `${RANKING} is past the ${ADMITTED_FILE_BYTES} bytes`]] as const) {
      await ownerFile(r.stub, r.bind(), RANKING, text);
      expect(await failure(() => helper()))
        .toMatchObject({ code: "invalid", message: expect.stringContaining(message) });
    }
    // Removed: the platform's ranking again.
    await ownerFile(r.stub, r.bind(), RANKING, null);
    expect(await helper()).toMatchObject(plan);
    // An `auto` agent that names a model runs on a source that offers it, whatever ranks
    // above, and one no source offers is refused, never passed to another vendor's CLI.
    await defineAgent(r.stub, r.bind(), "unoffered",
                      { ...AUTO_AGENT, model: "claude-sonnet-5-5" });
    expect((await failure(() => ownerRun(r.stub, r.bind(), undefined, "unoffered"))).message)
      .toMatch(/^no_source/);
    for (const [model, run] of [
      [PLAN_MODEL, { engine: "claude-code", model: PLAN_MODEL }],
      ["gpt-5.1-codex", { credential: "api_key", model: "gpt-5.1-codex" }]] as const) {
      expect((await ownerRun(r.stub, r.bind(), { ...AUTO_AGENT, model }, "pinned")).run)
        .toMatchObject(run);
    }
  });

  it("prefers the subscription to a key of its provider; a lapse is refused and noticed, " +
     "never a fall back to the key", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    await defineAgent(r.stub, r.bind(), "helper", AUTO_AGENT);
    // Only the key: auto runs on it.
    const keyed = await ownerRun(r.stub, r.bind(), undefined, "helper");
    expect(keyed.run).toMatchObject({ credential: "api_key" });
    expect(await r.sql("SELECT connection FROM run_grants WHERE run = ?", keyed.run.run_id))
      .toEqual([{ connection }]);
    // Signed in as well: the plan covers the key's provider, and is used, on its own model.
    await claudeSignin(r, keyed.start, true);
    const planned = await ownerRun(r.stub, r.bind(), undefined, "helper");
    expect(planned.run).toMatchObject({ engine: "claude-code",
                                        credential: "vendor_login_in_sandbox",
                                        model: PLAN_MODEL });
    // The sign-in lapses: a "sign in again" notice, and auto stops picking it, the key
    // never taken in its place.
    expect(await claudeSignin(r, keyed.start, false))
      .toEqual({ recorded: 1, lapsed: ["claude-code"] });
    expect((await r.stub.noticeList(r.bind())).notices
      .filter((notice) => notice.type === "signin_lapsed").map((notice) => notice.detail))
      .toEqual(["claude-code: sign in again"]);
    expect((await failure(() => ownerRun(r.stub, r.bind(), undefined, "helper"))).message)
      .toBe(`${SIGNIN_LAPSED} (claude-code)`);
    expect((await failure(() => ownerRun(r.stub, r.bind(), SIGNED_IN))).message)
      .toBe(`${SIGNIN_LAPSED} (claude-code)`);
    // Reported lapsed again, it is no new lapse; signed in again, auto picks it again.
    expect(await claudeSignin(r, keyed.start, false))
      .toEqual({ recorded: 1, lapsed: [] });
    await claudeSignin(r, keyed.start, true);
    expect((await ownerRun(r.stub, r.bind(), undefined, "helper")).run)
      .toMatchObject({ credential: "vendor_login_in_sandbox" });
    // The agent that names the key's connection runs on it, its own choice.
    await defineAgent(r.stub, r.bind(), "keyed", { ...AUTO_AGENT, connection });
    expect((await ownerRun(r.stub, r.bind(), undefined, "keyed")).run)
      .toMatchObject({ credential: "api_key" });
  });

  it("never picks on its own a model the key's source record marks not interactive; one the " +
     "agent names runs", async () => {
    // The stage finding of 2026-11-23: an OpenRouter key whose catalog lists Claude Fable
    // only as its batch variants and as `~…-latest`. The models are put on the fake upstream's
    // source, as its setup check and the gateway's catalog list them; the rule reads the
    // connection's source record, OpenRouter's.
    const [base] = await catalog(OPENAI);
    const BATCH = "anthropic/claude-fable-5.1:batch";
    const keyOn = async (r: Gated, display: string, ...ids: string[]) => {
      const models = ids.map((id) => ({ ...base!, id }));
      await r.gateway.arm("models", { answer: { models } });
      return r.connect("sk-good", OPENAI, display, models, "openrouter_user_models_v1");
    };
    const r = await gated();
    await keyOn(r, "OpenRouter", BATCH, "~anthropic/claude-fable-latest");
    await defineAgent(r.stub, r.bind(), "helper", AUTO_AGENT);
    expect((await ownerRun(r.stub, r.bind(), undefined, "helper")).run)
      .toMatchObject({ credential: "api_key", model: "~anthropic/claude-fable-latest" });
    // An agent naming its engine and no model is given the same, never its engine's default.
    expect((await ownerRun(r.stub, r.bind(), { engine: "pi", scopes: [] }, "engine")).run)
      .toMatchObject({ engine: "pi", model: "~anthropic/claude-fable-latest" });
    expect((await ownerRun(r.stub, r.bind(), { ...AUTO_AGENT, model: BATCH }, "pinned")).run)
      .toMatchObject({ credential: "api_key", model: BATCH });
    // A key listing only batch variants: no source for an agent that names no model, on
    // `auto` or on its engine and the connection, saying how to run one; naming one, it runs.
    const s = await gated();
    const only = await keyOn(s, "Batch only", BATCH);
    await defineAgent(s.stub, s.bind(), "helper", AUTO_AGENT);
    const unpicked = `${NO_SOURCE} (its connections list only models a run is given when its ` +
      "agent names one (agent.json's model))";
    expect((await failure(() => ownerRun(s.stub, s.bind(), undefined, "helper"))).message)
      .toBe(unpicked);
    const named = { engine: "pi", connection: only, scopes: [] };
    expect((await failure(() => ownerRun(s.stub, s.bind(), named, "named"))).message)
      .toBe(unpicked);
    expect((await ownerRun(s.stub, s.bind(), { ...named, model: BATCH }, "named")).run)
      .toMatchObject({ engine: "pi", credential: "api_key", model: BATCH });
  });

  it("tells the owner, once for each agent and model, of a run given a model its agent does " +
     "not name, with the remedy", async () => {
    const [base] = await catalog(OPENAI);
    const r = await gated();
    // Each key's models, as its setup check and the gateway's catalog list them.
    const keyOn = async (display: string, id: string) => {
      const models = [{ ...base!, id }];
      await r.gateway.arm("models", { answer: { models } });
      return r.connect("sk-good", OPENAI, display, models, "openrouter_user_models_v1");
    };
    const weaker = await keyOn("Weaker", "gpt-5.1-codex");
    const told = async () => (await r.stub.noticeList(r.bind())).notices
      .filter((notice) => notice.type === "model_chosen").map((notice) => notice.detail)
      .reverse();
    const words = (model: string, name: string, connection: string) => "engine: its runs are " +
      `given ${model} on ${name} (${connection}), chosen for it because its agent.json names ` +
      "no model; name a model in agent.json to pin one";
    const engine = { engine: "pi", scopes: [] };
    await ownerRun(r.stub, r.bind(), engine, "engine");
    await ownerRun(r.stub, r.bind(), engine, "engine");
    expect(await told()).toEqual([words("gpt-5.1-codex", "Weaker", weaker)]);
    // An agent that names its model is not told, nor is a run refused once its model is
    // chosen (a stronger key's, over the owner's cap).
    await ownerRun(r.stub, r.bind(), { ...engine, model: "gpt-5.1-codex" }, "pinned");
    const stronger = await keyOn("Stronger", "~anthropic/claude-fable-latest");
    const { generation, policy } = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: generation, policy: {
      ...policy, models: { ...policy.models, daily_cost: 100 } } });
    const day = `d:${periods(clock()).day}`;
    await r.sql("INSERT INTO model_spend (period, source, cost) VALUES (?, 'openrouter', 100)",
                day);
    expect((await failure(() => ownerRun(r.stub, r.bind(), engine, "engine"))).message)
      .toBe("owner_limit: daily_cost is spent");
    expect(await told()).toEqual([words("gpt-5.1-codex", "Weaker", weaker)]);
    // Its runs given another model, the stronger key's: the owner told again.
    await r.sql("DELETE FROM model_spend");
    await ownerRun(r.stub, r.bind(), engine, "engine");
    expect(await told()).toEqual([words("gpt-5.1-codex", "Weaker", weaker),
                                  words("~anthropic/claude-fable-latest", "Stronger", stronger)]);
  });

  it("shows the owner which model its agent's next run is given, attended and unattended, " +
     "telling nothing and refusing nothing", async () => {
    const [base] = await catalog(OPENAI);
    const r = await gated();
    const models = [{ ...base!, id: "gpt-5.1-codex" }];
    await r.gateway.arm("models", { answer: { models } });
    const key = await r.connect("sk-good", OPENAI, "Weaker", models, "openrouter_user_models_v1");
    await defineAgent(r.stub, r.bind(), "engine", { engine: "pi", scopes: [] });
    await defineAgent(r.stub, r.bind(), "coder", { engine: "codex",
                                                   credential: "vendor_login_in_sandbox",
                                                   scopes: [] });
    await defineAgent(r.stub, r.bind(), "gone", { engine: "pi", model: "gone-1", scopes: [] });
    const preview = (agent: string) => r.stub.agentModel(r.bind(), { agent_id: agent });
    const notices = async () => (await r.stub.noticeList(r.bind())).notices;
    const before = await notices();
    const given = { engine: "pi", model: "gpt-5.1-codex", place: `Weaker (${key})` };
    expect(await preview("engine")).toEqual({ attended: given, unattended: given });
    // A refusal its run would meet is the preview's answer, never its refusal.
    expect(await preview("coder")).toEqual({
      attended: { engine: "codex", model: servedRecord("codex")!.cli.login!.model.id,
                  place: "codex's sign-in" },
      unattended: { refused: UNATTENDED_REFUSED } });
    const unlisted = { refused: `${NO_SOURCE} (no connected key lists gone-1)` };
    expect(await preview("gone")).toEqual({ attended: unlisted, unattended: unlisted });
    // A ranking that ranks none of its models: the preview tells nothing, and the run it
    // previewed is given that model and tells it, the key named by its name and id.
    await ownerFile(r.stub, r.bind(), RANKING, JSON.stringify({ version: 2, about: "",
      tiers: [[{ provider: "*", model: "none-*" }]] }));
    expect(await preview("engine")).toEqual({ attended: given, unattended: given });
    expect(await notices()).toEqual(before);
    expect((await ownerRun(r.stub, r.bind(), undefined, "engine")).run)
      .toMatchObject({ engine: "pi", model: "gpt-5.1-codex" });
    expect((await notices()).filter((notice) => notice.type === "model_unranked")
      .map((notice) => notice.detail))
      .toEqual([`gpt-5.1-codex (Weaker (${key})): no entry of the model ranking ranks it, so ` +
                `it ranked below every ranked model; rank it in ${RANKING}`]);
    expect((await failure(() => preview("nobody"))).message).toBe(AGENT_UNDEFINED);
    // The owner's alone: an outside client is refused.
    const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    expect((await failure(() => r.stub.agentModel(client, { agent_id: "engine" }))).message)
      .toBe("only the owner may see which model its agents' runs are given");
  });

  it("picks on a key that pays for nothing only a model its source serves such a key and its " +
     "vendor still lists, and on one that pays the strongest", async () => {
    // The stage finding of 2026-11-23, past the batch variants: a free-tier OpenRouter key,
    // whose catalog prices a router at zero that routes to priced models.
    const [base] = await catalog(OPENAI);
    const zero = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
    const FREE = "nvidia/nemotron-3-ultra-550b-a55b:free";
    const PAID = "~anthropic/claude-fable-latest";
    const models = [{ ...base!, id: PAID }, { ...base!, id: "openrouter/auto", cost: zero },
                    { ...base!, id: "cohere/north-mini-code:free", cost: zero },
                    { ...base!, id: FREE, cost: zero }];
    const keyed = async (pays: boolean) => {
      const r = await gated();
      await r.gateway.arm("credit", { answer: { pays } });
      await r.gateway.arm("models", { answer: { models } });
      // The vendor scores none of them, and no longer serves the other free one, first by id.
      await r.gateway.arm("listing", { answer: { models: [
        { id: PAID, score: null }, { id: "openrouter/auto", score: null },
        { id: FREE, score: null }] } });
      await r.connect("sk-good", OPENAI, "OpenRouter", models, "openrouter_user_models_v1");
      await drainDue(r.stub);
      await defineAgent(r.stub, r.bind(), "helper", AUTO_AGENT);
      return r;
    };
    const free = await keyed(false);
    expect((await ownerRun(free.stub, free.bind(), undefined, "helper")).run)
      .toMatchObject({ credential: "api_key", model: FREE });
    expect((await ownerRun(free.stub, free.bind(), { engine: "pi", scopes: [] }, "engine")).run)
      .toMatchObject({ engine: "pi", model: FREE });
    // A model the agent names is its own choice.
    expect((await ownerRun(free.stub, free.bind(), { ...AUTO_AGENT, model: PAID }, "pinned"))
      .run).toMatchObject({ credential: "api_key", model: PAID });
    // One its vendor no longer lists is refused, saying so.
    expect((await failure(() => ownerRun(free.stub, free.bind(), {
      ...AUTO_AGENT, model: "cohere/north-mini-code:free" }, "dropped"))).message).toBe(
      `${NO_SOURCE} (no connected key lists cohere/north-mini-code:free)`);
    const paid = await keyed(true);
    expect((await ownerRun(paid.stub, paid.bind(), undefined, "helper")).run)
      .toMatchObject({ credential: "api_key", model: PAID });
  });

  it("takes a sign-in report only from the sandbox, for engines that sign in, each once",
     async () => {
    const r = await gated();
    const { start } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    const report = (binding: Binding, signins: unknown) =>
      failure(() => r.stub.signinReport(binding, { signins } as never));
    const one = [{ engine: "claude-code", signed_in: true }];
    expect((await report(r.bind(), one)).message).toBe(SANDBOX_ONLY);
    expect((await report(runBinding(r, start), one)).message).toBe(SANDBOX_ONLY);
    for (const bad of [[], [{ engine: "nope", signed_in: true }], [...one, ...one],
                       [{ engine: "pi", signed_in: true }],
                       [{ engine: "claude-code", signed_in: "yes" }],
                       // A plan's model is its record's, never the runner's word.
                       [{ engine: "claude-code", signed_in: true, models: [PLAN_MODEL] }]]) {
      expect((await report(runnerSandbox(r, start), bad)).code).toBe("invalid");
    }
    expect(await r.sql("SELECT engine FROM engine_signins")).toEqual([]);
    // Its lease over, the sandbox reports nothing.
    await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: 0 } });
    expect((await report(runnerSandbox(r, start), one)).message).toBe("egress_lease_closed");
  });
});

describe("the admission unit", () => {
  /** An input to `conversation`, by the owner. */
  const submit = (r: Gated, conversation: string, message: string, id: string) =>
    r.stub.agentSubmit(r.bind(), { message, client_send_id: id,
                                                  conversation_id: conversation });

  it("refuses an owner not admitted itself, whatever reached it (the lane's race)",
     async () => {
    const r = await gated();
    const { run, start } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    const waiting = await submit(r, run.conversation_id, "after", "a-1");
    // An agent.json past an inline file: the lane's read of it crosses to the files plane
    // between its unit that picks the waiting run (admitted then) and the run's admission.
    const path = "/workspace/agents/main/agent.json";
    const session = r.session();
    const { entry } = await r.stub.fileStat(session, { path });
    await r.stub.fileWrite(session, { path, expected_etag: entry!.etag, content: new TextEncoder()
      .encode(JSON.stringify(SIGNED_IN).padEnd(INLINE_BYTES + 1, " ")) });
    const files = gateFor(env.FILES_GATE, await spaceOf(r.stub));
    await files.arm("getChunks", { hold: "before" });
    await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: 0 } });
    const lane = drainDue(r.stub);
    try {
      await files.held("getChunks");
      await r.stub.admissionSet({ owner: r.name, caller: PLATFORM_CALLER, origin: null },
                                { reason: "billing", suspended: true });
    } finally {
      await files.release("getChunks");
    }
    await lane;
    // Its admission refused it: never started.
    expect((await recorder(r.stub)).starts.map((each) => each.run)).toEqual([run.run_id]);
    expect(await r.sql("SELECT state FROM runs WHERE id = ?", waiting.run_id))
      .not.toEqual([{ state: "running" }]);
  });

  it("finds a waiting run's turn: first in a conversation with no live run, while the " +
     "tier has room by the kept record's numbers", async () => {
    const r = await gated();
    const { run } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    const behind = await submit(r, run.conversation_id, "behind", "t-1");  // its run is live
    // A run of its own conversation that waits for the sandbox: the line's ring starts it.
    await scripted(r.stub, { answer: [{ started: true }, { started: false, code: SANDBOX_WAITING },
                                      { started: true }] });
    const own = await r.stub.agentSubmit(r.bind(),
                                         { message: "own", client_send_id: "t-2" });
    expect([behind.state, own.state]).toEqual(["waiting", "waiting"]);
    const asked = (await recorder(r.stub)).starts.length;  // the first's, and own's wait
    // The tier's simultaneous runs, by the record a door last handed: 1, none, then 2.
    const started = async (tiers: Tiers | undefined) => {
      await r.stub.fileStat(r.bind({ control: openRecord(tiers) }), { path: "/workspace" });
      await r.stub.sandboxRung();
      await drainDue(r.stub);
      return (await recorder(r.stub)).starts.slice(asked).map((each) => each.run);
    };
    expect(await started({ ...TIERS, free: { ...TIERS.free, runs: 1 } })).toEqual([]);
    expect(await started(undefined)).toEqual([]);
    // No tier capacity is available for readmission. The peer may use the freed slot.
    expect((await recorder(r.stub)).unwaits).toEqual([r.name]);
    expect(await started({ ...TIERS, free: { ...TIERS.free, runs: 2 } }))
      .toEqual([own.run_id]);
  });

  it.each(["binding", "KV refresh", "assignment"] as const)(
    "resumes a woken run when its tier gains a place through a %s", async (change) => {
      const r = await gated();
      if (change === "KV refresh") {
        const { generation, policy } = await r.stub.policyRead(r.bind());
        await r.stub.policyWrite(r.session(), { expected_generation: generation,
          policy: { ...policy, codemode: { on: true } } });
      }
      await ownerRun(r.stub, r.bind(), SIGNED_IN);
      await scripted(r.stub, { answer: [{ started: true }, { started: false, code: SANDBOX_WAITING },
                                        { started: true }] });
      const waiting = await r.stub.agentSubmit(r.bind(), { message: "wait", client_send_id: "wait" });
      expect(waiting).toMatchObject({ state: "waiting", code: SANDBOX_WAITING });
      const limited = { ...TIERS, free: { ...TIERS.free, runs: 1 } };
      await r.stub.fileStat(r.bind({ control: openRecord(limited) }), { path: "/workspace" });
      expect(await r.stub.sandboxRung()).toEqual({ waiting: 1 });
      await drainDue(r.stub);
      expect((await recorder(r.stub)).starts).toHaveLength(2);

      if (change === "binding") {
        await r.stub.fileStat(r.bind(), { path: "/workspace" });
      } else if (change === "KV refresh") {
        const result = await r.stub.codeRun(r.bind({ control: openRecord(limited) }),
          { script: "return 1;" }, new IdentityTransformStream().readable);
        expect(result).toMatchObject({ ok: true, value: 1 });
      } else {
        await tierSet(r, "paid");
      }
      await drainDue(r.stub);
      expect((await recorder(r.stub)).starts.map(start => start.run).slice(1))
        .toEqual([waiting.run_id, waiting.run_id]);
    });

  it("reads a waiting run's agent file on the lane without waiting on the pool: refused busy " +
     "at once, the lane holds, and the run starts once the pool frees", async () => {
    const r = await gated();
    const { run, start } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    // An agent.json past an inline file: its read holds the isolate's pool.
    const path = "/workspace/agents/main/agent.json";
    const session = r.session();
    const { entry } = await r.stub.fileStat(session, { path });
    await r.stub.fileWrite(session, { path, expected_etag: entry!.etag, content: new TextEncoder()
      .encode(JSON.stringify(SIGNED_IN).padEnd(INLINE_BYTES + 1, " ")) });
    const waiting = await submit(r, run.conversation_id, "after", "p-1");
    // Every unit of the pool held elsewhere, its reserve too, and no call waiting for one.
    const pool = ownerSlots();
    const large = await pool.acquire(pool.capacity - pool.reserve, "", undefined, true);
    const reserve = await pool.acquire(pool.reserve);
    try {
      await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: 0 } });
      // The pool's units stay held through the pass, so a read that waited would be refused
      // only by the pool's own timer, `SLOT_WAIT_MS` on: finishing inside it is the
      // never-waiting, by the code's constant.
      const before = Date.now();
      const began = clock();
      await drainDue(r.stub);
      expect(Date.now() - before).toBeLessThan(SLOT_WAIT_MS);
      expect(await r.sql("SELECT state FROM runs WHERE id = ?", waiting.run_id))
        .toEqual([{ state: "waiting" }]);
      const held = (await laneAt(r.stub, "runs"))!;
      expect(held).toBeGreaterThanOrEqual(began + slotBusy().retryAfter!);
      expect(held).toBeLessThan(began + LANE_RETRY_S);
    } finally {
      large.release();
      reserve.release();
    }
    await r.sql("UPDATE cf_agents_jobs SET time = 0 WHERE id = 'owner-schedule/runs'");
    await drainDue(r.stub);
    expect((await recorder(r.stub)).starts.map((each) => each.run))
      .toEqual([run.run_id, waiting.run_id]);
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
    await defineAgent(r.stub, r.bind(), "main", {
      engine: "claude-code", credential: "api_key",
      connection: await r.connect("sk-good"), scopes: ALL_SCOPES });
    const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    await r.stub.outsideAgents(client);
    const { revision } = (await r.stub.grantList(r.bind())).clients
      .find((entry) => entry.client === MUSE.client)!;
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "full",
                                         scopes: [], expected_revision: revision,
                                         name: "Muse" });
    const run = await r.stub.agentSubmit({ ...client, control: OPEN_RECORD },
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
    await defineAgent(r.stub, r.bind(), "main", {
      engine: "claude-code", credential: "api_key",
      connection: await r.connect("sk-good"), scopes: ALL_SCOPES });
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
    const run = await r.stub.agentSubmit({ ...client, control: OPEN_RECORD },
                                         { message: "hi", client_send_id: "c-1" });
    expect(run).toMatchObject({ state: "running", initiator: "client" });
    expect(await r.sql("SELECT scopes FROM run_grants")).toEqual([
      { scopes: "[\"read_graph\",\"converse\"]" }]);
    // The client reads its own runs only; the owner reads every one.
    expect(await r.stub.agentStatus(client, { run_id: run.run_id }))
      .toMatchObject({ run_id: run.run_id });
    const start = (await recorder(r.stub)).starts.at(-1)!;
    const asRun = runBinding(r, start);
    expect((await failure(() => r.stub.mailboxRead(asRun, { limit: 1 }))).message)
      .toBe(RUN_NO_MAILBOX);
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
  it("starts a due schedule's run, and nothing on an empty poll",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    // No schedule: the lane has no job, and nothing starts.
    await drainDue(r.stub);
    expect(await r.sql("SELECT id FROM cf_agents_jobs WHERE id = 'owner-schedule/runs'"))
      .toEqual([]);
    expect((await recorder(r.stub)).starts).toEqual([]);
    // A cron is its own work: due, it starts one run, unattended.
    await r.stub.agentRun(r.bind(), { agent: "main", input: "report",
                                                      cron: "30 * * * *" });
    await fireSchedules(r);
    const starts = (await recorder(r.stub)).starts;
    expect(starts.map((start) => [start.prompt, start.unattended])).toEqual([["report", true]]);
    expect(await r.stub.agentStatus(r.bind(), { run_id: starts[0]!.run }))
      .toMatchObject({ initiator: "schedule" });
  });

  it("leaves a schedule due when its launch is refused for the moment, and fires it once",
     async () => {
    const r = await gated();
    // An agent's file past an inline one, so the launch reads it from the files plane.
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    await r.stub.fileWrite(r.session(), {
      path: "/workspace/agents/main/agent.json", content: new TextEncoder().encode(
        `${JSON.stringify(SIGNED_IN)}${" ".repeat(INLINE_BYTES)}`),
      expected_etag: (await r.stub.fileStat(r.bind(), {
        path: "/workspace/agents/main/agent.json" })).entry!.etag });
    for (const trigger of [{ at: Date.now() / 1000 + 3600 }, { cron: "30 * * * *" }]) {
      const before = (await recorder(r.stub)).starts.length;
      await r.stub.agentRun(r.bind(), { agent: "main", input: "report",
                                                        ...trigger });
      // The read refused `busy` for the moment, as a full slot pool refuses it: the pass
      // holds the lane until its Retry-After, never failed, the schedule unclaimed and due.
      const busy = slotBusy();
      await gateFor(env.FILES_GATE, await spaceOf(r.stub)).arm("getChunks", { refuse: {
        code: busy.code, message: busy.message, retryAfterS: 60 } });
      const logged = vi.spyOn(console, "error");
      onTestFinished(() => logged.mockRestore());
      const began = Date.now() / 1000;
      await fireSchedules(r);
      const ended = Date.now() / 1000;
      expect(logged.mock.calls.map((call) => String(call[0])))
        .not.toContain("the owner's runs pass failed; it runs again later");
      logged.mockRestore();
      const held = (await laneAt(r.stub, "runs"))!;
      expect(held).toBeGreaterThanOrEqual(began + 60);
      expect(held).toBeLessThanOrEqual(Math.ceil((ended + 60) * 1000) / 1000);
      expect((await recorder(r.stub)).starts.length - before).toBe(0);
      expect(await r.sql("SELECT last_error FROM run_schedules WHERE next_at <= ?", ended))
        .toEqual([{ last_error: null }]);
      // The lane's next pass, as its hold brings it.
      await r.sql("UPDATE cf_agents_jobs SET time = 0 WHERE id = 'owner-schedule/runs'");
      await drainDue(r.stub);
      expect((await recorder(r.stub)).starts.length - before).toBe(1);
      const rows = await r.sql("SELECT trigger, next_at, last_error FROM run_schedules");
      if ("at" in trigger) {
        expect(rows).toEqual([]);  // fired, once
      } else {
        expect(rows).toEqual([{ trigger: "cron", next_at: expect.any(Number), last_error: null }]);
        expect(rows[0]!["next_at"] as number).toBeGreaterThan(Date.now() / 1000);
        await r.stub.agentCancel(r.bind(), { schedule_id: (await r.sql(
          "SELECT id FROM run_schedules"))[0]!["id"] as string });
      }
    }
  });

  it("keeps a refusal that crossed from the files plane on its schedule's row, the pass " +
     "never failed for it", async () => {
    const r = await gated();
    // An agent's file past an inline one, so the launch reads it from the files plane.
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    await r.stub.fileWrite(r.session(), {
      path: "/workspace/agents/main/agent.json", content: new TextEncoder().encode(
        `${JSON.stringify(SIGNED_IN)}${" ".repeat(INLINE_BYTES)}`),
      expected_etag: (await r.stub.fileStat(r.bind(), {
        path: "/workspace/agents/main/agent.json" })).entry!.etag });
    await r.stub.agentRun(r.bind({ control: OPEN_RECORD }), { agent: "main", input: "report",
                                                      cron: "30 * * * *" });
    await gateFor(env.FILES_GATE, await spaceOf(r.stub)).arm("getChunks", { refuse: {
      code: "invalid", message: "invalid: the files plane refuses this read" } });
    await fireSchedules(r);
    expect(await r.sql("SELECT last_error FROM run_schedules"))
      .toEqual([{ last_error: "invalid: the files plane refuses this read" }]);
    expect((await recorder(r.stub)).starts).toEqual([]);
  });

  it("waits on a due schedule's start only to the bound on a row's outside call: the pass " +
     "goes on, the run queued, and its answer is recorded when it comes", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    await r.stub.agentRun(r.bind({ control: OPEN_RECORD }), { agent: "main", input: "report",
                                                      at: Date.now() / 1000 + 3600 });
    const sandbox = gateFor(env.SANDBOX_GATE, r.name);
    await sandbox.arm("start", { hold: "before" });
    await r.sql("UPDATE run_schedules SET next_at = ?", Date.now() / 1000 - 1);
    // Time passes on the fake clock while the start is held, advanced inside the object
    // (workerd resolves a timer it set only in its own context): no guessed wait ends it.
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    // A door re-derives the lane's job, due now, and its pass sends the start.
    await r.stub.fileMkdir(r.bind(), { path: "/workspace/d-bound", expected_etag: null });
    const pass = runDurableObjectAlarm(r.stub);
    await until(async () => (await env.SANDBOX_GATE.held())
      .some((call) => call.name === r.name && call.method === "start"), "the start held");
    await runInDurableObject(r.stub, () => vi.advanceTimersByTimeAsync(LANE_CALL_MS));
    vi.useRealTimers();
    // The pass ended with the start still out: its job set from the rows, the run's own
    // look ahead, and the run queued, which no pass starts again.
    await until(async () => (await laneAt(r.stub, "runs") ?? 0) > Date.now() / 1000,
                "the pass's end");
    const [row] = await r.sql("SELECT id, state FROM runs");
    expect(row).toMatchObject({ state: "queued" });
    expect(await r.sql("SELECT id FROM run_schedules")).toEqual([]);
    await sandbox.release("start");
    await until(async () => (await r.sql("SELECT state FROM runs"))[0]!["state"] === "running",
                "the late start recorded");
    expect((await recorder(r.stub)).starts.map((start) => start.run)).toEqual([row!["id"]]);
    await pass;
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

