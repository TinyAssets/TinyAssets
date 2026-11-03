/**
 * Code mode at the MCP door (slice M): the object's `codeRun`, in workerd with the real
 * hooks Worker behind `HOOKS`. On by default, and refused and hidden from the caller's
 * tools once the owner's policy turns `hooks.codemode` off; served only where the caller's
 * profile holds `code`; one script calls several tools, each the caller's own door,
 * admitted again against its grant and the control record's gates (`SwitchedOwnerDO`, its
 * record flipped mid-script); no tool outside the profile and no capability outside the
 * grant; a revoked client and another owner's binding refused; a script past its wall time
 * or its compute stopped (`BoundedOwnerDO`, its bounds small; production's are
 * `CODEMODE_TIMEOUT_MS` and the hooks Worker's interrupt budget), saying which calls
 * landed; an answer past what the RPC carries refused `answer_too_large`.
 */
import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { OUTSIDE_OFF } from "../../doors/src/control";

import { profileNames } from "../../doors/src/tools";
import type { Origin } from "../src/ledger";
import {
  CODE_ANSWER_TOO_LARGE, CODE_MODE_OFF, CODE_NOT_SERVED, type OwnerDO,
} from "../src/owner-do";
import { customTools, validate } from "../src/policy";
import { MUSE, rig, type Rig } from "./ledger-support";
import { failure } from "./support";

/** The owner turns code mode on or off, and names its own `custom` tools, in its session. */
async function setPolicy(r: Rig, codemode: boolean, custom?: string[]): Promise<void> {
  const { generation, policy } = await r.stub.policyRead(r.bind(null));
  const { tools: _tools, ...rest } = policy as typeof policy & { tools?: unknown };
  await r.stub.policyWrite(r.session(null), { expected_generation: generation, policy: {
    ...rest, hooks: { codemode }, ...(custom === undefined ? {} : { tools: { custom } }) } });
}

/** The owner names a client's profile, and its mode and scopes, against its revision. */
async function profiled(r: Rig, origin: Origin, profile: string, mode = "full",
                               scopes: object[] = []) {
  const row = (await r.stub.grantList(r.bind(null))).clients
    .find((c) => c.client === origin.client);
  return r.stub.grantSet(r.session(null), {
    client: origin.client, source: "mcp", mode, scopes: scopes as never,
    expected_revision: row?.revision ?? 0, name: "", profile });
}

const lines = (...script: string[]): string => script.join("\n");

/** The platform's control record, outside access on, as the founder's admin path writes it. */
const OPEN = Object.freeze({ outside: "on", ordinary: "all", canary: [], inbound: false });

beforeEach(async () => {
  await env.CONTROL.put("c:control", JSON.stringify(OPEN));
});

