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
 * of fields, checked with everything else that can be checked here before the object is
 * reached: the owner comes from the token alone, and the connection and its version from
 * the object alone.
 *
 * - `connections/key` `{source, key, display_name?}`: the key held to the gateway's own
 *   secret grammar, then `connectionPrepare`, `GATEWAY.seal`, `GATEWAY.verify` (the setup
 *   check, which lists the models) and `connectionCommit`.
 * - `connections/oauth` `{source, display_name?}`: `connectionPrepare`, which begins the
 *   flow in the same unit, bound to this session; answers the authorize URL and sets the
 *   flow's browser binding (`session.ts`). Hosted presets (a key-exchange source,
 *   OpenRouter's) take the same flow, with their sealed endpoints from their record.
 * - `GET /oauth/connect/callback/<source>[/<state>]`: the provider's redirect, a top-level
 *   navigation. **Each source has its own redirect URI**, so a code one provider issued
 *   can never be finished as another's (the mix-up attack, RFC 9700 §4.4): an RFC 6749
 *   source's carries its state in the query, a key-exchange source's (which sends none) as
 *   one more path segment, and each takes only its own form, with the issuer (RFC 9207
 *   `iss`) its record declares. The navigation carries neither the owner's token nor its
 *   session (the session cookie is Strict), so it reaches no object: it checks the flow's
 *   browser binding, as the OAuth provider's `finishUpstream` does, and hands the code, the
 *   state and the redirect URI it arrived on to the owner's app page in the URL fragment,
 *   which no server sees.
 * - `connections/oauth/finish` `{state, code, redirect_uri}`: the app's post of them, in
 *   its session: the browser binding again, then `flowFinish` (the flow used up, in the
 *   session that began it), the redirect URI held to the one the flow was begun with, the
 *   source still connectable, `GATEWAY.exchange`, `GATEWAY.verify` and `connectionCommit`.
 * - `connections/revoke` `{connection}`: `connectionRevoke`, in the object first; the
 *   object's lane asks the provider to revoke (RFC 7009) with the blob it keeps for that,
 *   when its source can be asked (`revocable`, from the record, at prepare).
 * - `connections/list` `{}`: `connectionList`, which never holds a blob.
 *
 * A connect that fails after `connectionPrepare` leaves nothing live: a pending connection
 * nothing was issued for is revoked at once (so it never holds a slot), and a credential the
 * provider issued whose setup check then failed is committed `failed`, so the object
 * revokes it at the provider or tells the owner to delete it there. Nothing minted is
 * dropped silently.
 */
import { secret } from "../../gateway/src/credential";

