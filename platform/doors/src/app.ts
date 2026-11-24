/**
 * The owner surface, `/app/api/*` (research §8; checklist 7, 8, 13-16): the owner's own
 * app, with a first-party token for the owner and the session cookie for widenings. Its
 * routes are data (`routes`): each a door of the owner's table, or a sequence here.
 *
 * Order, before any object is reached:
 * 1. The credential is the owner's first-party token: validated for `/app` and issued to
 *    a first-party client. An outside credential (an API key, or an MCP token) is
 *    refused `403`, so an outside origin never reaches an owner-surface door. The socket's
 *    upgrade alone takes another: its ticket.
 * 2. A POST of exactly `application/json`, within `EGRESS_BODY_MAX` (an upload's bytes are a
 *    `PUT` of their own, `file-bytes.ts`).
 * 3. The maintenance gate; the session is attached by `session.ts`'s rules alone.
 *
 * **Which routes need the session** is the object's to say: every change to the owner's
 * agent definitions (`OWNER_TREES`: `files/write`, and `file_mkdir`, `file_remove`,
 * `file_rename` and `file_restore` through `tools/<name>`), which the object takes only in
 * the owner's live session on every route alike, since an agent's files are its instructions
 * and authority. The rest (collections, other files, digests, runs, `code`, whose script
 * calls are held the same way) change no agent's authority, so the owner's token suffices,
 * as it does for a full-mode API key.
 */
import { keyed } from "../../contract/src/checks";
import { EGRESS_BODY_MAX } from "../../contract/src/limits";
import { utf8Decoder } from "../../contract/src/text";
import { answerJson } from "./answers";
import {
  type Bound, bindOwner, bindOwnerGrant, bindSignOut, bindTicket, socketRequest, withAccount,
} from "./binder";
import { FIRST_PARTY_CLIENTS } from "./config";
import type { DoorsEnv } from "./env";
import { APP_RESOURCE, MCP_RESOURCE } from "../../contract/src/identity";
import { type ControlRead, gating, requireFresh } from "../../contract/src/control";
import type { DoorAnswer, JsonBody } from "../../contract/src/owner";
import type { ToolDoors } from "../../contract/src/requests";
import { ownerRead } from "./control";
import { deleteOwnKey, looksLikeKey, mintKey, revokeKey } from "./api-keys";
import { CONNECT_ROUTES, DOORBELL_VERSION, sealDoorbell } from "./connect";
import { eraseOwner } from "./erase";
import { streamUpload } from "./file-bytes";
import { HOOK_ROUTES } from "./inbound";
import { revokeClient } from "./revoke";
import { authorizationServer } from "./oauth";
import { answer, bearer, failure, Headed, jsonObject, mediaType, NO_STORE } from "./kit";
import { clearedCookie, OWNER_SURFACE, sessionCookie, sessionOf } from "./session";
import type { Sources } from "../../sources/src/schema";
import { signinsOn } from "../../sources/src/signin";
import { answered, asJson } from "./refused";
import { toolTable } from "../../contract/src/doors";
import { callDoor, type DoorName } from "./tools";
import type { ValidatedAccessToken } from "@cloudflare/workers-oauth-provider";

import { CAPABILITY_WORDS, RUN_CAPABILITIES } from "../../contract/src/capabilities";
import { SOCKET_FAILED, TICKET_REFUSED } from "../../contract/src/socket-shape";
import { callbackSecret, callbackUrl, kindNamed, wakeKinds } from "../../contract/src/wake-records";
import { follows, subscriptionId } from "../../contract/src/wake-kinds";

export const OUTSIDE_CREDENTIAL = "an outside credential never reaches the owner's surface";

/**
 * Every owner-surface answer but the socket's upgrade carries these: JSON is never sniffed
 * into anything else, and no answer runs or frames anything if opened as a page.
 */
const SURFACE_HEADERS: Readonly<Record<string, string>> = Object.freeze({
  "X-Content-Type-Options": "nosniff",
  "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
});

function surfaced(answer: Response): Response {
  if (answer.status === 101) {
    return answer;
  }
  const headers = new Headers(answer.headers);
  for (const [name, value] of Object.entries(SURFACE_HEADERS)) {
    headers.set(name, value);
  }
  return new Response(answer.body, { status: answer.status, statusText: answer.statusText,
                                     headers });
}

/**
 * What a route serves a call with: the Worker's env, the owner bound with the control
 * record's tiers, the call's JSON body, its presented token (raw and validated), the request,
 * the source records and the control record as the gate read it, and a prefix route's rest of
 * the path.
 */
