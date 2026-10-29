/**
 * The connect doors (gateway research §7.2, §7.3; tasks 4 G3): an owner connects a model
 * source by pasting a key or by OAuth, revokes one, and lists them. The only source that
 * reaches `GATEWAY`, and only its `ConnectGateway` entrypoint (`seal`, `exchange`,
 * `verify`).
 *
 * **No plaintext reaches the owner's object.** A pasted key exists only in this request
 * and in the gateway: it goes to `GATEWAY.seal` alone, never to an object call, a log, an
 * error or a receipt, and every message here is fixed text or the gateway's fixed code.
 * An OAuth code goes to `GATEWAY.exchange` alone. The object stores only the sealed blob,
 * which only the gateway can open, and only for this owner, connection and version.
 *
 * Every connect route is on the owner surface (`app.ts`): the owner's first-party token
 * (an outside credential is refused before any object call), a POST of exactly
 * `application/json` within `APP_BODY_MAX`, the maintenance gate, and the session
 * cookie, which the object checks on every door (`#widening`). Each body is a closed set
 * of fields: the owner comes from the token alone, and the connection and its version
 * from the object alone, so a body naming any of them is refused before any object call.
 *
 * - `connections/key` `{source, key, display_name?}`: `connectionPrepare`, then
 *   `GATEWAY.seal` and `GATEWAY.verify` (the setup check, which lists the models), then
 *   `connectionCommit`.
 * - `connections/oauth` `{source, display_name?}`: `connectionPrepare`, then `flowBegin`,
 *   bound to this session; answers the authorize URL and sets the flow's browser binding
 *   (`session.ts`). Hosted presets (a key-exchange source, OpenRouter's) take the same
 *   flow, with their sealed endpoints from their source record.
 * - `GET /oauth/connect/callback`: the provider's redirect, a top-level navigation. It
 *   carries neither the owner's token nor its session (the session cookie is Strict), so
 *   it reaches no object: it checks the flow's browser binding, as the OAuth provider's
 *   `finishUpstream` does, and hands the code and state to the owner's app page in the URL
 *   fragment, which no server sees.
 * - `connections/oauth/finish` `{state, code}`: the app's post of them, in its session:
 *   the browser binding again, then `flowFinish` (the flow used up, in the session that
 *   began it), `GATEWAY.exchange`, `GATEWAY.verify` and `connectionCommit`.
 * - `connections/revoke` `{connection}`: `connectionRevoke`, in the object first; the
 *   object's lane asks the provider to revoke (RFC 7009) with the blob it keeps for that.
 * - `connections/list` `{}`: `connectionList`, which never holds a blob.
 *
 * A step that fails after `connectionPrepare` leaves the pending connection for the
 * object's lane to sweep (`pendingTtlS`).
 */
import type { Bound } from "./binder";
import { navigation } from "./authorize";
import {
  CONNECT_CALLBACK, CONNECT_CALLBACK_PATH, CONNECTED_PAGE, type DoorsEnv, now,
} from "./config";
import { clearedConnectCookie, connectBound, connectCookie } from "./session";
import {
  type ConnectMethod, type OAuthSpec, sealedSource, type SourceRecord, type Sources,
} from "./sources";