import type { Bound } from "./binder";
import { navigation } from "./authorize";
import {
  CONNECT_CALLBACK, CONNECT_CALLBACK_PATH, CONNECTED_PAGE, type DoorsEnv, now, PUBLIC_ORIGIN,
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

const STATE = /^[A-Za-z0-9_-]{43}$/;
const CODE = /^[\x21-\x7e]{1,2048}$/;
/** A gateway failure's message: one of its fixed codes (`gateway/src/failure.ts`). */
const GATEWAY_CODE = /^(?:upstream|oauth)_[a-z_]+$/;
/** Any of the gateway's fixed codes, as a failed setup is committed with. */
const ANY_CODE = /^[a-z][a-z_]{0,63}$/;
/** The authorization response's parameters the callback reads, each at most once. */
const RESPONSE_PARAMS: readonly string[] = Object.freeze(["state", "code", "iss", "error"]);
const TOO_EARLY = "the source is not connectable yet";
const MIXED_UP = "the authorization came back on another source's callback";

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

/** The source's own redirect URI: its flows return there, and only its flows. */
export function redirectFor(record: SourceRecord): string {
  return `${CONNECT_CALLBACK}/${record.id}`;
}

/** Whether the source's provider can be asked to revoke what its OAuth flow issues. */
function revocable(oauth: OAuthSpec | undefined): boolean {
  return oauth?.protocol === "rfc6749" && oauth.revocation_endpoint !== undefined;
}

/**
 * A gateway failure as the connect answers it: a failure of the provider or its token
 * endpoint is the gateway's fixed code (`502`), its deadline `504`, a malformed request
 * `400`; anything else is a fault here or in the config, so it is thrown as it came,
 * loudly.
 */
function answered(error: unknown): never {
  const code = error instanceof Error ? error.message : "";
  if (GATEWAY_CODE.test(code)) {
    throw new ConnectRefused(502, code);
  }
  if (code === "deadline") {
    throw new ConnectRefused(504, code);  // the gateway's own status for its deadline
  }
  if (code === "invalid_request") {
    throw new ConnectRefused(400, code);
  }
  throw error;
}

/**
 * The pending connection given up, when nothing was issued for it: revoked at once, so it
 * holds no slot, then the failure answered.
 */
async function abandoned({ bound: { binding, stub } }: ConnectContext, connection: string,
                         error: unknown): Promise<never> {
  await stub.connectionRevoke(binding, { connection });
  return answered(error);
}

/** `connections/key`: a pasted key, sealed and checked by the gateway, its blob committed. */
async function connectKey(context: ConnectContext): Promise<Response> {
  const { env, bound: { binding, stub }, sources } = context;
  const body = fields(context.body, ["source", "key"], ["display_name"]);
  const record = connectable(sources, body["source"], "api_key");
  const key = body["key"];
  try {
    secret(key);  // the gateway's own grammar: what it would refuse is refused here first
  } catch {
    throw new ConnectRefused(400, "the key is 1 to 16384 visible ASCII characters, " +
                                  "with no space");
  }
  const sealing = sealedSource(record, "api_key");
  const name = displayName(body["display_name"], record);
  const { connection, version } = await stub.connectionPrepare(binding, {
    source: record.id, kind: "api_key", display_name: name,
    provider: record.upstream!.provider, revocable: false });
  let blob: string;
  let kid: string;
  let models: unknown[];
  try {
    ({ blob, kid } = await env.GATEWAY.seal(binding.owner, connection, version, key as string,
                                            sealing));
    models = [...(await env.GATEWAY.verify(binding.owner, connection, version, blob)).models];
  } catch (error) {
    // The owner holds the key: nothing was issued here, so the connection is given up.
    return abandoned(context, connection, error);
  }
  return json(200, await stub.connectionCommit(binding, { connection, version, blob, kid,
                                                          models }));
}

/** The provider's authorize URL for this flow: its record's endpoint and parameters, then ours. */
function authorizeUrl(record: SourceRecord, state: string, challenge: string): string {
  const oauth = record.oauth!;
  const url = new URL(oauth.authorization_endpoint);
  const params = new URLSearchParams(oauth.authorize_params);
  if (oauth.protocol === "pkce_user_key_v1") {
    // No `state` in this protocol: the flow's state is the callback's last path segment.
    params.set("callback_url", `${redirectFor(record)}/${state}`);
  } else {
    params.set("response_type", "code");
    params.set("client_id", oauth.client_id);
    params.set("redirect_uri", redirectFor(record));
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

/** `connections/oauth`: a pending connection and its flow, in one door, bound to this browser. */
async function beginOAuth(context: ConnectContext): Promise<Response> {
  const { bound: { binding, stub }, sources } = context;
  const body = fields(context.body, ["source"], ["display_name"]);
  const record = connectable(sources, body["source"], "oauth");
  const prepared = await stub.connectionPrepare(binding, {
    source: record.id, kind: "oauth2", display_name: displayName(body["display_name"], record),
    provider: record.upstream!.provider, revocable: revocable(record.oauth),
    redirect_uri: redirectFor(record) });
  const flow = prepared.flow!;
  // The binding lasts as long as the flow, in whole seconds (the object's are fractional).
  const life = Math.max(0, Math.floor(flow.expires_at - now()));
  return json(200, { connection: prepared.connection,
                     authorize_url: authorizeUrl(record, flow.state, flow.code_challenge),
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
 * The flow's state, in the one form its source's protocol returns it: an RFC 6749
 * source's in the query on `/<source>`, a key-exchange source's as `/<source>/<state>`
 * with no `state` in the query. Null for any other shape.
 */
function returned(sources: Sources, url: URL): { record: SourceRecord; state: string } | null {
  const segments = url.pathname.slice(CONNECT_CALLBACK_PATH.length + 1).split("/");
  const record = sources.get(segments[0] ?? "");
  if (record?.oauth === undefined) {
    return null;
  }
  const query = url.searchParams.get("state");
  const state = record.oauth.protocol === "rfc6749"
    ? (segments.length === 1 ? query : null)
    : (segments.length === 2 && query === null ? segments[1]! : null);
  return state !== null && STATE.test(state) ? { record, state } : null;
}

/**
 * Whether the authorization response names the record's issuer as RFC 9207 asks: when the
 * record declares its issuer supports `iss`, it must be there; when present, it must match.
 */
function issued(record: SourceRecord, url: URL): boolean {
  const oauth = record.oauth;
  const iss = url.searchParams.get("iss");
  if (oauth?.protocol !== "rfc6749" || oauth.issuer === undefined) {
    return true;
  }
  return iss === null ? oauth.authorization_response_iss_parameter_supported !== true
                      : iss === oauth.issuer;
}

/**
 * `GET /oauth/connect/callback/<source>[/<state>]`: the provider's redirect. A top-level
 * navigation only, on a source's own callback in its own form, with the flow's browser
 * binding and the record's issuer, or it is refused; then the code (or the provider's
 * refusal) and the redirect URI it arrived on go to the owner's app page in the fragment.
 * No object is reached.
 */
export async function connectCallback(request: Request, sources: Sources): Promise<Response> {
  if (request.method !== "GET") {
    return page(405, "method not allowed");
  }
  if (!navigation(request)) {
    return page(403, "Open this page in your browser to finish connecting.");
  }
  const url = new URL(request.url);
  // RFC 6749 §3.1: a response parameter is sent once. A repeated one is refused, never
  // read as its first (or last) value, so no copy can stand in for the provider's.
  if (RESPONSE_PARAMS.some((name) => url.searchParams.getAll(name).length > 1)) {
    return page(400, "This is not a connection's callback.");
  }
  const flow = returned(sources, url);
  if (flow === null) {
    return page(400, "This is not a connection's callback.");
  }
  if (!(await connectBound(request, flow.state))) {
    return page(403, "This connection was not started in this browser; start again.");
  }
  if (!issued(flow.record, url)) {
    return page(400, "This authorization did not come from this source's issuer.");
  }
  const code = url.searchParams.get("code");
  const redirect = `${PUBLIC_ORIGIN}${url.pathname}`;
  const handed = code !== null && CODE.test(code) && !url.searchParams.has("error")
    ? { state: flow.state, code, redirect_uri: redirect }
    : { state: flow.state, error: "access_denied", redirect_uri: redirect };
  return new Response(null, { status: 302, headers: {
    Location: `${CONNECTED_PAGE}#${new URLSearchParams(handed)}`, "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer" } });
}

/** `connections/oauth/finish`: the flow used up in its session, its code exchanged and committed. */
async function finishOAuth(context: ConnectContext): Promise<Response> {
  const { env, bound: { binding, stub }, sources, request } = context;
  const body = fields(context.body, ["state", "code", "redirect_uri"]);
  const { state, code, redirect_uri: arrived } = body;
  if (typeof state !== "string" || !STATE.test(state) || typeof code !== "string"
      || !CODE.test(code) || typeof arrived !== "string") {
    throw new ConnectRefused(400, "the state, code and redirect URI are the callback's");
  }
  if (!(await connectBound(request, state))) {
    throw new ConnectRefused(403, "this connection was not started in this browser");
  }
  const flow = await stub.flowFinish(binding, { state });
  const { connection, version } = flow;
  let record: SourceRecord;
  try {
    record = connectable(sources, flow.source, "oauth");
    // The code must have come back where this flow's source sends its own, and nowhere
    // else: a code another provider issued never reaches this source's token endpoint.
    const expected = record.oauth!.protocol === "pkce_user_key_v1"
      ? `${flow.redirect_uri}/${state}` : flow.redirect_uri;
    if (flow.redirect_uri !== redirectFor(record) || arrived !== expected) {
      throw new ConnectRefused(403, MIXED_UP);
    }
  } catch (error) {
    await stub.connectionRevoke(binding, { connection });
    throw error;
  }
  let exchanged: Awaited<ReturnType<DoorsEnv["GATEWAY"]["exchange"]>>;
  try {
    exchanged = await env.GATEWAY.exchange(
      binding.owner, connection, version,
      { code, verifier: flow.verifier, redirect_uri: flow.redirect_uri },
      sealedSource(record, "oauth"));
  } catch (error) {
    return abandoned(context, connection, error);
  }
  const { blob, kid, expires_at } = exchanged;
  let models: unknown[];
  try {
    models = [...(await env.GATEWAY.verify(binding.owner, connection, version, blob)).models];
  } catch (error) {
    // The provider issued a credential: it is committed failed, never dropped, so the
    // object revokes it at the provider or tells the owner to delete it there.
    const message = error instanceof Error ? error.message : "";
    await stub.connectionCommit(binding, {
      connection, version, blob, kid, models: [], expires_at,
      failed: ANY_CODE.test(message) ? message : "internal" });
    return answered(error);
  }
  const view = await stub.connectionCommit(binding, { connection, version, blob, kid, models,
                                                      expires_at });
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
