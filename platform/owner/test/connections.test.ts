/**
 * The owner's model connections (tasks 4 G2): the connect doors in the owner's session,
 * OAuth flows bound to it and cancelled with it, a revoke's crypto-delete and its provider
 * revoke, the refresh lease under concurrent calls, the reseal's compare-and-swap, the
 * largest built-in catalog, and the plaintext key never in the object.
 */
import { evictDurableObject, runDurableObjectAlarm } from "cloudflare:test";
import { isModelType } from "@earendil-works/pi-ai/models";
import { builtinProviders } from "@earendil-works/pi-ai/providers/all";
import { env } from "cloudflare:workers";
import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type Credential, type Source, SUPPORTED_APIS } from "../../gateway/src/credential";
import { recordOf } from "../../gateway/src/model-record";
import { open } from "../../gateway/src/keyring";
import { CONNECTION_LIMITS, type ModelInfo, MODELS_JSON_MAX } from "../src/connections";
import type { Binding } from "../src/owner-do";
import { MUSE } from "./ledger-support";
import { failure } from "./support";
import {
  ANTHROPIC, asked, catalog, direct, type Gated, gated, keyring, OAUTH, OPENAI, REDIRECT,
  provider, scopeOf, sealedAs, textOf, until,
} from "./gateway-support";

afterEach(() => {
  vi.restoreAllMocks();
});

/** The connection's blob rows, as stored. */
function secrets(r: Gated, connection: string) {
  return r.sql("SELECT version, kid, blob FROM connection_secrets WHERE connection_id = ?",
               connection);
}

/** Run the object's due lane jobs now. */
async function lanes(r: Gated): Promise<void> {
  await r.sql("UPDATE cf_agents_jobs SET time = 0");
  await runDurableObjectAlarm(r.stub);
  await until(async () => (await r.sql("SELECT id FROM cf_agents_jobs WHERE time <= ?",
                                       Date.now())).length === 0, "the due jobs");
}

/** Concurrent model calls through the object's provider, each in a session of its own. */
function calls(r: Gated, many: number, text = "cmd:echo") {
  return r.in(async (instance) => {
    const auto = instance.parts.provider.getModels()[0]!;
    return Promise.all(Array.from({ length: many }, async (_, i) =>
      (await provider(instance)).streamSimple(auto, asked(text), await direct(instance, `s-${i}`))
        .result()));
  });
}

describe("connecting a pasted key", () => {
  it("is prepared, sealed by the gateway, committed and listed without its blob",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-listed");
    const { connections } = await r.stub.connectionList(r.session());
    expect(connections).toHaveLength(1);
    const [listed] = connections;
    expect(listed).toMatchObject({ connection, kind: "api_key", provider: "anthropic",
                                   status: "active", version: 1, expires_at: null,
                                   provider_revoke_pending: false });
    expect(listed!.models).toEqual((await catalog(ANTHROPIC)).map((model) => model.id));
    expect(JSON.stringify(connections)).not.toContain("v1.");
    const [secret] = await secrets(r, connection);
    expect(secret!["version"]).toBe(1);
    expect(String(secret!["blob"]).startsWith(`v1.${String(secret!["kid"])}.`)).toBe(true);
  });

  it("is the owner's own act, in its live session, a refusal receipted", async () => {
    const r = await gated();
    const request = { source: "anthropic", kind: "api_key" as const, display_name: "Key",
                      provider: "anthropic", revocable: false };
    expect((await failure(() => r.stub.connectionPrepare(r.bind(), request))).message)
      .toBe("interactive owner session required");
    expect((await failure(() => r.stub.connectionPrepare(
      r.bind({ origin: MUSE, source: { kind: "mcp", ref: "conn-1" } }), request))).message)
      .toBe("only the owner may connect a model source");
    expect((await failure(() => r.stub.connectionList(r.bind()))).message)
      .toBe("interactive owner session required");
    const { receipts } = await r.stub.policyReceipts(r.bind());
    expect(receipts.filter((receipt) => receipt.kind === "refusal")).toHaveLength(3);
  });

  it("commits only a pending connection at its version, a sealed blob and valid models",
     async () => {
    const r = await gated();
    const prepared = await r.stub.connectionPrepare(r.session(), {
      source: "anthropic", kind: "api_key", display_name: "Key", provider: "anthropic",
      revocable: false });
    const sealed = await env.GATEWAY.seal(r.name, prepared.connection, 1, "sk-x", ANTHROPIC);
    const models = (await catalog(ANTHROPIC)).slice(0, 2);
    const commit = (extra: Record<string, unknown>) => r.stub.connectionCommit(r.session(), {
      connection: prepared.connection, version: 1, blob: sealed.blob, kid: sealed.kid, models,
      egress: scopeOf(ANTHROPIC), ...extra } as never);
    for (const [extra, message] of [
      [{ version: 2 }, "no pending connection at this version"],
      [{ kid: "k9" }, "invalid sealed credential"],
      [{ blob: "plaintext-key" }, "invalid sealed credential"],
      [{ models: [{ id: "m" }] }, "invalid connection model"],
      [{ models: [models[0], models[0]] }, "invalid connection models"],
      [{ expires_at: Date.now() }, "an OAuth connection's expiry is epoch ms or null; a key " +
                                   "has none"],
      [{ extra: 1 }, "invalid connection request"],
      [{ egress: undefined }, "invalid connection request"],
      [{ egress: { base_url: "https://api.anthropic.com" } }, "invalid connection egress scope"],
      [{ egress: { ...scopeOf(ANTHROPIC), base_url: "https://api.openai.com" } },
       "invalid connection egress scope"],
      [{ egress: { ...scopeOf(ANTHROPIC), endpoints: Array.from({ length: 16 }, () => ({
         origin: "https://api.anthropic.com", paths: Array.from({ length: 64 }, (_, i) =>
           `/v1/${"p".repeat(200)}${i}`), methods: ["POST"] })) } },
       "invalid connection egress scope"],
    ] as const) {
      expect((await failure(() => commit(extra))).message).toBe(message);
    }
    await r.sql("UPDATE connections SET pending_until = 1");
    expect((await failure(() => commit({}))).message).toBe("no pending connection at this version");
  });

  it("prepares at most the limit of live connections", async () => {
    const r = await gated();
    const request = { source: "anthropic", kind: "api_key" as const, display_name: "Key",
                      provider: "anthropic", revocable: false };
    for (let i = 0; i < CONNECTION_LIMITS.maxConnections; i++) {
      await r.stub.connectionPrepare(r.session(), request);
    }
    expect((await failure(() => r.stub.connectionPrepare(r.session(), request))).message)
      .toBe("the owner's connections are at their limit");
  });

  it("sweeps a pending connection never committed", async () => {
    const r = await gated();
    await r.stub.connectionPrepare(r.session(), { source: "anthropic", kind: "api_key",
                                                  display_name: "Key", provider: "anthropic",
                                                  revocable: false });
    await r.sql("UPDATE connections SET pending_until = ?", Date.now() / 1000 - 1);
    await lanes(r);
    expect(await r.sql("SELECT * FROM connections")).toEqual([]);
  });
});