interface Served {
  readonly env: DoorsEnv;
  readonly owner: Bound;
  readonly body: JsonBody;
  readonly presented: string;
  readonly token: ValidatedAccessToken<unknown>;
  readonly request: Request;
  readonly sources: Sources;
  readonly gate: ControlRead;
  readonly rest: string;
}

/**
 * A route's sequence: the value it answers, as JSON with `200` (`Headed` with the headers it
 * is answered with), or a response of its own (a failure, a stream).
 */
type Route = (served: Served) => Promise<unknown>;

/**
 * An owner-surface route, as data: a door of the owner's table (`contract/doors.json`),
 * called with the body as its request (none for a door that takes none); or a binder
 * sequence (`serve`); either taking only an empty object (`body: "empty"`). Two come before
 * the JSON body: `socket`, before the token too (its credential is its ticket), and `bytes`,
 * an upload's bytes. What each answers is its door's answer or its sequence's value
 * (`AppAnswer`), which the owner app's calls are typed by (`app/api.js` `call`).
 */
type OwnerRoute =
  | { readonly door: DoorName; readonly body?: "empty" }
  | { readonly serve: Route; readonly body?: "empty" }
  | { readonly stage: "socket" | "bytes" };

/**
 * Sign-out: the session closed in the object when the browser presented it, and the
 * presented token's own grant revoked in the provider, so neither outlives it. Other
 * devices' grants stand. Like every owner-surface route it sits behind the maintenance
 * gate: it needs the owner's object, and a `503` there is the honest answer. A token that
 * expired or was revoked since it was validated is `401`, as it would have been a moment
 * earlier.
 */
async function signOut({ env, owner: { binding, stub }, presented: token }: Served) {
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
  return new Headed({ ...closed, signed_out: true }, { "Set-Cookie": clearedCookie() });
}

const NO_FIELDS = "this route takes an empty object";

/**
 * `account`: what the owner's tier gives it, honestly: its tier (the platform's, on its own
 * admission row), every tier's numbers as the control record holds them (simultaneous runs
 * and storage, nothing else), and the files it holds now.
 */
async function account({ owner: { binding, stub }, gate }: Served) {
  const { tier } = await stub.tierRead(binding);
  return { tier, tiers: gating(gate)?.tiers ?? null, usage: await stub.fileUsage(binding) };
}

/** `grants/set`: a client's mode, scopes, profile or agent; never a revoke (`grants/revoke`). */
async function grantSet({ owner: { binding, stub }, body: request }: Served) {
  if (request["mode"] === "revoked") {
    return failure(400, "a revoke goes by grants/revoke, which also ends the client's tokens");
  }
  return stub.grantSet(binding, request);
}

/** `files/read` `{path}`: one file as UTF-8 text, or why it is not shown as text. */
async function fileRead({ owner: { binding, stub }, body: request }: Served) {
  if (!keyed(request, ["path"])) {
    return failure(400, "a read names the file's path");
  }
  const { entry, inline } = await stub.fileRead(binding, { path: request["path"] as string });
  if (inline === undefined) {
    return { entry, text: null, why: "larger than one chunk" };
  }
  try {
    return { entry, text: utf8Decoder("strip").decode(inline) };
  } catch {
    return { entry, text: null, why: "not UTF-8 text" };
  }
}

/** `files/write` `{path, expected_etag, text}`: one file's whole text, against its etag. */
async function fileWrite({ owner: { binding, stub }, body: request }: Served) {
  const text = request["text"];
  const fields = ["path", "expected_etag", "text"];
  if (!keyed(request, fields) || typeof text !== "string") {
    return failure(400, "a write names the path, the expected etag and the text");
  }
  // The etag goes to the object as it came: the object's compare-and-swap decides.
  const { path, expected_etag: expected } = request;
  return stub.fileWrite(binding, {
    path: path as string, expected_etag: expected as string | null,
    content: new TextEncoder().encode(text) });
}

/**
 * `sources`: what the app shows of each record, so its connect screen is the records' data;
 * nothing sealed, no endpoint, no argv. A sign-in shows as offered only on a record read fresh,
 * as the owner's object starts one (`FRESH_ONLY`; none on a missing record, `signinsOn`).
 */
function sourceList(sources: Sources, gate: ControlRead) {
  const switchedOn = signinsOn(sources, gate.kind === "read" ? gate.control : null);
  return {
    sources: sources.ids().map((id) => {
      const record = sources.get(id)!;
      return {
        id, display_name: record.display_name, help_url: record.help_url ?? null,
        suggested: record.suggested, connect: record.connect,
        engines: record.engines ?? [], credentials: record.cli?.credential ?? [],
        signin: record.cli?.login === undefined ? null : {
          // Whether the platform offers it (the control record's switch, or the verdict), and
          // the verdict on the vendor's terms for hosted use, cited.
          enabled: switchedOn.includes(id),
          hosted_use: record.cli.login.hosted_use },
      };
    }),
  };
}

