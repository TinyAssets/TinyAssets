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
 * landed; output past one answer cut and said so, a value past it refused `answer_too_large`.
 */
import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { OPEN_GATES, writeControl } from "../../ci/fixtures";
import { MAINTENANCE, OUTSIDE_OFF } from "../../contract/src/control";

import { profileNames } from "../../contract/src/doors";
import type { Origin } from "../../contract/src/binding";
import { jsonBytes } from "../../contract/src/encoded";
import { CODE_ANSWER_TOO_LARGE, CODE_MODE_OFF, CODE_NOT_SERVED } from "../src/codemode";
import type { OwnerDO } from "../src/owner-do";
import { PAGE_BYTES } from "../src/pages";
import { customTools, validate } from "../src/policy";
import { MUSE, rig, type Rig } from "./ledger-support";
import { failure } from "./support";
import { BOUNDED_TIMEOUT_MS } from "./worker";

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
const OPEN = OPEN_GATES;

beforeEach(async () => {
  await writeControl(env.CONTROL, OPEN);
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
      expect((await failure(door)).code).toBe("refused");
    }
  });

  it("stops a script past its wall time", async () => {
    const r = await rig(env.BOUNDED as unknown as DurableObjectNamespace<OwnerDO>);
    await setPolicy(r, true);
    const before = Date.now();
    const looped = await r.stub.codeRun(r.bind(null), { script: lines(
      "for (let n = 0; n < 3; n++) {",
      "  await tools.collection_write({ collection: 'board', row_id: 'r' + n, body: 'b',",
      "    expected_etag: '', private: false });",
      "}",
      "for (;;) { await tools.agents_list({}); }") });
    // Wall time is the property: the script ran to its deadline, never stopped early, and
    // was stopped by it, the deadline the code enforces. A script that ran on would hit
    // this test's class budget instead; no hand number stands in for either.
    expect(Date.now() - before).toBeGreaterThanOrEqual(BOUNDED_TIMEOUT_MS);
    expect(looped).toMatchObject({ ok: false, error: {
      kind: "timeout", message: new RegExp(`timed out after ${BOUNDED_TIMEOUT_MS} ms`) } });
    expect(looped.calls.ok).toBeGreaterThan(1);
    // Which writes landed before the stop; the reads are only counted.
    expect(looped.landed).toEqual([{ name: "collection_write", status: "ok", count: 3 }]);
  });

  it("stops a script past its compute", async () => {
    const r = await rig(env.BOUNDED as unknown as DurableObjectNamespace<OwnerDO>);
    await setPolicy(r, true);
    expect(await r.stub.codeRun(r.bind(null), { script: "let n = 0; for (;;) { n++; }" }))
      .toMatchObject({ ok: false, error: { kind: "timeout",
        message: "Execution exceeded its interrupt budget of 2000" } });
  });

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
    // The door's own read and one at each of the five calls.
    expect(await (env.SWITCHED.getByName(r.owner)).controlReads()).toBe(6);
    // The record off at the start refuses the call itself.
    expect((await failure(() => r.stub.codeRun(r.bind(MUSE), { script: "return 1;" })))
      .message).toBe(OUTSIDE_OFF);
  });

  it("holds the owner's own script to the maintenance gate alone, never to outside access",
     async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(MUSE));
    await writeControl(env.CONTROL, JSON.stringify({ ...OPEN, outside: "off" }));
    const script = lines(
      "await tools.collection_write({ collection: 'board', row_id: 'own', body: 'b',",
      "  expected_etag: '', private: false });",
      "return 1;");
    expect(await r.stub.codeRun(r.bind(null), { script })).toMatchObject({ ok: true, value: 1,
      landed: [{ name: "collection_write", status: "ok", count: 1 }] });
    expect((await failure(() => r.stub.codeRun(r.bind(MUSE), { script: "return 1;" })))
      .message).toBe(OUTSIDE_OFF);
    // The maintenance gate closed refuses the owner's own.
    await writeControl(env.CONTROL, JSON.stringify({ ...OPEN, ordinary: "none" }));
    expect((await failure(() => r.stub.codeRun(r.bind(null), { script }))).message)
      .toBe(MAINTENANCE);
  });

  it("serves a script's calls from one read of the control record within a second",
     async () => {
    // Production's freshness: the record reads open three times then off, but a script of
    // five quick calls reads it once, at its start, and all five land.
    const r = await rig(env.CACHED as unknown as DurableObjectNamespace<OwnerDO>);
    await r.stub.outsideAgents(r.bind(MUSE));
    const result = await r.stub.codeRun(r.bind(MUSE), { script: lines(
      "for (let n = 0; n < 5; n++) {",
      "  await tools.collection_write({ collection: 'board', row_id: 'c' + n, body: 'b',",
      "    expected_etag: '', private: false });",
      "}",
      "return 5;") });
    expect(result).toMatchObject({ ok: true, value: 5,
      landed: [{ name: "collection_write", status: "ok", count: 5 }] });
    expect(await env.CACHED.getByName(r.owner).controlReads()).toBe(1);
  });

  it("cuts output past one answer, saying so, and refuses only a value past it", async () => {
    const r = await rig();
    // A value past one answer alone: refused, its calls and what landed kept.
    const value = await r.stub.codeRun(r.bind(null), { script: lines(
      "await tools.collection_write({ collection: 'board', row_id: 'big', body: 'b',",
      "  expected_etag: '', private: false });",
      `return '\\u0001'.repeat(${Math.ceil(PAGE_BYTES / 6)});`) });
    expect(value).toEqual({ ok: false, error: { kind: "answer_too_large",
                                                message: CODE_ANSWER_TOO_LARGE },
                            output: [], calls: { ok: 1, error: 0, cancelled: 0 },
                            landed: [{ name: "collection_write", status: "ok", count: 1 }] });
    // Output past it: the value whole, the output's first item cut at a character, said so.
    const output = await r.stub.codeRun(r.bind(null), { script: lines(
      `text('x'.repeat(${PAGE_BYTES}));`, "text('after');", "return 1;") });
    expect(output).toMatchObject({ ok: true, value: 1, output_cut: { items: 2, kept: 0 } });
    expect(jsonBytes(output)).toBeLessThanOrEqual(PAGE_BYTES);
    expect((output as { output: { text: string }[] }).output[0]!.text.length)
      .toBeGreaterThan(PAGE_BYTES - 1000);
    const within = await r.stub.codeRun(r.bind(null), { script:
      `return 'x'.repeat(${PAGE_BYTES - 1000}).length;` });
    expect(within).toMatchObject({ ok: true, value: PAGE_BYTES - 1000 });
    expect(within).not.toHaveProperty("output_cut");
  });
});
