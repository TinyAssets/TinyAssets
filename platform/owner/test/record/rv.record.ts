/**
 * The reviewer's RPC recorder (`ci/record.ts`): every door of the table
 * (`contract/doors.json`), on the gated owner class (a recording sandbox and the real
 * gateway), against a spread of callers made real in the object (full, scoped in each way,
 * revoked, re-signed, directory profile, API keys, live runs of the owner and of clients, the
 * runner, the sync agent) and of request shapes built from each row's fields, each answer one
 * `RV` line, ids taken out (`clean`). Then each door under each admission state.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, it } from "vitest";

import { OwnerDO } from "../../src/owner-do";
import { apiKeyOrigin, type Origin } from "../../../contract/src/binding";
import { rpcReserved } from "../../../contract/src/doors";
import table from "../../../contract/doors.json" with { type: "json" };
import { gated, type Gated } from "../gateway-support";
import { MCP, origin, signedIn } from "../ledger-support";
import {
  ALL_SCOPES, defineAgent, ownerRun, runBinding, runnerBinding, runnerSandbox, sandboxOf,
  startOf, syncOf, TIERS,
} from "../runs-support";
import { asked } from "./support";

type Row = { door: string; args: unknown };
const ROWS = (table as { doors: Row[] }).doors;
const PROTO = Object.getOwnPropertyNames(OwnerDO.prototype)
  .filter((name) => !rpcReserved().includes(name)).sort();

function clean(text: string): string {
  return text
    .replace(/owner-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g, "<owner>")
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g, "<uuid>")
    .replace(/[A-Za-z0-9_+/=-]{40,}/g, "<tok>")
    .replace(/[0-9a-f]{16,}/g, "<hex>")
    .replace(/\b\d{9,}(?:\.\d+)?\b/g, "<n>");
}

/**
 * `value` as a line shows it: an outside agent's id is minted at random (`agent_<hex>`, which
 * `clean` takes out) and the owner answers its agents in that id's order (`agentsPage`), so a
 * list of them is shown in its rows' order once cleaned, a total order of what the line shows.
 * An answer out of that id's order fails its line, so the sort never hides a breach of it.
 */
function shown(value: unknown): unknown {
  const agents = (value as { agents?: unknown } | null)?.agents;
  if (!Array.isArray(agents)) {
    return value;
  }
  const ids = agents.map((row) => String((row as { agent_id?: unknown }).agent_id));
  if (ids.some((id, index) => index > 0 && ids[index - 1]! >= id)) {
    throw new Error("agents answered out of their agent_id order (agentsPage)");
  }
  const keyed = agents.map((row: unknown) => [clean(JSON.stringify(row)), row] as const);
  keyed.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return { ...(value as object), agents: keyed.map(([, row]) => row) };
}

const NAMED: Record<string, unknown> = {
  path: "/workspace/rv.md", dir: "/workspace", from: "/workspace/rv.md", to: "/workspace/rv2.md",
  limit: 5, head_chars: 32, max_bytes: 4096, pad_bytes: 0, body_offset: 0, message_id: "x",
  body_message_id: "x", collection: "board", row_id: "r1", body: "hello", private: false,
  history_op: "set", client_message_id: "c1", parent_id: null, through: 0, content: "x",
  old_text: "x", new_text: "y", mode: 0, offset: 0, query: "rv", cursor: "x",
  id: "/workspace/rv.md", recursive: false, replace: false, version: 1, before: 1, etag: "e",
  message: "hi", client_send_id: "s1", agent_id: "main", conversation_id: "conv", wait_ms: 0,
  agent: "main", input: "go", at: 4102444800, cron: "0 0 * * *",
  run_id: `run_${"0".repeat(32)}`, schedule_id: `sch_${"0".repeat(32)}`, script: "return 1",
  change: {}, request_id: "q1", command_center_id: "alpha", display_name: "Rv",
  delivery_method: "pull", recipe_id: null, export_id: "x", sha256s: [],
  expected_generation: 1, policy: {}, proposal: null, max_wakes_per_hour: 1,
  batch_window_s: 0, client: "a".repeat(64), source: "mcp", scopes: [], expected_revision: 0,
  name: "x", profile: "custom", clients: [], key_id: "0123456789abcdef", confirm: "nope",
  event_id: "e1", event_at: 1, headers: {}, intent_id: "0".repeat(32), chunks: [], size: 1,
  card_id: "x", digest: "x", reason: "x", kind: "x", subscription_id: "x", board: false,
  rooms: [], blob: "x", kid: "k", arguments: {}, expires_at: null, first: 0, count: 1,
  frames: [], engine: "claude-code", authority: "owner", region: null, code: "x",
  signins: [], lease_id: "x", epoch: 1, ops: [], writer: null, before_revision: 1,
};