/** `tools/<name>`: one tool of the table, called by the owner as itself. */
const ownerTool: Route = async ({ owner: { binding, stub }, body, rest: name, request }) => {
  const tool = toolTable().find((row) => row.name === name);
  if (tool === undefined) {
    return failure(404, "no such tool");
  }
  return answerJson(tool.name, await callDoor(stub, binding, tool.door, body, request.signal));
};

/**
 * The pages one export response carries before it ends with its cursor line, so one
 * request's work in this Worker stays bounded (a technical ceiling: a request's CPU; about
 * 4 MiB an object, 256 MiB a response). The client asks again with the last cursor line it
 * read, which names its export_id.
 */
const EXPORT_PAGES = 64;

/**
 * The owner's export, as newline-delimited JSON (`application/x-ndjson`): its generation's
 * objects one after another, each but the last followed by its cursor line
 * (`{"t":"cursor","cursor":...,"export_id":...}`), until the stream's `end` line,
 * `EXPORT_PAGES`, or an object not yet written (the client asks again later). The
 * first page is read before the response begins, so a refusal is the door's status; a
 * page that fails after it cuts the stream short of its `end` line, and the client resumes
 * from the last cursor it read.
 */
async function exportStream({ owner: { binding, stub }, body: request }: Served):
    Promise<Response> {
  if (!keyed(request, [], ["cursor", "export_id"])) {
    return failure(400, "an export request is at most its cursor and its export_id");
  }
  let page = await stub.ownerExport(binding, request);
  const exportId = page.export_id;
  let pages = 0;
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    async pull(controller) {
      pages++;
      const lines = [...page.lines];
      const next = page.next;
      if (next !== null) {
        lines.push(JSON.stringify({ t: "cursor", cursor: next, export_id: exportId }));
      }
      controller.enqueue(encoder.encode(lines.join("\n") + "\n"));
      if (next === null || pages >= EXPORT_PAGES || page.pending) {
        controller.close();
        return;
      }
      page = await stub.ownerExport(binding, { cursor: next, export_id: exportId });
    },
  });
  return new Response(body, { status: 200, headers: {
    "Content-Type": "application/x-ndjson", ...NO_STORE } });
}

/**
 * `agent/doorbell`: the owner's pasted wake token for one of its agents, for a kind whose
 * record has the owner hand it over. Checked against the record here and again in the
 * gateway, which seals it with the URL; the object stores the blob under the id derived
 * from the owner, URL, kind and what it follows. Never echoed.
 */
async function doorbell({ env, owner: { binding, stub }, body: request }: Served) {
  const kind = typeof request["kind"] === "string" ? kindNamed(request["kind"]) : null;
  if (!keyed(request, ["agent_id", "kind", "url", "secret"], ["board", "rooms"])
      || typeof request["agent_id"] !== "string" || kind === null || kind.push === null
      || kind.push.callback !== "owner") {
    return failure(400, "a doorbell names an agent, its kind, the URL and the token");
  }
  const follow = follows({ board: request["board"] ?? false, rooms: request["rooms"] ?? [] });
  const url = callbackUrl(kind.push, request["url"]);
  const secret = callbackSecret(kind.push, request["secret"]);
  const args = { agent_id: request["agent_id"], board: follow.board, rooms: follow.rooms };
  const id = subscriptionId("owner", url, kind.id, args);
  const sealed = await sealDoorbell(env, binding.owner, id, { record: kind.id, url, secret });
  return stub.agentDoorbell(binding, {
    agent_id: request["agent_id"], kind: kind.id, subscription_id: id, board: follow.board,
    rooms: follow.rooms, blob: sealed.blob, kid: sealed.kid, version: DOORBELL_VERSION });
}

/** `agent/doorbell/kinds`: the wake kinds the owner pastes a URL and token for. */
function doorbellKinds() {
  return { kinds: wakeKinds().filter((kind) => kind.push?.callback === "owner")
    .map(({ id, vendor, agents }) => ({ id, vendor, agents })) };
}

/**
 * A socket ticket: the owner's token's grant (the provider's own unwrap of the presented
 * token) and expiry bound in, then the object mints it for the conversation named.
 */