describe("an OAuth flow", () => {
  async function begun(r: Gated, session: () => Binding = r.session) {
    const prepared = await r.stub.connectionPrepare(session(), {
      source: "openai-oauth", kind: "oauth2", display_name: "OAuth", provider: "openai",
      revocable: true, redirect_uri: REDIRECT });
    return { connection: prepared.connection, ...prepared.flow! };
  }

  it("hands back the verifier of its challenge once, in the session that began it",
     async () => {
    const r = await gated();
    const flow = await begun(r);
    expect(flow.code_challenge_method).toBe("S256");
    const [row] = await r.sql("SELECT flow, session_id FROM connection_flows");
    expect(row!["flow"]).toBe(createHash("sha256").update(flow.state).digest("hex"));
    expect(JSON.stringify(row)).not.toContain(flow.state);
    // Another session of the same owner cannot finish it.
    const { session: other } = await r.stub.sessionOpen(
      r.bind({ authenticated_at: Math.floor(Date.now() / 1000) }));
    expect((await failure(() => r.stub.flowFinish(r.bind({ session: other }),
                                                  { state: flow.state }))).message)
      .toBe("no open connection flow for this state in this session");
    const finished = await r.stub.flowFinish(r.session(), { state: flow.state });
    expect(finished).toMatchObject({ connection: flow.connection, version: 1,
                                     redirect_uri: REDIRECT });
    expect(createHash("sha256").update(finished.verifier).digest("base64url"))
      .toBe(flow.code_challenge);
    expect((await failure(() => r.stub.flowFinish(r.session(), { state: flow.state }))).message)
      .toBe("no open connection flow for this state in this session");
    expect(await r.sql("SELECT status, verifier FROM connection_flows"))
      .toEqual([{ status: "used", verifier: null }]);
  });

  it("expires, and is swept at its expiry", async () => {
    const r = await gated();
    const flow = await begun(r);
    await r.sql("UPDATE connection_flows SET expires_at = 1");
    expect((await failure(() => r.stub.flowFinish(r.session(), { state: flow.state }))).message)
      .toBe("no open connection flow for this state in this session");
    await lanes(r);
    expect(await r.sql("SELECT * FROM connection_flows")).toEqual([]);
  });

  it("keeps at most the limit open, and a refused one leaves no pending connection",
     async () => {
    const r = await gated();
    for (let i = 0; i < CONNECTION_LIMITS.maxOpenFlows; i++) {
      await begun(r);
    }
    expect((await failure(() => begun(r))).message)
      .toBe("too many connection flows; finish or wait for one");
    expect(await r.sql("SELECT id FROM connections"))
      .toHaveLength(CONNECTION_LIMITS.maxOpenFlows);
  });

  it("begins with its connection, in one door: OAuth needs a redirect URI, a key none",
     async () => {
    const r = await gated();
    const base = { source: "openai-oauth", display_name: "OAuth", provider: "openai" };
    for (const [request, message] of [
      [{ ...base, kind: "oauth2", revocable: true },
       "an OAuth connection begins its flow at a redirect URI; a key has none"],
      [{ ...base, kind: "api_key", revocable: false, redirect_uri: REDIRECT },
       "an OAuth connection begins its flow at a redirect URI; a key has none"],
      [{ ...base, kind: "api_key", revocable: true },
       "revocable is a boolean, and false for a pasted key"],
      [{ ...base, kind: "oauth2", redirect_uri: REDIRECT },
       "invalid connection request"],
      [{ ...base, kind: "oauth2", revocable: "yes", redirect_uri: REDIRECT },
       "revocable is a boolean, and false for a pasted key"],
      [{ ...base, kind: "oauth2", revocable: true, redirect_uri: "http://tinyassets.io/cb" },
       "invalid redirect URI"],
    ] as const) {
      expect((await failure(() => r.stub.connectionPrepare(r.session(), request as never)))
        .message).toBe(message);
    }
    expect(await r.sql("SELECT id FROM connections")).toEqual([]);
    expect(await r.sql("SELECT flow FROM connection_flows")).toEqual([]);
    const key = await r.stub.connectionPrepare(r.session(), {
      ...base, kind: "api_key", revocable: false });
    expect(key).not.toHaveProperty("flow");
  });

  it("is cancelled with every session by sessionCloseAll, so a late callback is refused",
     async () => {
    const r = await gated();
    const flows = [await begun(r), await begun(r)];
    const { session: other } = await r.stub.sessionOpen(
      r.bind({ authenticated_at: Math.floor(Date.now() / 1000) }));
    const theirs = await begun(r, () => r.bind({ session: other }));
    // Signing one session out cancels that session's flows only.
    expect(await r.stub.sessionClose(r.bind({ session: other }))).toEqual({ closed: 1 });
    expect((await r.sql("SELECT status FROM connection_flows ORDER BY created_at"))
      .map((row) => row["status"])).toEqual(["open", "open", "cancelled"]);
    expect(await r.stub.sessionCloseAll(r.bind())).toEqual({ closed: 1 });
    expect((await r.sql("SELECT status, verifier FROM connection_flows ORDER BY created_at"))
      .map((row) => [row["status"], row["verifier"]]))
      .toEqual([["cancelled", null], ["cancelled", null], ["cancelled", null]]);
    // The owner signs in again: the late callbacks still find no flow.
    const { session } = await r.stub.sessionOpen(
      r.bind({ authenticated_at: Math.floor(Date.now() / 1000) }));
    for (const flow of [...flows, theirs]) {
      expect((await failure(() => r.stub.flowFinish(r.bind({ session }),
                                                    { state: flow.state }))).message)
        .toBe("no open connection flow for this state in this session");
    }
  });

  it("connects through the gateway's exchange, the tokens only in the sealed blob",
     async () => {
    const r = await gated();
    const connection = await r.connectOAuth();
    const [listed] = (await r.stub.connectionList(r.session())).connections;
    expect(listed).toMatchObject({ connection, kind: "oauth2", status: "active", version: 1 });
    expect(listed!.expires_at).toBeGreaterThan(Date.now());
    const [secret] = await secrets(r, connection);
    const opened = await open(keyring("k2"), { owner: r.name, connection, version: 1 },
                              secret!["blob"]);
    expect(opened.credential.kind === "oauth2" && opened.credential.oauth.refresh).toBe("rt-1");
    expect(JSON.stringify(await r.sql("SELECT * FROM connections"))).not.toContain("rt-1");
  });
});