function valueOf(name: string, schema: Record<string, unknown>): unknown {
  const type = schema["type"];
  if (name === "after") {
    return type === "string" ? "" : 0;
  }
  if (name === "expected_etag") {
    return Array.isArray(type) ? null : "";
  }
  if (name in NAMED) {
    return NAMED[name];
  }
  return type === "string" ? "x" : type === "integer" || type === "number" ? 1
    : type === "boolean" ? false : null;
}

type Fields = { required: Record<string, Record<string, unknown>>;
                optional: Record<string, Record<string, unknown>> };

function filled(part: Fields, all: boolean): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, s] of Object.entries(part.required)) {
    out[k] = valueOf(k, s);
  }
  if (all) {
    for (const [k, s] of Object.entries(part.optional)) {
      out[k] = valueOf(k, s);
    }
  }
  return out;
}

function shapes(row: Row): [string, unknown][] {
  const out: [string, unknown][] = [["undef", undefined], ["null", null], ["empty", {}],
    ["extra", { __x: 1 }], ["array", [1]]];
  const args = row.args;
  if (typeof args !== "object" || args === null) {
    return out;
  }
  const parts: [string, Fields][] = "refused" in args ? [["", args as unknown as Fields]]
    : [["owner.", (args as { owner: Fields }).owner],
       ["outside.", (args as { outside: Fields }).outside]];
  for (const [label, part] of parts) {
    const req = filled(part, false);
    const all = filled(part, true);
    out.push([`${label}req`, req], [`${label}all`, all], [`${label}all+x`, { ...all, __x: 1 }]);
    const first = Object.keys(part.required)[0];
    if (first !== undefined) {
      const { [first]: _gone, ...less } = all;
      out.push([`${label}-${first}`, less], [`${label}${first}=undef`, { ...all, [first]: undefined }],
               [`${label}${first}=obj`, { ...all, [first]: { y: 1 } }]);
    }
  }
  return out;
}

type Caller = [string, unknown];

async function outcome(act: () => Promise<unknown>): Promise<string> {
  try {
    const value = await act();
    return `OK\t${clean(JSON.stringify(shown(value)) ?? "undefined").slice(0, 600)}`;
  } catch (error) {
    const e = error as Error & { code?: unknown };
    return `THROW\t${String(e?.name)}\t${String(e?.code)}\t${clean(String(e?.message)).slice(0, 400)}`;
  }
}

/** A client of `o` registered in alpha (its grant full), then its grant set. */
async function client(r: Gated, o: Origin, grant: { mode: string; scopes: unknown[];
                                                    profile?: string } | null,
                      register = true): Promise<string> {
  const bound = r.bind({ origin: o, source: MCP, authenticated_at: signedIn() });
  const log: string[] = [];
  if (register) {
    log.push(await outcome(async () => {
      const { card_id, digest } = await r.stub.registerPropose(bound, {
        command_center_id: "alpha", display_name: `A${o.client.slice(0, 4)}`,
        delivery_method: "pull", recipe_id: null });
      return r.stub.registerAgent(r.session(), { card_id, digest });
    }));
  } else {
    log.push(await outcome(() => r.stub.outsideAgents(bound)));
  }
  if (grant !== null) {
    log.push(await outcome(async () => {
      const row = (await r.stub.grantList(r.bind())).clients.find((c) => c.client === o.client)!;
      return r.stub.grantSet(r.session(), { client: o.client, source: "mcp", mode: grant.mode,
                                            scopes: grant.scopes as never,
                                            expected_revision: row.revision, name: "",
                                            ...(grant.profile === undefined ? {}
                                              : { profile: grant.profile }) });
    }));
  }
  return log.join(" | ");
}