async function socketTicket({ env, token, presented, body: request }: Served) {
  const unwrapped = await authorizationServer(env).getOAuthApi(env).unwrapToken(presented);
  if (unwrapped === null) {
    return failure(401, "sign in required");
  }
  const { binding, stub } = bindOwnerGrant(env, token, unwrapped.grantId);
  return stub.socketTicket(binding, request);
}

/**
 * The owner app's live socket: a `GET` upgrade with exactly `?conversation_id=` and
 * `?ticket=`, its one credential the ticket (a browser's `WebSocket` sends no
 * `Authorization`, so no bearer is read here), forwarded by the binder to the object's
 * `fetch` (`socketRequest`), whose answer is returned as it is. A failure past the doors
 * answers a fixed code, never its text.
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
  await ownerRead(env, held.bound.binding.owner);
  try {
    return await held.bound.stub.fetch(socketRequest(held.bound, conversation, held.hash));
  } catch {
    console.error("an app socket's upgrade failed past the doors");  // no ids, no text
    return failure(500, SOCKET_FAILED);
  }
}

/**
 * The owner surface's routes, as data (`OwnerRoute`), by path; a path ending in `/` is a
 * prefix, its rest the route's own (`tools/<name>`, an upload's `files/upload/<intent_id>`).
 */
function routes() {
  return {
    "agent/socket": { stage: "socket" },
    "files/upload/": { stage: "bytes" },
    "tools/": { serve: ownerTool },
    "agent/socket-ticket": { serve: socketTicket },
    "sources": { body: "empty", serve: async ({ sources, gate }) => sourceList(sources, gate) },
    "export": { serve: exportStream },
    "export/chunks": { door: "exportChunks" },
    "export/end": { door: "exportEnd" },
    "storage": { door: "storageUsage" },
    "agent/doorbell": { serve: doorbell },
    "agent/doorbell/end": { door: "agentDoorbellEnd" },
    "agent/wake-limits": { door: "policyWakeLimits" },
    "agent/doorbell/kinds": { body: "empty", serve: async () => doorbellKinds() },
    "account": { body: "empty", serve: account },
    "notices": { door: "noticeList", body: "empty" },
    "policy": { door: "policyRead", body: "empty" },
    "policy/write": { door: "policyWrite" },
    "policy/proposals": { door: "policyProposals", body: "empty" },
    "policy/apply": { door: "policyApply" },
    "policy/decline": { door: "policyDecline" },
    "actions": { door: "actionRequests", body: "empty" },
    "actions/approve": { door: "actionApprove" },
    "actions/refuse": { door: "actionRefuse" },
    "registrations": { door: "registerProposals", body: "empty" },
    "registrations/apply": { door: "registerAgent" },
    "registrations/decline": { door: "registerDecline" },
    "grants": { door: "grantList", body: "empty" },
    "grants/set": { serve: grantSet },
    "capabilities": { body: "empty", serve: async () => ({
      capabilities: RUN_CAPABILITIES.map((id) => ({ id, words: CAPABILITY_WORDS[id] })) }) },
    "tools": { body: "empty", serve: async () => ({
      tools: toolTable().map(({ name, title }) => ({ name, title })) }) },
    "files/read": { serve: fileRead },
    "files/write": { serve: fileWrite },
    "keys": { serve: async ({ env, owner, body, gate }) => {
      requireFresh(gate, "key");
      return mintKey(env, owner, body);
    } },
    "keys/list": { door: "keyList" },
    "account/erase": { serve: async ({ owner, body }) =>
      new Headed(await eraseOwner(owner, body), { "Set-Cookie": clearedCookie() }) },
    "files/upload/begin": { door: "fileUploadBegin" },
    "files/upload/commit": { door: "fileUploadCommit" },
    "files/upload/abort": { door: "fileUploadAbort" },
    ...Object.fromEntries(Object.entries(HOOK_ROUTES).map(([path, hook]) =>
      [path, { serve: ({ env, owner, body }: Served) => hook(env, owner, body) }])),
    "keys/revoke": { serve: ({ env, owner, body }) => revokeKey(env, owner, body) },
    "keys/delete": { serve: ({ env, owner, body }) => deleteOwnKey(env, owner, body) },
    "grants/revoke": { serve: ({ env, owner, body }) => revokeClient(env, owner, body["client"]) },
    "session/extend": { serve: async ({ owner: { binding, stub } }) => {
      const extended = await stub.sessionExtend(binding);
      return new Headed(extended, { "Set-Cookie": sessionCookie(binding.session ?? "") });
    } },
    "session/close": { serve: signOut },
    "agent/submit": { door: "agentSubmit" },
    "agent/run": { door: "agentRun" },
    "agent/status": { door: "agentStatus" },
    "agent/cancel": { door: "agentCancel" },
    "runs": { door: "runList" },
    "agent/model": { door: "agentModel" },
    "signin/start": { door: "signinStart" },
    "signin/code": { door: "signinCode" },
    "signin/cancel": { door: "signinCancel" },
    "signin/list": { door: "signinList", body: "empty" },
    ...Object.fromEntries(Object.entries(CONNECT_ROUTES).map(([path, connect]) =>
      [path, { serve: ({ env, owner, body, request, sources }: Served) =>
        connect({ env, bound: owner, body, request, sources }) }])),
  } satisfies Readonly<Record<string, OwnerRoute>>;
}

