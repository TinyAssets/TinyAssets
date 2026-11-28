/**
 * Code mode at the MCP door: the object's `codeRun`, in workerd with the real codemode Worker
 * behind `CODEMODE_VM`. Off by default, refused saying why and withheld from the caller's tools
 * until the owner's policy turns `codemode.on` on; served only where the caller's profile holds
 * `code`; one
 * script calls several tools, each the caller's own door, admitted again against its grant and the
 * control record's gates (the record flipped while a call is held at the FILES gate, the object's
 * clock moved by a fake `Date`); no tool outside the profile and no capability outside the grant; a
 * revoked client and another owner's binding refused; a script whose caller stopped waiting (its
 * stop closed while a call is held at the FILES gate) stopped, saying which calls landed, the
 * held one as it ended; a call without its caller's stop refused, after its admission; output
 * past one answer cut and said so, a value past it refused `answer_too_large`. That no clock stops a script whose caller waits is the executor's
 * (`codemode.test.ts`).
 * A script past its compute is the codemode Worker's to stop (`codemode/test/`).
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OPEN, writeControl } from "../../ci/fixtures";
import { type ControlStore, MAINTENANCE, OUTSIDE_OFF } from "../../contract/src/control";

import { profileNames } from "../../contract/src/doors";
import type { Binding, Origin } from "../../contract/src/binding";
import { jsonBytes } from "../../contract/src/encoded";
import { PLACE_BYTES } from "../../contract/src/limits";
import { CODE_ANSWER_TOO_LARGE, CODE_NOT_SERVED, CODE_OUT_OF_MEMORY } from "../src/codemode";
import { unportable } from "../../contract/src/paths";
import { CONTROL_FRESH_MS, ControlGate, objectKeeper } from "../src/control-gate";
import { WriteGate } from "../src/gate";
import type { OwnerDO } from "../src/owner-do";
import { PAGE_BYTES } from "../src/bodies";
import {
  CODE_MODE_OFF, DEFAULT_POLICY_JSON, validate,
} from "../../contract/src/policy-document";
import { customTools } from "../src/policy";
import { MUSE, rig, type Rig } from "./ledger-support";
import { later } from "./runs-support";
import { failure, gateFor, spaceOf } from "./support";

/** What a script's refused call says after its message when the refusal is final. */
const FINAL = "This refusal is final: the same call is refused again until what it names changes.";

/** The owner turns code mode on or off, and names its own `custom` tools, in its session. */
async function setPolicy(r: Rig, codemode: boolean, custom?: string[]): Promise<void> {
  const { generation, policy } = await r.stub.policyRead(r.bind(null));
  const { tools: _tools, ...rest } = policy as typeof policy & { tools?: unknown };
  await r.stub.policyWrite(r.session(null), { expected_generation: generation, policy: {
    ...rest, codemode: { on: codemode },
    ...(custom === undefined ? {} : { tools: { custom } }) } });
}