describe("a revoke", () => {
  it("deletes an API key's blob in its own unit, and the connection is unusable at once",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-revoked");
    expect(await r.stub.connectionRevoke(r.session(), { connection }))
      .toEqual({ connection, revoked: true, provider_revoke: false });
    expect(await secrets(r, connection)).toEqual([]);
    expect(await r.in((instance) => instance.parts.provider.getModels())).toEqual([]);
    expect(await r.stub.connectionRevoke(r.session(), { connection }))
      .toEqual({ connection, revoked: false, provider_revoke: false });
    expect((await r.stub.connectionList(r.session())).connections[0])
      .toMatchObject({ status: "revoked", provider_revoke_pending: false });
  });

  it("queues an OAuth credential's provider revoke, which the lane sends, then deletes it",
     async () => {
    const r = await gated();
    const connection = await r.connectOAuth();
    const [held] = await secrets(r, connection);
    expect(await r.stub.connectionRevoke(r.session(), { connection }))
      .toEqual({ connection, revoked: true, provider_revoke: true });
    // Unusable at once, the blob kept only for the provider revoke, which the lane sends
    // (its job is due at once, so the runtime may already have run it).
    expect(await r.in((instance) => instance.parts.provider.getModels())).toEqual([]);
    await lanes(r);
    const sent = await r.in((instance) =>
      instance.calls.filter((call) => call.method === "revokeAtProvider"));
    expect(sent.map((call) => JSON.parse(call.text)))
      .toEqual([[r.name, connection, 1, held!["blob"]]]);
    expect(await secrets(r, connection)).toEqual([]);
    expect((await r.stub.connectionList(r.session())).connections[0])
      .toMatchObject({ status: "revoked", provider_revoke_pending: false });
  });

  it("retries a provider revoke that fails, then deletes the blob anyway, with a notice",
     async () => {
    const r = await gated();
    const prepared = await r.stub.connectionPrepare(r.session(), {
      source: "openai-oauth", kind: "oauth2", display_name: "OAuth", provider: "openai",
      revocable: true, redirect_uri: REDIRECT });
    const plaintext: Credential = {
      kind: "oauth2", upstream: OAUTH.upstream, endpoints: OAUTH.endpoints, sealed_at: 1,
      oauth: { ...OAUTH.oauth!, access: "at-down", refresh: "rt-down", expires_at: null } };
    const blob = await sealedAs("k2", r.name, prepared.connection, 1, plaintext);
    await r.stub.connectionCommit(r.session(), { connection: prepared.connection, version: 1,
                                                 blob, kid: "k2", models: [],
                                                 expires_at: null, egress: scopeOf(OAUTH) });
    await r.stub.connectionRevoke(r.session(), { connection: prepared.connection });
    // The first attempt is due at once (the runtime may run it first); each later one waits.
    await lanes(r);
    expect((await r.sql("SELECT revoke_tries FROM connections"))[0]!["revoke_tries"]).toBe(1);
    for (let attempt = 2; attempt < CONNECTION_LIMITS.revokeTries; attempt++) {
      await r.sql("UPDATE connections SET revoke_due = 1");
      await lanes(r);
      expect((await r.sql("SELECT revoke_tries FROM connections"))[0]!["revoke_tries"])
        .toBe(attempt);
      expect(await secrets(r, prepared.connection)).toHaveLength(1);
    }
    await r.sql("UPDATE connections SET revoke_due = 1");
    await lanes(r);
    expect(await secrets(r, prepared.connection)).toEqual([]);
    const { notices } = await r.stub.noticeList(r.bind());
    expect(notices.map((notice) => notice.type)).toContain("connection_revoke_failed");
    expect((await r.sql("SELECT revoke_due FROM connections"))[0]!["revoke_due"]).toBeNull();
  });
});

