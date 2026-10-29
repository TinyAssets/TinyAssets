/**
 * The connect doors (gateway research §7.2, §7.3; tasks 4 G3), in workerd, through the
 * real gateway Worker (its `ConnectGateway` entrypoint, as the config binds it) and the
 * real owner object, against fake providers.
 *
 * The rule each test holds: a pasted key or an OAuth code reaches the gateway only, never
 * an argument of any call on the owner's object (a spy on the stub), its storage, a log, an
 * error or an answer; every connect door is the owner's own, in its live session, and
 * refuses an outside credential before any object call; a flow finishes only in the
 * browser and the session that began it, once, while it lasts, and only with a code that
 * came back on its own source's callback; a connect that fails leaves nothing live.
 */
import { createExecutionContext, env, runInDurableObject } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OwnerDO } from "../../owner/src/owner-do";
import { CONNECT_CALLBACK, CONNECTED_PAGE, type DoorsEnv, now } from "../src/config";
import { serve } from "../src/doors";
import { bundledRecords, catalogOf } from "../src/sources";
import { TEST_OAUTH } from "./entry";
import { FAKE_UPSTREAM } from "./fake-upstream";
import { mintKeyFor } from "./keys";
import {
  call, connect, doorsEnv, NAVIGATE, OPEN, ORIGIN, registerClient, serveFirstPartyDocument,
  setControl, signInApp, url,
} from "./support";

interface Call {
  readonly method: string;
  readonly args: string;
}

/** The doors' env with every call on an owner's stub recorded, its arguments as JSON. */
function spied(base: DoorsEnv = doorsEnv): { environment: DoorsEnv; calls: Call[] } {
  const calls: Call[] = [];
  const real = base.OWNER;
  const OWNER = new Proxy(real, {
    get(target, prop) {
      if (prop === "getByName") {
        return (name: string) => {
          const stub = target.getByName(name);
          return new Proxy(stub, {
            get(inner, method) {
              const value = Reflect.get(inner, method) as unknown;
              if (typeof method !== "string" || typeof value !== "function") {
                return value;
              }
              return (...args: unknown[]) => {
                calls.push({ method, args: JSON.stringify(args) });
                return Reflect.apply(value as (...a: unknown[]) => unknown, inner, args);
              };
            },
          });
        };
      }
      const value = Reflect.get(target, prop) as unknown;
      return typeof value === "function" ? (value as () => unknown).bind(target) : value;
    },
  });
  return { environment: { ...base, OWNER }, calls };
}

function methods(calls: readonly Call[]): string[] {
  return calls.map((made) => made.method);
}

function newOwner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

interface App {
  readonly owner: string;
  readonly token: string;
  readonly session: string;
}

async function signedIn(owner = newOwner()): Promise<App> {
  const app = await signInApp({ owner });
  return { owner, token: app.access_token, session: app.session };
}

/** A same-origin JSON POST from the owner's app, with its token, session and any cookies. */
function post(app: App, path: string, body: unknown, environment: DoorsEnv = doorsEnv,
              cookies: readonly string[] = [], session: string | null = app.session,
              headers: Record<string, string> = {}): Promise<Response> {
  const jar = [...(session === null ? [] : [`__Host-ta-owner=${session}`]), ...cookies];
  return call(new Request(url(`/app/api/${path}`), {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: ORIGIN, "Sec-Fetch-Site": "same-origin",
               Authorization: `Bearer ${app.token}`,
               ...(jar.length > 0 ? { Cookie: jar.join("; ") } : {}), ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  }), environment);
}

/** What the fake providers received. */
async function received(): Promise<{ method: string; url: string; headers: Record<string, string>;
                                     body: string }[]> {
  return (await env.PROVIDERS.fetch("https://status.test/log")).json();
}

/** Every value in every table of the owner's object, as one string. */
function storageOf(owner: string): Promise<string> {
  return runInDurableObject(env.OWNER.getByName(owner), (_instance: OwnerDO, state) => {
    const sql = state.storage.sql;
    const tables = sql.exec<{ name: string }>(
      "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' " +
        "AND name NOT LIKE '_cf_%'")
      .toArray().map((row) => row.name);
    return JSON.stringify(tables.map((table) =>
      sql.exec(`SELECT * FROM "${table.replace(/"/g, "\"\"")}"`).toArray()));
  });
}

function sqlOf<T extends Record<string, SqlStorageValue>>(owner: string, statement: string):
    Promise<T[]> {
  return runInDurableObject(env.OWNER.getByName(owner), (_instance: OwnerDO, state) =>
    state.storage.sql.exec<T>(statement).toArray());
}

const flowRows = (owner: string) => sqlOf<{ status: string }>(
  owner, "SELECT status FROM connection_flows");
const connectionRows = (owner: string) => sqlOf<{ status: string; revoke_due: number | null }>(
  owner, "SELECT status, revoke_due FROM connections");
const blobRows = (owner: string) => sqlOf(owner, "SELECT connection_id FROM connection_secrets");

async function noticeTypes(owner: string): Promise<string[]> {
  const { notices } = await env.OWNER.getByName(owner).noticeList(
    { owner, caller: owner, origin: null });
  return notices.map((notice) => `${notice.type}: ${notice.detail}`);
}

const CONSOLE = ["log", "info", "warn", "error", "debug"] as const;
let logged: string[] = [];
let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl(OPEN);
  await env.PROVIDERS.fetch("https://status.test/log", { method: "DELETE" });
  restore = serveFirstPartyDocument();
  logged = [];
  for (const level of CONSOLE) {
    vi.spyOn(console, level).mockImplementation((...args: unknown[]) => {
      logged.push(args.map((arg) => (arg instanceof Error ? `${arg.message} ${arg.stack}`
                                                            : String(arg))).join(" "));
    });
  }
});

