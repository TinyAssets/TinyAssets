/**
 * Driving the doors as a browser, an MCP client and the fake upstream would, in workerd.
 */
import { createExecutionContext, waitOnExecutionContext } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { vi } from "vitest";

import { type Binding, PLATFORM_CALLER } from "../../contract/src/binding";
import { base64url } from "../../contract/src/envelope";
import { acceptanceKey, answerKey } from "../../contract/src/operator-message";
import type { DoorAnswer } from "../../contract/src/owner";
import { QUEUES } from "../../contract/src/queue-names";
import type { Refusal } from "../../contract/src/refusal";
import { upstreamConfig } from "../../contract/src/signin";
import { OPEN, OPEN_RECORD, writeControl } from "../../ci/fixtures";
import { until } from "../../ci/workerd";
import { now } from "../src/config";
import type { DoorsEnv } from "../src/env";
import {
  APP_RESOURCE, MCP_RESOURCE, ORIGIN, WEB_CLIENT, WEB_REDIRECT,
} from "../../contract/src/identity";
import { serve } from "../src/doors";
import doors from "../src/index";
import { authorizationServer } from "../src/oauth";
import { callDoor, type DoorName } from "../src/tools";
import type { Sources } from "../../sources/src/schema";
import { signInCode } from "./fake-providers";
import { NAVIGATE } from "../../contract/src/navigation";


/**
 * What `owner`'s object asked of its sandbox: the owner's fake sandbox Worker, bound by
 * config to the owner Worker (`vitest.config.ts`), reads it back.
 */
export async function recorded(owner: string) {
  return await env.SANDBOX_LOG.recorded(owner);
}
/**
 * The statuses the fake AuthKit answered `subject`'s identity deletions, oldest first
 * (`fake-providers.ts`): the doors' `forget` at an erasure, and at an erased owner's sign-in.
 */
export async function forgotten(subject: string): Promise<number[]> {
  return await (await env.PROVIDERS.fetch(
    `https://status.test/forgotten?sub=${encodeURIComponent(subject)}`)).json();
}

/** `document` served at `clientId` by the fake outside world, as a client's host serves its CIMD. */
export async function serveDocument(clientId: string, document: Record<string, unknown>):
    Promise<void> {
  await env.PROVIDERS.fetch(`https://status.test/document?url=${encodeURIComponent(clientId)}`,
                            { method: "POST", body: JSON.stringify(document) });
}

/** `subject`'s next identity deletion at the fake AuthKit answered `503`, as an outage would. */
export async function failNextForget(subject: string): Promise<void> {
  await env.PROVIDERS.fetch(`https://status.test/fail-forget?sub=${encodeURIComponent(subject)}`,
                            { method: "POST" });
}
export const doorsEnv = env as unknown as DoorsEnv;
export { DARK, OPEN, OPEN_RECORD, TIERS } from "../../ci/fixtures";

/**
 * One request through the doors' own entry, its waitUntil work finished; or through their
 * router over `sources`, a catalog with a record the bundle lacks (`connect.test.ts`).
 */
export async function call(request: Request, environment: DoorsEnv = doorsEnv,
                           sources?: Sources): Promise<Response> {
  const ctx = createExecutionContext();
  const response = sources === undefined ? await doors.fetch(request, environment, ctx)
    : await serve(request, environment, ctx, sources);
  await waitOnExecutionContext(ctx);
  return response;
}

/** One delivery of `body` to the operator queue through the doors' own entry, and how it ended. */
export async function deliver(body: unknown, environment: DoorsEnv = doorsEnv) {
  const acked: number[] = [];
  const retried: number[] = [];
  const batch = { queue: QUEUES.OPERATOR, ackAll: () => undefined, retryAll: () => undefined,
                  messages: [{ id: "0", timestamp: new Date(), attempts: 1, body,
                               ack: () => acked.push(0), retry: () => retried.push(0) }] };
  await doors.queue(batch as unknown as MessageBatch<unknown>, environment);
  return { acked: acked.length, retried: retried.length };
}

/** What the operator bucket holds for `ask`: its acceptance record and its answer, or null. */
export async function held(ask: string) {
  const read = async (key: string) => {
    const object = await doorsEnv.OPERATOR_LOG.get(key);
    return object === null ? null : await object.json() as Record<string, unknown>;
  };
  return { accepted: await read(acceptanceKey(ask)), answer: await read(answerKey(ask)) };
}