describe("a credential its provider cannot be asked to revoke", () => {
  async function issued(r: Gated, revocable: boolean) {
    const prepared = await r.stub.connectionPrepare(r.session(), {
      source: "openrouter_user_models_v1", kind: "oauth2", display_name: "Bought",
      provider: "anthropic", revocable, redirect_uri: REDIRECT });
    const blob = await sealedAs("k2", r.name, prepared.connection, 1, {
      kind: "api_key", secret: "sk-bought", upstream: ANTHROPIC.upstream,
      endpoints: ANTHROPIC.endpoints, sealed_at: 1 });
    return { connection: prepared.connection, blob };
  }

  it("is deleted in the revoke's unit, never claimed revocable, with a notice", async () => {
    const r = await gated();
    const { connection, blob } = await issued(r, false);
    const view = await r.stub.connectionCommit(r.session(), {
      connection, version: 1, blob, kid: "k2", models: [], expires_at: null,
      egress: scopeOf(ANTHROPIC) });
    expect(view).toMatchObject({ status: "active", provider_revocable: false });
    expect(await r.stub.connectionRevoke(r.session(), { connection }))
      .toEqual({ connection, revoked: true, provider_revoke: false });
    expect(await secrets(r, connection)).toEqual([]);
    expect((await r.stub.connectionList(r.session())).connections[0])
      .toMatchObject({ status: "revoked", provider_revocable: false,
                       provider_revoke_pending: false });
    const { notices } = await r.stub.noticeList(r.bind());
    expect(notices.find((made) => made.type === "connection_revoke_unsupported")?.detail)
      .toContain("delete it at the provider");
  });

  it("leaves a pasted key without a notice: the owner holds it", async () => {
    const r = await gated();
    const connection = await r.connect("sk-mine");
    await r.stub.connectionRevoke(r.session(), { connection });
    expect((await r.stub.noticeList(r.bind())).notices).toEqual([]);
  });
});

describe("an issued credential whose setup check failed", () => {
  it("is committed in error, its blob kept for the lane's provider revoke", async () => {
    const r = await gated();
    const prepared = await r.stub.connectionPrepare(r.session(), {
      source: "openai-oauth", kind: "oauth2", display_name: "OAuth", provider: "openai",
      revocable: true, redirect_uri: REDIRECT });
    const blob = await sealedAs("k2", r.name, prepared.connection, 1, {
      kind: "oauth2", upstream: OAUTH.upstream, endpoints: OAUTH.endpoints, sealed_at: 1,
      oauth: { ...OAUTH.oauth!, access: "at-1", refresh: "rt-1", expires_at: null } });
    const view = await r.stub.connectionCommit(r.session(), {
      connection: prepared.connection, version: 1, blob, kid: "k2", models: [],
      expires_at: null, failed: "upstream_timeout" });
    expect(view).toMatchObject({ status: "error", provider_revoke_pending: true });
    expect(await r.in((instance) => instance.parts.provider.getModels())).toEqual([]);
    await lanes(r);
    const sent = await r.in((instance) =>
      instance.calls.filter((call) => call.method === "revokeAtProvider"));
    expect(sent.map((call) => JSON.parse(call.text)))
      .toEqual([[r.name, prepared.connection, 1, blob]]);
    expect(await secrets(r, prepared.connection)).toEqual([]);
    expect((await r.stub.connectionList(r.session())).connections[0])
      .toMatchObject({ status: "error", provider_revoke_pending: false });
  });

  it("is never stored when it cannot be revoked, and the owner is told", async () => {
    const r = await gated();
    const prepared = await r.stub.connectionPrepare(r.session(), {
      source: "openrouter_user_models_v1", kind: "oauth2", display_name: "Bought",
      provider: "anthropic", revocable: false, redirect_uri: REDIRECT });
    const blob = await sealedAs("k2", r.name, prepared.connection, 1, {
      kind: "api_key", secret: "sk-bought", upstream: ANTHROPIC.upstream,
      endpoints: ANTHROPIC.endpoints, sealed_at: 1 });
    expect(await r.stub.connectionCommit(r.session(), {
      connection: prepared.connection, version: 1, blob, kid: "k2", models: [],
      expires_at: null, failed: "deadline" }))
      .toMatchObject({ status: "error", provider_revoke_pending: false });
    expect(await secrets(r, prepared.connection)).toEqual([]);
    const { notices } = await r.stub.noticeList(r.bind());
    expect(notices.find((made) => made.type === "connection_setup_failed")?.detail)
      .toMatch(/failed \(deadline\).*delete it at the provider/);
  });

  it("is refused with models, without a code, or for a pasted key", async () => {
    const r = await gated();
    const oauth = await r.stub.connectionPrepare(r.session(), {
      source: "openai-oauth", kind: "oauth2", display_name: "OAuth", provider: "openai",
      revocable: true, redirect_uri: REDIRECT });
    const key = await r.stub.connectionPrepare(r.session(), {
      source: "anthropic", kind: "api_key", display_name: "Key", provider: "anthropic",
      revocable: false });
    const models = (await catalog(ANTHROPIC)).slice(0, 1);
    for (const [connection, extra] of [
      [oauth.connection, { failed: "deadline", models }],
      [oauth.connection, { failed: "Not A Code", models: [] }],
      [key.connection, { failed: "deadline", models: [] }],
    ] as const) {
      const blob = await sealedAs("k2", r.name, connection, 1, {
        kind: "api_key", secret: "sk-x", upstream: ANTHROPIC.upstream,
        endpoints: ANTHROPIC.endpoints, sealed_at: 1 });
      expect((await failure(() => r.stub.connectionCommit(r.session(), {
        connection, version: 1, blob, kid: "k2", ...extra } as never))).message)
        .toBe("a failed setup commits an issued credential, its code and no models");
    }
  });
});

