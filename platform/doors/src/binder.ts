/**
 * The binder (ADR-032 Decision 2; research checklist 1, 2, 9, 10): the only place a
 * verified credential becomes an owner's object and the `Binding` its doors take.
 *
 * Every field of a binding comes from the credential, never from the request: the owner
 * and caller from the token's grant, the key's directory entry or the verified sign-in;
 * the outside origin and its `source` from the token's client or the key id; and
 * `authenticated_at` from the grant's original sign-in or the key's mint time. A body,
 * query, header or cookie never names any of them. `session` is the one field the
 * request supplies, and only `session.ts` reads it, as the browser presented it: the
 * object checks it against its own session row and the binder never vouches for it.
 *
 * Each function below is one kind of verified credential, and each returns the owner's
 * object by `getByName(owner)` with the binding: no other source names `getByName` or
 * builds a binding (`guards/binder.test.ts`). The object checks the owner again against
 * its own name and stored owner, so a bug here reaching the wrong object fails there.
 */
import { createHash } from "node:crypto";

import type { ValidatedAccessToken } from "@cloudflare/workers-oauth-provider";

import type { Binding, Origin, OwnerDO } from "../../owner/src/owner-do";
import { principal } from "../../owner/src/text";

import {
  APP_RESOURCE, type DoorsEnv, FIRST_PARTY_CLIENTS, ISSUER, MCP_RESOURCE,
} from "./config";
import type { KeyEntry } from "./directory";

const HEX64 = /^[0-9a-f]{64}$/;
const KEY_ID = /^[0-9a-f]{16}$/;

/** What an MCP grant's props hold (research §1.4), encrypted by the provider. */
export interface McpProps {
  readonly kind: "mcp";
  readonly owner: string;
  readonly client: string;
  readonly family: string;
  /** The OAuth `client_id`, the outside call's `source.ref`. */
  readonly source_ref: string;
  /** The original interactive sign-in, whole seconds; no refresh moves it. */
  readonly authenticated_at: number;
}

/** What a first-party grant's props hold: the owner, and nothing to vouch for a session. */
export interface AppProps {
  readonly kind: "app";
  readonly owner: string;
}

export interface Bound {
  readonly binding: Binding;
  readonly stub: DurableObjectStub<OwnerDO>;
}

async function sha256(text: string): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * The outside identity an MCP client resolves to: `client` from the issuer and its
 * `client_id` (the Python's `issuer \0 client`), `family` from those and the redirect
 * URI, the installation boundary, so a re-authorization of one installation keeps its
 * agents' mailbox (research §1.4).
 */
export async function mcpOrigin(clientId: string, redirectUri: string):
    Promise<{ client: string; family: string }> {
  return {
    client: await mcpClient(clientId),
    family: await sha256(`tinyassets-mcp-family\0${ISSUER}\0${clientId}\0${redirectUri}`),
  };
}

/**
 * The outside identity an API key resolves to: the Python's `api_keys._client` and
 * `_family`, byte for byte, which the object's own `apiKeyOrigin` (`ledger.ts`) also is.
 * The object re-derives it from the key id wherever it matters (`registrant`), and the
 * tests hold the two equal; the ledger module itself is the object's alone (the
 * ledger-boundary guard).
 */
export function apiKeyOrigin(keyId: string): Origin {
  const hash = (text: string) => createHash("sha256").update(text, "utf8").digest("hex");
  return { client: hash(`tinyassets-api-key-client\0${keyId}`),
           family: hash(`tinyassets-api-key-family\0${keyId}`) };
}

/** The 64-hex outside client an OAuth `client_id` is, at our issuer. */
export function mcpClient(clientId: string): Promise<string> {
  return sha256(`tinyassets-mcp-client\0${ISSUER}\0${clientId}`);
}

function seconds(value: unknown, what: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new Error(`${what} is whole seconds`);
  }
  return value;
}

function bound(env: DoorsEnv, binding: Binding): Bound {
  return { binding, stub: env.OWNER.getByName(binding.owner) };
}

/**
 * An MCP access token the authorization server validated for `/mcp`: its grant's props,
 * cross-checked against what the provider itself recorded (the grant's user and client).
 * A mismatch is a bug, never a request to act on.
 */
