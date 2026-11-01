/**
 * The owner object on the production gateway provider (`GatedOwnerDO`), and the calls a
 * binder makes to connect a model source: prepare in the owner's session, seal or
 * exchange with the real gateway Worker run beside the tests, then commit. The source
 * records are the gateway tests' own, so every model call reaches the gateway's fake
 * upstream in the provider's own wire format.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { type AssistantMessage, normalizeContext, type TranscriptContext } from "@earendil-works/pi-ai";
import { ProviderDoc, ROOT_CONVERSATION_ID } from "@earendil-works/pi-durable";
import type { TelemetrySpan } from "@earendil-works/pi-telemetry";

import type { Credential, Source } from "../../gateway/src/credential";
import { seal } from "../../gateway/src/keyring";
import type { Provider } from "@earendil-works/pi-ai";

import type { AgentParts, OwnerAgent } from "../src/agent";
import type { ModelInfo } from "../src/connections";
import { originContext } from "../src/origin";
import type { Binding } from "../src/owner-do";
import { signedIn } from "./ledger-support";
import type { TestHarness } from "./agent-support";
import type { GatedOwnerDO } from "./worker";

export const ANTHROPIC: Source = Object.freeze({
  upstream: { provider: "anthropic", api: "anthropic-messages", base_url: "https://api.anthropic.com",
              auth: { header: "x-api-key", scheme: "" },
              verify_url: "https://api.anthropic.com/v1/models" },
  endpoints: [{ origin: "https://api.anthropic.com", paths: ["/v1/messages", "/v1/models"],
                methods: ["GET", "POST"] }],
});

export const OPENAI: Source = Object.freeze({
  upstream: { provider: "openai", api: "openai-responses", base_url: "https://api.openai.com/v1",
              auth: { header: "authorization", scheme: "Bearer" } },
  endpoints: [{ origin: "https://api.openai.com", paths: ["/v1/responses", "/v1/models"],
                methods: ["GET", "POST"] }],
});

/** An OAuth source on the fake token and revocation endpoints, its model calls OpenAI's. */
export const OAUTH: Source = Object.freeze({
  upstream: OPENAI.upstream, endpoints: OPENAI.endpoints,
  oauth: { client_id: "client-1", client_auth: { method: "none" as const },
           token_endpoint: "https://auth.test/token",
           revocation_endpoint: "https://auth.test/revoke", refresh_with: "rfc6749" as const },
});

export const REDIRECT = "https://tinyassets.io/oauth/connect/callback";

/**
 * The models the gateway's setup check lists for a source: its provider's catalog, once
 * its verify URL answers 2xx. Only the fake's Anthropic models endpoint answers a check, so
 * a throwaway working key is checked there, under the source's own provider and API.
 */
export async function catalog(record: Source): Promise<ModelInfo[]> {
  const probe: Source = {
    upstream: { ...record.upstream, auth: ANTHROPIC.upstream.auth,
                verify_url: ANTHROPIC.upstream.verify_url! },
    endpoints: [...record.endpoints, ...ANTHROPIC.endpoints],
  };
  const { blob } = await env.GATEWAY.seal("catalog", "c-catalog", 1, "sk-good", probe);
  return (await env.GATEWAY.verify("catalog", "c-catalog", 1, blob)).models as unknown as
    ModelInfo[];
}

/** The keyring the gateway holds, with `active` made the active kid. */
export function keyring(active: "k1" | "k2"): { GATEWAY_KEYRING: string } {
  const ring = JSON.parse(env.TEST_KEYRING) as { keys: Record<string, string> };
  return { GATEWAY_KEYRING: JSON.stringify({ active, keys: ring.keys }) };
}

/** A credential sealed by the gateway's own code under kid `kid`, as a test needs it. */
export async function sealedAs(kid: "k1" | "k2", owner: string, connection: string,
                               version: number, plaintext: Credential): Promise<string> {
  return (await seal(keyring(kid), { owner, connection, version }, plaintext)).blob;
}