describe("a provider revoke and a refresh in flight", () => {
  /** An OAuth connection whose token is near expiry, its refresh held until released. */
  async function heldRefresh(r: Gated) {
    const connection = await r.connectOAuth(OAUTH, Date.now() + 60_000);
    let release!: () => void;
    await r.in((instance) => {
      instance.faults.refreshGate = new Promise<void>((resolve) => { release = resolve; });
    });
    const call = r.in(async (instance) => (await provider(instance)).streamSimple(
      instance.parts.provider.getModels()[0]!, asked("cmd:echo"), await direct(instance)).result());
    await until(async () => (await r.in((instance) => instance.calls))
      .some((made) => made.method === "refresh"), "the refresh to be sent");
    // Resolved in the object, where the gate was made: a promise is its I/O.
    return { connection, call, release: () => r.in(() => release()) };
  }

  it("revokes the rotated tokens of a refresh that lands after the lane revoked the old",
     async () => {
    const r = await gated();
    const { connection, call, release } = await heldRefresh(r);
    const [old] = await secrets(r, connection);
    await r.stub.connectionRevoke(r.session(), { connection });
    await until(async () => {
      await lanes(r);
      return (await secrets(r, connection)).length === 0;
    }, "the old tokens' revoke", 50);
    expect((await r.sql("SELECT revoke_due FROM connections"))[0]!["revoke_due"]).toBeNull();
    await release();
    expect((await call).errorMessage).toBe("connection_unavailable");
    // The rotated tokens are kept, and owed a revoke of their own.
    expect((await secrets(r, connection)).map((row) => row["version"])).toEqual([2]);
    await until(async () => {
      await lanes(r);
      return (await secrets(r, connection)).length === 0;
    }, "the rotated tokens' revoke", 50);
    const sent = (await r.in((instance) => instance.calls))
      .filter((made) => made.method === "revokeAtProvider").map((made) => JSON.parse(made.text));
    expect(sent.map((args) => args.slice(0, 3))).toEqual([[r.name, connection, 1],
                                                         [r.name, connection, 2]]);
    expect(sent[0]![3]).toBe(old!["blob"]);
  });

  it("revokes the newer tokens a refresh stored after the revoke, whenever the lane runs",
     async () => {
    const r = await gated();
    const { connection, call, release } = await heldRefresh(r);
    await r.stub.connectionRevoke(r.session(), { connection });
    // The lane's revoke is due at once: the runtime may send it before the refresh lands.
    await release();
    // The call that asked for the refresh finds its connection gone.
    expect((await call).errorMessage).toBe("connection_unavailable");
    await until(async () => {
      await lanes(r);
      return (await secrets(r, connection)).length === 0;
    }, "every owed revoke to be sent", 50);
    const sent = (await r.in((instance) => instance.calls))
      .filter((made) => made.method === "revokeAtProvider").map((made) => JSON.parse(made.text));
    expect(sent.at(-1)?.slice(0, 3)).toEqual([r.name, connection, 2]);
    expect((await r.sql("SELECT revoke_due FROM connections"))[0]!["revoke_due"]).toBeNull();
  });

  it("asks again for tokens a refresh stored while the lane's revoke was in flight",
     async () => {
    const r = await gated();
    const { connection, call, release } = await heldRefresh(r);
    const [old] = await secrets(r, connection);
    let letRevoke!: () => void;
    await r.in((instance) => {
      instance.faults.revokeGate = new Promise<void>((resolve) => { letRevoke = resolve; });
    });
    await r.stub.connectionRevoke(r.session(), { connection });
    // The lane sends the revoke of version 1, held, and the refresh lands meanwhile.
    await r.sql("UPDATE cf_agents_jobs SET time = 0");
    void runDurableObjectAlarm(r.stub).catch(() => false);
    const revokes = async () => (await r.in((instance) => instance.calls))
      .filter((made) => made.method === "revokeAtProvider").map((made) => JSON.parse(made.text));
    await until(async () => (await revokes()).length === 1, "the first revoke to be sent");
    await release();
    await call;
    await until(async () => (await secrets(r, connection))
      .some((row) => row["version"] === 2), "the refresh to land");
    await r.in((instance) => {
      instance.faults.revokeGate = null;
      letRevoke();
    });
    // The revoke of version 1 settles, and the lane is owed one for version 2.
    await until(async () => {
      await lanes(r);
      return (await secrets(r, connection)).length === 0;
    }, "the newer tokens' revoke", 50);
    const sent = await revokes();
    expect(sent[0]).toEqual([r.name, connection, 1, old!["blob"]]);
    expect(sent.at(-1)?.slice(0, 3)).toEqual([r.name, connection, 2]);
  });

  it("deletes the blob of a source with no revocation endpoint, with a notice that the " +
     "provider honours the token until it expires", async () => {
    const r = await gated();
    const prepared = await r.stub.connectionPrepare(r.session(), {
      source: "openai-oauth", kind: "oauth2", display_name: "OAuth", provider: "openai",
      revocable: true, redirect_uri: REDIRECT });
    const { revocation_endpoint: _none, ...oauth } = OAUTH.oauth!;
    const blob = await sealedAs("k2", r.name, prepared.connection, 1, {
      kind: "oauth2", upstream: OAUTH.upstream, endpoints: OAUTH.endpoints, sealed_at: 1,
      oauth: { ...oauth, access: "at-1", refresh: "rt-1", expires_at: null } });
    await r.stub.connectionCommit(r.session(), { connection: prepared.connection, version: 1,
                                                 blob, kid: "k2", models: [],
                                                 expires_at: null, egress: scopeOf(OAUTH) });
    expect(await r.stub.connectionRevoke(r.session(), { connection: prepared.connection }))
      .toMatchObject({ provider_revoke: true });
    await lanes(r);
    expect(await secrets(r, prepared.connection)).toEqual([]);
    expect((await r.sql("SELECT revoke_due FROM connections"))[0]!["revoke_due"]).toBeNull();
    const { notices } = await r.stub.noticeList(r.bind());
    const notice = notices.find((made) => made.type === "connection_revoke_unsupported");
    expect(notice?.detail).toContain("honours its token until it expires");
    expect(notices.map((made) => made.type)).not.toContain("connection_revoke_failed");
  });
});