describe("the code door", () => {
  it("is on by default, and refused and hidden once the owner turns it off", async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(MUSE));
    // A client's grant names custom, every tool, until the owner names another.
    expect(await r.stub.toolProfile(r.bind(MUSE)))
      .toEqual({ profile: "custom", tools: profileNames("custom") });
    expect(await r.stub.codeRun(r.bind(MUSE), { script: "return 1;" }))
      .toMatchObject({ ok: true, value: 1 });
    await setPolicy(r, false);
    for (const caller of [r.bind(null), r.bind(MUSE)]) {
      expect((await failure(() => r.stub.codeRun(caller, { script: "return 1;" }))).message)
        .toBe(CODE_MODE_OFF);
      expect((await r.stub.toolProfile(caller)).tools).not.toContain("code");
    }
    await setPolicy(r, true);
    expect(await r.stub.codeRun(r.bind(MUSE), { script: "return 1;" }))
      .toMatchObject({ ok: true, value: 1 });
  });

  it("is served only where the caller's profile holds it", async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(MUSE));
    await profiled(r, MUSE, "directory");
    expect(await r.stub.toolProfile(r.bind(MUSE)))
      .toEqual({ profile: "directory", tools: profileNames("directory") });
    expect((await failure(() => r.stub.codeRun(r.bind(MUSE), { script: "return 1;" })))
      .message).toBe(CODE_NOT_SERVED);
    // The owner's custom choice leaves code out.
    await profiled(r, MUSE, "custom");
    await setPolicy(r, true, ["agents_list", "collection_get"]);
    expect((await r.stub.toolProfile(r.bind(MUSE))).tools)
      .toEqual(["agents_list", "collection_get"]);
    expect((await failure(() => r.stub.codeRun(r.bind(MUSE), { script: "return 1;" })))
      .message).toBe(CODE_NOT_SERVED);
  });

  it("runs one script calling two tools, each the caller's own door", async () => {
    const r = await rig();
    await setPolicy(r, true);
    await r.stub.outsideAgents(r.bind(MUSE));
    await profiled(r, MUSE, "custom");
    const result = await r.stub.codeRun(r.bind(MUSE), { script: lines(
      "const put = await tools.collection_write({ collection: 'board', row_id: 'task-1',",
      "  body: 'ship it', expected_etag: '', private: false });",
      "const got = await tools.collection_get({ collection: 'board', row_id: 'task-1' });",
      "text('wrote ' + put.row.etag);",
      "return got.row.body;") });
    expect(result).toMatchObject({ ok: true, value: "ship it",
                                   calls: { ok: 2, error: 0, cancelled: 0 } });
    // What changed something landed, in order; a read is only counted.
    expect(result.landed).toEqual([{ name: "collection_write", status: "ok", count: 1 }]);
    expect(result.output).toEqual([{ type: "text", text: expect.stringMatching(/^wrote /) }]);
    expect((await r.stub.collectionGet(r.bind(null), { collection: "board", row_id: "task-1" }))
      .row?.body).toBe("ship it");
  });

  it("reaches no tool outside the caller's profile, and no capability outside its grant",
     async () => {
    const r = await rig();
    await r.register(MUSE);
    await setPolicy(r, true, ["code", "collection_get", "collection_write"]);
    await profiled(r, MUSE, "custom", "scoped",
                   [{ universe: "alpha", agent: "main", capability: "read_graph" }]);
    const result = await r.stub.codeRun(r.bind(MUSE), { script: lines(
      "const seen = ['file_usage' in tools, 'code' in tools, 'room_post' in tools];",
      "try { tools.file_usage; } catch (error) { seen.push(error.name); }",
      "try {",
      "  await tools.collection_write({ collection: 'board', row_id: 'x', body: 'b',",
      "    expected_etag: '', private: false });",
      "} catch (error) { seen.push(error.message); }",
      "seen.push((await tools.collection_get({ collection: 'board', row_id: 'x' })).row);",
      "return seen;") });
    expect(result).toMatchObject({ ok: true, value: [false, false, false, "TypeError",
      "outside client capability not granted", null], calls: { ok: 1, error: 1 } });
  });

  it("refuses a revoked client and a call bound to another owner", async () => {
    const r = await rig();
    await setPolicy(r, true);
    await r.stub.outsideAgents(r.bind(MUSE));
    await profiled(r, MUSE, "custom");
    await r.stub.grantRevoke(r.bind(null), { client: MUSE.client });
    for (const door of [() => r.stub.codeRun(r.bind(MUSE), { script: "return 1;" }),
                        () => r.stub.toolProfile(r.bind(MUSE))]) {
      expect((await failure(door)).message).toBe("outside client revoked");
    }
    const stranger = { ...r.bind(null), owner: "someone-else", caller: "someone-else" };
    for (const door of [() => r.stub.codeRun(stranger, { script: "return 1;" }),
                        () => r.stub.toolProfile(stranger)]) {
      expect((await failure(door)).name).toBe("Refused");
    }
  });

  it("stops a script past its wall time", async () => {
    const r = await rig(env.BOUNDED as unknown as DurableObjectNamespace<OwnerDO>);
    await setPolicy(r, true);
    const started = Date.now();
    const looped = await r.stub.codeRun(r.bind(null), { script: lines(
      "for (let n = 0; n < 3; n++) {",
      "  await tools.collection_write({ collection: 'board', row_id: 'r' + n, body: 'b',",
      "    expected_etag: '', private: false });",
      "}",
      "for (;;) { await tools.agents_list({}); }") });
    expect(Date.now() - started).toBeLessThan(10_000);
    expect(looped).toMatchObject({ ok: false, error: { kind: "timeout",
                                                       message: /timed out after 2000 ms/ } });
    expect(looped.calls.ok).toBeGreaterThan(1);
    // Which writes landed before the stop; the reads are only counted.
    expect(looped.landed).toEqual([{ name: "collection_write", status: "ok", count: 3 }]);
  }, 30_000);

  it("stops a script past its compute", async () => {
    const r = await rig(env.BOUNDED as unknown as DurableObjectNamespace<OwnerDO>);
    await setPolicy(r, true);
    expect(await r.stub.codeRun(r.bind(null), { script: "let n = 0; for (;;) { n++; }" }))
      .toMatchObject({ ok: false, error: { kind: "timeout",
        message: "Execution exceeded its interrupt budget of 2000" } });
  }, 30_000);

  it("takes the owner's custom tools only as distinct names of the door's tools", () => {
    const base = { entries: [], egress: { mode: "open", destinations: [] },
                   spend: { daily: null, monthly: null, transaction: null, ask_above: null,
                            source_ceilings: {} } };
    expect(customTools(validate(base))).toEqual(profileNames("custom"));
    expect(customTools(validate({ ...base, tools: { custom: ["code"] } }))).toEqual(["code"]);
    for (const tools of [{ custom: ["bash"] }, { custom: ["code", "code"] },
                         { custom: "code" }, { directory: [] }, []]) {
      expect(() => validate({ ...base, tools })).toThrow("invalid tools policy");
    }
  });

  it("reads the control record's gates again at each of a script's calls", async () => {
    // The record reads open three times (the door's own check and two calls), then off.
    const r = await rig(env.SWITCHED as unknown as DurableObjectNamespace<OwnerDO>);
    await r.stub.outsideAgents(r.bind(MUSE));
    const result = await r.stub.codeRun(r.bind(MUSE), { script: lines(
      "const seen = [];",
      "for (let n = 0; n < 5; n++) {",
      "  try {",
      "    await tools.collection_write({ collection: 'board', row_id: 'w' + n, body: 'b',",
      "      expected_etag: '', private: false });",
      "    seen.push('ok');",
      "  } catch (error) { seen.push(error.message); }",
      "}",
      "return seen;") });
    expect(result).toMatchObject({ ok: true,
      value: ["ok", "ok", OUTSIDE_OFF, OUTSIDE_OFF, OUTSIDE_OFF] });
    expect(result.landed).toEqual([{ name: "collection_write", status: "ok", count: 2 },
                                   { name: "collection_write", status: "error", count: 3 }]);
    expect((await r.stub.collectionGet(r.bind(null), { collection: "board", row_id: "w2" }))
      .row).toBeNull();
    // The record off at the start refuses the call itself.
    expect((await failure(() => r.stub.codeRun(r.bind(MUSE), { script: "return 1;" })))
      .message).toBe(OUTSIDE_OFF);
  });

  it("refuses an answer past what the RPC carries, keeping what landed", async () => {
    const r = await rig();
    // 12 Mi characters of a two-byte character: 24 MiB as V8 serializes it, 36 MiB as the
    // answer's UTF-8 JSON, past CODE_ANSWER_MAX; each tool call carries its own output.
    const output = await r.stub.codeRun(r.bind(null), { script: lines(
      "const chunk = String.fromCharCode(0x4e00).repeat(1024 * 1024);",
      "for (let n = 0; n < 12; n++) { text(chunk); await tools.agents_list({}); }",
      "return 1;") });
    expect(output).toEqual({ ok: false, error: { kind: "answer_too_large",
                                                 message: CODE_ANSWER_TOO_LARGE },
                             output: [], calls: { ok: 12, error: 0, cancelled: 0 },
                             landed: [] });
    const value = await r.stub.codeRun(r.bind(null), { script: lines(
      "const chunk = String.fromCharCode(0x4e00).repeat(1024 * 1024);",
      "for (let n = 0; n < 8; n++) { text(chunk); await tools.agents_list({}); }",
      "await tools.collection_write({ collection: 'board', row_id: 'big', body: 'b',",
      "  expected_etag: '', private: false });",
      "return chunk.repeat(4);") });
    expect(value).toMatchObject({ ok: false, error: { kind: "answer_too_large" },
      landed: [{ name: "collection_write", status: "ok", count: 1 }] });
    // Within it, an answer of the same characters crosses whole.
    const within = await r.stub.codeRun(r.bind(null), { script:
      "return String.fromCharCode(0x4e00).repeat(4 * 1024 * 1024).length;" });
    expect(within).toMatchObject({ ok: true, value: 4 * 1024 * 1024 });
  }, 120_000);
});
