/**
 * Runs at the doors (slice R), in workerd, through the real owner object with a recording
 * fake behind its `SANDBOX` (`entry.ts`): the run tools on `/mcp` and `/api`, the owner's
 * `/app/api/agent/*` routes, the tier numbers the control record holds, a run's own MCP door
 * and event door on the egress host, the sandbox's sign-in report door, and the owner
 * app's live socket on a conversation
 * (A6's ticket, binder path and sign-out, salvaged: a ticket is single-use, a bearer opens
 * nothing, and a sign-out closes exactly its device's sockets).
 */
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { env, runInDurableObject } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

import {
  EGRESS_ORIGIN, GENERATION_HEADER, RUN_HEADER, RUNNER_HEADER, SANDBOX_HEADER,
} from "../../contract/src/egress";
import type { Binding } from "../../owner/src/owner-do";
import type { SandboxStart } from "../../owner/src/sandbox";
import { TICKET_REFUSED } from "../../contract/src/socket-shape";
import { defineAgent, secretOf } from "../../owner/test/runs-support";
import { refusedStatus } from "../src/egress";
import { RUN_INSTRUCTIONS } from "../src/mcp";
import { profileNames } from "../src/tools";
import { EVENT_FRAMES_MAX } from "../../contract/src/run-events";
import { SESSION_REFUSED } from "../../owner/src/runs";
import type { OwnerDO } from "./entry";
import {
  appPost, call, connect, OPEN, reached, registerClient, rpcBody, serveFirstPartyDocument,
  setControl, signInApp, sqlIn, type Tokens, url,
  answerOf,
} from "./support";

/** The run doors' tools: what a run's own MCP door never serves. */
const RUN_TOOLS: readonly string[] = Object.freeze(["converse", "run_graph", "run_status",
                                                    "run_cancel"]);

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

const SIGNED_IN = Object.freeze({ engine: "claude-code", credential: "vendor_login_in_sandbox",
                                  scopes: ["read_files", "read_graph"] });

let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

/** The owner's own binding, as the object's file doors take it in a test. */
function ownerBinding(who: string): Binding {
  return { owner: who, caller: who, origin: null, tiers: OPEN.tiers };
}

/** A signed-in owner whose `main` agent is Claude Code on its own sign-in. */
async function owned(file: Record<string, unknown> = SIGNED_IN) {
  const who = owner();
  const app = await signInApp({ owner: who });
  await defineAgent(env.OWNER.getByName(who), ownerBinding(who), "main", file);
  return { who, app };
}

/**
 * A signed-in owner whose `main` agent runs pi on the owner's own key: an outside client's
 * run spends it, its sandbox holding no login (a login is never copied between sandboxes).
 */
async function ownedOnKey() {
  const who = owner();
  const app = await signInApp({ owner: who });
  const connected = await appPost("connections/key", app.access_token, app.session,
                                  { source: "groq", key: `gsk-good-${crypto.randomUUID()}` });
  expect(connected.status).toBe(200);
  const { connection } = await connected.json<{ connection: string }>();
  await defineAgent(env.OWNER.getByName(who), ownerBinding(who), "main",
                    { engine: "pi", credential: "api_key", connection,
                      scopes: ["read_files", "read_graph"] });
  return { who, app };
}

/** The recorded starts of `who`'s object. */
function starts(who: string): Promise<SandboxStart[]> {
  return runInDurableObject(env.OWNER.getByName(who), (instance: unknown) =>
    (instance as OwnerDO).recorder.starts);
}

/** Each lease's egress token and runner secret, as the start that opened it got them. */
const TOKENS = new Map<string, { egress: string; runner: string }>();

function tokenOf(start: SandboxStart): string {
  if (start.tokens !== null) {
    TOKENS.set(start.lease.lease_id, { egress: start.tokens.egress_token,
                                       runner: start.tokens.runner_token });
  }
  return TOKENS.get(start.lease.lease_id)!.egress;
}