describe("the refresh lease", () => {
  it("refreshes an expiring OAuth credential once, however many calls need it", async () => {
    const r = await gated();
    const connection = await r.connectOAuth(OAUTH, Date.now() + 60_000);
    const answers = await calls(r, 4);
    expect(answers.map((answer) => answer.stopReason)).toEqual(["stop", "stop", "stop", "stop"]);
    // Every call carried the refreshed token: the one refresh's rotation.
    expect(answers.map((answer) => textOf(answer).includes("Bearer at-2|none:client-1")))
      .toEqual([true, true, true, true]);
    const made = await r.in((instance) => instance.calls.map((call) => call.method));
    expect(made.filter((method) => method === "refresh")).toEqual(["refresh"]);
    const [row] = await r.sql("SELECT version, expires_at, lease_id, lease_until " +
                              "FROM connections");
    expect(row).toMatchObject({ version: 2, lease_id: null, lease_until: null });
    expect(row!["expires_at"]).toBeGreaterThan(Date.now() + 1_000_000);
    const [secret] = await secrets(r, connection);
    const opened = await open(keyring("k2"), { owner: r.name, connection, version: 2 },
                              secret!["blob"]);
    expect(opened.credential.kind === "oauth2" && opened.credential.oauth.refresh).toBe("rt-2");
    // Fresh now: the next calls refresh nothing.
    await calls(r, 2);
    expect((await r.in((instance) => instance.calls.map((call) => call.method)))
      .filter((method) => method === "refresh")).toEqual(["refresh"]);
  });

  it("takes over a lease a dead instance held, and refreshes once", async () => {
    const r = await gated();
    await r.connectOAuth(OAUTH, Date.now() + 60_000);
    await r.sql("UPDATE connections SET lease_id = 'dead', lease_until = ?", Date.now() + 60_000);
    await evictDurableObject(r.stub);
    const answers = await calls(r, 2);
    expect(answers.map((answer) => answer.stopReason)).toEqual(["stop", "stop"]);
    expect((await r.sql("SELECT version FROM connections"))[0]!["version"]).toBe(2);
  });

  it("fails every waiting call loudly when the provider refuses the refresh, and the " +
     "connection until the owner reconnects", async () => {
    const r = await gated();
    const prepared = await r.stub.connectionPrepare(r.session(), {
      source: "openai-oauth", kind: "oauth2", display_name: "OAuth", provider: "openai",
      revocable: true, redirect_uri: REDIRECT });
    const plaintext: Credential = {
      kind: "oauth2", upstream: OAUTH.upstream, endpoints: OAUTH.endpoints, sealed_at: 1,
      oauth: { ...OAUTH.oauth!, access: "at-dead", refresh: "rt-dead",
               expires_at: Date.now() + 60_000 } };
    const blob = await sealedAs("k2", r.name, prepared.connection, 1, plaintext);
    await r.stub.connectionCommit(r.session(), {
      connection: prepared.connection, version: 1, blob, kid: "k2",
      models: await catalog(OPENAI), expires_at: Date.now() + 60_000, egress: scopeOf(OAUTH) });
    const answers = await calls(r, 3);
    expect(answers.map((answer) => answer.errorMessage)).toEqual(Array(3).fill(
      "credential_refresh_failed: oauth_invalid_grant"));
    expect((await r.in((instance) => instance.calls.map((call) => call.method)))
      .filter((method) => method === "refresh")).toEqual(["refresh"]);
    expect(await r.sql("SELECT status, lease_id FROM connections"))
      .toEqual([{ status: "error", lease_id: null }]);
  });
});

describe("a refresh that lands on a changed connection", () => {
  it.each([
    ["failed meanwhile, as a refused grant leaves it", "UPDATE connections SET status = 'error'",
     { status: "error", version: 1, lease_id: null }],
    ["at a version it was not refreshed from", "UPDATE connections SET version = 7",
     { status: "active", version: 7, lease_id: null }],
    ["under a lease another holder took over", "UPDATE connections SET lease_id = 'other'",
     { status: "active", version: 1, lease_id: "other" }],
  ])("is dropped when the connection %s, and its own lease with it", async (_what, change,
                                                                             after) => {
    const r = await gated();
    const connection = await r.connectOAuth(OAUTH, Date.now() + 60_000);
    let release!: () => void;
    await r.in((instance) => {
      instance.faults.refreshGate = new Promise<void>((resolve) => { release = resolve; });
    });
    const call = r.in(async (instance) => (await provider(instance)).streamSimple(
      instance.parts.provider.getModels()[0]!, asked("cmd:echo"), await direct(instance)).result());
    await until(async () => (await r.sql("SELECT lease_id FROM connections"))[0]!["lease_id"]
      !== null, "the lease to be taken");
    await r.sql(change);
    await r.in(() => release());
    await call;
    expect(await r.sql("SELECT status, version, lease_id FROM connections")).toEqual([after]);
    // The refreshed tokens were never stored.
    expect((await secrets(r, connection)).map((row) => row["version"])).toEqual([1]);
  });
});

describe("a credential", () => {
  it("is refused as expired when its refresh came back already past its expiry", async () => {
    const r = await gated();
    await r.connectOAuth(OAUTH, Date.now() + 60_000);
    await r.in((instance) => {
      instance.faults.refreshed = (answer) => ({ ...answer, expires_at: Date.now() - 1 });
    });
    const [answer] = await calls(r, 1);
    expect(answer!.errorMessage).toBe("credential_expired");
    expect(await r.sql("SELECT state FROM model_reservations")).toEqual([]);
  });
});