/** A fresh owner on the gated class, signed in once, and a binder's calls on its object. */
export async function gated() {
  const name = `owner-${crypto.randomUUID()}`;
  const stub: DurableObjectStub<GatedOwnerDO> = env.GATED.getByName(name);
  const bind = (extra: Partial<Binding> = {}): Binding =>
    ({ owner: name, caller: name, origin: null, ...extra });
  const { session: cookie } = await stub.sessionOpen(bind({ authenticated_at: signedIn() }));
  const session = (): Binding => bind({ session: cookie });

  /** Connect a pasted key: prepare, seal, the models, commit; returns the connection. */
  const connect = async (key: string, record: Source = ANTHROPIC, display = "Key",
                         models?: ModelInfo[]) => {
    const prepared = await stub.connectionPrepare(session(), {
      source: record.upstream.provider, kind: "api_key", display_name: display,
      provider: record.upstream.provider, revocable: false });
    const sealed = await env.GATEWAY.seal(name, prepared.connection, 1, key, record);
    const committed = await stub.connectionCommit(session(), {
      connection: prepared.connection, version: 1, blob: sealed.blob, kid: sealed.kid,
      models: models ?? await catalog(record) });
    return committed.connection;
  };

  /** Connect by OAuth: prepare (its flow begun), finish, the gateway's exchange, commit. */
  const connectOAuth = async (record: Source = OAUTH, expiresAt?: number) => {
    const prepared = await stub.connectionPrepare(session(), {
      source: "openai-oauth", kind: "oauth2", display_name: "OAuth",
      provider: record.upstream.provider, revocable: true, redirect_uri: REDIRECT });
    const finished = await stub.flowFinish(session(), { state: prepared.flow!.state });
    const exchanged = await env.GATEWAY.exchange(name, prepared.connection, 1, {
      code: "good-code", verifier: finished.verifier, redirect_uri: finished.redirect_uri },
      record);
    await stub.connectionCommit(session(), {
      connection: prepared.connection, version: 1, blob: exchanged.blob, kid: exchanged.kid,
      models: await catalog(record), expires_at: expiresAt ?? exchanged.expires_at });
    return prepared.connection;
  };

  return {
    name, stub, bind, session, cookie, connect, connectOAuth,
    in: <R>(act: (instance: GatedOwnerDO, state: DurableObjectState) => R | Promise<R>) =>
      runInDurableObject(stub, act),
    sql: (statement: string, ...params: SqlStorageValue[]) =>
      runInDurableObject(stub, (_instance: GatedOwnerDO, state) =>
        state.storage.sql.exec<Record<string, SqlStorageValue>>(statement, ...params).toArray()),
  };
}

export type Gated = Awaited<ReturnType<typeof gated>>;

/**
 * The options of a test's own call to the provider, outside any pi run: the provider
 * session of the owner's root conversation (a call must name a conversation the object
 * made), and the telemetry context of an input of its own, `turn`, as pi would hand it
 * (`origin.ts`), its turn open as an input's would be (an owner of the test's own, kept
 * as the thread host keeps them; a reopened Session's reconcile lets it go).
 */
export async function direct(instance: { harness: TestHarness; parts: AgentParts },
                             turn: string = crypto.randomUUID()):
    Promise<{ sessionId: string; telemetry: TelemetrySpan }> {
  const pi = await instance.harness.pi();
  const root = await pi.snapshot(ProviderDoc, ROOT_CONVERSATION_ID, BACKGROUND_CONTEXT);
  // One owner a turn, by its name: a turn asked for again is the one open, as pi's input
  // that owns it is, and the thread's open inputs stay within their bound.
  const owner = [...turn].reduce((sum, char) => (sum * 31 + char.charCodeAt(0)) % 1_000_000_000,
                                 7);
  await instance.parts.turns.owned(await instance.harness.thread(), [
    { owner: `s:${1_000_000_000 + owner}`, turn: `input:${turn}` }]);
  return { sessionId: root!.sessionId, telemetry: originContext(`input:${turn}`) };
}

/**
 * The provider as the owner's own thread's Session sees it (`forSession`): a test's own
 * call names that Session's conversations, as pi's calls do.
 */
export async function provider(instance: { agent: OwnerAgent; parts: AgentParts;
                                           harness: TestHarness }): Promise<Provider> {
  const pi = await instance.harness.pi();
  return instance.parts.provider.forSession((sessionId) =>
    instance.agent.modelAuthority(pi, sessionId));
}

/** A one-message context, as pi sends a request. */
export function asked(text: string, at = Date.now()): TranscriptContext {
  return normalizeContext({ messages: [{ role: "user", content: text, timestamp: at }] });
}

/** An answer's text. */
export function textOf(message: AssistantMessage): string {
  return message.content.map((part) => (part.type === "text" ? part.text : "")).join("");
}

/** Wait until `check` holds. */
export async function until(check: () => Promise<boolean> | boolean, what: string,
                            tries = 500): Promise<void> {
  for (let i = 0; i < tries; i++) {
    if (await check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timed out waiting for ${what}`);
}