/**
 * The platform's explicit admit of `owner`, as the sign-in callback sends it at sign-up
 * (`authorize.ts`), for a test that reaches an owner's object without signing it in.
 */
export async function admitOwner(owner: string): Promise<void> {
  await doorsEnv.OWNER.getByName(owner).admissionSet(
    { owner, caller: PLATFORM_CALLER, origin: null }, { admit: true });
}

export function url(path: string): string {
  return `${ORIGIN}${path}`;
}

/** The control record, as the founder's admin path writes it; null removes it. */
export async function setControl(value: unknown): Promise<void> {
  await writeControl(env.CONTROL, value);
}

/** A browser's cookies, by name, kept from each response's `Set-Cookie`. */
export class Jar {
  readonly cookies = new Map<string, string>();

  take(response: Response): Response {
    for (const line of response.headers.getSetCookie()) {
      const [pair] = line.split(";");
      const at = pair!.indexOf("=");
      const name = pair!.slice(0, at).trim();
      const value = pair!.slice(at + 1);
      if (/max-age=0/i.test(line) || value === "") {
        this.cookies.delete(name);
      } else {
        this.cookies.set(name, value);
      }
    }
    return response;
  }

  header(): string {
    return [...this.cookies].map(([name, value]) => `${name}=${value}`).join("; ");
  }
}

export async function pkce(): Promise<{ verifier: string; challenge: string }> {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = base64url(new Uint8Array(await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(verifier))));
  return { verifier, challenge };
}

export interface Client {
  readonly clientId: string;
  readonly redirectUri: string;
}

/** A pre-registered public MCP client. */
export async function registerClient(name = "Muse",
                                     redirectUri = "https://client.example/callback"):
    Promise<Client> {
  const oauth = authorizationServer(doorsEnv).getOAuthApi(doorsEnv);
  const client = await oauth.createClient({ redirectUris: [redirectUri], clientName: name,
                                            tokenEndpointAuthMethod: "none" });
  return { clientId: client.clientId, redirectUri };
}

export function authorizeUrl(client: Client, challenge: string, resource: string = MCP_RESOURCE,
                             state = "client-state"): string {
  const params = new URLSearchParams({
    response_type: "code", client_id: client.clientId, redirect_uri: client.redirectUri,
    state, code_challenge: challenge, code_challenge_method: "S256", resource,
  });
  return url(`/authorize?${params}`);
}

/** The consent page's handle, from its form. */
export function handleOf(html: string): string {
  const match = /name="handle" value="([^"]+)"/.exec(html);
  if (match === null) {
    throw new Error("no consent form on the page");
  }
  return match[1]!.replace(/&#(\d+);/g, (_m, code: string) => String.fromCharCode(Number(code)));
}

export interface SignInAs {
  readonly owner: string;
  /** When the person signed in, 100 s ago by default; null: a token with no `auth_time`. */
  readonly authenticatedAt?: number | null;
}

/**
 * The upstream sign-in after the doors redirected to it, at the authorize endpoint the
 * doors' `UPSTREAM` names: back to the callback with the fake AuthKit's code.
 */
export async function upstreamReturns(jar: Jar, location: string, who: SignInAs,
                                      environment: DoorsEnv = doorsEnv,
                                      extra: Record<string, string> = {}): Promise<Response> {
  const sent = new URL(location);
  if (`${sent.origin}${sent.pathname}`
      !== upstreamConfig(environment.UPSTREAM).authorization_endpoint) {
    throw new Error(`the doors sent the browser to ${location}`);
  }
  const code = signInCode({ subject: who.owner,
                            authenticated_at: who.authenticatedAt === undefined ? now() - 100
                              : who.authenticatedAt,
                            challenge: sent.searchParams.get("code_challenge")! });
  const back = new URLSearchParams({ code, state: sent.searchParams.get("state")!, ...extra });
  return jar.take(await call(new Request(url(`/oauth/upstream/callback?${back}`), {
    headers: { ...NAVIGATE, Cookie: jar.header() } }), environment));
}

/** The consent page for `client`, as the owner's browser opens it. */
export async function consent(jar: Jar, client: Client, challenge: string): Promise<Response> {
  return jar.take(await call(new Request(authorizeUrl(client, challenge),
                                         { headers: { ...NAVIGATE } })));
}

