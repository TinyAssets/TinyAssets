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
 * - `keys`, `keys/list`, `keys/revoke`, `keys/delete`: `api-keys.ts` (the key store's mint
 *   and list in the object).
 * - `grants/revoke`: an MCP client's revoke, object then provider (`revoke.ts`).
 * - `account/erase`: the owner's erasure, in session and confirmed (`erase.ts`).
 * - `hooks/create`, `hooks/list`, `hooks/set`, `hooks/revoke`: the owner's inbound
 *   bindings and their directory entries (`inbound.ts`).
 * - `files/upload/begin`, `PUT files/upload/<intent_id>`, `files/upload/commit`: an upload,
 *   its bytes streamed to `FILES` by the binder (`file-bytes.ts`).
 * - `outside-agents`: the owner's browser registering an agent for an API key it holds.
 * - `session/extend`: the session's renewal.
 * - `session/close`: sign-out, the session closed and the presented token's grant revoked.
 * - `connections/key`, `connections/oauth`, `connections/oauth/finish`,
 *   `connections/revoke`, `connections/list`: the model connect doors (`connect.ts`).
 *
 * - `agent/doorbell`, `agent/doorbell/end`, `agent/wake-limits`: an agent's doorbell (slice
 *   W): the owner pastes the `/fire` or routine URL and token a vendor showed it, which go
 *   straight to the gateway's seal (`connect.ts`), the object storing only the blob under the
 *   id derived here (the answer never echoes the token); one ended by its id; and the owner's
 *   own wake settings for an agent (`policyWakeLimits`), off by default; and
 *   `agent/doorbell/kinds`, the wake kinds whose records have the owner paste the URL and
 *   token (id and the agents each wakes), so the app's wake-up form is the records' data.
 * - The owner app's reads and settings (the app slice), each one door: `account` (the
 *   owner's tier, `tierRead`, beside the control record's tier numbers and its file usage:
 *   tiers differ only by simultaneous runs and storage), `notices`, `policy` and
 *   `policy/write` (code mode's switch and the owner's `custom` tools are policy),
 *   `grants` and `grants/set` (a client's mode, scopes and profile; a revoke goes by
 *   `grants/revoke` alone, so the provider's refresh tokens die with it), `files/read` and
 *   `files/write` (an agent is files: the owner reads and writes them as UTF-8 text, a file
 *   of at most one chunk).
 * - `sources`: the source records' public facts (name, how each connects, which engines a
 *   key runs, which credentials an engine takes, why one is unwired), so the app's connect
 *   screen is the records' data, never a list of its own.
 * - `tools`: the MCP door's table, each tool's name and title (the owner's `custom` choice).
 * - `capabilities`: the capabilities an agent's runs and a key's scopes hold (one list, the
 *   grants' own), each in plain words, so the app's agent and key forms are the object's.
 * - `tools/<name>`: the owner calling one tool of the MCP door's table (`tools.ts`) as
 *   itself, the same doors and fields an outside client's call takes, held by the object to
 *   the owner's own authority. **Which need the session** (app review M1): every change to
 *   the owner's agent definitions (`OWNER_TREES`: `file_mkdir`, `file_remove`, `file_rename`,
 *   `file_restore` there, as `files/write`), which the object takes only in the owner's live
 *   session on every route alike, since an agent's files are its instructions and authority.
 *   The rest (collections, other files, digests, runs, `code`, whose script calls are held
 *   the same way) change no agent's authority, so the owner's token suffices, as it does for
 *   a full-mode API key.
 * - `signin/start`, `signin/code`, `signin/cancel`, `signin/list` (slice SIGNIN): the
 *   vendor's own sign-in (or sign-out) inside the owner's own sandbox or one client's, its
 *   prompt watched on the app socket by the sign-in's id (`agent/socket-ticket` takes it as
 *   its `conversation_id`), a pasted code handed back, a sign-in stopped, and the sign-ins in
 *   progress with each engine's presence.
 * - `runs` (slice SIGNIN): the owner's past runs, newest first, a page at a time.
 *
 * Every answer but a socket's upgrade carries `SURFACE_HEADERS` (nosniff, a CSP of none).
 * - `agent/submit`, `agent/run`, `agent/status`, `agent/cancel`: the run doors (slice R),
 *   the same doors `converse`, `run_graph`, `run_status` and `run_cancel` call for an
 *   outside client; the control record's tier numbers ride the binding (`withTiers`).
 * - `storage`: the owner's one storage number, used and left (`storageUsage`).
 * - `export`: the owner's export manifest (`owner/src/export.ts`), streamed as
 *   newline-delimited JSON (`exportStream`), resumable from any cursor line (it names the
 *   export's id), ending as the owner at one moment however the owner writes meanwhile (a
 *   generation, `owner/src/backup.ts`); `export/chunks` its chunks' bytes by hash;
 *   `export/end` its id let go.
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
import { refusalOf } from "../../contract/src/refusal";
import { utf8Decoder } from "../../contract/src/text";
import { answerJson } from "./answers";
import {
  type Bound, bindKeyRegistration, bindOwner, bindOwnerGrant, bindSignOut, bindTicket,
  socketRequest, withTiers,
} from "./binder";
import { jsonObject } from "./body";
import { APP_BODY_MAX, type DoorsEnv, FIRST_PARTY_CLIENTS, MCP_RESOURCE } from "./config";
import { APP_RESOURCE } from "../../contract/src/identity";
import type { ControlRead } from "../../contract/src/control";
import { ownerGate, ownerRead } from "./control";
import {
  deleteOwnKey, KeyRefused, listKeys, looksLikeKey, mintKey, registeringEntry, revokeKey,
} from "./api-keys";
import { CONNECT_ROUTES, ConnectRefused, sealDoorbell } from "./connect";
import { eraseOwner } from "./erase";
import { streamUpload } from "./file-bytes";
import { HOOK_ROUTES } from "./inbound";
import { revokeClient } from "./revoke";
import { authorizationServer } from "./oauth";
import { clearedCookie, mediaType, OWNER_SURFACE, sessionCookie, sessionOf } from "./session";
import type { Sources } from "../../sources/src/schema";
import { signinsRead } from "../../sources/src/signin";
import { refusalResponse } from "./refused";
import { toolTable } from "../../contract/src/doors";
import { callDoor, type DoorName, toolDoor } from "./tools";
import type { ValidatedAccessToken } from "@cloudflare/workers-oauth-provider";

