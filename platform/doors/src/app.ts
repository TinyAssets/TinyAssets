/**
 * The owner surface, `/app/api/*` (research §8; checklist 7, 8, 13-16): the owner's own
 * app, with a first-party token for the owner and the session cookie for widenings.
 *
 * Order, before any object is reached:
 * 1. The credential is the owner's first-party token: validated for `/app` and issued to
 *    a first-party client. An outside credential (an API key, or an MCP token) is
 *    refused `403`, so an outside origin never reaches an owner-surface door (the
 *    Python's `mailbox_door._owner`). The one pairing of an outside origin with the
 *    session is the registering route below, and its credential is still this token.
 * 2. The maintenance gate.
 * 3. A POST of exactly `application/json`; the session is attached by `session.ts`'s
 *    rules alone.
 *
 * Routes, each a door or a binder sequence:
 * - `keys`, `keys/revoke`, `keys/delete`: `api-keys.ts`.
 * - `grants/revoke`: an MCP client's revoke, object then provider (`revoke.ts`).
 * - `outside-agents`: the owner's browser registering an agent for an API key it holds.
 * - `session/extend`: the session's renewal.
 * - `session/close`: sign-out, the session closed and the presented token's grant revoked.
 * - `connections/key`, `connections/oauth`, `connections/oauth/finish`,
 *   `connections/revoke`, `connections/list`: the model connect doors (`connect.ts`).
 *
 * - `agent/submit`, `agent/run`, `agent/status`, `agent/cancel`: the run doors (slice R),
 *   the same doors `converse`, `run_graph`, `run_status` and `run_cancel` call for an
 *   outside client; the control record's tier numbers ride the binding (`withTiers`).
 * - `export`: the owner's export (`owner/src/export.ts`), streamed as newline-delimited
 *   JSON (`exportStream`), resumable from any page's cursor line.
 * - `agent/socket-ticket`: a single-use socket ticket for one of the owner's conversations
 *   (A6, salvaged), behind every check above, bound to the token's grant and expiry
 *   (`bindOwnerGrant`).
 * - `agent/socket`: the owner app's live socket on one of its conversations: a `GET` with
 *   `Upgrade: websocket`, `?conversation_id=` and `?ticket=`, its one credential the ticket
 *   (a browser's `WebSocket` sends no `Authorization`; no bearer is read here), a
 *   routing-MAC token the doors verify before any object (`bindTicket`), behind the
 *   maintenance gate; the binder forwards it to the object's `fetch` as a fresh request
 *   (`socketRequest`), the object consumes the ticket, and its answer, the socket or its
 *   fixed refusal, is the door's.
 *
 * Sign-out tells the object first, whether or not a session cookie came, so the sockets
 * opened under the device's grant close before the grant is revoked (`socketsEnd`).
 */
import {
  type Bound, bindKeyRegistration, bindOwner, bindOwnerGrant, bindSignOut, bindTicket,
  socketRequest, withTiers,
} from "./binder";
import { jsonObject } from "./body";
import {
  APP_BODY_MAX, APP_RESOURCE, type DoorsEnv, FIRST_PARTY_CLIENTS, MCP_RESOURCE,
} from "./config";
import { ownerGate, ownerRead } from "./control";
import {
  deleteOwnKey, KeyRefused, looksLikeKey, mintKey, registeringEntry, revokeKey,
} from "./api-keys";
import { CONNECT_ROUTES, ConnectRefused } from "./connect";
import { revokeClient } from "./revoke";
import { authorizationServer } from "./oauth";
import { clearedCookie, mediaType, OWNER_SURFACE, sessionCookie, sessionOf } from "./session";
import type { Sources } from "./sources";
import { refusal } from "./tools";
import type { ValidatedAccessToken } from "@cloudflare/workers-oauth-provider";

import { SOCKET_FAILED, TICKET_REFUSED } from "../../owner/src/socket-shape";

export const OUTSIDE_CREDENTIAL = "an outside credential never reaches the owner's surface";

function json(status: number, body: unknown, headers: HeadersInit = {}): Response {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store", ...headers } });
}

function failure(status: number, message: string): Response {
  return json(status, { error: message });
}

export function bearer(request: Request): string | null {
  const header = request.headers.get("Authorization") ?? "";
  const match = /^Bearer ([^\s]+)$/.exec(header);
  return match === null ? null : match[1]!;
}

type Route = (env: DoorsEnv, owner: Bound, request: Record<string, unknown>,
              token: string) => Promise<Response>;

/**
 * Sign-out: the session closed in the object when the browser presented it, and the
 * presented token's own grant revoked in the provider, so neither outlives it. Other
 * devices' grants stand. Like every owner-surface route it sits behind the maintenance
 * gate: it needs the owner's object, and a `503` there is the honest answer. A token that
 * expired or was revoked since it was validated is `401`, as it would have been a moment
 * earlier.
 */