/** The browser posting the consent form's decision. */
export async function decide(jar: Jar, handle: string, decision: "approve" | "deny"):
    Promise<Response> {
  return jar.take(await call(new Request(url("/authorize"), {
    method: "POST", headers: { Cookie: jar.header(), Origin: ORIGIN },
    body: new URLSearchParams({ handle, decision }),
  })));
}

/** A full authorization: consent, approval, the upstream sign-in; the client's code. */
export async function authorizeMcp(client: Client, who: SignInAs,
                                   environment: DoorsEnv = doorsEnv):
    Promise<{ code: string; verifier: string; redirect: URL }> {
  const jar = new Jar();
  const { verifier, challenge } = await pkce();
  const page = await consent(jar, client, challenge);
  if (page.status !== 200) {
    throw new Error(`consent page: ${page.status} ${await page.text()}`);
  }
  const approved = await decide(jar, handleOf(await page.text()), "approve");
  const back = await upstreamReturns(jar, approved.headers.get("Location")!, who, environment);
  const redirect = new URL(back.headers.get("Location")!);
  const code = redirect.searchParams.get("code");
  if (back.status !== 302 || code === null) {
    throw new Error(`callback: ${back.status} ${redirect.href}`);
  }
  return { code, verifier, redirect };
}

/** A token endpoint call. */
export function tokenCall(params: Record<string, string>): Promise<Response> {
  return call(new Request(url("/oauth/token"), {
    method: "POST", body: new URLSearchParams(params),
  }));
}

export interface Tokens {
  readonly access_token: string;
  readonly refresh_token: string;
}

/** An MCP client connected for `who`: authorized and its code exchanged. */
export async function connect(client: Client, who: SignInAs): Promise<Tokens> {
  const { code, verifier } = await authorizeMcp(client, who);
  const response = await tokenCall({ grant_type: "authorization_code", code,
                                     redirect_uri: client.redirectUri,
                                     client_id: client.clientId, code_verifier: verifier,
                                     resource: MCP_RESOURCE });
  if (response.status !== 200) {
    throw new Error(`token: ${response.status} ${await response.text()}`);
  }
  return await response.json();
}

export async function refresh(client: Client, refreshToken: string): Promise<Response> {
  return tokenCall({ grant_type: "refresh_token", refresh_token: refreshToken,
                     client_id: client.clientId });
}

/**
 * Serve the first-party app's metadata document from the doors themselves when the
 * provider fetches it, as production does over the public internet.
 */
export function serveFirstPartyDocument(): () => void {
  const real = globalThis.fetch;
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const request = new Request(input, init);
    if (request.url === WEB_CLIENT) {
      return call(new Request(WEB_CLIENT));
    }
    return real(input, init);
  });
  return () => spy.mockRestore();
}

/** The owner's app sign-in up to the upstream's return to the callback, and its answer. */
export async function appCallback(who: SignInAs, environment: DoorsEnv = doorsEnv):
    Promise<{ jar: Jar; verifier: string; back: Response }> {
  const jar = new Jar();
  const { verifier, challenge } = await pkce();
  const params = new URLSearchParams({
    response_type: "code", client_id: WEB_CLIENT, redirect_uri: WEB_REDIRECT,
    state: "app-state", code_challenge: challenge, code_challenge_method: "S256",
    resource: APP_RESOURCE,
  });
  const started = jar.take(await call(new Request(url(`/authorize?${params}`),
                                                  { headers: { ...NAVIGATE } })));
  if (started.status !== 302) {
    throw new Error(`app sign-in: ${started.status} ${await started.text()}`);
  }
  const back = await upstreamReturns(jar, started.headers.get("Location")!, who, environment);
  return { jar, verifier, back };
}

/** The owner's own sign-in in its app: the session cookie, and a first-party token. */
export async function signInApp(who: SignInAs):
    Promise<{ jar: Jar; access_token: string; session: string }> {
  const { jar, verifier, back } = await appCallback(who);
  const code = new URL(back.headers.get("Location")!).searchParams.get("code")!;
  const response = await tokenCall({ grant_type: "authorization_code", code,
                                     redirect_uri: WEB_REDIRECT, client_id: WEB_CLIENT,
                                     code_verifier: verifier, resource: APP_RESOURCE });
  if (response.status !== 200) {
    throw new Error(`app token: ${response.status} ${await response.text()}`);
  }
  const { access_token } = await response.json<{ access_token: string }>();
  return { jar, access_token, session: jar.cookies.get("__Host-ta-owner")! };
}