/** The runner's own secret, as its event and sign-in posts present it. */
function runnerOf(start: SandboxStart): Record<string, string> {
  tokenOf(start);
  return { [RUNNER_HEADER]: TOKENS.get(start.lease.lease_id)!.runner };
}

async function mcpClient(fetcher: typeof fetch, at: string,
                         headers: Record<string, string>): Promise<Client> {
  const client = new Client({ name: "test-agent", version: "1.0.0" },
                            { versionNegotiation: { mode: { pin: "2026-07-28" } } });
  await client.connect(new StreamableHTTPClientTransport(new URL(at), {
    fetch: fetcher, requestInit: { headers } }));
  return client;
}

/** An outside client's MCP session on `/mcp`. */
function outside(tokens: Tokens): Promise<Client> {
  return mcpClient((input, init) => call(new Request(input, init)), url("/mcp"),
                   { Authorization: `Bearer ${tokens.access_token}` });
}

/** A run's engine's MCP session on its own door, its egress token set as E2B's transform. */
function engine(start: SandboxStart): Promise<Client> {
  return mcpClient((input, init) => call(new Request(input, init)), start.urls.mcp,
                   { [SANDBOX_HEADER]: tokenOf(start), [GENERATION_HEADER]: "1" });
}

/** The runner's report of its run's frames, on its event door. */
function report(start: SandboxStart, body: unknown): Promise<Response> {
  return call(new Request(`${EGRESS_ORIGIN}/r/${start.run}/events`, {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", [SANDBOX_HEADER]: tokenOf(start),
               [GENERATION_HEADER]: "1", ...runnerOf(start) } }));
}

function structured(result: unknown): Record<string, unknown> {
  return answerOf(result);
}

describe("the run tools", () => {
  it("start, read and cancel a run over MCP, each client held to its own", async () => {
    const { who } = await ownedOnKey();
    const client = await outside(await connect(await registerClient(), { owner: who }));
    const { tools } = await client.listTools();
    expect(RUN_TOOLS.filter((name) => tools.some((tool) => tool.name === name)))
      .toEqual(RUN_TOOLS);
    const run = structured(await client.callTool({ name: "converse", arguments: {
      message: "hello", client_send_id: "c-1" } }));
    expect(run).toMatchObject({ state: "running", initiator: "client", agent: "main" });
    expect((await starts(who)).map((start) => start.prompt)).toEqual(["hello"]);
    expect(structured(await client.callTool({ name: "run_status",
                                              arguments: { run_id: run["run_id"] } })))
      .toMatchObject({ run_id: run["run_id"], state: "running" });
    // Another client of the owner's reads none of this one's runs.
    const other = await outside(await connect(await registerClient("Dot"), { owner: who }));
    const peeked = await other.callTool({ name: "run_status",
                                         arguments: { run_id: run["run_id"] } });
    expect(peeked.isError).toBe(true);
    expect(JSON.stringify(peeked.content)).toContain("no_run");
    const cancelled = structured(await client.callTool({ name: "run_cancel",
                                                         arguments: { run_id: run["run_id"] } }));
    expect(cancelled).toMatchObject({ run_id: run["run_id"] });
    await client.close();
    await other.close();
  });

  it("are refused without the control record's tier numbers, and held to them", async () => {
    const { who, app } = await owned();
    await setControl({ ...OPEN, tiers: undefined });
    const unread = await appPost("agent/submit", app.access_token, null,
                                 { message: "x", client_send_id: "t-1" });
    expect(unread.status).toBe(403);
    expect(await unread.json()).toEqual({ error: expect.stringMatching(/^tier_unread/) });
    // The founder edits the record: one run at once on the free tier.
    await setControl({ ...OPEN, tiers: { free: { runs: 1, storage_bytes: 1 },
                                         paid: { runs: 2, storage_bytes: 1 } } });
    const first = await appPost("agent/submit", app.access_token, null,
                                { message: "x", client_send_id: "t-2" });
    expect(first.status).toBe(200);
    const second = await appPost("agent/submit", app.access_token, null,
                                 { message: "x", client_send_id: "t-3" });
    expect([second.status, await second.json()])
      .toEqual([403, { error: "tier_runs: the owner's tier runs this many runs at once (free: 1)" }]);
    expect(await starts(who)).toHaveLength(1);
  });
});