/** The value a route's sequence answers as JSON (not a response of its own). */
type Value<A> = A extends Response ? never : A extends Headed<infer V> ? V : A;
/** What a route answers as JSON: its door's answer, or its sequence's value. */
type Valued<R> = R extends { readonly door: infer D extends string } ? DoorAnswer<D>
  : R extends { readonly serve: (served: Served) => Promise<infer A> } ? Value<A> : never;
/** The owner surface's paths: each route's, a tool's (`tools/<name>`) and a connect route's. */
export type AppPath = Exclude<keyof ReturnType<typeof routes>, `${string}/`>
  | `tools/${keyof ToolDoors}` | keyof typeof CONNECT_ROUTES;
/**
 * What the owner surface answers at `path`: its door's answer, a tool's door's, or its
 * sequence's value, which the owner app's calls read (`app/api.js` `call`); nothing at a path
 * no call names (every path, as a script's untyped string is).
 */
export type AppAnswer<P extends AppPath> = [AppPath] extends [P] ? never
  : P extends `tools/${infer T extends keyof ToolDoors}` ? DoorAnswer<ToolDoors[T]>
    : P extends keyof typeof CONNECT_ROUTES ? Value<Awaited<ReturnType<(typeof CONNECT_ROUTES)[P]>>>
      : P extends keyof ReturnType<typeof routes> ? Valued<ReturnType<typeof routes>[P]> : never;

/** The route a path names, exactly or by its prefix, and the path's rest. */
function routeOf(path: string): { route: OwnerRoute; rest: string } | null {
  const table: Readonly<Record<string, OwnerRoute>> = routes();
  if (Object.hasOwn(table, path)) {
    return { route: table[path]!, rest: "" };
  }
  const prefix = Object.keys(table).find((each) => each.endsWith("/") && path.startsWith(each));
  return prefix === undefined ? null : { route: table[prefix]!, rest: path.slice(prefix.length) };
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
  return surfaced(await answered(asJson, () => surface(request, env, sources)));
}

async function surface(request: Request, env: DoorsEnv, sources: Sources):
    Promise<Response> {
  const path = new URL(request.url).pathname.slice(OWNER_SURFACE.length);
  const found = routeOf(path);
  const route = found?.route;
  if (route !== undefined && "stage" in route && route.stage === "socket") {
    return socketUpgrade(request, env);  // its credential is its ticket, never a bearer
  }
  const presented = bearer(request);
  const token = await ownerToken(env, presented);
  if (token instanceof Response) {
    return token;
  }
  const session = sessionOf(request);
  if (route !== undefined && "stage" in route) {
    // An upload's bytes: the owner's token, the maintenance gate, then the stream.
    const owner = bindOwner(env, token, undefined);
    await ownerRead(env, owner.binding.owner);
    return streamUpload(request, env, owner, found!.rest);
  }
  if (route === undefined) {
    return failure(404, "not found");
  }
  if (request.method !== "POST") {
    return failure(405, "method not allowed");
  }
  if (mediaType(request) !== "application/json") {
    return failure(415, "the owner surface takes application/json");
  }
  const parsed = await jsonObject(request, EGRESS_BODY_MAX);
  if (parsed instanceof Response) {
    return parsed;
  }
  const unread = bindOwner(env, token, session);
  const gate = await ownerRead(env, unread.binding.owner);
  const owner = withAccount(unread, gate);
  if (route.body === "empty" && !keyed(parsed, [])) {
    return failure(400, NO_FIELDS);
  }
  if ("door" in route) {
    return answer(200, await callDoor(owner.stub, owner.binding, route.door, parsed,
                                      request.signal));
  }
  const served = await route.serve({ env, owner, body: parsed, presented: presented!, token,
                                    request, sources, gate, rest: found!.rest });
  return served instanceof Response ? served : served instanceof Headed
    ? answer(200, served.value, served.headers) : answer(200, served);
}
