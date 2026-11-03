/**
 * Code mode at the MCP door (slice M): the object's `codeRun`, in workerd with the real
 * hooks Worker behind `HOOKS`. Refused, and hidden from the caller's tools, until the
 * owner's policy turns `hooks.codemode` on; served only where the caller's profile holds
 * `code`; one script calls several tools, each the caller's own door, admitted again
 * against its grant; no tool outside the profile and no capability outside the grant; a
 * revoked client and another owner's binding refused; a script past its wall time or its
 * compute stopped (`BoundedOwnerDO`, its bounds small; production's are
 * `CODEMODE_TIMEOUT_MS` and the hooks Worker's interrupt budget).
 */
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { profileNames } from "../../doors/src/tools";
import type { Origin } from "../src/ledger";
import { CODE_MODE_OFF, CODE_NOT_SERVED, type OwnerDO } from "../src/owner-do";
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

describe("the code door", () => {
  it("is refused until the owner's policy turns code mode on, and hidden till then",
     async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(MUSE));
    await profiled(r, MUSE, "custom");
    for (const caller of [r.bind(null), r.bind(MUSE)]) {
      expect((await failure(() => r.stub.codeRun(caller, { script: "return 1;" }))).message)
        .toBe(CODE_MODE_OFF);
      expect((await r.stub.toolProfile(caller)).tools).not.toContain("code");
    }
    await setPolicy(r, true);
    expect(await r.stub.toolProfile(r.bind(MUSE)))
      .toEqual({ profile: "custom", tools: profileNames("custom") });
    expect(await r.stub.codeRun(r.bind(MUSE), { script: "return 1;" }))
      .toMatchObject({ ok: true, value: 1 });
    await setPolicy(r, false);
    expect((await failure(() => r.stub.codeRun(r.bind(MUSE), { script: "return 1;" })))
      .message).toBe(CODE_MODE_OFF);
  });

  it("is served only where the caller's profile holds it", async () => {
    const r = await rig();
    await setPolicy(r, true);
    // A client's grant names the directory's profile until the owner names another.
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
    const looped = await r.stub.codeRun(r.bind(null), {
      script: "for (;;) { await tools.agents_list({}); }" });
    expect(Date.now() - started).toBeLessThan(10_000);
    expect(looped).toMatchObject({ ok: false, error: { kind: "timeout",
                                                       message: /timed out after 2000 ms/ } });
    expect(looped.calls.ok).toBeGreaterThan(1);
  }, 30_000);

  it("stops a script past its compute", async () => {
    const r = await rig(env.BOUNDED as unknown as DurableObjectNamespace<OwnerDO>);
    await setPolicy(r, true);
    expect(await r.stub.codeRun(r.bind(null), { script: "let n = 0; for (;;) { n++; }" }))
      .toMatchObject({ ok: false, error: { kind: "timeout" } });
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
});