describe("the owner's /app/api/agent/* routes", () => {
  it("submit, read, cancel and schedule runs", async () => {
    const { who, app } = await owned();
    const submitted = await appPost("agent/submit", app.access_token, null,
                                    { message: "hi", client_send_id: "a-1" });
    expect(submitted.status).toBe(200);
    const run = await submitted.json<{ run_id: string; conversation_id: string }>();
    const status = await appPost("agent/status", app.access_token, null, { run_id: run.run_id });
    expect(await status.json()).toMatchObject({ run_id: run.run_id, state: "running" });
    const cancel = await appPost("agent/cancel", app.access_token, null, { run_id: run.run_id });
    expect(cancel.status).toBe(200);
    expect(await runInDurableObject(env.OWNER.getByName(who), (instance: unknown) =>
      (instance as OwnerDO).recorder.cancels.map((each) => each.run))).toEqual([run.run_id]);
    const scheduled = await appPost("agent/run", app.access_token, null, {
      agent: "main", input: "nightly", cron: "0 3 * * *" });
    expect(await scheduled.json()).toMatchObject({ agent: "main", trigger: "cron" });
  });
});

describe("a run's own doors on the egress host", () => {
  it("serve its MCP tools but converse, held to its grant's scopes", async () => {
    const { who, app } = await owned();
    await appPost("agent/submit", app.access_token, null, { message: "go", client_send_id: "e-1" });
    const [start] = await starts(who);
    const client = await engine(start!);
    const { tools } = await client.listTools();
    // Work between agents is mail: no converse. Its own runs and schedules it makes, reads
    // and cancels (the founder, 2026-11-03).
    expect(tools.filter((tool) => RUN_TOOLS.includes(tool.name)).map((tool) => tool.name))
      .toEqual(["run_graph", "run_status", "run_cancel"]);
    // Nor a mailbox tool (slice ID): the inbox digest's mail part is a mailbox read.
    expect(tools.some((tool) => ["mailbox_read", "mailbox_ack", "inbox_digest"]
      .includes(tool.name))).toBe(false);
    // Exactly the run profile, code mode on by default.
    expect(tools.map((tool) => tool.name)).toEqual(profileNames("run"));
    // The engine is told where working files go: its scratch, never the owner's storage.
    expect(client.getInstructions()).toBe(RUN_INSTRUCTIONS);
    expect(structured(await client.callTool({ name: "file_stat",
                                              arguments: { path: "/workspace" } })))
      .toMatchObject({ entry: { kind: "dir" } });
    // Its grant holds no write_files: the door refuses.
    const refused = await client.callTool({ name: "file_mkdir", arguments: {
      path: "/workspace/x", expected_etag: null } });
    expect(refused.isError).toBe(true);
    expect(JSON.stringify(refused.content)).toContain("capability not granted");
    await client.close();
    // A forged token reaches no object; a browser's call is none of the engine's.
    const forged = await call(new Request(`${EGRESS_ORIGIN}/r/${start!.run}/events`, {
      method: "POST", body: "{}", headers: { [SANDBOX_HEADER]: "x.1.y.z",
                                             [GENERATION_HEADER]: "1" } }));
    expect(forged.status).toBe(401);
    const browser = await call(new Request(start!.urls.mcp, {
      method: "POST", body: "{}", headers: { Origin: url(""), [SANDBOX_HEADER]: tokenOf(start!),
                                             [GENERATION_HEADER]: "1" } }));
    expect(browser.status).toBe(404);
  });

  it("serve a run no MCP Events: it lists none and may subscribe or end none", async () => {
    const { who, app } = await owned();
    await appPost("agent/submit", app.access_token, null, { message: "go", client_send_id: "v-1" });
    const [start] = await starts(who);
    const post = async (method: string, params: unknown) => rpcBody(await call(new Request(
      start!.urls.mcp, { method: "POST", body: JSON.stringify({ jsonrpc: "2.0", id: 1, method,
                                                                params }),
                         headers: { [SANDBOX_HEADER]: tokenOf(start!), [GENERATION_HEADER]: "1",
                                    "Content-Type": "application/json",
                                    Accept: "application/json, text/event-stream",
                                    "MCP-Protocol-Version": "2025-11-25" } })));
    expect((await post("events/list", {}))["result"]).toEqual({ events: [] });
    const args = { command_center_id: "alpha", board: true };
    const delivery = { mode: "webhook", url: "https://callback.test/echo/run",
                       secret: "whsec_" + btoa("k".repeat(32)) };
    for (const [method, params] of [
      ["events/subscribe", { name: "tinyassets.changed", arguments: args, delivery }],
      ["events/unsubscribe", { name: "tinyassets.changed", arguments: args, delivery }],
    ] as const) {
      expect((await post(method, params))["error"]).toMatchObject({ code: -32024 });
    }
    expect(await sqlIn(who, "SELECT 1 FROM wake_callbacks")).toEqual([]);
  });

  it("take a run's own secret as its header, where its CLI sends one", async () => {
    const { who, app } = await owned();
    await appPost("agent/submit", app.access_token, null, { message: "go", client_send_id: "h-1" });
    const [start] = await starts(who);
    const sandbox = { [SANDBOX_HEADER]: tokenOf(start!), [GENERATION_HEADER]: "1" };
    const viaHeader = await mcpClient((input, init) => call(new Request(input, init)),
                                      start!.urls.mcp_bare,
                                      { ...sandbox, [RUN_HEADER]: secretOf(start!) });
    expect((await viaHeader.listTools()).tools.length).toBeGreaterThan(0);
    await viaHeader.close();
    const bare = await call(new Request(start!.urls.mcp_bare, {
      method: "POST", body: "{}", headers: { "Content-Type": "application/json", ...sandbox } }));
    expect(bare.status).toBe(401);
    const wrong = await call(new Request(start!.urls.mcp_bare, {
      method: "POST", body: "{}",
      headers: { "Content-Type": "application/json", ...sandbox, [RUN_HEADER]: "0".repeat(64) } }));
    expect(wrong.status).not.toBe(200);
  });

  it("answer a post the object holds no room for 503, for the runner to post again", () => {
    expect(refusedStatus("events_busy", 403)).toBe(503);
    expect(refusedStatus("run_ended", 403)).toBe(403);
  });

  it("take a run's events only by the runner's secret, within the post's bounds",
     async () => {
    const { who, app } = await owned();
    await appPost("agent/submit", app.access_token, null, { message: "go", client_send_id: "b-1" });
    const [start] = await starts(who);
    const post = (headers: Record<string, string>, frames: unknown[]) =>
      call(new Request(`${EGRESS_ORIGIN}/r/${start!.run}/events`, {
        method: "POST", body: JSON.stringify({ frames }),
        headers: { "Content-Type": "application/json", [SANDBOX_HEADER]: tokenOf(start!),
                   [GENERATION_HEADER]: "1", ...headers } }));
    const missing = await post({}, []);
    expect([missing.status, await missing.json()])
      .toEqual([401, { error: { code: "runner_token_missing", message: "runner_token_missing" } }]);
    const many = Array.from({ length: EVENT_FRAMES_MAX + 1 },
                            (_, i) => ({ seq: i + 1, at: 1, event: {} }));
    const large = await post(runnerOf(start!), many);
    expect([large.status, ((await large.json()) as { error: { code: string } }).error.code])
      .toEqual([413, "events_too_large"]);
    expect((await post(runnerOf(start!), many.slice(0, 2))).status).toBe(200);
  });

  it("take the runner's sign-in report by the sandbox's egress token, and nothing else",
     async () => {
    const { who, app } = await owned();
    await appPost("agent/submit", app.access_token, null, { message: "go", client_send_id: "s-1" });
    const [start] = await starts(who);
    const signin = (headers: Record<string, string>, method = "POST") =>
      call(new Request(`${EGRESS_ORIGIN}/signin`, {
        method, body: method === "POST"
          ? JSON.stringify({ signins: [{ engine: "claude-code", signed_in: true }] }) : null,
        headers: { "Content-Type": "application/json", ...headers } }));
    const sandbox = { [SANDBOX_HEADER]: tokenOf(start!), [GENERATION_HEADER]: "1",
                      ...runnerOf(start!) };
    const reported = await signin(sandbox);
    expect(reported.status).toBe(200);
    expect(await reported.json()).toEqual({ recorded: 1, lapsed: [] });
    expect(start!.urls.signin).toBe(`${EGRESS_ORIGIN}/signin`);
    expect((await signin({ [SANDBOX_HEADER]: "x.1.y.z", [GENERATION_HEADER]: "1",
                           ...runnerOf(start!) })).status).toBe(401);
    // The egress token alone, without the runner's secret (a CLI's call), is refused.
    expect((await signin({ [SANDBOX_HEADER]: tokenOf(start!), [GENERATION_HEADER]: "1" }))
      .status).toBe(401);
    // A wrong runner secret reaches the object, which refuses it.
    expect((await signin({ ...sandbox, [RUNNER_HEADER]: "0".repeat(64) })).status).toBe(403);
    expect((await signin({})).status).toBe(401);
    expect((await signin({ ...sandbox, Origin: url("") })).status).toBe(404);
    expect((await signin(sandbox, "GET")).status).toBe(405);
    expect(await runInDurableObject(env.OWNER.getByName(who), (_instance, state) =>
      state.storage.sql.exec("SELECT engine, signed_in FROM engine_signins").toArray()))
      .toEqual([{ engine: "claude-code", signed_in: 1 }]);
  });
});

