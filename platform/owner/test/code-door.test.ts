/**
 * Code mode at the MCP door (slice M): the object's `codeRun`, in workerd with the real
 * codemode Worker behind `CODEMODE_VM`. On by default, and refused and hidden from the caller's
 * tools once the owner's policy turns `codemode.on` off; served only where the caller's
 * profile holds `code`; one script calls several tools, each the caller's own door,
 * admitted again against its grant and the control record's gates (the record flipped while
 * a call is held at the FILES gate, the object's clock moved by a fake `Date`); no tool outside the profile and no capability outside the
 * grant; a revoked client and another owner's binding refused; a script past its wall time
 * (`CODEMODE_TIMEOUT_MS`, under a clock that moves a second at each reading) stopped, saying
 * which calls landed; output past one answer cut and said so, a value past it refused
 * `answer_too_large`. A script past its compute is the codemode Worker's to stop
 * (`codemode/test/`).
 */
import { env } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OPEN_GATES, writeControl } from "../../ci/fixtures";
import { MAINTENANCE, OUTSIDE_OFF } from "../../contract/src/control";

import { profileNames } from "../../contract/src/doors";
import type { Origin } from "../../contract/src/binding";
import { jsonBytes } from "../../contract/src/encoded";
import { PLACE_BYTES } from "../../contract/src/limits";
import {
  CODE_ANSWER_TOO_LARGE, CODE_MODE_OFF, CODE_NOT_SERVED, CODEMODE_TIMEOUT_MS,
} from "../src/codemode";
import { CONTROL_FRESH_MS } from "../src/control-gate";
import { PAGE_BYTES } from "../src/pages";
import { DEFAULT_POLICY_JSON, validate } from "../../contract/src/policy-document";
import { customTools } from "../src/policy";
import { MUSE, rig, type Rig } from "./ledger-support";
import { later } from "./runs-support";
import { failure, gateFor, spaceOf } from "./support";

/** The owner turns code mode on or off, and names its own `custom` tools, in its session. */
async function setPolicy(r: Rig, codemode: boolean, custom?: string[]): Promise<void> {
  const { generation, policy } = await r.stub.policyRead(r.bind(null));
  const { tools: _tools, ...rest } = policy as typeof policy & { tools?: unknown };
  await r.stub.policyWrite(r.session(null), { expected_generation: generation, policy: {
    ...rest, codemode: { on: codemode },
    ...(custom === undefined ? {} : { tools: { custom } }) } });
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

afterEach(() => {
  vi.useRealTimers();
});

/**
 * A script of five board writes by Muse, each one's answer kept, its second's body placed in
 * R2 and held at the FILES gate. While it is held, the control record turns outside access
 * off and `meanwhile` runs (the object's clock moved, or not); then the script goes on.
 */
async function switchedMidScript(r: Rig, meanwhile: () => void) {
  await r.stub.outsideAgents(r.bind(MUSE));
  // A placed body first, so the owner's files namespace (the gate's name) exists.
  await r.stub.collectionWrite(r.bind(null), { collection: "board", row_id: "first",
    body: "é".repeat(PLACE_BYTES), expected_etag: "", private: false });
  const files = gateFor(env.FILES_GATE, await spaceOf(r.stub));
  await files.arm("putChunks", { hold: "before" });
  const running = r.stub.codeRun(r.bind(MUSE), { script: lines(
    "const seen = [];",
    "for (let n = 0; n < 5; n++) {",
    "  try {",
    "    await tools.collection_write({ collection: 'board', row_id: 'w' + n,",
    `      body: n === 1 ? 'é'.repeat(${PLACE_BYTES}) : 'b', expected_etag: '', private: false });`,
    "    seen.push('ok');",
    "  } catch (error) { seen.push(error.message); }",
    "}",
    "return seen;") });
  await files.held("putChunks");
  await writeControl(env.CONTROL, { ...OPEN, outside: "off" });
  meanwhile();
  await files.release("putChunks");
  return running;
}

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
    const r = await rig();
    await setPolicy(r, true);
    // The object runs in this isolate, so it reads this clock, which moves a second at every
    // reading: the script's tool calls take it past production's wall time in moments. A
    // clock that never moved would hold no deadline; one moved by the test could not be,
    // since a script calling back to back leaves the test's own turn no time to run.
    let now = Date.now();
    const clock = vi.spyOn(Date, "now").mockImplementation(() => (now += 1000));
    const before = Date.now();
    const looped = await r.stub.codeRun(r.bind(null), { script: lines(
      "for (let n = 0; n < 3; n++) {",
      "  await tools.collection_write({ collection: 'board', row_id: 'r' + n, body: 'b',",
      "    expected_etag: '', private: false });",
      "}",
      "for (;;) { await tools.agents_list({}); }") }).finally(() => clock.mockRestore());
    // Wall time is the property: the script ran to its deadline, never stopped early, and
    // was stopped by it, the deadline the code enforces. A script that ran on would hit
    // this test's class budget instead; no hand number stands in for either.
    expect(now - before).toBeGreaterThanOrEqual(CODEMODE_TIMEOUT_MS);
    expect(looped).toMatchObject({ ok: false, error: {
      kind: "timeout", message: new RegExp(`timed out after ${CODEMODE_TIMEOUT_MS} ms`) } });
    expect(looped.calls.ok).toBeGreaterThan(1);
    // Which writes landed before the stop; the reads are only counted.
    expect(looped.landed).toEqual([{ name: "collection_write", status: "ok", count: 3 }]);
  });

  it("takes the owner's custom tools only as distinct names of the door's tools", () => {
    const base = JSON.parse(DEFAULT_POLICY_JSON) as object;
    expect(customTools(validate(base))).toEqual(profileNames("custom"));
    expect(customTools(validate({ ...base, tools: { custom: ["code"] } }))).toEqual(["code"]);
    for (const tools of [{ custom: ["bash"] }, { custom: ["code", "code"] },
                         { custom: "code" }, { directory: [] }, []]) {
      expect(() => validate({ ...base, tools })).toThrow("invalid tools policy");
    }
  });

  it("reads the control record's gates again at a script's call once its last read is a " +
     "second old", async () => {
    // Outside access turned off while the second call is held, and the object's clock moved
    // past the freshness of its last read: the calls after it read the record again.
    const r = await rig();
    const result = await switchedMidScript(r, () => later(CONTROL_FRESH_MS / 1000 + 1));
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
    // The same switch with the object's clock still: the read the script began with serves
    // every call, and all five land; the next script's door reads the record again. The
    // clock is held still from the script's start, so a slow host moves it no second on.
    const r = await rig();
    later(0);
    const result = await switchedMidScript(r, () => undefined);
    expect(result).toMatchObject({ ok: true, value: ["ok", "ok", "ok", "ok", "ok"],
      landed: [{ name: "collection_write", status: "ok", count: 5 }] });
    later(CONTROL_FRESH_MS / 1000 + 1);
    expect((await failure(() => r.stub.codeRun(r.bind(MUSE), { script: "return 1;" })))
      .message).toBe(OUTSIDE_OFF);
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