/**
 * A same-origin JSON POST from the owner's app, with its token and cookie, to the doors
 * serving the test entry's catalog or `sources`.
 */
export function appPost(path: string, token: string, session: string | null, body: unknown,
                        headers: Record<string, string> = {}, sources?: Sources):
    Promise<Response> {
  return call(new Request(url(`/app/api/${path}`), {
    method: "POST",
    headers: {
      "Content-Type": "application/json", Origin: ORIGIN, "Sec-Fetch-Site": "same-origin",
      Authorization: `Bearer ${token}`,
      ...(session === null ? {} : { Cookie: `__Host-ta-owner=${session}` }), ...headers,
    },
    body: JSON.stringify(body),
  }), doorsEnv, sources);
}

/** A JSON-RPC response from `/mcp`, whether the server answered as JSON or as SSE. */
export async function rpcBody(response: Response): Promise<Record<string, unknown>> {
  const text = await response.text();
  if ((response.headers.get("Content-Type") ?? "").startsWith("text/event-stream")) {
    const data = text.split("\n").filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trim()).filter((line) => line.length > 0);
    return JSON.parse(data.at(-1)!) as Record<string, unknown>;
  }
  return JSON.parse(text) as Record<string, unknown>;
}

export interface ToolResult {
  readonly isError?: boolean;
  readonly content: { type: string; text: string }[];
}

/**
 * A tool result's answer: its one text block's JSON. Every result is sent once, as text,
 * so a result that also carries `structuredContent` fails here.
 */
export function answerOf(result: unknown): Record<string, unknown> {
  const { content, structuredContent } = result as {
    content: { type: string; text: string }[]; structuredContent?: unknown };
  if (structuredContent !== undefined || content.length !== 1 || content[0]!.type !== "text") {
    throw new Error(`a result is one text block and nothing else: ${JSON.stringify(result)}`);
  }
  return JSON.parse(content[0]!.text) as Record<string, unknown>;
}

/** A POST to `/mcp` as a 2025-11-25 client on MCP SDK 1.x sends one. */
export function legacyPost(token: string, message: unknown, headers: Record<string, string> = {},
                           environment: DoorsEnv = doorsEnv): Promise<Response> {
  return call(new Request(url("/mcp"), {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json",
               Accept: "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-11-25", ...headers },
    body: JSON.stringify(message),
  }), environment);
}

/**
 * One tool call as a 2025-11-25 client makes it: `initialize`, `initialized`, then
 * `tools/call`, each its own stateless request. A non-200 answer throws with its status.
 */
export async function legacyTool(token: string, name: string, args: unknown):
    Promise<ToolResult> {
  const steps: unknown[] = [
    { jsonrpc: "2.0", id: 0, method: "initialize", params: {
      protocolVersion: "2025-11-25", capabilities: {},
      clientInfo: { name: "legacy-test", version: "1" } } },
    { jsonrpc: "2.0", method: "notifications/initialized" },
    { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } },
  ];
  let last: Response | undefined;
  for (const step of steps) {
    last = await legacyPost(token, step);
    if (last.status !== 200 && last.status !== 202) {
      throw new Error(`/mcp answered ${last.status}: ${await last.text()}`);
    }
  }
  const body = await rpcBody(last!);
  if (body["error"] !== undefined) {
    throw new Error(`/mcp error: ${JSON.stringify(body["error"])}`);
  }
  return body["result"] as ToolResult;
}

/**
 * `owner`'s next call of `door` refused at the owner gate (`ci/gate.ts`) with `refusal`,
 * crossing the object's RPC to the doors as a refusal thrown in the object does (as a full
 * slot pool refuses it `busy`, `contract/src/slots.ts`).
 */
export async function refuseOnce(door: string, owner: string, refusal: Refusal): Promise<void> {
  const { code, message, name, retryAfterS, facts } = refusal;
  await env.OWNER_GATE.arm({ method: door, name: owner, refuse: {
    code, message, name, ...(retryAfterS === undefined ? {} : { retryAfterS }),
    ...(facts === undefined ? {} : { facts }) } });
}