afterEach(() => {
  restore();
  vi.restoreAllMocks();
});

/** The secret reached nothing but the gateway and its provider. */
async function keptFromObject(owner: string, secret: string, calls: readonly Call[],
                              answer: string): Promise<void> {
  expect(calls.length).toBeGreaterThan(0);
  for (const made of calls) {
    expect(made.args, made.method).not.toContain(secret);
  }
  expect(await storageOf(owner)).not.toContain(secret);
  expect(answer).not.toContain(secret);
  expect(logged.join("\n")).not.toContain(secret);
}

describe("a pasted key", () => {
  it("is sealed and checked by the gateway; the object gets only the blob", async () => {
    const app = await signedIn();
    const key = `gsk-good-${crypto.randomUUID()}`;
    const { environment, calls } = spied();
    const answer = await post(app, "connections/key", { source: "groq", key }, environment);
    const text = await answer.text();
    expect(answer.status, text).toBe(200);
    const view = JSON.parse(text) as Record<string, unknown>;
    expect(view).toMatchObject({ source: "groq", kind: "api_key", provider: "groq",
                                 status: "active", version: 1, display_name: "Groq",
                                 expires_at: null, provider_revocable: false });
    expect((view["models"] as string[]).length).toBeGreaterThan(0);
    expect(view).not.toHaveProperty("blob");
    expect(methods(calls)).toEqual(["connectionPrepare", "connectionCommit"]);
    expect(JSON.parse(calls[0]!.args)[1]).toMatchObject({ kind: "api_key", revocable: false });
    expect(JSON.parse(calls[1]!.args)[1].blob).toMatch(/^v1\.k1\./);
    // The key went to the provider, from the gateway, on its sealed setup check alone.
    const sent = await received();
    expect(sent.map((request) => `${request.method} ${request.url}`))
      .toEqual(["GET https://api.groq.com/openai/v1/models"]);
    expect(sent[0]!.headers["authorization"]).toBe(`Bearer ${key}`);
    await keptFromObject(app.owner, key, calls, text);
  });

  it("is sent as the record's own header, and takes the owner's name for it", async () => {
    const app = await signedIn();
    const key = `AIza-good-${crypto.randomUUID()}`;
    const answer = await post(app, "connections/key",
                              { source: "google_ai_studio", key, display_name: "Work" });
    expect(answer.status).toBe(200);
    expect(await answer.json()).toMatchObject({ provider: "google", display_name: "Work",
                                                status: "active" });
    expect((await received())[0]!.headers["x-goog-api-key"]).toBe(key);
  });

  it("refused by its provider is the gateway's fixed code, and holds no slot", async () => {
    const app = await signedIn();
    const key = `gsk-bad-${crypto.randomUUID()}`;
    const { environment, calls } = spied();
    const answer = await post(app, "connections/key", { source: "groq", key }, environment);
    const text = await answer.text();
    expect(answer.status).toBe(502);
    expect(JSON.parse(text)).toEqual({ error: "upstream_unauthorized" });
    expect(methods(calls)).toEqual(["connectionPrepare", "connectionRevoke"]);
    await keptFromObject(app.owner, key, calls, text);
    expect(await connectionRows(app.owner)).toEqual([{ status: "revoked", revoke_due: null }]);
    expect(await blobRows(app.owner)).toEqual([]);
  });

  it.each([
    ["the provider's", "upstream_rate_limited", 502],
    ["a token endpoint's", "oauth_unavailable", 502],
    ["a malformed request's", "invalid_request", 400],
  ])("answers %s failure with the gateway's fixed code", async (_what, code, status) => {
    const app = await signedIn();
    const failing = { ...doorsEnv, GATEWAY: { ...doorsEnv.GATEWAY,
                                              seal: () => Promise.reject(new Error(code)) } };
    const answer = await post(app, "connections/key", { source: "groq", key: "gsk-good-4" },
                              failing);
    expect(answer.status).toBe(status);
    expect(await answer.json()).toEqual({ error: code });
    expect(await connectionRows(app.owner)).toEqual([{ status: "revoked", revoke_due: null }]);
  });

  it("throws any other gateway failure as it came, giving the connection up first", async () => {
    const app = await signedIn();
    for (const message of ["keyring_unavailable", "internal", "Network connection lost."]) {
      const failing = { ...doorsEnv, GATEWAY: { ...doorsEnv.GATEWAY,
                                                seal: () => Promise.reject(new Error(message)) } };
      await expect(post(app, "connections/key", { source: "groq", key: "gsk-good-5" }, failing))
        .rejects.toThrow(message);
    }
    expect((await connectionRows(app.owner)).map((row) => row.status))
      .toEqual(["revoked", "revoked", "revoked"]);
  });

  it.each([
    ["no key", { source: "groq" }, 400],
    ["a key that is not text", { source: "groq", key: 7 }, 400],
    ["an empty key", { source: "groq", key: "" }, 400],
    ["a key past the gateway's bound", { source: "groq", key: `gsk-good-${"x".repeat(16 * 1024)}` }, 400],
    ["a key with a trailing newline", { source: "groq", key: "gsk-good-abc\n" }, 400],
    ["a key with a leading space", { source: "groq", key: " gsk-good-abc" }, 400],
    ["a key with a space inside", { source: "groq", key: "gsk-good a" }, 400],
    ["a key outside ASCII", { source: "groq", key: "gsk-good-é" }, 400],
    ["an owner", { source: "groq", key: "gsk-good-1", owner: "bob" }, 400],
    ["a connection", { source: "groq", key: "gsk-good-1", connection: "c0123456789abcdef01234567" }, 400],
    ["a version", { source: "groq", key: "gsk-good-1", version: 2 }, 400],
    ["a display name that is not text", { source: "groq", key: "gsk-good-1", display_name: 1 }, 400],
    ["an unknown source", { source: "nope", key: "gsk-good-1" }, 404],
    ["a source that is not text", { source: ["groq"], key: "gsk-good-1" }, 404],
    ["a prototype's name", { source: "__proto__", key: "gsk-good-1" }, 404],
    ["another prototype name", { source: "constructor", key: "gsk-good-1" }, 404],
    ["a source with no key", { source: "test_oauth", key: "gsk-good-1" }, 400],
    ["a vendor CLI", { source: "claude-code", key: "gsk-good-1" }, 400],
  ])("is refused, before any object call, with %s", async (_what, body, status) => {
    const app = await signedIn();
    const { environment, calls } = spied();
    const answer = await post(app, "connections/key", body, environment);
    const text = await answer.text();
    expect(answer.status, text).toBe(status);
    expect(calls).toEqual([]);
    expect(await received()).toEqual([]);
    expect(text).not.toContain("gsk-good");
    expect(logged.join("\n")).not.toContain("gsk-good");
  });

  it("with a display name the object refuses leaves nothing behind", async () => {
    const app = await signedIn();
    for (const display_name of ["x".repeat(5000), "a\u0000b", ""]) {
      const answer = await post(app, "connections/key",
                                { source: "groq", key: "gsk-good-1", display_name });
      expect(answer.status).toBe(400);
    }
    expect(await connectionRows(app.owner)).toEqual([]);
    expect(await received()).toEqual([]);
  });

  it("is refused 413 past the owner surface's body bound, before any object call", async () => {
    const app = await signedIn();
    const { environment, calls } = spied();
    const body = JSON.stringify({ source: "groq", key: `gsk-good-${"x".repeat(70 * 1024)}` });
    const answer = await post(app, "connections/key", body, environment);
    expect(answer.status).toBe(413);
    expect(calls).toEqual([]);
  });

  it("needs the live session: the object refuses, and the gateway is never asked", async () => {
    const app = await signedIn();
    const { environment, calls } = spied();
    const answer = await post(app, "connections/key", { source: "groq", key: "gsk-good-2" },
                              environment, [], null);
    expect(answer.status).toBe(403);
    expect(await answer.json()).toEqual({ error: "interactive owner session required" });
    expect(methods(calls)).toEqual(["connectionPrepare"]);
    expect(await received()).toEqual([]);
  });
});