const all = (universe: string, agent: string) =>
  ALL_SCOPES.map((capability) => ({ universe, agent, capability }));

/** Every caller, made real in a fresh owner; the setup's own answers logged. */
async function callers(r: Gated, setup: string[]): Promise<Caller[]> {
  const conn = await r.connect("sk-good");
  const file = { engine: "claude-code", credential: "api_key", connection: conn,
                 scopes: ALL_SCOPES };
  await defineAgent(r.stub, r.bind(), "main", file);
  await defineAgent(r.stub, r.bind(), "narrow", { ...file, scopes: ["read_files"] });
  const FULL = origin(1);
  const FDIR = origin(2);
  const SALL = origin(3);
  const SUNI = origin(4);
  const SAGT = origin(5);
  const SREAD = origin(6);
  const SNOAG = origin(7);
  const REV = origin(8);
  const RESIGN = origin(9);
  setup.push(await client(r, FULL, null));
  setup.push(await client(r, FDIR, { mode: "full", scopes: [], profile: "directory" }));
  setup.push(await client(r, SALL, { mode: "scoped", scopes: all("alpha", "main") }));
  setup.push(await client(r, SUNI, { mode: "scoped", scopes: all("beta", "main") }));
  setup.push(await client(r, SAGT, { mode: "scoped", scopes: all("alpha", "other") }));
  setup.push(await client(r, SREAD, { mode: "scoped", scopes: [
    { universe: "alpha", agent: "main", capability: "read_files" },
    { universe: "alpha", agent: "main", capability: "read_graph" }] }));
  setup.push(await client(r, SNOAG, { mode: "scoped", scopes: all("alpha", "main") }, false));
  setup.push(await client(r, REV, null));
  setup.push(await outcome(() => r.stub.grantRevoke(r.bind(), { client: REV.client })));
  setup.push(await client(r, RESIGN, null));
  setup.push(await outcome(() => r.stub.grantRevoke(r.bind(), { client: RESIGN.client })));
  setup.push(await outcome(async () => {
    const row = (await r.stub.grantList(r.bind())).clients.find((c) => c.client === RESIGN.client)!;
    return r.stub.grantSet(r.session(), { client: RESIGN.client, source: "mcp", mode: "full",
                                          scopes: [], expected_revision: row.revision, name: "" });
  }));
  // API keys: none granted, full, scoped (registered in alpha), and one revoked.
  const keyOf = (id: string) => ({ o: apiKeyOrigin(id), source: { kind: "api_key", ref: id } as const });
  const K0 = keyOf("0000000000000000");
  const KF = keyOf("1111111111111111");
  const KS = keyOf("2222222222222222");
  const KR = keyOf("3333333333333333");
  for (const [k, mode, scopes] of [[KF, "full", []], [KS, "full", []], [KR, "full", []]] as const) {
    setup.push(await outcome(() => r.stub.grantSet(r.session(), {
      client: k.o.client, source: "api_key", mode, scopes: scopes as never, expected_revision: 0,
      name: "" })));
  }
  setup.push(await outcome(() => r.stub.registerAgent(
    r.bind({ origin: KS.o, source: KS.source, session: r.cookie }),
    { command_center_id: "alpha", display_name: "Key", delivery_method: "pull",
      recipe_id: null } as never)));
  setup.push(await outcome(async () => {
    const row = (await r.stub.grantList(r.bind())).clients.find((c) => c.client === KS.o.client)!;
    return r.stub.grantSet(r.session(), { client: KS.o.client, source: "api_key", mode: "scoped",
      scopes: [{ universe: "alpha", agent: "main", capability: "read_files" }] as never,
      expected_revision: row.revision, name: "" });
  }));
  setup.push(await outcome(() => r.stub.grantRevoke(r.bind(), { client: KR.o.client })));
  // Live runs: the owner's (all scopes, and narrowed), a full client's, a scoped client's.
  const own = await ownerRun(r.stub, r.bind(), undefined, "main");
  const narrow = await ownerRun(r.stub, r.bind(), undefined, "narrow");
  const fullRun = await r.stub.agentSubmit({ ...r.bind({ origin: FULL, source: MCP,
                                                         authenticated_at: signedIn() }),
                                             tiers: TIERS }, { message: "hi", client_send_id: "f-1" });
  const fullStart = await startOf(r.stub, fullRun.run_id);
  const scopedRun = await r.stub.agentSubmit({ ...r.bind({ origin: SALL, source: MCP,
                                                           authenticated_at: signedIn() }),
                                               tiers: TIERS }, { message: "hi", client_send_id: "s-1" });
  const scopedStart = await startOf(r.stub, scopedRun.run_id);
  const b = (o: Origin, extra: Record<string, unknown> = {}) =>
    r.bind({ origin: o, source: MCP, ...extra });
  const owner = r.name;
  return [
    ["undefined", undefined],
    ["emptybinding", {}],
    ["anon", { owner, caller: "someone", origin: null }],
    ["wrongowner", { owner: "owner-other", caller: "owner-other", origin: null, tiers: TIERS }],
    ["wrongcaller", { owner, caller: "owner-other", origin: null, tiers: TIERS, session: r.cookie }],
    ["badorigin", { owner, caller: owner, origin: { client: "x" }, source: MCP }],
    ["noorigin", { owner, caller: owner }],
    ["visitor", { owner, caller: "owner-visitor", origin: null, tiers: TIERS }],
    ["platform", { owner, caller: "platform", origin: null }],
    ["platform+session", { owner, caller: "platform", origin: null, session: r.cookie }],
    ["platform+source", { owner, caller: "platform", origin: null, source: MCP }],
    ["mcpfresh", b(origin(20))],
    ["mcp+session", b(origin(21), { session: r.cookie })],
    ["full", b(FULL)],
    ["full+signed", b(FULL, { authenticated_at: signedIn() })],
    ["fulldir", b(FDIR)],
    ["scopedall", b(SALL)],
    ["scopeduniverse", b(SUNI)],
    ["scopedagent", b(SAGT)],
    ["scopedread", b(SREAD)],
    ["scopednoagent", b(SNOAG)],
    ["revoked", b(REV)],
    ["resignstale", b(RESIGN)],
    ["resignfresh", b(RESIGN, { authenticated_at: signedIn() + 3600 })],
    ["key0", r.bind({ origin: K0.o, source: K0.source })],
    ["keyfull", r.bind({ origin: KF.o, source: KF.source })],
    ["keyscoped", r.bind({ origin: KS.o, source: KS.source })],
    ["keyrevoked", r.bind({ origin: KR.o, source: KR.source })],
    ["keyregistering", r.bind({ origin: KF.o, source: KF.source, session: r.cookie })],
    ["keysource-mismatch", r.bind({ origin: FULL, source: KF.source })],
    ["runowner", runBinding(r, own.start)],
    ["runnarrow", runBinding(r, narrow.start)],
    ["runfull", runBinding(r, fullStart)],
    ["runscoped", runBinding(r, scopedStart)],
    ["runwrongsecret", { ...runBinding(r, own.start), run: narrow.run.run_id }],
    ["runner", runnerBinding(r, own.start)],
    ["runnersandbox", runnerSandbox(r, own.start)],
    ["runnersandboxclient", runnerSandbox(r, fullStart)],
    ["egresslease", sandboxOf(r, own.start)],
    ["sync", syncOf(r, own.start)],
    ["syncclient", syncOf(r, fullStart)],
    ["syncscoped", syncOf(r, scopedStart)],
    ["syncmixed", { ...syncOf(r, own.start), sandbox: { ...(syncOf(r, own.start).sandbox as object),
                                                         egress: (syncOf(r, fullStart).sandbox as { egress: string }).egress } }],
    ["ownernotiers", { owner, caller: owner, origin: null }],
    ["owner", r.bind()],
    ["owner+source", r.bind({ source: MCP })],
    ["owner+signed", r.bind({ authenticated_at: signedIn() })],
    ["ownersession", r.session()],
    ["ownerbadsession", r.bind({ session: "nope" })],
  ];
}

