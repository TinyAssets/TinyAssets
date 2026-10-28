/**
 * Driving the doors as a browser, an MCP client and the fake upstream would, in workerd.
 */
import { createExecutionContext, runInDurableObject, waitOnExecutionContext } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { vi } from "vitest";

import type { OwnerDO } from "../../owner/src/owner-do";
import { APP_RESOURCE, type DoorsEnv, MCP_RESOURCE, now, PUBLIC_ORIGIN, WEB_CLIENT,
         WEB_REDIRECT } from "../src/config";
import { authorizationServer } from "../src/oauth";
import worker from "./entry";
import { fakeCode, UPSTREAM_AUTHORIZE } from "./fake-upstream";

export const ORIGIN = PUBLIC_ORIGIN;
export const doorsEnv = env as unknown as DoorsEnv;
export const OPEN = Object.freeze({ outside: "on", ordinary: "all", canary: [], inbound: false });
export const NAVIGATE = Object.freeze({ "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document" });

/** One request through the doors, its waitUntil work finished. */
export async function call(request: Request, environment: DoorsEnv = doorsEnv):
    Promise<Response> {
  const ctx = createExecutionContext();
  const response = await worker.fetch(request, environment, ctx);
  await waitOnExecutionContext(ctx);
  return response;
}

export function url(path: string): string {
  return `${ORIGIN}${path}`;
}

/** The control record, as the founder's admin path writes it; null removes it. */
export async function setControl(value: unknown): Promise<void> {
  if (value === null) {
    await env.DIRECTORY.delete("c:control");
  } else {
    await env.DIRECTORY.put("c:control", JSON.stringify(value));
  }
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

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_")
    .replace(/=+$/, "");
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
  readonly authenticatedAt?: number;
  /** The issuer the upstream reports; the owner issuer by default. */
  readonly issuer?: string;
}

/** The upstream sign-in after the doors redirected to it: back to the callback. */
export async function upstreamReturns(jar: Jar, location: string, who: SignInAs,
                                      environment: DoorsEnv = doorsEnv,
                                      extra: Record<string, string> = {}): Promise<Response> {
  const sent = new URL(location);
  if (`${sent.origin}${sent.pathname}` !== UPSTREAM_AUTHORIZE) {
    throw new Error(`the doors sent the browser to ${location}`);
  }
  const code = fakeCode({ ...(who.issuer === undefined ? {} : { issuer: who.issuer }),
                          subject: who.owner, authenticated_at: who.authenticatedAt ?? now() - 100,
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

/** A same-origin JSON POST from the owner's app, with its token and cookie. */
export function appPost(path: string, token: string, session: string | null, body: unknown,
                        headers: Record<string, string> = {}): Promise<Response> {
  return call(new Request(url(`/app/api/${path}`), {
    method: "POST",
    headers: {
      "Content-Type": "application/json", Origin: ORIGIN, "Sec-Fetch-Site": "same-origin",
      Authorization: `Bearer ${token}`,
      ...(session === null ? {} : { Cookie: `__Host-ta-owner=${session}` }), ...headers,
    },
    body: JSON.stringify(body),
  }));
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
  readonly structuredContent?: Record<string, unknown>;
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

type Row = Record<string, SqlStorageValue>;

/** Rows a query returns in an owner's object. */
export function sqlIn(owner: string, statement: string, ...params: SqlStorageValue[]):
    Promise<Row[]> {
  return runInDurableObject(env.OWNER.getByName(owner), (_instance: OwnerDO, state) =>
    state.storage.sql.exec<Row>(statement, ...params).toArray());
}

/** Whether a door was ever served in an owner's object: its owner is stored on first use. */
export async function reached(owner: string): Promise<boolean> {
  return (await sqlIn(owner, "SELECT owner FROM object_owner")).length > 0;
}