describe("the owner surface's routes", () => {
  it.each(["toString", "__proto__", "hasOwnProperty", "constructor", "connections/key/",
           "connections//key", "connections%2Fkey"])(
    "answers %s 404, never a prototype's member", async (path) => {
    const app = await signedIn();
    const { environment, calls } = spied();
    expect((await post(app, path, {}, environment)).status).toBe(404);
    expect(calls).toEqual([]);
  });
});

describe("an outside credential", () => {
  const ROUTES: readonly [string, unknown][] = [
    ["connections/key", { source: "groq", key: "gsk-good-3" }],
    ["connections/oauth", { source: "test_oauth" }],
    ["connections/oauth/finish", { state: "s".repeat(43), code: "good-code",
                                   redirect_uri: `${CONNECT_CALLBACK}/test_oauth` }],
    ["connections/revoke", { connection: "c0123456789abcdef01234567" }],
    ["connections/list", {}],
  ];

  it.each(ROUTES)("is refused on %s before any object call", async (path, body) => {
    const owner = newOwner();
    const { key, session } = await mintKeyFor(owner);
    const tokens = await connect(await registerClient(), { owner });
    for (const outside of [key, tokens.access_token]) {
      const { environment, calls } = spied();
      const answer = await post({ owner, token: outside, session }, path, body, environment);
      expect(answer.status).toBe(403);
      expect(await answer.json()).toEqual({
        error: "an outside credential never reaches the owner's surface" });
      expect(calls).toEqual([]);
    }
    expect(await received()).toEqual([]);
  });
});

/** The begin of an OAuth flow for `source`: its answer, URL, state and binding cookie. */
async function begin(app: App, source: string, environment: DoorsEnv = doorsEnv) {
  const answer = await post(app, "connections/oauth", { source }, environment);
  const text = await answer.text();
  expect(answer.status, text).toBe(200);
  const started = JSON.parse(text) as { connection: string; authorize_url: string;
                                        expires_at: number };
  const set = answer.headers.get("Set-Cookie")!;
  const binding = set.split(";")[0]!;
  const authorize = new URL(started.authorize_url);
  const callbackUrl = authorize.searchParams.get("callback_url");
  const state = callbackUrl === null ? authorize.searchParams.get("state")!
                                     : callbackUrl.split("/").at(-1)!;
  return { ...started, url: authorize, state, binding, set };
}

/** The provider's redirect back to a callback path, as the browser navigates it. */
function callback(path: string, query: string, cookies: readonly string[],
                  headers: Record<string, string> = NAVIGATE): Promise<Response> {
  return call(new Request(`${CONNECT_CALLBACK}${path}?${query}`, {
    headers: { ...headers, ...(cookies.length > 0 ? { Cookie: cookies.join("; ") } : {}) } }));
}