/** A socket ticket for `conversation`, from the owner's token. */
async function ticketFor(token: string, conversation: string): Promise<string> {
  const minted = await appPost("agent/socket-ticket", token, null,
                               { conversation_id: conversation });
  expect(minted.status).toBe(200);
  const body = await minted.json<{ ticket: string; conversation_id: string }>();
  expect(body.conversation_id).toBe(conversation);
  return body.ticket;
}

/** The app's upgrade on the doors, as a browser's `WebSocket` sends it. */
function socket(ticket: string | null, conversation: string | null,
                headers: Record<string, string> = {}): Promise<Response> {
  const query = new URLSearchParams();
  if (conversation !== null) {
    query.set("conversation_id", conversation);
  }
  if (ticket !== null) {
    query.set("ticket", ticket);
  }
  return call(new Request(url(`/app/api/agent/socket?${query}`), {
    headers: { Upgrade: "websocket", Origin: url(""), ...headers } }));
}

type Frame = { v: number; run: string; seq: number; kind: string; event: unknown };

function frames(response: Response) {
  expect(response.status).toBe(101);
  const ws = response.webSocket!;
  const seen: Frame[] = [];
  let closed: number | null = null;
  ws.accept();
  ws.addEventListener("message", (event) => seen.push(JSON.parse(String(event.data)) as Frame));
  ws.addEventListener("close", (event) => {
    closed = event.code;
  });
  return { frames: seen, ws, closed: () => closed };
}