export async function bindMcp(env: DoorsEnv, token: ValidatedAccessToken<unknown>):
    Promise<Bound> {
  const props = token.props as Partial<McpProps> | null;
  if (token.audience !== MCP_RESOURCE || typeof props !== "object" || props === null
      || props.kind !== "mcp") {
    throw new Error("an MCP binding needs an MCP grant's token");
  }
  const owner = principal(props.owner);
  if (owner !== token.userId || props.source_ref !== token.clientId) {
    throw new Error("an MCP grant's props disagree with its token");
  }
  if (typeof props.client !== "string" || props.client !== await mcpClient(token.clientId)
      || typeof props.family !== "string" || !HEX64.test(props.family)) {
    throw new Error("an MCP grant's origin is not its client's");
  }
  return bound(env, {
    owner, caller: owner,
    origin: { client: props.client, family: props.family },
    source: { kind: "mcp", ref: token.clientId },
    authenticated_at: seconds(props.authenticated_at, "an MCP grant's sign-in"),
  });
}

/**
 * An API key whose secret matched its directory entry (`api-keys.ts`): the entry's
 * owner, the key's own origin (`apiKeyOrigin`, which the object re-derives), and the
 * key's mint time as its sign-in, so a revoked key never passes the fence.
 */
export function bindApiKey(env: DoorsEnv, keyId: string, entry: KeyEntry): Bound {
  if (!KEY_ID.test(keyId)) {
    throw new Error("an API key binding needs its key id");
  }
  const owner = principal(entry.o);
  return bound(env, {
    owner, caller: owner, origin: apiKeyOrigin(keyId),
    source: { kind: "api_key", ref: keyId },
    authenticated_at: seconds(entry.c, "an API key's mint time"),
  });
}

/**
 * The owner's first-party token, validated for `/app` and issued to a first-party
 * client: the owner on its own account, with the browser's session cookie when
 * `session.ts` attached one.
 */
export function bindOwner(env: DoorsEnv, token: ValidatedAccessToken<unknown>,
                          session: string | undefined): Bound {
  const owner = appOwner(token);
  return bound(env, session === undefined ? { owner, caller: owner, origin: null }
                                          : { owner, caller: owner, origin: null, session });
}

/**
 * The owner's browser registering an agent for an API key it holds (research §5): the
 * one binding that pairs an outside origin with the owner's session. The key's entry
 * must be the owner's own; the object re-derives the origin from the key id.
 */
export function bindKeyRegistration(env: DoorsEnv, token: ValidatedAccessToken<unknown>,
                                    keyId: string, entry: KeyEntry,
                                    session: string | undefined): Bound {
  const owner = appOwner(token);
  if (!KEY_ID.test(keyId) || principal(entry.o) !== owner) {
    throw new Error("a key registration needs the owner's own key");
  }
  const base = {
    owner, caller: owner, origin: apiKeyOrigin(keyId),
    source: { kind: "api_key", ref: keyId } as const,
    authenticated_at: seconds(entry.c, "an API key's mint time"),
  };
  return bound(env, session === undefined ? base : { ...base, session });
}

/**
 * A fresh upstream sign-in at the callback (`authorize.ts`): the owner on its own
 * account, at the verified sign-in's time, which opens a session (`sessionOpen`).
 */
export function bindSignIn(env: DoorsEnv, subject: string, authenticatedAt: number): Bound {
  const owner = principal(subject);
  return bound(env, { owner, caller: owner, origin: null,
                      authenticated_at: seconds(authenticatedAt, "a sign-in's time") });
}

function appOwner(token: ValidatedAccessToken<unknown>): string {
  const props = token.props as Partial<AppProps> | null;
  if (token.audience !== APP_RESOURCE || !FIRST_PARTY_CLIENTS.includes(token.clientId)
      || typeof props !== "object" || props === null || props.kind !== "app") {
    throw new Error("an owner binding needs a first-party token");
  }
  const owner = principal(props.owner);
  if (owner !== token.userId) {
    throw new Error("a first-party grant's props disagree with its token");
  }
  return owner;
}