describe("the largest built-in catalog", () => {
  it("connects, found by a scan of pi-ai's providers", async () => {
    let largest: { provider: string; api: string; baseUrl: string; count: number } | undefined;
    for (const provider of builtinProviders()) {
      const byApi = new Map<string, { count: number; baseUrl: string }>();
      for (const model of provider.getModels()) {
        const seen = byApi.get(model.api) ?? { count: 0, baseUrl: model.baseUrl };
        byApi.set(model.api, { ...seen, count: seen.count + 1 });
      }
      for (const [api, { count, baseUrl }] of byApi) {
        if (SUPPORTED_APIS.includes(api) && count > (largest?.count ?? 0)) {
          largest = { provider: provider.id, api, baseUrl, count };
        }
      }
    }
    expect(largest!.count).toBeGreaterThan(256);
    expect(largest!.count * 2).toBeLessThanOrEqual(CONNECTION_LIMITS.maxModels);
    const base = new URL(largest!.baseUrl);
    const record: Source = {
      upstream: { provider: largest!.provider, api: largest!.api, base_url: largest!.baseUrl,
                  auth: { header: "authorization", scheme: "Bearer" } },
      endpoints: [{ origin: base.origin, paths: [`${base.pathname.replace(/\/$/, "")}/*`],
                    methods: ["GET", "POST"] }],
    };
    const listed = await catalog(record);
    expect(listed).toHaveLength(largest!.count);
    // `maxModels` of the largest record of any built-in catalog fit within the JSON bound.
    const largestRecord = Math.max(...builtinProviders().flatMap((provider) =>
      provider.getModels().filter((model) => isModelType(model, "chat"))
        .map((model) => JSON.stringify(recordOf(model)).length)));
    expect(largestRecord * CONNECTION_LIMITS.maxModels).toBeLessThanOrEqual(MODELS_JSON_MAX);
    expect(JSON.stringify(listed)).toContain("thinkingLevelMap");
    const r = await gated();
    const connection = await r.connect("sk-large", record, "Large", listed);
    expect((await r.stub.connectionList(r.session())).connections[0])
      .toMatchObject({ connection, status: "active" });
    expect((await r.stub.connectionList(r.session())).connections[0]!.models)
      .toHaveLength(largest!.count);
  });
});

describe("a connected model's record", () => {
  /** Commit `models` on a fresh pending connection; the refusal's message, or null. */
  async function committed(r: Gated, models: unknown[]): Promise<string | null> {
    const prepared = await r.stub.connectionPrepare(r.session(), {
      source: "anthropic", kind: "api_key", display_name: "Key", provider: "anthropic",
      revocable: false });
    const sealed = await env.GATEWAY.seal(r.name, prepared.connection, 1, "sk-x", ANTHROPIC);
    return r.stub.connectionCommit(r.session(), { connection: prepared.connection, version: 1,
                                                  blob: sealed.blob, kid: sealed.kid,
                                                  models: models as ModelInfo[],
                                                  egress: scopeOf(ANTHROPIC) })
      .then(() => null, (error: Error) => error.message);
  }

  it("is kept whole for every model of every built-in catalog", async () => {
    const records = builtinProviders().flatMap((provider) => provider.getModels()
      .filter((model) => isModelType(model, "chat")).map((model, i) => ({
        ...JSON.parse(JSON.stringify(recordOf(model))) as ModelInfo,
        id: `${provider.id}.${i}` })));
    expect(records.length).toBeGreaterThan(CONNECTION_LIMITS.maxModels);
    const r = await gated();
    for (let at = 0; at < records.length; at += CONNECTION_LIMITS.maxModels) {
      expect(await committed(r, records.slice(at, at + CONNECTION_LIMITS.maxModels))).toBeNull();
    }
    const kept = await r.in((instance) => instance.parts.provider.getModels());
    expect(kept.length).toBe(records.length + 1);
  });

  it("keeps maxModels of the largest built-in record", async () => {
    const largest = builtinProviders().flatMap((provider) => provider.getModels()
      .filter((model) => isModelType(model, "chat"))
      .map((model) => JSON.parse(JSON.stringify(recordOf(model))) as ModelInfo))
      .reduce((a, b) => (JSON.stringify(b).length > JSON.stringify(a).length ? b : a));
    const models = Array.from({ length: CONNECTION_LIMITS.maxModels }, (_, i) =>
      ({ ...largest, id: `${largest.id.slice(0, 190)}.${String(i).padStart(4, "0")}` }));
    expect(JSON.stringify(models).length).toBeGreaterThan(600 * 1024);
    const r = await gated();
    expect(await committed(r, models)).toBeNull();
    expect(await r.in((instance) => instance.parts.provider.getModels().length))
      .toBe(CONNECTION_LIMITS.maxModels + 1);
  });

  it.each([
    ["an unknown field", { compat: {} }],
    ["the gateway's own field", { baseUrl: "https://api.anthropic.com" }],
    ["an unknown thinking level", { thinkingLevelMap: { extreme: "x" } }],
    ["a thinking value of another type", { thinkingLevelMap: { high: 1 } }],
    ["a cache lifetime of zero", { promptCache: { short: 0 } }],
    ["an unknown input limit", { inputLimits: { maxTokens: 1 } }],
    ["a request byte limit of zero", { inputLimits: { maxRequestBytes: 0 } }],
    ["an unknown image limit", { inputLimits: { images: { maxSize: 1 } } }],
    ["a fractional image count", { inputLimits: { images: { maxPerMessage: 1.5 } } }],
    ["a JPEG quality past 100", { inputLimits: { images: { resize: { jpegQuality: 101 } } } }],
    ["an unknown resize field", { inputLimits: { images: { resize: { dpi: 72 } } } }],
    ["no tiers in a tier list", { cost: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0,
                                          tiers: [] } }],
    ["a tier with no threshold", { cost: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0,
      tiers: [{ input: 2, output: 2, cacheRead: 0, cacheWrite: 0 }] } }],
    ["a tier at a negative price", { cost: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0,
      tiers: [{ inputTokensAbove: 1, input: -2, output: 2, cacheRead: 0, cacheWrite: 0 }] } }],
    ["a cost with an unknown rate", { cost: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0,
                                              cacheWrite1h: 2 } }],
    ["a context window of zero", { contextWindow: 0 }],
  ])("refuses a model with %s", async (_what, change) => {
    const r = await gated();
    const [model] = await catalog(ANTHROPIC);
    expect(await committed(r, [{ ...model, ...change }])).toBe("invalid connection model");
  });
});