/** A connect request refused at the doors, with its status. */
export class ConnectRefused extends Error {
  override readonly name = "ConnectRefused";
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** What a connect route takes beside the owner's binding. */
export interface ConnectContext {
  readonly env: DoorsEnv;
  readonly bound: Bound;
  readonly body: Record<string, unknown>;
  readonly request: Request;
  readonly sources: Sources;
}

/** The gateway's own most: a key is at most 16 KiB of visible ASCII (`credential.ts`). */
const KEY_MAX = 16 * 1024;
const STATE = /^[A-Za-z0-9_-]{43}$/;
const CODE = /^[\x21-\x7e]{1,2048}$/;
/** A gateway failure's message: one of its fixed codes (`gateway/src/failure.ts`). */
const GATEWAY_CODE = /^(?:upstream|oauth)_[a-z_]+$/;
const TOO_EARLY = "the source is not connectable yet";

function json(status: number, body: unknown, headers: HeadersInit = {}): Response {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store", ...headers } });
}

/** Exactly `required`, and any of `optional`: the owner, connection and version are never fields. */
function fields(body: Record<string, unknown>, required: readonly string[],
                optional: readonly string[] = []): Record<string, unknown> {
  const keys = Object.keys(body);
  if (!required.every((key) => keys.includes(key))
      || !keys.every((key) => required.includes(key) || optional.includes(key))) {
    throw new ConnectRefused(400, `the request has exactly ${required.join(", ") || "no fields"}` +
                                  (optional.length > 0 ? ` (and optionally ${optional.join(", ")})`
                                                       : ""));
  }
  return body;
}

/** The record for `source`, served by `method`; refused when it is not, or not yet. */
function connectable(sources: Sources, source: unknown, method: ConnectMethod): SourceRecord {
  const record = typeof source === "string" ? sources.get(source) : undefined;
  if (record === undefined) {
    throw new ConnectRefused(404, "no such model source");
  }
  if (!record.connect.includes(method)) {
    throw new ConnectRefused(400, `this model source does not connect by ${method}`);
  }
  if (record.unwired !== undefined) {
    throw new ConnectRefused(501, `${TOO_EARLY}: ${record.unwired}`);
  }
  return record;
}

function displayName(value: unknown, record: SourceRecord): string {
  if (value === undefined) {
    return record.display_name;
  }
  if (typeof value !== "string") {
    throw new ConnectRefused(400, "display_name is text");
  }
  return value;
}

/**
 * One gateway call: a failure of the provider or its token endpoint is answered with the
 * gateway's fixed code (`502`), a malformed request `400`; anything else is a fault here
 * or in the config, so it is thrown as it came, loudly.
 */
async function gateway<T>(call: () => Promise<T>): Promise<T> {
  try {
    return await call();
  } catch (error) {
    const code = error instanceof Error ? error.message : "";
    if (GATEWAY_CODE.test(code)) {
      throw new ConnectRefused(502, code);
    }
    if (code === "invalid_request") {
      throw new ConnectRefused(400, code);
    }
    throw error;
  }
}

/** The pending connection the object prepares for `record`. */
async function prepare({ bound: { binding, stub } }: ConnectContext, record: SourceRecord,
                       kind: "api_key" | "oauth2", name: string) {
  return stub.connectionPrepare(binding, { source: record.id, kind, display_name: name,
                                           provider: record.upstream!.provider });
}

/** `connections/key`: a pasted key, sealed and checked by the gateway, its blob committed. */
async function connectKey(context: ConnectContext): Promise<Response> {
  const { env, bound: { binding, stub }, sources } = context;
  const body = fields(context.body, ["source", "key"], ["display_name"]);
  const record = connectable(sources, body["source"], "api_key");
  const key = body["key"];
  if (typeof key !== "string" || key.length === 0 || key.length > KEY_MAX) {
    throw new ConnectRefused(400, `the key is 1 to ${KEY_MAX} characters`);
  }
  const sealing = sealedSource(record, "api_key");
  const prepared = await prepare(context, record, "api_key",
                                 displayName(body["display_name"], record));
  const { connection, version } = prepared;
  const { blob, kid } = await gateway(() =>
    env.GATEWAY.seal(binding.owner, connection, version, key, sealing));
  const { models } = await gateway(() =>
    env.GATEWAY.verify(binding.owner, connection, version, blob));
  return json(200, await stub.connectionCommit(binding, {
    connection, version, blob, kid, models: [...models] }));
}

/** The provider's authorize URL for this flow: its record's endpoint and parameters, then ours. */
function authorizeUrl(oauth: OAuthSpec, state: string, challenge: string): string {
  const url = new URL(oauth.authorization_endpoint);
  const params = new URLSearchParams(oauth.authorize_params);
  if (oauth.protocol === "pkce_user_key_v1") {
    // No `state` in this protocol: the flow's state is the callback's last path segment.
    params.set("callback_url", `${CONNECT_CALLBACK}/${state}`);
  } else {
    params.set("response_type", "code");
    params.set("client_id", oauth.client_id);
    params.set("redirect_uri", CONNECT_CALLBACK);
    params.set("state", state);
    if (oauth.scope !== undefined) {
      params.set("scope", oauth.scope);
    }
    if (oauth.resource !== undefined) {
      params.set("resource", oauth.resource);
    }
  }
  params.set("code_challenge", challenge);
  params.set("code_challenge_method", "S256");
  url.search = params.toString();
  return url.href;
}

/** `connections/oauth`: a pending connection and its flow, bound to this session and browser. */
async function beginOAuth(context: ConnectContext): Promise<Response> {
  const { bound: { binding, stub }, sources } = context;
  const body = fields(context.body, ["source"], ["display_name"]);
  const record = connectable(sources, body["source"], "oauth");
  const { connection } = await prepare(context, record, "oauth2",
                                       displayName(body["display_name"], record));
  const flow = await stub.flowBegin(binding, { connection, redirect_uri: CONNECT_CALLBACK });
  // The binding lasts as long as the flow, in whole seconds (the object's are fractional).
  const life = Math.max(0, Math.floor(flow.expires_at - now()));
  return json(200, { connection, authorize_url: authorizeUrl(record.oauth!, flow.state,
                                                             flow.code_challenge),
                     expires_at: flow.expires_at },
              { "Set-Cookie": await connectCookie(flow.state, life) });
}

function page(status: number, message: string): Response {
  return new Response(message, {
    status, headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store",
                       "Referrer-Policy": "no-referrer" },
  });
}

