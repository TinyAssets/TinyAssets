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
 * - `grants/revoke`: an MCP client's revoke, object then provider (`grants.ts`).
 * - `outside-agents`: the owner's browser registering an agent for an API key it holds.
 * - `session/extend`, `session/close`: the session's renewal and sign-out.
 */
import {
  type Bound, bindKeyRegistration, bindOwner,
} from "./binder";
import {
  APP_RESOURCE, type DoorsEnv, FIRST_PARTY_CLIENTS, MCP_RESOURCE,
} from "./config";
import { ownerGate } from "./control";
import {
  deleteOwnKey, KeyRefused, looksLikeKey, mintKey, registeringEntry, revokeKey,
} from "./api-keys";
import { revokeClient } from "./revoke";
import { authorizationServer } from "./oauth";
import { clearedCookie, mediaType, OWNER_SURFACE, sessionCookie, sessionOf } from "./session";
import { refusal } from "./tools";

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

async function body(request: Request): Promise<Record<string, unknown> | null> {
  try {
    const value: unknown = await request.json();
    return typeof value === "object" && value !== null && !Array.isArray(value)
      ? value as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

type Route = (env: DoorsEnv, owner: Bound, request: Record<string, unknown>,
              raw: Request) => Promise<Response>;

const ROUTES: Readonly<Record<string, Route>> = Object.freeze({
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
  "session/close": async (_env, { binding, stub }) =>
    json(200, await stub.sessionClose(binding), { "Set-Cookie": clearedCookie() }),
});

/** The owner's first-party token, or the refusal for what came instead. */
async function ownerToken(request: Request, env: DoorsEnv) {
  const token = bearer(request);
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

export async function serveApp(request: Request, env: DoorsEnv): Promise<Response> {
  const token = await ownerToken(request, env);
  if (token instanceof Response) {
    return token;
  }
  const session = sessionOf(request);
  const path = new URL(request.url).pathname.slice(OWNER_SURFACE.length);
  const registering = path === "outside-agents";
  const route = ROUTES[path];
  if (route === undefined && !registering) {
    return failure(404, "not found");
  }
  if (request.method !== "POST") {
    return failure(405, "method not allowed");
  }
  if (mediaType(request) !== "application/json") {
    return failure(415, "the owner surface takes application/json");
  }
  const parsed = await body(request);
  if (parsed === null) {
    return failure(400, "the request is one JSON object");
  }
  const owner = bindOwner(env, token, session);
  const gated = await ownerGate(env, owner.binding.owner);
  if (gated !== null) {
    return gated;
  }
  try {
    if (registering) {
      const { key_id: id, ...register } = parsed;
      const { keyId, entry } = await registeringEntry(env, owner.binding.owner, id);
      const { binding, stub } = bindKeyRegistration(env, token, keyId, entry, session);
      return json(200, await stub.registerAgent(binding, register as never));
    }
    return await route!(env, owner, parsed, request);
  } catch (error) {
    if (error instanceof KeyRefused) {
      return failure(error.status, error.message);
    }
    const refused = refusal(error);
    if (refused !== null) {
      return failure(refused.status, refused.message);
    }
    throw error;
  }
}
