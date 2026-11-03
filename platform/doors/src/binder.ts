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
import {
  BINDING_HEADER, CONVERSATION_HEADER, SOCKET_URL, TICKET_HEADER,
} from "../../owner/src/socket-shape";
import { principal } from "../../owner/src/text";

import { LEASE_LIVED, verifyToken } from "./routing-mac";

import {
  APP_RESOURCE, type DoorsEnv, FIRST_PARTY_CLIENTS, foreignIssuers, ISSUER, MCP_RESOURCE,
  ownerIssuer,
} from "./config";
import type { KeyEntry } from "./directory";
import type { SignIn } from "./upstream";

const HEX64 = /^[0-9a-f]{64}$/;
const KEY_ID = /^[0-9a-f]{16}$/;
const RUN = /^run_[0-9a-f]{32}$/;

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
 * agents' mailbox (research §1.4). A loopback redirect's port is dropped first: a native
 * client listens on whatever port is free (RFC 8252 §7.3), so its port names no
 * installation.
 */
export async function mcpOrigin(clientId: string, redirectUri: string):
    Promise<{ client: string; family: string }> {
  const installation = installationOf(redirectUri);
  return {
    client: await mcpClient(clientId),
    family: await sha256(`tinyassets-mcp-family\0${ISSUER}\0${clientId}\0${installation}`),
  };
}

const LOOPBACK_V4 = /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/;

/** A loopback host, as the provider counts one: `localhost`, `127.0.0.0/8` and `::1`. */
export function loopback(hostname: string): boolean {
  return hostname === "localhost" || hostname === "[::1]" || LOOPBACK_V4.test(hostname);
}

/** A redirect URI as an installation: a loopback one without its port. */
export function installationOf(redirectUri: string): string {
  const url = new URL(redirectUri);
  if (url.protocol === "http:" && loopback(url.hostname)) {
    url.port = "";
    return url.href;
  }
  return redirectUri;
}

/** Where a namespaced issuer's owners start: never an owner issuer's subject. */
const FOREIGN = "iss-";

/**
 * The owner a verified upstream sign-in names, from its issuer and subject, against an
 * explicit allow-list. The owner issuer's (AuthKit's) subject is the owner id as it
 * stands, so today's owners keep theirs. A listed foreign issuer's subject (Sign in with
 * ChatGPT, say, once its slice lists it) is namespaced by that issuer, so two providers'
 * subjects never name one owner; linking it to an existing owner is that slice's act.
 * Any other issuer is refused loudly, so an `iss` that differs from the configured one
 * by so much as a trailing slash can never move an owner into a new, empty account.
 */