async function until(check: () => boolean, what: string): Promise<void> {
  for (let i = 0; i < 500; i++) {
    if (check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timed out waiting for ${what}`);
}

describe("a run's live events", () => {
  it("reach a socket on the run's conversation, a late joiner its tail, never committed",
     async () => {
    const { who, app } = await owned();
    const run = await (await appPost("agent/submit", app.access_token, null, {
      message: "go", client_send_id: "l-1" })).json<{ conversation_id: string }>();
    const [start] = await starts(who);
    const early = frames(await socket(await ticketFor(app.access_token, run.conversation_id),
                                      run.conversation_id));
    const sent = await report(start!, { frames: [
      { seq: 1, at: 1, event: { type: "system", subtype: "init", session_id: "s-1" } },
      { seq: 2, at: 2, event: { type: "assistant", message: { content: [] } } }] });
    expect(await sent.json()).toEqual({ accepted: 2, settled: null });
    await until(() => early.frames.length === 2, "the frames");
    expect(early.frames.map((frame) => [frame.v, frame.run, frame.seq, frame.kind]))
      .toEqual([[1, start!.run, 1, "init"], [1, start!.run, 2, "tool_call"]]);
    // A late joiner gets the live run's tail first.
    const late = frames(await socket(await ticketFor(app.access_token, run.conversation_id),
                                     run.conversation_id));
    await until(() => late.frames.length === 2, "the tail");
    // A retried batch's frames are not relayed twice; the end settles the run.
    await report(start!, { frames: [
      { seq: 2, at: 2, event: { type: "assistant", message: { content: [] } } },
      { seq: 3, at: 3, event: { type: "result", result: "done" } }], end: { exit_code: 0 } });
    await until(() => early.frames.length === 3, "the result");
    expect(early.frames[2]).toMatchObject({ seq: 3, kind: "result" });
    // What is durable is the run, its result and the session id; no frame is a row.
    const stored = await runInDurableObject(env.OWNER.getByName(who), (_instance, state) =>
      JSON.stringify(state.storage.sql.exec("SELECT * FROM runs").toArray()));
    expect(stored).toContain("\"result\":\"done\"");
    expect(stored).not.toContain("assistant");
    // Its tail went with its settle: a joiner now sees none of its frames.
    const after = frames(await socket(await ticketFor(app.access_token, run.conversation_id),
                                      run.conversation_id));
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(after.frames).toEqual([]);
    after.ws.close(1000, "done");
    early.ws.close(1000, "done");
    late.ws.close(1000, "done");
  });

  it("relay a frame whose session id is outside its shape, refusing the id with a fixed code " +
     "(app review)", async () => {
    const { who, app } = await owned();
    const run = await (await appPost("agent/submit", app.access_token, null, {
      message: "go", client_send_id: "bad-sid" })).json<{ run_id: string;
                                                          conversation_id: string }>();
    const [start] = await starts(who);
    const sent = await report(start!, { frames: [
      { seq: 1, at: 1, event: { type: "system", subtype: "init", session_id: "has a space" } },
      { seq: 2, at: 2, event: { type: "result", result: "done" } }], end: { exit_code: 0 } });
    expect(sent.status).toBe(200);
    expect(await sent.json()).toMatchObject({ accepted: 2 });
    const status = await (await appPost("agent/status", app.access_token, null,
                                        { run_id: run.run_id })).json<Record<string, unknown>>();
    expect(status["error"]).toBe(SESSION_REFUSED);
    const kept = await runInDurableObject(env.OWNER.getByName(who), (_instance, state) =>
      state.storage.sql.exec("SELECT session_id FROM conversations WHERE id = ?",
                             run.conversation_id).toArray());
    expect(kept).toEqual([{ session_id: null }]);
  });

  it("keep a settled run's tail no longer: the live runs' tails have its room", async () => {
    const { who, app } = await owned();
    await appPost("agent/submit", app.access_token, null, { message: "a", client_send_id: "t-a" });
    const b = await (await appPost("agent/submit", app.access_token, null, {
      message: "b", client_send_id: "t-b" })).json<{ conversation_id: string }>();
    const [first, second] = await starts(who);
    // Frames of 30,000 units, two a post (within the post's own bounds).
    const big = (seq: number) => ({ seq, at: seq,
                                    event: { type: "assistant", text: "x".repeat(30_000) } });
    const post = async (start: SandboxStart, frames: number) => {
      for (let seq = 1; seq <= frames; seq += 2) {
        await report(start, { frames: [big(seq), big(seq + 1)] });
      }
    };
    await post(first!, 34);
    await report(first!, { frames: [], end: { exit_code: 0 } });
    // The live run's tail, near the bound: the settled run's took no room of it.
    await post(second!, 118);
    const late = frames(await socket(await ticketFor(app.access_token, b.conversation_id),
                                     b.conversation_id));
    await until(() => late.frames.length === 118, "the live run's whole tail");
    expect(late.frames.map((frame) => frame.seq))
      .toEqual(Array.from({ length: 118 }, (_, i) => i + 1));
    late.ws.close(1000, "done");
  });

  it("reach only the sockets on the run's own conversation", async () => {
    const { who, app } = await owned();
    const first = await (await appPost("agent/submit", app.access_token, null, {
      message: "a", client_send_id: "o-1" })).json<{ conversation_id: string }>();
    const second = await (await appPost("agent/submit", app.access_token, null, {
      message: "b", client_send_id: "o-2" })).json<{ conversation_id: string }>();
    const [start] = await starts(who);
    const mine = frames(await socket(await ticketFor(app.access_token, first.conversation_id),
                                     first.conversation_id));
    const theirs = frames(await socket(await ticketFor(app.access_token,
                                                       second.conversation_id),
                                       second.conversation_id));
    await report(start!, { frames: [{ seq: 1, at: 1, event: { type: "result", result: "x" } }] });
    await until(() => mine.frames.length === 1, "the frame");
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(theirs.frames).toEqual([]);
    mine.ws.close(1000, "done");
    theirs.ws.close(1000, "done");
  });

  it("take no forged ticket: the doors refuse it before any object", async () => {
    const forged = `user_${crypto.randomUUID().replace(/-/g, "")}`;
    const answer = await socket(forged, `cv_${"0".repeat(32)}`);
    expect([answer.status, await answer.json()]).toEqual([403, { error: TICKET_REFUSED }]);
    expect(await reached(forged)).toBe(false);
  });

  it("take no bearer: a ticket opens one socket, once, for its own conversation, and a " +
     "sign-out closes it", async () => {
    const { who, app } = await owned();
    const first = await (await appPost("agent/submit", app.access_token, null, {
      message: "a", client_send_id: "k-1" })).json<{ conversation_id: string }>();
    const second = await (await appPost("agent/submit", app.access_token, null, {
      message: "b", client_send_id: "k-2" })).json<{ conversation_id: string }>();
    // The token as a bearer, with no ticket, opens nothing; nor does a token as a ticket.
    expect((await socket(null, first.conversation_id,
                         { Authorization: `Bearer ${app.access_token}` })).status).toBe(400);
    expect((await socket(app.access_token, first.conversation_id)).status).toBe(403);
    const ticket = await ticketFor(app.access_token, first.conversation_id);
    const elsewhere = await socket(ticket, second.conversation_id);
    expect([elsewhere.status, await elsewhere.json()]).toEqual([403, { error: TICKET_REFUSED }]);
    expect((await socket(ticket, first.conversation_id)).status).toBe(403);  // spent
    const fresh = await ticketFor(app.access_token, first.conversation_id);
    const open = frames(await socket(fresh, first.conversation_id));
    expect((await socket(fresh, first.conversation_id)).status).toBe(403);  // replayed
    // Another device of the owner's, on its own grant, with its own socket.
    const other = await signInApp({ owner: who });
    const kept = frames(await socket(await ticketFor(other.access_token, first.conversation_id),
                                     first.conversation_id));
    // The sign-out of the device's grant closes its socket, cookie or not; not the other's.
    const out = await appPost("session/close", app.access_token, null, {});
    expect(out.status).toBe(200);
    await until(() => open.closed() !== null, "the close");
    expect(open.closed()).toBe(4440);
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(kept.closed()).toBeNull();
    kept.ws.close(1000, "done");
    void who;
  });
});