describe("rv rpc: the table's doors", () => {
  it("lists the same methods", () => {
    console.log(`RV\tPROTO\t${PROTO.join(",")}`);
    console.log(`RV\tTABLE\t${ROWS.map((row) => row.door).sort().join(",")}`);
  });
  for (const row of ROWS) {
    it(`door ${row.door}`, { tags: ["large"] }, async () => {
      const r = await gated();
      const setup: string[] = [];
      const list = await callers(r, setup);
      const lines = [`RV\t${row.door}\tSETUP\t${clean(setup.join(" || ")).slice(0, 2000)}`];
      for (const [who, binding] of list) {
        for (const [shape, request] of shapes(row)) {
          lines.push(`RV\t${row.door}\t${who}\t${shape}\t${await outcome(() =>
            asked(r.stub, row.door, binding, request))}`);
        }
      }
      console.log(lines.join("\n"));
    });
  }
});

describe("rv rpc: each door under each admission state", () => {
  const STATES: [string, Record<string, unknown>[] | "pending"][] = [
    ["billing", [{ reason: "billing", suspended: true }]],
    ["abuse", [{ reason: "abuse", suspended: true }]],
    ["both", [{ reason: "billing", suspended: true }, { reason: "abuse", suspended: true }]],
    ["pending", "pending"],
  ];
  for (const row of ROWS) {
    it(`admission ${row.door}`, { tags: ["large"] }, async () => {
      const lines: string[] = [];
      for (const [state, changes] of STATES) {
        const r = await gated();
        const conn = await r.connect("sk-good");
        await defineAgent(r.stub, r.bind(), "main", { engine: "claude-code", credential: "api_key",
                                                      connection: conn, scopes: ALL_SCOPES });
        const own = await ownerRun(r.stub, r.bind(), undefined, "main");
        await client(r, origin(1), null);
        const platform = { owner: r.name, caller: "platform", origin: null };
        const list: Caller[] = [["owner", r.bind()], ["session", r.session()],
          ["full", r.bind({ origin: origin(1), source: MCP })], ["run", runBinding(r, own.start)],
          ["runner", runnerBinding(r, own.start)], ["sync", syncOf(r, own.start)],
          ["platform", platform]];
        if (changes === "pending") {
          await runInDurableObject(r.stub, (_i: OwnerDO, s) => {
            s.storage.sql.exec("UPDATE owner_admission SET state = 'pending', reasons = '[]'");
          });
        } else {
          for (const change of changes) {
            lines.push(`RV\tADM\t${row.door}\t${state}\tset\t${await outcome(() =>
              r.stub.admissionSet(platform as never, change as never))}`);
          }
        }
        for (const [who, binding] of list) {
          for (const [shape, request] of shapes(row).filter(([s]) =>
            ["empty", "req", "all", "owner.req", "outside.req", "undef"].includes(s))) {
            lines.push(`RV\tADM\t${row.door}\t${state}\t${who}\t${shape}\t${await outcome(() =>
              asked(r.stub, row.door, binding, request))}`);
          }
        }
      }
      console.log(lines.join("\n"));
    });
  }
});