import { CAPABILITY_WORDS, RUN_CAPABILITIES } from "../../contract/src/capabilities";
import { SOCKET_FAILED, TICKET_REFUSED } from "../../contract/src/socket-shape";
import { callbackSecret, callbackUrl, kindNamed, wakeKinds } from "../../contract/src/wake-records";
import { follows, subscriptionId } from "../../contract/src/wake-kinds";

export const OUTSIDE_CREDENTIAL = "an outside credential never reaches the owner's surface";

const NO_STORE = Object.freeze({ "Cache-Control": "no-store" });

function json(status: number, body: unknown, headers: HeadersInit = {}): Response {
  return Response.json(body, { status, headers: { ...NO_STORE, ...headers } });
}

/**
 * Every owner-surface answer but the socket's upgrade carries these: JSON is never sniffed
 * into anything else, and no answer runs or frames anything if opened as a page.
 */
export const SURFACE_HEADERS: Readonly<Record<string, string>> = Object.freeze({
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

function failure(status: number, message: string, headers: HeadersInit = {}): Response {
  return json(status, { error: message }, headers);
}

export function bearer(request: Request): string | null {
  const header = request.headers.get("Authorization") ?? "";
  const match = /^Bearer ([^\s]+)$/.exec(header);
  return match === null ? null : match[1]!;
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
  readonly body: Record<string, unknown>;
  readonly presented: string;
  readonly token: ValidatedAccessToken<unknown>;
  readonly request: Request;
  readonly sources: Sources;
  readonly gate: ControlRead;
  readonly rest: string;
}

type Route = (served: Served) => Promise<Response>;

/**
 * An owner-surface route, as data: a door of the owner's table (`contract/doors.json`),
 * called with the body as its request (none for a door that takes none); or a binder
 * sequence (`serve`); either taking only an empty object (`body: "empty"`). Two come before
 * the JSON body: `socket`, before the token too (its credential is its ticket), and `bytes`,
 * an upload's bytes.
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
async function signOut({ env, owner: { binding, stub }, presented: token }: Served):
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

/** Whether a body names exactly `required` and at most `optional` besides. */
function shaped(request: Record<string, unknown>, required: readonly string[],
                optional: readonly string[] = []): boolean {
  const keys = Object.keys(request);
  return required.every((key) => keys.includes(key))
    && keys.every((key) => required.includes(key) || optional.includes(key));
}

const NO_FIELDS = "this route takes an empty object";

/**
 * `account`: what the owner's tier gives it, honestly: its tier (the platform's, on its own
 * admission row), every tier's numbers as the control record holds them (simultaneous runs
 * and storage, nothing else), and the files it holds now.
 */
const account: Route = async ({ owner: { binding, stub } }) => {
  const { tier } = await stub.tierRead(binding);
  return json(200, { tier, tiers: binding.tiers ?? null, usage: await stub.fileUsage(binding) });
};

/** `grants/set`: a client's mode, scopes or profile; never a revoke, which is `grants/revoke`. */
const grantSet: Route = async ({ owner: { binding, stub }, body: request }) => {
  if (request["mode"] === "revoked") {
    return failure(400, "a revoke goes by grants/revoke, which also ends the client's tokens");
  }
  return json(200, await stub.grantSet(binding, request as never));
};

/** `files/read` `{path}`: one file as UTF-8 text, or why it is not shown as text. */
const fileRead: Route = async ({ owner: { binding, stub }, body: request }) => {
  if (!shaped(request, ["path"])) {
    return failure(400, "a read names the file's path");
  }
  const { entry, inline } = await stub.fileRead(binding, { path: request["path"] as string });
  if (inline === undefined) {
    return json(200, { entry, text: null, why: "larger than one chunk" });
  }
  try {
    return json(200, { entry, text: utf8Decoder("strip").decode(inline) });
  } catch {
    return json(200, { entry, text: null, why: "not UTF-8 text" });
  }
};

/** `files/write` `{path, expected_etag, text}`: one file's whole text, against its etag. */
const fileWrite: Route = async ({ owner: { binding, stub }, body: request }) => {
  const text = request["text"];
  const fields = ["path", "expected_etag", "text"];
  if (!shaped(request, fields) || typeof text !== "string") {
    return failure(400, "a write names the path, the expected etag and the text");
  }
  // The etag goes to the object as it came: the object's compare-and-swap decides.
  const { path, expected_etag: expected } = request;
  return json(200, await stub.fileWrite(binding, {
    path: path as string, expected_etag: expected as string | null,
    content: new TextEncoder().encode(text) }));
};

/** `sources`: what the app shows of each record; nothing sealed, no endpoint, no argv. */
function sourceList(sources: Sources, switchedOn: readonly string[]): unknown {
  return {
    sources: sources.ids().map((id) => {
      const record = sources.get(id)!;
      return {
        id, display_name: record.display_name, help_url: record.help_url ?? null,
        suggested: record.suggested, connect: record.connect, unwired: record.unwired ?? null,
        engines: record.engines ?? [], credentials: record.cli?.credential ?? [],
        // The vendor's own sign-in, as the app offers it: whether there is one, and the
        // regions the owner chooses among (their names alone).
        signin: record.cli?.login === undefined ? null : {
          regions: Object.entries(record.cli.regions ?? {}).map(([region, { display_name }]) =>
            ({ id: region, display_name })),
          // Whether the platform offers it (the control record's switch, or the verdict), and
          // the verdict on the vendor's terms for hosted use, cited.
          enabled: switchedOn.includes(id),
          hosted_use: record.cli.login.hosted_use },
      };
    }),
  };
}

/** `tools/<name>`: one tool of the table, called by the owner as itself. */
const ownerTool: Route = async ({ owner: { binding, stub }, body: request, rest: name }) => {
  const tool = toolTable().find((row) => row.name === name);
  if (tool === undefined) {
    return failure(404, "no such tool");
  }
  return answerJson(tool.name, await callDoor(stub, binding, toolDoor(tool), request));
};

/**
 * The pages one export response carries before it ends with its cursor line, so one
 * request's work in this Worker stays bounded (a technical ceiling: a request's CPU; about
 * 4 MiB an object, 256 MiB a response). The client asks again with the last cursor line it
 * read, which names its export_id.
 */
export const EXPORT_PAGES = 64;

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
  if (Object.keys(request).some((key) => key !== "cursor" && key !== "export_id")) {
    return failure(400, "an export request is at most its cursor and its export_id");
  }
  let page = await stub.ownerExport(binding, request as { cursor?: string | null;
                                                          export_id?: string });
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
      // An object not yet written: the response ends at its cursor, to ask again.
      if (next === null || pages >= EXPORT_PAGES || page.pending) {
        controller.close();
        return;
      }
      page = await stub.ownerExport(binding, { cursor: next, export_id: exportId });
    },
  });
  return new Response(body, { status: 200, headers: {
    "Content-Type": "application/x-ndjson", "Cache-Control": "no-store" } });
}