/**
 * `GET /oauth/connect/callback[/<state>]`: the provider's redirect. A top-level navigation
 * only, with the flow's browser binding, or it is refused; then the code (or the
 * provider's refusal) goes to the owner's app page in the fragment. No object is reached.
 */
export async function connectCallback(request: Request): Promise<Response> {
  if (request.method !== "GET") {
    return page(405, "method not allowed");
  }
  if (!navigation(request)) {
    return page(403, "Open this page in your browser to finish connecting.");
  }
  const url = new URL(request.url);
  const rest = url.pathname.slice(CONNECT_CALLBACK_PATH.length);
  const query = url.searchParams.get("state");
  const state = rest === "" ? query : query === null ? rest.slice(1) : null;
  if (state === null || !STATE.test(state)) {
    return page(400, "This is not a connection's callback.");
  }
  if (!(await connectBound(request, state))) {
    return page(403, "This connection was not started in this browser; start again.");
  }
  const code = url.searchParams.get("code");
  const handed = code !== null && CODE.test(code) && !url.searchParams.has("error")
    ? { state, code } : { state, error: "access_denied" };
  return new Response(null, { status: 302, headers: {
    Location: `${CONNECTED_PAGE}#${new URLSearchParams(handed)}`, "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer" } });
}

/** `connections/oauth/finish`: the flow used up in its session, its code exchanged and committed. */
async function finishOAuth(context: ConnectContext): Promise<Response> {
  const { env, bound: { binding, stub }, sources, request } = context;
  const body = fields(context.body, ["state", "code"]);
  const state = body["state"];
  const code = body["code"];
  if (typeof state !== "string" || !STATE.test(state) || typeof code !== "string"
      || !CODE.test(code)) {
    throw new ConnectRefused(400, "the state and code are the callback's");
  }
  if (!(await connectBound(request, state))) {
    throw new ConnectRefused(403, "this connection was not started in this browser");
  }
  const flow = await stub.flowFinish(binding, { state });
  const record = connectable(sources, flow.source, "oauth");
  const { connection, version } = flow;
  const exchanged = await gateway(() => env.GATEWAY.exchange(
    binding.owner, connection, version,
    { code, verifier: flow.verifier, redirect_uri: flow.redirect_uri },
    sealedSource(record, "oauth")));
  const { models } = await gateway(() =>
    env.GATEWAY.verify(binding.owner, connection, version, exchanged.blob));
  const view = await stub.connectionCommit(binding, {
    connection, version, blob: exchanged.blob, kid: exchanged.kid, models: [...models],
    expires_at: exchanged.expires_at });
  return json(200, view, { "Set-Cookie": await clearedConnectCookie(state) });
}

/** `connections/revoke`: in the object first; its lane asks the provider. */
async function revoke({ bound: { binding, stub }, body }: ConnectContext): Promise<Response> {
  const { connection } = fields(body, ["connection"]);
  return json(200, await stub.connectionRevoke(binding, { connection: connection as string }));
}

/** `connections/list`: every connection, never its blob. */
async function list({ bound: { binding, stub }, body }: ConnectContext): Promise<Response> {
  fields(body, []);
  return json(200, await stub.connectionList(binding));
}

/** The connect routes, by their path under the owner surface. */
export const CONNECT_ROUTES: Readonly<Record<string, (context: ConnectContext) =>
    Promise<Response>>> = Object.freeze({
  "connections/key": connectKey,
  "connections/oauth": beginOAuth,
  "connections/oauth/finish": finishOAuth,
  "connections/revoke": revoke,
  "connections/list": list,
});