/** A rig whose owner has turned code mode on. */
async function onRig(): Promise<Rig> {
  const r = await rig();
  await setPolicy(r, true);
  return r;
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

/**
 * `codeRun` called as the doors call it, with its caller's stop (`stop`): by default one the
 * doors never close, a caller that waits for the script's end.
 */
function ran(r: Rig, binding: Binding, request: { script: string },
             stop: ReadableStream<Uint8Array> = new IdentityTransformStream().readable) {
  return r.stub.codeRun(binding, request, stop);
}

// Outside access on, as the founder's admin path writes it: the outside clients' calls pass.
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
  const running = ran(r, r.bind(MUSE), { script: lines(
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
  it("is off by default, refused saying why and withheld, until the owner turns it on",
     async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(MUSE));
    // A client's grant names custom, every tool, until the owner names another: `code`
    // withheld while code mode is off.
    const off = { profile: "custom", withheld: ["code"],
                  tools: profileNames("custom").filter((name) => name !== "code") };
    for (const caller of [r.bind(null), r.bind(MUSE)]) {
      expect((await failure(() => ran(r, caller, { script: "return 1;" }))).message)
        .toBe(CODE_MODE_OFF);
      expect(await r.stub.toolProfile(caller)).toEqual(off);
    }
    await setPolicy(r, true);
    expect(await r.stub.toolProfile(r.bind(MUSE)))
      .toEqual({ profile: "custom", tools: profileNames("custom"), withheld: [] });
    expect(await ran(r, r.bind(MUSE), { script: "return 1;" }))
      .toMatchObject({ ok: true, value: 1 });
    await setPolicy(r, false);
    expect((await failure(() => ran(r, r.bind(MUSE), { script: "return 1;" })))
      .message).toBe(CODE_MODE_OFF);
  });

  it("is served only where the caller's profile holds it", async () => {
    const r = await onRig();
    await r.stub.outsideAgents(r.bind(MUSE));
    await profiled(r, MUSE, "directory");
    expect(await r.stub.toolProfile(r.bind(MUSE)))
      .toEqual({ profile: "directory", tools: profileNames("directory"), withheld: [] });
    expect((await failure(() => ran(r, r.bind(MUSE), { script: "return 1;" })))
      .message).toBe(CODE_NOT_SERVED);
    // Code mode off withholds nothing it does not hold: turning it on would not serve it.
    await setPolicy(r, false);
    expect((await r.stub.toolProfile(r.bind(MUSE))).withheld).toEqual([]);
    expect((await failure(() => ran(r, r.bind(MUSE), { script: "return 1;" })))
      .message).toBe(CODE_NOT_SERVED);
    // The owner's custom choice leaves code out.
    await profiled(r, MUSE, "custom");
    await setPolicy(r, true, ["agents_list", "collection_get"]);
    expect((await r.stub.toolProfile(r.bind(MUSE))).tools)
      .toEqual(["agents_list", "collection_get"]);
    expect((await failure(() => ran(r, r.bind(MUSE), { script: "return 1;" })))
      .message).toBe(CODE_NOT_SERVED);
  });

  it("runs one script calling two tools, each the caller's own door", async () => {
    const r = await onRig();
    await r.stub.outsideAgents(r.bind(MUSE));
    await profiled(r, MUSE, "custom");
    const result = await ran(r, r.bind(MUSE), { script: lines(
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
    const r = await onRig();
    await r.register(MUSE);
    await setPolicy(r, true, ["code", "collection_get", "collection_write"]);
    await profiled(r, MUSE, "custom", "scoped",
                   [{ universe: "alpha", agent: "main", capability: "read_graph" }]);
    const result = await ran(r, r.bind(MUSE), { script: lines(
      "const seen = ['file_usage' in tools, 'code' in tools, 'room_post' in tools];",
      "try { tools.file_usage; } catch (error) { seen.push(error.name); }",
      "try {",
      "  await tools.collection_write({ collection: 'board', row_id: 'x', body: 'b',",
      "    expected_etag: '', private: false });",
      "} catch (error) { seen.push(error.message); }",
      "seen.push((await tools.collection_get({ collection: 'board', row_id: 'x' })).row);",
      "return seen;") });
    // A refused call throws into the script as an MCP tool's error result reads: final.
    expect(result).toMatchObject({ ok: true, value: [false, false, false, "TypeError",
      `outside client capability not granted\n${FINAL}`, null], calls: { ok: 1, error: 1 } });
  });

  it("refuses a revoked client and a call bound to another owner", async () => {
    const r = await onRig();
    await r.stub.outsideAgents(r.bind(MUSE));
    await profiled(r, MUSE, "custom");
    await r.stub.grantRevoke(r.bind(null), { client: MUSE.client });
    for (const door of [() => ran(r, r.bind(MUSE), { script: "return 1;" }),
                        () => r.stub.toolProfile(r.bind(MUSE))]) {
      expect((await failure(door)).message).toBe("outside client revoked");
    }
    const stranger = { ...r.bind(null), owner: "someone-else", caller: "someone-else" };
    for (const door of [() => ran(r, stranger, { script: "return 1;" }),
                        () => r.stub.toolProfile(stranger)]) {
      expect((await failure(door)).code).toBe("refused");
    }
  });

  it("stops a script whose caller stopped waiting, saying which calls landed", async () => {
    const r = await onRig();
    await r.stub.collectionWrite(r.bind(null), { collection: "board", row_id: "first",
      body: "é".repeat(PLACE_BYTES), expected_etag: "", private: false });
    const files = gateFor(env.FILES_GATE, await spaceOf(r.stub));
    await files.arm("putChunks", { hold: "before" });
    const { readable, writable } = new IdentityTransformStream();
    const running = ran(r, r.bind(null), { script: lines(
      "for (let n = 0; n < 3; n++) {",
      "  await tools.collection_write({ collection: 'board', row_id: 'r' + n, body: 'b',",
      "    expected_etag: '', private: false });",
      "}",
      "await tools.collection_write({ collection: 'board', row_id: 'placed',",
      `  body: 'è'.repeat(${PLACE_BYTES}), expected_etag: '', private: false });`,
      "for (let n = 0; n < 50; n++) { await tools.agents_list({}); }", "return 'ran';") },
      readable);
    await files.held("putChunks");
    // The caller stops waiting while the fourth call is held: the doors close its stop.
    await writable.getWriter().close();
    await files.release("putChunks");
    const stopped = await running;
    // Stopped by its caller's leaving, never run on: the fifty calls after it were not made.
    expect(stopped).toMatchObject({ ok: false, error: { kind: "stopped",
      message: "the script's caller stopped waiting, so it was stopped" },
      calls: { ok: 4, error: 0, cancelled: 0 } });
    // The held write went on in its door and landed, and is said so, never `cancelled`.
    expect(stopped.landed).toEqual([{ name: "collection_write", status: "ok", count: 4 }]);
    expect((await r.stub.collectionGet(r.bind(null), { collection: "board", row_id: "placed" }))
      .row).not.toBeNull();
  });

  it("refuses a call that carries no caller's stop, before any script runs", async () => {
    const r = await onRig();
    const stub = r.stub as unknown as { codeRun(...args: unknown[]): Promise<unknown> };
    for (const stop of [undefined, "stop", {}]) {
      expect(await failure(() => stub.codeRun(r.bind(null), { script: "return 1;" }, stop)))
        .toMatchObject({ code: "invalid", message: "codeRun takes its caller's stop" });
    }
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
    const r = await onRig();
    const result = await switchedMidScript(r, () => later(CONTROL_FRESH_MS / 1000 + 1));
    // Each refused call throws into the script with its wait: a closed gate's, for the moment.
    const off = `${OUTSIDE_OFF}\nThis refusal is temporary: try the same call again in 60 ` +
      "seconds.";
    expect(result).toMatchObject({ ok: true, value: ["ok", "ok", off, off, off] });
    expect(result.landed).toEqual([{ name: "collection_write", status: "ok", count: 2 },
                                   { name: "collection_write", status: "error", count: 3 }]);
    expect((await r.stub.collectionGet(r.bind(null), { collection: "board", row_id: "w2" }))
      .row).toBeNull();
    // The record off at the start refuses the call itself, for the moment, as a closed gate
    // is refused at every door.
    const closed = await failure(() => ran(r, r.bind(MUSE), { script: "return 1;" }));
    expect([closed.code, closed.message]).toEqual(["unavailable", OUTSIDE_OFF]);
  });

  it("holds the owner's own script to the maintenance gate alone, never to outside access",
     async () => {
    const r = await onRig();
    await r.stub.outsideAgents(r.bind(MUSE));
    await writeControl(env.CONTROL, JSON.stringify({ ...OPEN, outside: "off" }));
    const script = lines(
      "await tools.collection_write({ collection: 'board', row_id: 'own', body: 'b',",
      "  expected_etag: '', private: false });",
      "return 1;");
    expect(await ran(r, r.bind(null), { script })).toMatchObject({ ok: true, value: 1,
      landed: [{ name: "collection_write", status: "ok", count: 1 }] });
    expect((await failure(() => ran(r, r.bind(MUSE), { script: "return 1;" })))
      .message).toBe(OUTSIDE_OFF);
    // The maintenance gate closed refuses the owner's own.
    await writeControl(env.CONTROL, JSON.stringify({ ...OPEN, ordinary: "none" }));
    const closed = await failure(() => ran(r, r.bind(null), { script }));
    expect([closed.code, closed.message]).toEqual(["unavailable", MAINTENANCE]);
  });

  it("serves the object's gates from its own kept copy while KV can't be read", async () => {
    // The object's keeper as the object composes it: its own SQLite row, written in a quiet
    // unit of a write gate over its storage.
    const down: ControlStore = { get: (() => Promise.reject(new Error("KV down"))) as never };
    const gates = (stub: DurableObjectStub<OwnerDO>, owner: string) => runInDurableObject(stub,
      async (_instance, state) => {
        const write = new WriteGate(state.storage);
        const keeper = objectKeeper(write.storage, write.quiet);
        return [await new ControlGate(down, keeper).refusal("outside", owner),
                await new ControlGate(down, keeper).refusal("owner", owner),
                (await new ControlGate(down, keeper).read()).kind];
      });
    // An object that never read the record keeps no copy: every gate closed.
    const unread = `owner-${crypto.randomUUID()}`;
    expect(await gates(env.OWNER.getByName(unread), unread))
      .toEqual([OUTSIDE_OFF, MAINTENANCE, "unreadable"]);
    const r = await onRig();
    expect(await ran(r, r.bind(MUSE), { script: "return 1;" }))
      .toMatchObject({ ok: true, value: 1 });
    // The door's read kept: the copy admits as the record did, served as kept.
    expect(await gates(r.stub, r.owner)).toEqual([null, null, "kept"]);
    // A kill flipped before the outage holds through it.
    await writeControl(env.CONTROL, { ...OPEN, outside: "off" });
    later(CONTROL_FRESH_MS / 1000 + 1);
    expect((await failure(() => ran(r, r.bind(MUSE), { script: "return 1;" })))
      .message).toBe(OUTSIDE_OFF);
    expect(await gates(r.stub, r.owner)).toEqual([OUTSIDE_OFF, null, "kept"]);
  });

  it("serves a script's calls from one read of the control record within a second",
     async () => {
    // The same switch with the object's clock still: the read the script began with serves
    // every call, and all five land; the next script's door reads the record again. The
    // clock is held still from the script's start, so a slow host moves it no second on.
    const r = await onRig();
    later(0);
    const result = await switchedMidScript(r, () => undefined);
    expect(result).toMatchObject({ ok: true, value: ["ok", "ok", "ok", "ok", "ok"],
      landed: [{ name: "collection_write", status: "ok", count: 5 }] });
    later(CONTROL_FRESH_MS / 1000 + 1);
    expect((await failure(() => ran(r, r.bind(MUSE), { script: "return 1;" })))
      .message).toBe(OUTSIDE_OFF);
  });

  it("answers a script's bad path and missing file as each call's refusal, saying why, and " +
     "its memory past the cap naming it, the object serving the next script", async () => {
    const r = await onRig();
    const result = await ran(r, r.bind(null), { script: lines(
      "const seen = [];",
      "for (const path of ['/workspace/a\\u0000b.md', '/workspace/a\\u0001b.md',",
      "                    '/workspace/back\\\\slash.md']) {",
      "  try { await tools.file_write({ path, expected_etag: '', content: 'x' }); }",
      "  catch (error) { seen.push(error.message.split('\\n')[0]); }",
      "}",
      "try { await tools.file_read({ path: '/workspace/missing.md' }); }",
      "catch (error) { seen.push(error.message.split('\\n')[0]); }",
      "return seen;") });
    expect(result).toMatchObject({ ok: true, value: [
      "a path holds no NUL character", unportable("a\u0001b.md"), unportable("back\\slash.md"),
      "not_found: no such file"], calls: { ok: 0, error: 4, cancelled: 0 } });
    expect(await ran(r, r.bind(null), { script:
      "const all = []; for (;;) { all.push('x'.repeat(1 << 20) + all.length); }" }))
      .toMatchObject({ ok: false, error: { kind: "script", message: CODE_OUT_OF_MEMORY } });
    expect(await ran(r, r.bind(null), { script: "return 1;" }))
      .toMatchObject({ ok: true, value: 1 });
  });

  it("cuts output past one answer, saying so, and refuses only a value past it", async () => {
    const r = await onRig();
    // A value past one answer alone: refused, its calls and what landed kept.
    const value = await ran(r, r.bind(null), { script: lines(
      "await tools.collection_write({ collection: 'board', row_id: 'big', body: 'b',",
      "  expected_etag: '', private: false });",
      `return '\\u0001'.repeat(${Math.ceil(PAGE_BYTES / 6)});`) });
    expect(value).toEqual({ ok: false, error: { kind: "answer_too_large",
                                                message: CODE_ANSWER_TOO_LARGE },
                            output: [], calls: { ok: 1, error: 0, cancelled: 0 },
                            landed: [{ name: "collection_write", status: "ok", count: 1 }] });
    // Output past it: the value whole, the output's first item cut at a character, said so.
    const output = await ran(r, r.bind(null), { script: lines(
      `text('x'.repeat(${PAGE_BYTES}));`, "text('after');", "return 1;") });
    expect(output).toMatchObject({ ok: true, value: 1, output_cut: { items: 2, kept: 0 } });
    expect(jsonBytes(output)).toBeLessThanOrEqual(PAGE_BYTES);
    expect((output as { output: { text: string }[] }).output[0]!.text.length)
      .toBeGreaterThan(PAGE_BYTES - 1000);
    const within = await ran(r, r.bind(null), { script:
      `return 'x'.repeat(${PAGE_BYTES - 1000}).length;` });
    expect(within).toMatchObject({ ok: true, value: PAGE_BYTES - 1000 });
    expect(within).not.toHaveProperty("output_cut");
  });
});