/**
 * `agent/doorbell`: the owner's pasted wake token for one of its agents, for a kind whose
 * record has the owner hand it over. Checked against the record here and again in the
 * gateway, which seals it with the URL; the object stores the blob under the id derived
 * from the owner, URL, kind and what it follows. Never echoed.
 */
async function doorbell({ env, owner: { binding, stub }, body: request }: Served):
    Promise<Response> {
  const keys = Object.keys(request);
  const kind = typeof request["kind"] === "string" ? kindNamed(request["kind"]) : null;
  if (!["agent_id", "kind", "url", "secret"].every((key) => keys.includes(key))
      || !keys.every((key) => ["agent_id", "kind", "url", "secret", "board", "rooms"]
        .includes(key))
      || typeof request["agent_id"] !== "string" || kind === null || kind.push === null
      || kind.push.callback !== "owner") {
    return failure(400, "a doorbell names an agent, its kind, the URL and the token");
  }
  let follow;
  let url: string;
  let secret: string;
  try {
    follow = follows({ board: request["board"] ?? false, rooms: request["rooms"] ?? [] });
    url = callbackUrl(kind.push, request["url"]);
    secret = callbackSecret(kind.push, request["secret"]);
  } catch (error) {
    return failure(400, error instanceof Error ? error.message : "invalid doorbell");
  }
  const args = { agent_id: request["agent_id"], board: follow.board, rooms: follow.rooms };
  const id = subscriptionId("owner", url, kind.id, args);
  const sealed = await sealDoorbell(env, binding.owner, id, { record: kind.id, url, secret });
  return json(200, await stub.agentDoorbell(binding, {
    agent_id: request["agent_id"], kind: kind.id, subscription_id: id, board: follow.board,
    rooms: follow.rooms, blob: sealed.blob, kid: sealed.kid, version: 1 }));
}