async function signOut(env: DoorsEnv, { binding, stub }: Bound, _request: unknown,
                       token: string):
    Promise<Response> {
  const oauth = authorizationServer(env).getOAuthApi(env);
  const presented = await oauth.unwrapToken(token);
  if (presented === null) {
    return failure(401, "sign in required");
  }
  if (presented.userId !== binding.owner) {
    throw new Error("a validated first-party token unwraps to its owner's grant");
  }
  // The device's sockets first, cookie or not: none outlives its grant's revoke.
  const ending = bindSignOut({ binding, stub }, presented.grantId);
  await ending.stub.socketsEnd(ending.binding);
  const closed = binding.session === undefined ? { closed: 0 } : await stub.sessionClose(binding);
  await oauth.revokeGrant(presented.grantId, binding.owner);
  return json(200, { ...closed, signed_out: true }, { "Set-Cookie": clearedCookie() });
}

/**
 * The pages one export response carries before it ends with its cursor line, so one
 * request's work in this Worker stays bounded (a technical ceiling: a request's CPU; at
 * most 8 MiB of chunks a page, about 512 MiB a response). The client asks again with the
 * last cursor line it read.
 */
export const EXPORT_PAGES = 64;

/**
 * The owner's export, as newline-delimited JSON (`application/x-ndjson`): the object's
 * pages one after another, each but the last followed by its cursor line
 * (`{"t":"cursor","cursor":...}`), until the stream's `end` line or `EXPORT_PAGES`. The
 * first page is read before the response begins, so a refusal is the door's status; a
 * page that fails after it cuts the stream short of its `end` line, and the client resumes
 * from the last cursor it read.
 */
async function exportStream(_env: DoorsEnv, { binding, stub }: Bound,
                            request: Record<string, unknown>): Promise<Response> {
  if (Object.keys(request).some((key) => key !== "cursor")) {
    return failure(400, "an export request is at most its cursor");
  }
  let page = await stub.ownerExport(binding, { cursor: request["cursor"] as string | null });
  let pages = 0;
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    async pull(controller) {
      pages++;
      const lines = [...page.lines];
      const next = page.next;
      if (next !== null) {
        lines.push(JSON.stringify({ t: "cursor", cursor: next }));
      }
      controller.enqueue(encoder.encode(lines.join("\n") + "\n"));
      if (next === null || pages >= EXPORT_PAGES) {
        controller.close();
        return;
      }
      page = await stub.ownerExport(binding, { cursor: next });
    },
  });
  return new Response(body, { status: 200, headers: {
    "Content-Type": "application/x-ndjson", "Cache-Control": "no-store" } });
}

const ROUTES: Readonly<Record<string, Route>> = Object.freeze({
  "export": exportStream,
  "keys": async (env, owner, request) => json(200, await mintKey(env, owner, request)),
  "keys/revoke": async (env, owner, request) => json(200, await revokeKey(env, owner, request)),
  "keys/delete": async (env, owner, request) =>
    json(200, await deleteOwnKey(env, owner, request)),
  "grants/revoke": async (env, owner, request) =>
    json(200, await revokeClient(env, owner, request["client"])),
  "session/extend": async (_env, { binding, stub }) => {
    const extended = await stub.sessionExtend(binding);
    return json(200, extended, { "Set-Cookie": sessionCookie(binding.session ?? "") });
  },
  "session/close": signOut,
  "agent/submit": async (_env, { binding, stub }, request) =>
    json(200, await stub.agentSubmit(binding, request as never)),
  "agent/run": async (_env, { binding, stub }, request) =>
    json(200, await stub.agentRun(binding, request as never)),
  "agent/status": async (_env, { binding, stub }, request) =>
    json(200, await stub.agentStatus(binding, request as never)),
  "agent/cancel": async (_env, { binding, stub }, request) =>
    json(200, await stub.agentCancel(binding, request as never)),
});

/** The owner app's socket door's path, and its ticket door's, under the owner surface. */
export const SOCKET_PATH = "agent/socket";
export const TICKET_PATH = "agent/socket-ticket";

/**
 * A socket ticket: the owner's token's grant (the provider's own unwrap of the presented
 * token) and expiry bound in, then the object mints it for the conversation named.
 */
async function socketTicket(env: DoorsEnv, token: ValidatedAccessToken<unknown>,
                            presented: string, request: Record<string, unknown>):
    Promise<Response> {
  const unwrapped = await authorizationServer(env).getOAuthApi(env).unwrapToken(presented);
  if (unwrapped === null) {
    return failure(401, "sign in required");
  }
  const { binding, stub } = bindOwnerGrant(env, token, unwrapped.grantId);
  return json(200, await stub.socketTicket(binding, request as never));
}