/** `owner`'s admission and tier, as its `ownerState` door answers the platform. */
export async function admissionOf(owner: string) {
  const { state, reasons, tier } = await doorsEnv.OWNER.getByName(owner).ownerState(
    { owner, caller: PLATFORM_CALLER, origin: null });
  return { state, reasons, tier };
}

/** The owner's own binding, as a door takes it from the owner's app. */
export function ownBinding(owner: string): Binding {
  return { owner, caller: owner, origin: null, control: OPEN_RECORD };
}

/**
 * `owner`'s door `door` called with its own binding (`ownBinding`), as the doors call a table
 * door (`callDoor`): its answer, as the contract types it (`DoorAnswer`), or its refusal.
 * Awaited here, so a refusal is this promise's alone: a test that asserted on the stub's own
 * promise would leave a rejected pipelined property of it unhandled.
 */
export async function ownDoor<D extends DoorName>(owner: string, door: D,
                                                  request?: unknown): Promise<DoorAnswer<D>> {
  return await callDoor(doorsEnv.OWNER.getByName(owner), ownBinding(owner), door, request,
                        new AbortController().signal);
}

/** `owner`'s grants, as its `grantList` door answers them to its own binding. */
export async function grantsOf(owner: string) {
  return (await ownDoor(owner, "grantList")).clients;
}

/**
 * `owner`'s `main` agent defined as `file`, as its app writes it in its session, once the
 * owner's starter seed (its own `main` among it), which its sign-in began, has landed.
 */
export async function defineMain(owner: string, app: { access_token: string; session: string },
                                 file: Record<string, unknown>): Promise<void> {
  await until(async () => (await ownDoor(owner, "starterReceipts"))
    .version > 0, "the starter's seed");
  const path = "/workspace/agents/main/agent.json";
  const read = await appPost("files/read", app.access_token, app.session, { path });
  const { entry } = await read.json<{ entry: { etag: string } | null }>();
  const written = await appPost("files/write", app.access_token, app.session,
                                { path, expected_etag: entry?.etag ?? null,
                                  text: JSON.stringify(file) });
  if (written.status !== 200) {
    throw new Error(`main's agent.json: ${written.status} ${await written.text()}`);
  }
}

/**
 * The calls of `door` that crossed into `owner`'s object since the last look (the owner
 * gate's record, `ci/gate.ts`): each one's binding and request, as the doors sent them.
 */
export async function crossed(owner: string, door: string):
    Promise<{ binding: Binding; request: Record<string, unknown> }[]> {
  return (await env.OWNER_GATE.seen(owner)).filter((call) => call.method === door)
    .map((call) => {
      const [binding, request] = call.args as [Binding, Record<string, unknown>];
      return { binding, request };
    });
}

/**
 * The agent `owner` registers for the client `tokens` connects, as a client registers one:
 * the client files its card (`agent_register`) and the owner applies it in a session of its
 * own (`registerAgent`). Its id.
 */
export async function registerAgent(tokens: Tokens, owner: string, delivery_method: string,
                                    command_center_id = "alpha"): Promise<string> {
  const filed = answerOf(await legacyTool(tokens.access_token, "agent_register", {
    command_center_id, display_name: "Dot", delivery_method, recipe_id: null })) as
    { card_id: string; digest: string };
  const stub = doorsEnv.OWNER.getByName(owner);
  const { session } = await stub.sessionOpen({ ...ownBinding(owner),
                                               authenticated_at: now() - 5 });
  const registered = await callDoor(stub, { ...ownBinding(owner), session }, "registerAgent",
                                    { card_id: filed.card_id, digest: filed.digest },
                                    new AbortController().signal);
  return registered.agent_id;
}

/**
 * What crossed `owner`'s gate so far, forgotten: a test's own setup's calls (a sign-in's
 * admit), before the call whose reach it watches (`reached`).
 */
export async function unseen(owner: string): Promise<void> {
  await env.OWNER_GATE.seen(owner);
}

/** Whether the doors called `owner`'s object since the last look: what crossed its gate. */
export async function reached(owner: string): Promise<boolean> {
  return (await env.OWNER_GATE.seen(owner)).length > 0;
}