/** `agent/doorbell/kinds`: the wake kinds the owner pastes a URL and token for. */
function doorbellKinds(): unknown {
  return { kinds: wakeKinds().filter((kind) => kind.push?.callback === "owner")
    .map(({ id, vendor, agents }) => ({ id, vendor, agents })) };
}

/**
 * A socket ticket: the owner's token's grant (the provider's own unwrap of the presented
 * token) and expiry bound in, then the object mints it for the conversation named.
 */
async function socketTicket({ env, token, presented, body: request }: Served):
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

/**
 * `outside-agents`: the owner's browser registering an agent for an API key it holds, the
 * key's directory entry read for its identity (`api-keys.ts`).
 */
async function registering({ env, owner, body, token }: Served): Promise<Response> {
  const { key_id: id, ...register } = body;
  const { keyId, entry } = await registeringEntry(env, owner.binding.owner, id);
  const { binding, stub } = bindKeyRegistration(env, token, keyId, entry, owner.binding.session);
  return json(200, await stub.registerAgent(binding, register as never));
}

/** A route that answers a binder module's sequence's value as JSON. */
function answered(value: (served: Served) => Promise<unknown>,
                  headers: HeadersInit = {}): { serve: Route } {
  return { serve: async (served) => json(200, await value(served), headers) };
}

/**
 * The owner surface's routes, as data (`OwnerRoute`), by path; a path ending in `/` is a
 * prefix, its rest the route's own (`tools/<name>`, an upload's `files/upload/<intent_id>`).
 */