export async function ownerOf(env: DoorsEnv, issuer: string, subject: string): Promise<string> {
  if (typeof issuer !== "string" || !issuer || typeof subject !== "string") {
    throw new Error("a sign-in names its issuer and subject");
  }
  if (issuer === ownerIssuer(env)) {
    if (subject.startsWith(FOREIGN) || subject.includes(":")) {
      throw new Error("the owner issuer's subject is outside the owner namespace");
    }
    return principal(subject);
  }
  if (!foreignIssuers(env).includes(issuer)) {
    throw new Error(`the sign-in's issuer ${JSON.stringify(issuer)} is neither the owner ` +
                    "issuer nor a listed one");
  }
  return principal(`${FOREIGN}${(await sha256(issuer)).slice(0, 32)}-${
    encodeURIComponent(principal(subject))}`);
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
 * A sandbox's egress token (`egress.ts`): the owner its own routing part names, on its own
 * account (the sandbox runs the owner's own commands), with what the object checks the
 * lease by: the SHA-256 of the token's secret, and the egress generation E2B's transform
 * set beside it. No directory is read: the object, strongly consistent, checks the hash,
 * the lease and its expiry. It refuses this binding at every door but the egress doors.
 */
export function bindSandbox(env: DoorsEnv, owner: string, tokenHash: string,
                            generation: number, presented: Presented = {}): Bound {
  if (!HEX64.test(tokenHash) || !Number.isSafeInteger(generation) || generation < 1) {
    throw new Error("a sandbox binding needs its token's hash and its network's generation");
  }
  const named = principal(owner);
  const extra = presented.secret !== undefined
    ? { secret: createHash("sha256").update(presented.secret, "utf8").digest("hex") }
    : presented.runner !== undefined
      ? { runner: createHash("sha256").update(presented.runner, "utf8").digest("hex") } : {};
  return bound(env, { owner: named, caller: named, origin: null,
                      sandbox: { kind: "egress", token: tokenHash, generation, ...extra } });
}

/**
 * The in-VM sync agent's call (slice S's runner) by its sync token, verified at the door
 * (`routing-mac.ts`, the `sync` kind), with the egress token E2B's transform set beside it
 * (verified as the egress doors verify it): the owner both name, and the SHA-256 of each
 * one's secret, which the object checks are its one live lease's (`#syncer`), so a sync
 * token taken out of the VM reaches nothing. The sync doors alone take it.
 */
export function bindSync(env: DoorsEnv, owner: string, tokenHash: string,
                         egressHash: string): Bound {
  if (!HEX64.test(tokenHash) || !HEX64.test(egressHash)) {
    throw new Error("a sync binding needs its token's hash and its egress token's");
  }
  const named = principal(owner);
  return bound(env, { owner: named, caller: named, origin: null,
                      sandbox: { kind: "sync", token: tokenHash, egress: egressHash } });
}

/**
 * The platform's sandbox line rang `owner` (slice S, the ring queue's consumer,
 * `ring.ts`): that owner's object, to start its runs waiting for a sandbox again. It
 * carries no binding: the ring grants nothing.
 */
export function ringOwner(env: DoorsEnv, owner: string): DurableObjectStub<OwnerDO> {
  return env.OWNER.getByName(principal(owner));
}

/**
 * A verified E2B lifecycle event's owner (`e2b-hooks.ts`, slice P), by the route E2B echoed
 * and the doors opened: that owner's object, whose one door here (`sandboxBilled`) records
 * the sandbox time E2B billed. It carries no binding: the event grants nothing.
 */
export function sandboxHookOwner(env: DoorsEnv, owner: string): DurableObjectStub<OwnerDO> {
  return env.OWNER.getByName(principal(owner));
}

/**
 * What a sandbox's call presents beside its egress token: a run's own secret (its path's,
 * on its model calls and MCP door), or the runner's (`x-ta-runner`, on its event door and
 * sign-in report). The binding carries only each one's SHA-256, which the object checks.
 */
export interface Presented {
  readonly secret?: string;
  readonly runner?: string;
}

/**
 * A run's call on the egress host (its MCP door, its event door): the egress token's
 * binding, as `bindSandbox`'s, with the run its path names and the secret it presented.
 * The object resolves the live lease, the run under it and its grant (by the run's secret)
 * or its runner (by the runner's), and holds every door to that grant.
 */
export function bindRun(env: DoorsEnv, owner: string, tokenHash: string, generation: number,
                        run: string, presented: Presented): Bound {
  if (!RUN.test(run)) {
    throw new Error("a run binding names its run");
  }
  const { binding, stub } = bindSandbox(env, owner, tokenHash, generation, presented);
  return { binding: { ...binding, run }, stub };
}

/**
 * The binding with the platform control record's tier numbers, as the doors read the record
 * for this call (`control.ts`): the run doors' admission reads them; no request names them.
 */
export function withTiers({ binding, stub }: Bound, tiers: unknown): Bound {
  return { binding: { ...binding, tiers }, stub };
}

/**
 * The owner's first-party token, bound with the grant it was issued under (`grantId`, from
 * the provider's own unwrap of that token) and its expiry: a socket ticket's binding, so
 * the object ties the socket to the device's grant (A6, salvaged).
 */
export function bindOwnerGrant(env: DoorsEnv, token: ValidatedAccessToken<unknown>,
                               grantId: string): Bound {
  const owner = appOwner(token);
  if (typeof grantId !== "string" || grantId === "") {
    throw new Error("a first-party token unwraps to its grant");
  }
  return bound(env, { owner, caller: owner, origin: null, grant: grantId,
                      expires_at: seconds(token.expiresAt, "a token's expiry") });
}

/** A sign-out's binding: the owner's, with the grant its presented token unwrapped to. */
export function bindSignOut({ binding, stub }: Bound, grantId: string): Bound {
  if (typeof grantId !== "string" || grantId === "") {
    throw new Error("a first-party token unwraps to its grant");
  }
  return { binding: { owner: binding.owner, caller: binding.caller, origin: null,
                      grant: grantId }, stub };
}

/**
 * The owner app's socket ticket, as the upgrade presents it: a routing-MAC token of the
 * `socket` kind (`routing-mac.ts`), verified here before any object is reached, and not
 * past its expiry; the owner's object it names, bound as that owner, and its secret's hash,
 * which only the object can check (it keeps the one row). Null for anything else. The
 * ticket is the upgrade's credential: no bearer is read for it.
 */
export function bindTicket(env: DoorsEnv, ticket: string): { bound: Bound; hash: string } | null {
  const parsed = verifyToken(env.ROUTING_MAC_KEY, "socket", ticket);
  if (parsed === null || parsed.expires === LEASE_LIVED || parsed.expires <= Date.now() / 1000) {
    return null;
  }
  return { bound: bound(env, { owner: parsed.owner, caller: parsed.owner, origin: null }),
           hash: parsed.hash };
}

/**
 * The owner app's socket upgrade, as the binder forwards it to the bound object's `fetch`
 * (`owner/src/relay.ts`): a fresh request it builds itself, so no header the client sent
 * survives the hop. Its identity is the bound `binding`; its conversation and the ticket's
 * hash are the object's to check against its own rows.
 */
export function socketRequest({ binding }: Bound, conversation: string, hash: string): Request {
  const headers = new Headers({ Upgrade: "websocket" });
  headers.set(BINDING_HEADER, JSON.stringify(binding));
  headers.set(CONVERSATION_HEADER, conversation);
  headers.set(TICKET_HEADER, hash);
  return new Request(SOCKET_URL, { headers });
}

/**
 * The same binding's object again, through a fresh stub: a settle after the object reset
 * (a deploy) reaches its new instance, never the broken stub.
 */
export function rebound(env: DoorsEnv, { binding }: Bound): Bound {
  return bound(env, binding);
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
 * A fresh upstream sign-in at the callback (`authorize.ts`): the owner its issuer and
 * subject name (`ownerOf`), on its own account, at the verified sign-in's time, which
 * opens a session (`sessionOpen`).
 */
export async function bindSignIn(env: DoorsEnv, signIn: SignIn): Promise<Bound> {
  const owner = await ownerOf(env, signIn.issuer, signIn.subject);
  return bound(env, { owner, caller: owner, origin: null,
                      authenticated_at: seconds(signIn.authenticated_at, "a sign-in's time") });
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