describe("a blob resealed under the active kid", () => {
  async function oldKid(r: Gated) {
    const prepared = await r.stub.connectionPrepare(r.session(), {
      source: "anthropic", kind: "api_key", display_name: "Old", provider: "anthropic",
      revocable: false });
    const blob = await sealedAs("k1", r.name, prepared.connection, 1, {
      kind: "api_key", secret: "sk-old", upstream: ANTHROPIC.upstream,
      endpoints: ANTHROPIC.endpoints, sealed_at: 1 });
    await r.stub.connectionCommit(r.session(), { connection: prepared.connection, version: 1,
                                                 blob, kid: "k1",
                                                 models: await catalog(ANTHROPIC),
                                                 egress: scopeOf(ANTHROPIC) });
    return prepared.connection;
  }

  it("is stored by compare-and-swap: concurrent reseals keep one, at the next version",
     async () => {
    const r = await gated();
    const connection = await oldKid(r);
    const answers = await calls(r, 3);
    expect(answers.map((answer) => answer.stopReason)).toEqual(["stop", "stop", "stop"]);
    const rows = await secrets(r, connection);
    expect(rows.map((row) => [row["version"], row["kid"]])).toEqual([[2, "k2"]]);
    const opened = await open(keyring("k2"), { owner: r.name, connection, version: 2 },
                              rows[0]!["blob"]);
    expect(opened.credential.kind === "api_key" && opened.credential.secret).toBe("sk-old");
    expect((await r.sql("SELECT version FROM connections"))[0]!["version"]).toBe(2);
    // The next call carries the resealed blob, and nothing is resealed again.
    await calls(r, 1);
    expect((await secrets(r, connection)).map((row) => row["version"])).toEqual([2]);
  });

  it("never overwrites tokens a refresh stored while its call was in flight", async () => {
    const r = await gated();
    const prepared = await r.stub.connectionPrepare(r.session(), {
      source: "openai-oauth", kind: "oauth2", display_name: "OAuth", provider: "openai",
      revocable: true, redirect_uri: REDIRECT });
    const connection = prepared.connection;
    const far = Date.now() + 86_400_000;
    const blob = await sealedAs("k1", r.name, connection, 1, {
      kind: "oauth2", upstream: OAUTH.upstream, endpoints: OAUTH.endpoints, sealed_at: 1,
      oauth: { ...OAUTH.oauth!, access: "at-1", refresh: "rt-1", expires_at: far } });
    await r.stub.connectionCommit(r.session(), { connection, version: 1, blob, kid: "k1",
                                                 models: await catalog(OPENAI), expires_at: far,
                                                 egress: scopeOf(OAUTH) });
    const id = crypto.randomUUID().replace(/-/g, "").slice(0, 12);
    const opened = async () => ((await (await fetch(`https://status.test/cancelled?id=${id}`))
      .json()) as { cancelled: boolean | null }).cancelled === false;
    await r.in(async (instance, state) => {
      const auto = instance.parts.provider.getModels()[0]!;
      const stop = new AbortController();
      // A call in flight on version 1, under the old kid: its answer carries a reseal.
      const slow = (await provider(instance)).streamSimple(auto, asked(`cmd:slow-${id}`),
                                                        { ...await direct(instance, "slow"),
                                                          signal: stop.signal });
      await until(opened, "the slow call to open");
      // The token nears expiry, and another call refreshes it to version 2.
      state.storage.sql.exec("UPDATE connections SET expires_at = ?", Date.now() + 60_000);
      const fresh = await (await provider(instance)).streamSimple(
        auto, asked("cmd:echo"), await direct(instance, "fresh")).result();
      expect(textOf(fresh)).toContain("Bearer at-2|none:client-1");
      stop.abort();
      await slow.result();
    });
    const rows = await secrets(r, connection);
    expect(rows.map((row) => [row["version"], row["kid"]])).toEqual([[2, "k2"]]);
    const kept = await open(keyring("k2"), { owner: r.name, connection, version: 2 },
                            rows[0]!["blob"]);
    expect(kept.credential.kind === "oauth2" && kept.credential.oauth.refresh).toBe("rt-2");
  });

  it("is dropped while a refresh holds the lease", async () => {
    const r = await gated();
    const connection = await oldKid(r);
    await r.sql("UPDATE connections SET lease_id = 'refreshing', lease_until = ?",
                Date.now() + 60_000);
    expect((await calls(r, 1))[0]!.stopReason).toBe("stop");
    expect((await secrets(r, connection)).map((row) => [row["version"], row["kid"]]))
      .toEqual([[1, "k1"]]);
  });
});

describe("the plaintext key", () => {
  it("never reaches the object's storage, its gateway calls or its logs", async () => {
    const r = await gated();
    const key = `sk-plaintext-${crypto.randomUUID()}`;
    const logged: string[] = [];
    for (const method of ["log", "info", "warn", "error", "debug"] as const) {
      vi.spyOn(console, method).mockImplementation((...args: unknown[]) => {
        logged.push(args.map(String).join(" "));
      });
    }
    // The binder seals the key with the gateway; the object is handed only the blob.
    const connection = await r.connect(key);
    const answer = await r.in((instance) => instance.harness.prompt("cmd:drip"));
    expect(answer.status).toBe("done");
    await calls(r, 1, "cmd:drip");
    await r.stub.connectionRevoke(r.session(), { connection });
    const made = await r.in((instance) => instance.calls);
    expect(made.filter((call) => call.method === "fetch").length).toBeGreaterThan(1);
    expect(made.filter((call) => call.text.includes(key))).toEqual([]);
    const tables = await r.sql("SELECT name FROM sqlite_master WHERE type = 'table' " +
                               "AND name NOT LIKE '_cf_%'");
    expect(tables.length).toBeGreaterThan(20);
    for (const { name } of tables) {
      const rows = await r.sql(`SELECT * FROM "${String(name)}"`);
      expect([name, JSON.stringify(rows).includes(key)]).toEqual([name, false]);
    }
    expect(logged.filter((line) => line.includes(key))).toEqual([]);
  }, 15_000);  // two drips of 1.8 s each, past the default 5 s under a full run's load
});