function routes(): Readonly<Record<string, OwnerRoute>> {
  return {
    "agent/socket": { stage: "socket" },
    "files/upload/": { stage: "bytes" },
    "tools/": { serve: ownerTool },
    "outside-agents": { serve: registering },
    "agent/socket-ticket": { serve: socketTicket },
    "sources": { body: "empty",
                 serve: async ({ sources, gate }) =>
                   json(200, sourceList(sources, signinsRead(sources, gate))) },
    "export": { serve: exportStream },
    "export/chunks": { door: "exportChunks" },
    "export/end": { door: "exportEnd" },
    "storage": { door: "storageUsage" },
    "agent/doorbell": { serve: doorbell },
    "agent/doorbell/end": { door: "agentDoorbellEnd" },
    "agent/wake-limits": { door: "policyWakeLimits" },
    "agent/doorbell/kinds": { body: "empty", ...answered(async () => doorbellKinds()) },
    "account": { body: "empty", serve: account },
    "notices": { door: "noticeList", body: "empty" },
    "policy": { door: "policyRead", body: "empty" },
    "policy/write": { door: "policyWrite" },
    "grants": { door: "grantList", body: "empty" },
    "grants/set": { serve: grantSet },
    "capabilities": { body: "empty", ...answered(async () => ({
      capabilities: RUN_CAPABILITIES.map((id) => ({ id, words: CAPABILITY_WORDS[id] })) })) },
    "tools": { body: "empty", ...answered(async () => ({
      tools: toolTable().map(({ name, title }) => ({ name, title })) })) },
    "files/read": { serve: fileRead },
    "files/write": { serve: fileWrite },
    "keys": answered(({ env, owner, body }) => mintKey(env, owner, body)),
    "keys/list": answered(({ owner }) => listKeys(owner)),
    "account/erase": answered(({ owner, body }) => eraseOwner(owner, body),
                              { "Set-Cookie": clearedCookie() }),
    "files/upload/begin": { door: "fileUploadBegin" },
    "files/upload/commit": { door: "fileUploadCommit" },
    "files/upload/abort": { door: "fileUploadAbort" },
    ...Object.fromEntries(Object.entries(HOOK_ROUTES).map(([path, hook]) =>
      [path, answered(({ env, owner, body }) => hook(env, owner, body))])),
    "keys/revoke": answered(({ env, owner, body }) => revokeKey(env, owner, body)),
    "keys/delete": answered(({ env, owner, body }) => deleteOwnKey(env, owner, body)),
    "grants/revoke": answered(({ env, owner, body }) => revokeClient(env, owner, body["client"])),
    "session/extend": { serve: async ({ owner: { binding, stub } }) => {
      const extended = await stub.sessionExtend(binding);
      return json(200, extended, { "Set-Cookie": sessionCookie(binding.session ?? "") });
    } },
    "session/close": { serve: signOut },
    "agent/submit": { door: "agentSubmit" },
    "agent/run": { door: "agentRun" },
    "agent/status": { door: "agentStatus" },
    "agent/cancel": { door: "agentCancel" },
    "runs": { door: "runList" },
    "signin/start": { door: "signinStart" },
    "signin/code": { door: "signinCode" },
    "signin/cancel": { door: "signinCancel" },
    "signin/list": { door: "signinList", body: "empty" },
    ...Object.fromEntries(Object.entries(CONNECT_ROUTES).map(([path, connect]) =>
      [path, { serve: ({ env, owner, body, request, sources }: Served) =>
        connect({ env, bound: owner, body, request, sources }) }])),
  };
}

/** The route a path names, exactly or by its prefix, and the path's rest. */
function routeOf(path: string): { route: OwnerRoute; rest: string } | null {
  const table = routes();
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
  return surfaced(await surface(request, env, sources));
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
    const gated = await ownerGate(env, owner.binding.owner);
    return gated ?? await streamUpload(request, env, owner, found!.rest);
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
  const parsed = await jsonObject(request, APP_BODY_MAX);
  if (parsed instanceof Response) {
    return parsed;
  }
  const unread = bindOwner(env, token, session);
  const gated = await ownerRead(env, unread.binding.owner);
  if (gated instanceof Response) {
    return gated;
  }
  const owner = withTiers(unread, gated, sources);
  try {
    if (route.body === "empty" && !shaped(parsed, [])) {
      return failure(400, NO_FIELDS);
    }
    if ("door" in route) {
      return json(200, await callDoor(owner.stub, owner.binding, route.door, parsed));
    }
    return await route.serve({ env, owner, body: parsed, presented: presented!, token, request,
                               sources, gate: gated, rest: found!.rest });
  } catch (error) {
    if (error instanceof KeyRefused || error instanceof ConnectRefused) {
      return failure(error.status, error.message);
    }
    const refused = refusalOf(error);
    if (refused !== null) {
      return refusalResponse(refused, NO_STORE);
    }
    throw error;
  }
}