/** What the callback handed the app page in its fragment. */
function handed(response: Response): Record<string, string> {
  expect(response.status).toBe(302);
  const location = response.headers.get("Location")!;
  expect(location.startsWith(`${CONNECTED_PAGE}#`)).toBe(true);
  return Object.fromEntries(new URLSearchParams(location.split("#")[1]));
}

const TEST_CALLBACK = `${CONNECT_CALLBACK}/test_oauth`;
const ISS = "iss=https%3A%2F%2Fauth.test";

describe("an OAuth connect", () => {
  it("runs begin, the browser-bound callback and finish; the code reaches only the gateway",
     async () => {
    const app = await signedIn();
    const { environment, calls } = spied();
    const started = await begin(app, "test_oauth", environment);
    expect(`${started.url.origin}${started.url.pathname}`).toBe("https://auth.test/authorize");
    const params = Object.fromEntries(started.url.searchParams);
    expect(params).toMatchObject({ response_type: "code", client_id: "tinyassets-test",
                                   redirect_uri: TEST_CALLBACK, scope: "models.read",
                                   code_challenge_method: "S256", prompt: "consent" });
    expect(params["state"]).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(started.set).toMatch(/^__Host-ta-connect-[0-9a-f]{16}=[0-9a-f]{64}; Secure; HttpOnly; Path=\/; SameSite=Lax; Max-Age=(?:[5-9]\d\d|600)$/);
    expect(JSON.parse(calls[0]!.args)[1]).toMatchObject({
      kind: "oauth2", revocable: true, redirect_uri: TEST_CALLBACK });
    const back = await callback("/test_oauth", `code=good-code&state=${started.state}&${ISS}`,
                                [started.binding]);
    expect(back.headers.get("Referrer-Policy")).toBe("no-referrer");
    const given = handed(back);
    expect(given).toEqual({ state: started.state, code: "good-code",
                            redirect_uri: TEST_CALLBACK });
    const finished = await post(app, "connections/oauth/finish", given, environment,
                                [started.binding]);
    const text = await finished.text();
    expect(finished.status, text).toBe(200);
    const view = JSON.parse(text) as Record<string, unknown>;
    expect(view).toMatchObject({ connection: started.connection, source: "test_oauth",
                                 kind: "oauth2", status: "active", provider: "openai",
                                 provider_revocable: true });
    expect(view["expires_at"] as number).toBeGreaterThan(Date.now());
    expect(finished.headers.get("Set-Cookie"))
      .toBe(`${started.binding.split("=")[0]}=; Secure; HttpOnly; Path=/; SameSite=Lax; Max-Age=0`);
    expect(methods(calls)).toEqual(["connectionPrepare", "flowFinish", "connectionCommit"]);
    // The gateway exchanged with our PKCE verifier and the source's own redirect URI, then
    // checked the token on the sealed setup check.
    const sent = await received();
    expect(sent.map((request) => `${request.method} ${request.url}`))
      .toEqual(["POST https://auth.test/token", "GET https://api.openai.com/v1/models"]);
    const form = new URLSearchParams(sent[0]!.body);
    expect(form.get("redirect_uri")).toBe(TEST_CALLBACK);
    const digest = await crypto.subtle.digest("SHA-256",
                                              new TextEncoder().encode(form.get("code_verifier")!));
    expect(btoa(String.fromCharCode(...new Uint8Array(digest))).replace(/\+/g, "-")
      .replace(/\//g, "_").replace(/=+$/, "")).toBe(params["code_challenge"]);
    expect(sent[1]!.headers["authorization"]).toBe("Bearer at-good");
    for (const secret of ["good-code", "at-good", "rt-good"]) {
      await keptFromObject(app.owner, secret, calls, text);
    }
  });

  it("takes a hosted preset's key exchange the same way, its state in its callback's path",
     async () => {
    const app = await signedIn();
    const { environment, calls } = spied();
    const started = await begin(app, "openrouter_user_models_v1", environment);
    expect(`${started.url.origin}${started.url.pathname}`).toBe("https://openrouter.ai/auth");
    const params = Object.fromEntries(started.url.searchParams);
    expect(Object.keys(params).sort())
      .toEqual(["callback_url", "code_challenge", "code_challenge_method", "key_label"]);
    expect(params["key_label"]).toBe("TinyAssets");
    const own = `${CONNECT_CALLBACK}/openrouter_user_models_v1`;
    expect(params["callback_url"]).toBe(`${own}/${started.state}`);
    expect(JSON.parse(calls[0]!.args)[1]).toMatchObject({ revocable: false, redirect_uri: own });
    const back = await callback(`/openrouter_user_models_v1/${started.state}`, "code=or-good",
                                [started.binding]);
    const given = handed(back);
    expect(given).toEqual({ state: started.state, code: "or-good",
                            redirect_uri: `${own}/${started.state}` });
    const finished = await post(app, "connections/oauth/finish", given, environment,
                                [started.binding]);
    const text = await finished.text();
    expect(finished.status, text).toBe(200);
    expect(JSON.parse(text)).toMatchObject({ source: "openrouter_user_models_v1",
                                             kind: "oauth2", provider: "openrouter",
                                             status: "active", expires_at: null,
                                             provider_revocable: false });
    const sent = await received();
    expect(sent.map((request) => `${request.method} ${request.url}`))
      .toEqual(["POST https://openrouter.ai/api/v1/auth/keys",
                "GET https://openrouter.ai/api/v1/models/user"]);
    expect(JSON.parse(sent[0]!.body)).toMatchObject({ code: "or-good",
                                                      code_challenge_method: "S256" });
    expect(sent[1]!.headers["authorization"]).toBe("Bearer sk-or-bought");
    for (const secret of ["or-good", "sk-or-bought"]) {
      await keptFromObject(app.owner, secret, calls, text);
    }
  });

  it("answers a refused exchange with the gateway's fixed code, and holds no slot", async () => {
    const app = await signedIn();
    const started = await begin(app, "openrouter_user_models_v1");
    const finished = await post(app, "connections/oauth/finish", {
      state: started.state, code: "or-stolen",
      redirect_uri: `${CONNECT_CALLBACK}/openrouter_user_models_v1/${started.state}` },
                                doorsEnv, [started.binding]);
    expect(finished.status).toBe(502);
    expect(await finished.json()).toEqual({ error: "oauth_rejected" });
    expect(await flowRows(app.owner)).toEqual([{ status: "used" }]);
    expect(await connectionRows(app.owner)).toEqual([{ status: "revoked", revoke_due: null }]);
  });

  it("answers a provider key outside the secret grammar as oauth_malformed, never a 500",
     async () => {
    const app = await signedIn();
    const started = await begin(app, "openrouter_user_models_v1");
    const finished = await post(app, "connections/oauth/finish", {
      state: started.state, code: "or-badkey",
      redirect_uri: `${CONNECT_CALLBACK}/openrouter_user_models_v1/${started.state}` },
                                doorsEnv, [started.binding]);
    expect(finished.status).toBe(502);
    expect(await finished.json()).toEqual({ error: "oauth_malformed" });
  });

  it("refuses Sign in with ChatGPT loudly, unwired until OpenAI's approval", async () => {
    const app = await signedIn();
    const { environment, calls } = spied();
    const answer = await post(app, "connections/oauth", { source: "chatgpt" }, environment);
    expect(answer.status).toBe(501);
    expect((await answer.json<{ error: string }>()).error)
      .toMatch(/^the source is not connectable yet: .*OpenAI's approval/);
    expect(calls).toEqual([]);
  });

  it.each([
    ["a key-only source", { source: "groq" }, 400],
    ["a vendor CLI", { source: "codex" }, 400],
    ["an unknown source", { source: "nope" }, 404],
    ["an owner", { source: "test_oauth", owner: "bob" }, 400],
    ["a redirect URI", { source: "test_oauth", redirect_uri: "https://evil.test/cb" }, 400],
  ])("refuses to begin with %s, before any object call", async (_what, body, status) => {
    const app = await signedIn();
    const { environment, calls } = spied();
    expect((await post(app, "connections/oauth", body, environment)).status).toBe(status);
    expect(calls).toEqual([]);
  });

  it("refused past the open-flow limit leaves no pending connection behind", async () => {
    const app = await signedIn();
    const answers: number[] = [];
    for (let i = 0; i < 12; i++) {
      answers.push((await post(app, "connections/oauth", { source: "test_oauth" })).status);
    }
    expect(answers).toEqual([...Array<number>(10).fill(200), 403, 403]);
    expect(await connectionRows(app.owner)).toHaveLength(10);
  });
});

describe("a credential the provider issued whose setup check then fails", () => {
  function failingVerify(code: string): DoorsEnv {
    return { ...doorsEnv, GATEWAY: {
      ...doorsEnv.GATEWAY,
      seal: (...args: Parameters<DoorsEnv["GATEWAY"]["seal"]>) => doorsEnv.GATEWAY.seal(...args),
      exchange: (...args: Parameters<DoorsEnv["GATEWAY"]["exchange"]>) =>
        doorsEnv.GATEWAY.exchange(...args),
      verify: () => Promise.reject(new Error(code)) } };
  }

  it("is committed in error, its blob kept for the provider revoke", async () => {
    const app = await signedIn();
    const started = await begin(app, "test_oauth");
    const { environment, calls } = spied(failingVerify("upstream_timeout"));
    const finished = await post(app, "connections/oauth/finish", {
      state: started.state, code: "good-code", redirect_uri: TEST_CALLBACK },
                                environment, [started.binding]);
    expect(finished.status).toBe(502);
    expect(await finished.json()).toEqual({ error: "upstream_timeout" });
    expect(methods(calls)).toEqual(["flowFinish", "connectionCommit"]);
    expect(JSON.parse(calls[1]!.args)[1]).toMatchObject({ failed: "upstream_timeout",
                                                          models: [] });
    const [row] = await connectionRows(app.owner);
    expect(row!.status).toBe("error");
    expect(row!.revoke_due).not.toBeNull();
    expect(await blobRows(app.owner)).toHaveLength(1);
  });

  it("is never kept when it cannot be revoked, and the owner is told to delete it", async () => {
    const app = await signedIn();
    const started = await begin(app, "openrouter_user_models_v1");
    // A code the doors do not answer as the provider's is thrown as it came, loudly, but
    // only once the issued credential is accounted for.
    await expect(post(app, "connections/oauth/finish", {
      state: started.state, code: "or-good",
      redirect_uri: `${CONNECT_CALLBACK}/openrouter_user_models_v1/${started.state}` },
                      failingVerify("deadline"), [started.binding])).rejects.toThrow("deadline");
    expect(await connectionRows(app.owner)).toEqual([{ status: "error", revoke_due: null }]);
    expect(await blobRows(app.owner)).toEqual([]);
    expect((await noticeTypes(app.owner)).join("\n"))
      .toMatch(/connection_setup_failed: .*failed \(deadline\).*delete it at the provider/);
  });
});

describe("the connect callback", () => {
  it("refuses a request that is not a top-level navigation", async () => {
    const app = await signedIn();
    const started = await begin(app, "test_oauth");
    const answer = await callback("/test_oauth", `code=good-code&state=${started.state}&${ISS}`,
                                  [started.binding], {});
    expect(answer.status).toBe(403);
    const posted = await call(new Request(`${TEST_CALLBACK}?state=${started.state}`,
                                          { method: "POST", headers: { ...NAVIGATE } }));
    expect(posted.status).toBe(405);
  });

  it("refuses a browser without the flow's binding, or with another flow's", async () => {
    const app = await signedIn();
    const first = await begin(app, "test_oauth");
    const second = await begin(app, "test_oauth");
    const query = `code=good-code&state=${first.state}&${ISS}`;
    expect((await callback("/test_oauth", query, [])).status).toBe(403);
    expect((await callback("/test_oauth", query, [second.binding])).status).toBe(403);
    const forged = `${first.binding.split("=")[0]}=${"0".repeat(64)}`;
    expect((await callback("/test_oauth", query, [forged])).status).toBe(403);
    expect((await callback("/test_oauth", query, [first.binding.slice(0, -1)])).status)
      .toBe(403);
    expect((await callback("/test_oauth", query, [first.binding, first.binding])).status)
      .toBe(403);
    expect(handed(await callback("/test_oauth", query, [first.binding, second.binding])))
      .toEqual({ state: first.state, code: "good-code", redirect_uri: TEST_CALLBACK });
  });

  it("takes each source's own form only", async () => {
    const app = await signedIn();
    const oauth = await begin(app, "test_oauth");
    const kx = await begin(app, "openrouter_user_models_v1");
    const cookies = [oauth.binding, kx.binding];
    for (const [path, query] of [
      ["", `code=x&state=${oauth.state}&${ISS}`],
      ["/", `code=x&state=${oauth.state}&${ISS}`],
      ["/nope", `code=x&state=${oauth.state}`],
      ["/groq", `code=x&state=${oauth.state}`],
      ["/test_oauth", `code=x&${ISS}`],
      ["/test_oauth", `code=x&state=short&${ISS}`],
      // An RFC 6749 source never takes its state in the path.
      [`/test_oauth/${oauth.state}`, `code=x&${ISS}`],
      [`/test_oauth/${oauth.state}`, `code=x&state=${oauth.state}&${ISS}`],
      // A key-exchange source never takes it in the query, or with a trailing segment.
      ["/openrouter_user_models_v1", `code=x&state=${kx.state}`],
      [`/openrouter_user_models_v1/${kx.state}`, `code=x&state=${kx.state}`],
      [`/openrouter_user_models_v1/${kx.state}/`, "code=x"],
      [`/openrouter_user_models_v1/${kx.state}/x`, "code=x"],
    ] as const) {
      expect((await callback(path, query, cookies)).status, `${path}?${query}`).toBe(400);
    }
  });

  it("holds an RFC 9207 source to its issuer", async () => {
    const app = await signedIn();
    const started = await begin(app, "test_oauth");
    const base = `code=good-code&state=${started.state}`;
    expect((await callback("/test_oauth", base, [started.binding])).status).toBe(400);
    expect((await callback("/test_oauth", `${base}&iss=https%3A%2F%2Fevil.test`,
                           [started.binding])).status).toBe(400);
    expect(handed(await callback("/test_oauth", `${base}&${ISS}`, [started.binding])))
      .toMatchObject({ code: "good-code" });
  });

  it("hands the provider's refusal on as access_denied, never a code", async () => {
    const app = await signedIn();
    const started = await begin(app, "test_oauth");
    const base = `state=${started.state}&${ISS}`;
    const refusal = { state: started.state, error: "access_denied", redirect_uri: TEST_CALLBACK };
    expect(handed(await callback("/test_oauth", `error=access_denied&${base}`,
                                 [started.binding]))).toEqual(refusal);
    expect(handed(await callback("/test_oauth", `code=good-code&error=server_error&${base}`,
                                 [started.binding]))).toEqual(refusal);
    expect(handed(await callback("/test_oauth", `code=${encodeURIComponent("a b")}&${base}`,
                                 [started.binding]))).toEqual(refusal);
  });

  it("reaches no object", async () => {
    const app = await signedIn();
    const started = await begin(app, "test_oauth");
    const { environment, calls } = spied();
    const back = await call(new Request(
      `${TEST_CALLBACK}?code=good-code&state=${started.state}&${ISS}`,
      { headers: { ...NAVIGATE, Cookie: started.binding } }), environment);
    expect(back.status).toBe(302);
    expect(calls).toEqual([]);
  });
});

describe("a flow's finish", () => {
  async function begun(source = "test_oauth") {
    const app = await signedIn();
    const started = await begin(app, source);
    return { app, started, state: started.state,
             body: { state: started.state, code: "good-code", redirect_uri: TEST_CALLBACK } };
  }

  it("is refused without the flow's browser binding, before any object call", async () => {
    const { app, body } = await begun();
    const { environment, calls } = spied();
    const answer = await post(app, "connections/oauth/finish", body, environment);
    expect(answer.status).toBe(403);
    expect(calls).toEqual([]);
  });

  it("is used up: a replayed state is refused", async () => {
    const { app, started, body } = await begun();
    expect((await post(app, "connections/oauth/finish", body, doorsEnv,
                       [started.binding])).status).toBe(200);
    const replayed = await post(app, "connections/oauth/finish", body, doorsEnv,
                                [started.binding]);
    expect(replayed.status).toBe(403);
    expect(await replayed.json()).toEqual({
      error: "no open connection flow for this state in this session" });
    expect((await received()).filter((request) => request.url.includes("/token"))).toHaveLength(1);
  });

  it("is refused in another session of the same owner", async () => {
    const { app, started, body } = await begun();
    const other = await signedIn(app.owner);
    const answer = await post(other, "connections/oauth/finish", body, doorsEnv,
                              [started.binding]);
    expect(answer.status).toBe(403);
    expect(await answer.json()).toEqual({
      error: "no open connection flow for this state in this session" });
    expect(await received()).toEqual([]);
  });

  it("is refused when the flow has expired", async () => {
    const { app, started, body } = await begun();
    await runInDurableObject(env.OWNER.getByName(app.owner), (_instance: OwnerDO, storage) => {
      storage.storage.sql.exec("UPDATE connection_flows SET expires_at = ?", now() - 1);
    });
    const answer = await post(app, "connections/oauth/finish", body, doorsEnv,
                              [started.binding]);
    expect(answer.status).toBe(403);
    expect(await received()).toEqual([]);
  });

  it("is refused after sessionCloseAll, the flow cancelled", async () => {
    const { app, started, body } = await begun();
    await env.OWNER.getByName(app.owner).sessionCloseAll(
      { owner: app.owner, caller: app.owner, origin: null });
    expect(await flowRows(app.owner)).toEqual([{ status: "cancelled" }]);
    for (const who of [app, await signedIn(app.owner)]) {
      const answer = await post(who, "connections/oauth/finish", body, doorsEnv,
                                [started.binding]);
      expect(answer.status).toBe(403);
    }
    expect(await received()).toEqual([]);
  });

  it("is refused after the session that began it signs out", async () => {
    const { app, started, body } = await begun();
    const kept = await signedIn(app.owner);
    const kept2 = await begin(kept, "test_oauth");
    expect((await post(app, "session/close", {})).status).toBe(200);
    expect((await flowRows(app.owner)).map((row) => row.status).sort())
      .toEqual(["cancelled", "open"]);
    const fresh = await signedIn(app.owner);
    expect((await post(fresh, "connections/oauth/finish", body, doorsEnv,
                       [started.binding])).status).toBe(403);
    // The other session's flow stands.
    expect((await post(kept, "connections/oauth/finish",
                       { state: kept2.state, code: "good-code", redirect_uri: TEST_CALLBACK },
                       doorsEnv, [kept2.binding])).status).toBe(200);
  });

  it("does not use the flow up when the request never reaches the object", async () => {
    const { app, started, body } = await begun();
    const { environment, calls } = spied();
    expect((await post(app, "connections/oauth/finish", body, environment, [started.binding],
                       app.session, { Origin: "https://evil.test",
                                      "Sec-Fetch-Site": "cross-site" })).status).toBe(403);
    expect((await post(app, "connections/oauth/finish", body, environment, [started.binding],
                       app.session, { "Content-Type": "text/plain" })).status).toBe(415);
    expect((await post(app, "connections/oauth/finish",
                       JSON.stringify({ ...body, code: "x".repeat(70 * 1024) }), environment,
                       [started.binding])).status).toBe(413);
    expect(methods(calls)).toEqual(["flowFinish"]);
    expect(await flowRows(app.owner)).toEqual([{ status: "open" }]);
    expect((await post(app, "connections/oauth/finish", body, doorsEnv,
                       [started.binding])).status).toBe(200);
  });

  it("refuses a code that came back on another source's callback: the mix-up", async () => {
    // A flow begun for OpenRouter (a key exchange); provider H redirects with its own code
    // and that flow's state, which A's authorize URL showed it. The finish is refused, and
    // H's code never reaches OpenRouter's exchange endpoint.
    const { app, started, state } = await begun("openrouter_user_models_v1");
    for (const redirect_uri of [TEST_CALLBACK, CONNECT_CALLBACK,
                                `${CONNECT_CALLBACK}/openrouter_user_models_v1`]) {
      const fresh = redirect_uri === TEST_CALLBACK ? started
        : await begin(app, "openrouter_user_models_v1");
      const answer = await post(app, "connections/oauth/finish",
                                { state: fresh.state, code: "good-code", redirect_uri },
                                doorsEnv, [fresh.binding]);
      expect(answer.status).toBe(403);
      expect(await answer.json()).toEqual({
        error: "the authorization came back on another source's callback" });
    }
    expect(state).toBe(started.state);
    expect(await received()).toEqual([]);
    expect((await connectionRows(app.owner)).map((row) => row.status))
      .toEqual(["revoked", "revoked", "revoked"]);
  });

  it("re-checks the source is still connectable, before any exchange", async () => {
    const { app, started, body } = await begun();
    const paused = catalogOf([...bundledRecords(), { ...TEST_OAUTH, unwired: "paused" }]);
    const ctx = createExecutionContext();
    const answer = await serve(new Request(url("/app/api/connections/oauth/finish"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: ORIGIN,
                 "Sec-Fetch-Site": "same-origin", Authorization: `Bearer ${app.token}`,
                 Cookie: `__Host-ta-owner=${app.session}; ${started.binding}` },
      body: JSON.stringify(body) }), doorsEnv, ctx, FAKE_UPSTREAM, paused);
    expect(answer.status).toBe(501);
    expect(await received()).toEqual([]);
    expect(await connectionRows(app.owner)).toEqual([{ status: "revoked", revoke_due: null }]);
  });

  it.each([
    ["a forged connection", { connection: "c0123456789abcdef01234567" }],
    ["a forged version", { version: 2 }],
    ["an owner", { owner: "bob" }],
  ])("refuses %s in its body, before any object call", async (_what, extra) => {
    const { app, started, body } = await begun();
    const { environment, calls } = spied();
    const answer = await post(app, "connections/oauth/finish", { ...body, ...extra },
                              environment, [started.binding]);
    expect(answer.status).toBe(400);
    expect(calls).toEqual([]);
  });

  it("refuses a malformed state, code or redirect URI, before any object call", async () => {
    const { app, started, state, body } = await begun();
    const { environment, calls } = spied();
    for (const bad of [{ ...body, state: "short" }, { ...body, code: "a b" },
                       { ...body, code: 1 }, { state, code: "good-code" },
                       { ...body, redirect_uri: 1 }]) {
      expect((await post(app, "connections/oauth/finish", bad, environment,
                         [started.binding])).status).toBe(400);
    }
    expect(calls).toEqual([]);
  });
});

describe("list and revoke", () => {
  async function connected(app: App): Promise<string> {
    const answer = await post(app, "connections/key",
                              { source: "groq", key: `gsk-good-${crypto.randomUUID()}` });
    expect(answer.status).toBe(200);
    return (await answer.json<{ connection: string }>()).connection;
  }

  it("lists every connection and never a blob", async () => {
    const app = await signedIn();
    const connection = await connected(app);
    const { environment, calls } = spied();
    const answer = await post(app, "connections/list", {}, environment);
    const text = await answer.text();
    expect(answer.status).toBe(200);
    const listed = JSON.parse(text) as { connections: Record<string, unknown>[] };
    expect(listed.connections.map((row) => [row["connection"], row["status"]]))
      .toEqual([[connection, "active"]]);
    expect(text).not.toMatch(/"blob"|v1\.k1\./);
    expect(methods(calls)).toEqual(["connectionList"]);
  });

  it("revokes in the object, and the connection is unusable at once", async () => {
    const app = await signedIn();
    const connection = await connected(app);
    const { environment, calls } = spied();
    const answer = await post(app, "connections/revoke", { connection }, environment);
    expect(answer.status).toBe(200);
    expect(await answer.json()).toEqual({ connection, revoked: true, provider_revoke: false });
    expect(methods(calls)).toEqual(["connectionRevoke"]);
    const listed = await (await post(app, "connections/list", {})).json<{
      connections: { status: string }[] }>();
    expect(listed.connections.map((row) => row.status)).toEqual(["revoked"]);
    expect(await storageOf(app.owner)).not.toMatch(/v1\.k1\./);
  });

  it("tells the truth for a key exchange: no provider revoke, and the owner is told", async () => {
    const app = await signedIn();
    const started = await begin(app, "openrouter_user_models_v1");
    const finished = await post(app, "connections/oauth/finish", {
      state: started.state, code: "or-good",
      redirect_uri: `${CONNECT_CALLBACK}/openrouter_user_models_v1/${started.state}` },
                                doorsEnv, [started.binding]);
    const { connection } = await finished.json<{ connection: string }>();
    const revoked = await post(app, "connections/revoke", { connection });
    expect(await revoked.json()).toEqual({ connection, revoked: true, provider_revoke: false });
    const listed = await (await post(app, "connections/list", {})).json<{
      connections: Record<string, unknown>[] }>();
    expect(listed.connections[0]).toMatchObject({ status: "revoked", provider_revocable: false,
                                                  provider_revoke_pending: false });
    expect(await blobRows(app.owner)).toEqual([]);
    expect((await noticeTypes(app.owner)).join("\n"))
      .toMatch(/connection_revoke_unsupported: .*delete it at the provider/);
  });

  it("refuses another owner's connection, which it never reaches", async () => {
    const alice = await signedIn();
    const connection = await connected(alice);
    const bob = await signedIn();
    const answer = await post(bob, "connections/revoke", { connection });
    expect(answer.status).toBe(403);
    expect(await answer.json()).toEqual({ error: "no such connection" });
    const listed = await (await post(alice, "connections/list", {})).json<{
      connections: { status: string }[] }>();
    expect(listed.connections.map((row) => row.status)).toEqual(["active"]);
  });

  it.each([
    ["connections/revoke", { connection: "c0123456789abcdef01234567", owner: "bob" }],
    ["connections/revoke", {}],
    ["connections/list", { owner: "bob" }],
  ])("refuses a body %s does not take, before any object call", async (path, body) => {
    const app = await signedIn();
    const { environment, calls } = spied();
    expect((await post(app, path, body, environment)).status).toBe(400);
    expect(calls).toEqual([]);
  });

  it.each([123, { a: 1 }, "../x"])("refuses a connection id %j as invalid", async (connection) => {
    const app = await signedIn();
    expect((await post(app, "connections/revoke", { connection })).status).toBe(400);
  });

  it("needs the live session for both", async () => {
    const app = await signedIn();
    for (const [path, body] of [["connections/list", {}],
                                ["connections/revoke", { connection: "c0123456789abcdef01234567" }]] as const) {
      const answer = await post(app, path, body, doorsEnv, [], null);
      expect(answer.status).toBe(403);
      expect(await answer.json()).toEqual({ error: "interactive owner session required" });
    }
  });
});