/**
 * The owner app's live socket: a `GET` upgrade with exactly `?conversation_id=` and
 * `?ticket=`, its one credential the ticket, forwarded by the binder to the object's `fetch`
 * (`socketRequest`), whose answer is returned as it is. A failure past the doors answers a
 * fixed code, never its text.
 */
async function socketUpgrade(request: Request, env: DoorsEnv): Promise<Response> {
  if (request.method !== "GET") {
    return failure(405, "method not allowed");
  }
  if ((request.headers.get("Upgrade") ?? "").toLowerCase() !== "websocket") {
    return failure(426, "the socket door takes a WebSocket upgrade");
  }
  const query = new URL(request.url).searchParams;
  const conversation = query.get("conversation_id");
  const ticket = query.get("ticket");
  if (conversation === null || ticket === null || [...query.keys()].length !== 2) {
    return failure(400, "the socket door takes a conversation_id and a ticket");
  }
  const held = bindTicket(env, ticket);
  if (held === null) {
    return failure(403, TICKET_REFUSED);
  }
  const gated = await ownerGate(env, held.bound.binding.owner);
  if (gated !== null) {
    return gated;
  }
  try {
    return await held.bound.stub.fetch(socketRequest(held.bound, conversation, held.hash));
  } catch {
    console.error("an app socket's upgrade failed past the doors");  // no ids, no text
    return failure(500, SOCKET_FAILED);
  }
}

/** The owner's first-party token, or the refusal for what came instead. */
async function ownerToken(env: DoorsEnv, token: string | null) {
  if (token === null) {
    return failure(401, "sign in required");
  }
  if (looksLikeKey(token)) {
    return failure(403, OUTSIDE_CREDENTIAL);
  }
  const issuer = authorizationServer(env);
  const app = await issuer.validateToken(APP_RESOURCE, token, env);
  if (app === null) {
    const outside = await issuer.validateToken(MCP_RESOURCE, token, env);
    return outside === null ? failure(401, "sign in required")
                            : failure(403, OUTSIDE_CREDENTIAL);
  }
  if (!FIRST_PARTY_CLIENTS.includes(app.clientId)) {
    return failure(403, OUTSIDE_CREDENTIAL);
  }
  return app;
}

export async function serveApp(request: Request, env: DoorsEnv, sources: Sources):
    Promise<Response> {
  if (new URL(request.url).pathname.slice(OWNER_SURFACE.length) === SOCKET_PATH) {
    return socketUpgrade(request, env);  // its credential is its ticket, never a bearer
  }
  const presented = bearer(request);
  const token = await ownerToken(env, presented);
  if (token instanceof Response) {
    return token;
  }
  const session = sessionOf(request);
  const path = new URL(request.url).pathname.slice(OWNER_SURFACE.length);
  const registering = path === "outside-agents";
  const ticketing = path === TICKET_PATH;
  const route = Object.hasOwn(ROUTES, path) ? ROUTES[path] : undefined;
  const connect = Object.hasOwn(CONNECT_ROUTES, path) ? CONNECT_ROUTES[path] : undefined;
  if (route === undefined && connect === undefined && !registering && !ticketing) {
    return failure(404, "not found");
  }
  if (request.method !== "POST") {
    return failure(405, "method not allowed");
  }
  if (mediaType(request) !== "application/json") {
    return failure(415, "the owner surface takes application/json");
  }
  const parsed = await jsonObject(request, APP_BODY_MAX);
  if (parsed instanceof Response) {
    return parsed;
  }
  const unread = bindOwner(env, token, session);
  const gated = await ownerRead(env, unread.binding.owner);
  if (gated instanceof Response) {
    return gated;
  }
  const owner = withTiers(unread, gated.tiers);
  try {
    if (registering) {
      const { key_id: id, ...register } = parsed;
      const { keyId, entry } = await registeringEntry(env, owner.binding.owner, id);
      const { binding, stub } = bindKeyRegistration(env, token, keyId, entry, session);
      return json(200, await stub.registerAgent(binding, register as never));
    }
    if (ticketing) {
      return await socketTicket(env, token, presented!, parsed);
    }
    if (connect !== undefined) {
      return await connect({ env, bound: owner, body: parsed, request, sources });
    }
    return await route!(env, owner, parsed, presented!);
  } catch (error) {
    if (error instanceof KeyRefused || error instanceof ConnectRefused) {
      return failure(error.status, error.message);
    }
    const refused = refusal(error);
    if (refused !== null) {
      return failure(refused.status, refused.message);
    }
    throw error;
  }
}
