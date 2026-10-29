/**
 * The connect doors (gateway research §7.2, §7.3; tasks 4 G3), in workerd, through the
 * real gateway Worker (its `ConnectGateway` entrypoint, as the config binds it) and the
 * real owner object, against fake providers.
 *
 * The rule each test holds: a pasted key or an OAuth code reaches the gateway only, never
 * an argument of any call on the owner's object (a spy on the stub), its storage, a log, an
 * error or an answer; every connect door is the owner's own, in its live session, and
 * refuses an outside credential before any object call; a flow finishes only in the
 * browser and the session that began it, once, while it lasts.
 */
import { env, runInDurableObject } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OwnerDO } from "../../owner/src/owner-do";
import { CONNECT_CALLBACK, CONNECTED_PAGE, type DoorsEnv, now } from "../src/config";
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
function spied(): { environment: DoorsEnv; calls: Call[] } {
  const calls: Call[] = [];
  const real = doorsEnv.OWNER;
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
  return { environment: { ...doorsEnv, OWNER }, calls };
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
              token = app.token): Promise<Response> {
  const jar = [...(session === null ? [] : [`__Host-ta-owner=${session}`]), ...cookies];
  return call(new Request(url(`/app/api/${path}`), {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: ORIGIN, "Sec-Fetch-Site": "same-origin",
               Authorization: `Bearer ${token}`, ...(jar.length > 0 ? { Cookie: jar.join("; ") } : {}) },
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

function flowRows(owner: string): Promise<{ status: string }[]> {
  return runInDurableObject(env.OWNER.getByName(owner), (_instance: OwnerDO, state) =>
    state.storage.sql.exec<{ status: string }>("SELECT status FROM connection_flows").toArray());
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

/** The key reached nothing but the gateway and its provider. */
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
                                 expires_at: null });
    expect((view["models"] as string[]).length).toBeGreaterThan(0);
    expect(view).not.toHaveProperty("blob");
    expect(calls.map((made) => made.method)).toEqual(["connectionPrepare", "connectionCommit"]);
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

  it("refused by its provider is the gateway's fixed code, and nothing commits", async () => {
    const app = await signedIn();
    const key = `gsk-bad-${crypto.randomUUID()}`;
    const { environment, calls } = spied();
    const answer = await post(app, "connections/key", { source: "groq", key }, environment);
    const text = await answer.text();
    expect(answer.status).toBe(502);
    expect(JSON.parse(text)).toEqual({ error: "upstream_unauthorized" });
    expect(calls.map((made) => made.method)).toEqual(["connectionPrepare"]);
    await keptFromObject(app.owner, key, calls, text);
    const listed = await (await post(app, "connections/list", {})).json<{
      connections: { status: string }[] }>();
    expect(listed.connections.map((row) => row.status)).toEqual(["pending"]);
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
  });

  it("throws any other gateway failure as it came: a fault here or in the config", async () => {
    const app = await signedIn();
    for (const message of ["keyring_unavailable", "internal", "Network connection lost."]) {
      const failing = { ...doorsEnv, GATEWAY: { ...doorsEnv.GATEWAY,
                                                seal: () => Promise.reject(new Error(message)) } };
      await expect(post(app, "connections/key", { source: "groq", key: "gsk-good-5" }, failing))
        .rejects.toThrow(message);
    }
  });

  it.each([
    ["no key", { source: "groq" }, 400],
    ["a key that is not text", { source: "groq", key: 7 }, 400],
    ["an empty key", { source: "groq", key: "" }, 400],
    ["a key past the gateway's bound", { source: "groq", key: `gsk-good-${"x".repeat(16 * 1024)}` }, 400],
    ["an owner", { source: "groq", key: "gsk-good-1", owner: "bob" }, 400],
    ["a connection", { source: "groq", key: "gsk-good-1", connection: "c0123456789abcdef01234567" }, 400],
    ["a version", { source: "groq", key: "gsk-good-1", version: 2 }, 400],
    ["a display name that is not text", { source: "groq", key: "gsk-good-1", display_name: 1 }, 400],
    ["an unknown source", { source: "nope", key: "gsk-good-1" }, 404],
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
    expect(calls.map((made) => made.method)).toEqual(["connectionPrepare"]);
    expect(await received()).toEqual([]);
  });
});

describe("an outside credential", () => {
  const ROUTES: readonly [string, unknown][] = [
    ["connections/key", { source: "groq", key: "gsk-good-3" }],
    ["connections/oauth", { source: "test_oauth" }],
    ["connections/oauth/finish", { state: "s".repeat(43), code: "good-code" }],
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

/** The begin of an OAuth flow for `source`: its answer, URL and binding cookie. */
async function begin(app: App, source: string, environment: DoorsEnv = doorsEnv) {
  const answer = await post(app, "connections/oauth", { source }, environment);
  const text = await answer.text();
  expect(answer.status, text).toBe(200);
  const started = JSON.parse(text) as { connection: string; authorize_url: string;
                                        expires_at: number };
  const set = answer.headers.get("Set-Cookie")!;
  const binding = set.split(";")[0]!;
  return { ...started, url: new URL(started.authorize_url), binding, set };
}

/** The provider's redirect back to the callback, as the browser navigates it. */
function callback(query: string, cookies: readonly string[], path = "",
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

describe("an OAuth connect", () => {
  it("runs begin, the browser-bound callback and finish; the code reaches only the gateway",
     async () => {
    const app = await signedIn();
    const { environment, calls } = spied();
    const started = await begin(app, "test_oauth", environment);
    expect(`${started.url.origin}${started.url.pathname}`).toBe("https://auth.test/authorize");
    const params = Object.fromEntries(started.url.searchParams);
    expect(params).toMatchObject({ response_type: "code", client_id: "tinyassets-test",
                                   redirect_uri: CONNECT_CALLBACK, scope: "models.read",
                                   code_challenge_method: "S256", prompt: "consent" });
    expect(params["state"]).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(started.set).toMatch(/^__Host-ta-connect-[0-9a-f]{16}=[0-9a-f]{64}; Secure; HttpOnly; Path=\/; SameSite=Lax; Max-Age=(?:[5-9]\d\d|600)$/);
    const back = await callback(`code=good-code&state=${params["state"]}`, [started.binding]);
    expect(back.headers.get("Referrer-Policy")).toBe("no-referrer");
    expect(handed(back)).toEqual({ state: params["state"], code: "good-code" });
    const finished = await post(app, "connections/oauth/finish",
                                { state: params["state"], code: "good-code" }, environment,
                                [started.binding]);
    const text = await finished.text();
    expect(finished.status, text).toBe(200);
    const view = JSON.parse(text) as Record<string, unknown>;
    expect(view).toMatchObject({ connection: started.connection, source: "test_oauth",
                                 kind: "oauth2", status: "active", provider: "openai" });
    expect(view["expires_at"] as number).toBeGreaterThan(Date.now());
    expect(finished.headers.get("Set-Cookie"))
      .toBe(`${started.binding.split("=")[0]}=; Secure; HttpOnly; Path=/; SameSite=Lax; Max-Age=0`);
    expect(calls.map((made) => made.method))
      .toEqual(["connectionPrepare", "flowBegin", "flowFinish", "connectionCommit"]);
    // The gateway exchanged with our PKCE verifier and the exact redirect URI, then checked
    // the token on the sealed setup check.
    const sent = await received();
    expect(sent.map((request) => `${request.method} ${request.url}`))
      .toEqual(["POST https://auth.test/token", "GET https://api.openai.com/v1/models"]);
    const form = new URLSearchParams(sent[0]!.body);
    expect(form.get("redirect_uri")).toBe(CONNECT_CALLBACK);
    const digest = await crypto.subtle.digest("SHA-256",
                                              new TextEncoder().encode(form.get("code_verifier")!));
    expect(btoa(String.fromCharCode(...new Uint8Array(digest))).replace(/\+/g, "-")
      .replace(/\//g, "_").replace(/=+$/, "")).toBe(params["code_challenge"]);
    expect(sent[1]!.headers["authorization"]).toBe("Bearer at-good");
    for (const secret of ["good-code", "at-good", "rt-good"]) {
      await keptFromObject(app.owner, secret, calls, text);
    }
  });

  it("takes a hosted preset's key exchange the same way, its state in the callback's path",
     async () => {
    const app = await signedIn();
    const { environment, calls } = spied();
    const started = await begin(app, "openrouter_user_models_v1", environment);
    expect(`${started.url.origin}${started.url.pathname}`).toBe("https://openrouter.ai/auth");
    const params = Object.fromEntries(started.url.searchParams);
    expect(Object.keys(params).sort())
      .toEqual(["callback_url", "code_challenge", "code_challenge_method", "key_label"]);
    expect(params["key_label"]).toBe("TinyAssets");
    const state = params["callback_url"]!.slice(`${CONNECT_CALLBACK}/`.length);
    expect(params["callback_url"]).toBe(`${CONNECT_CALLBACK}/${state}`);
    const back = await callback("code=or-good", [started.binding], `/${state}`);
    expect(handed(back)).toEqual({ state, code: "or-good" });
    const finished = await post(app, "connections/oauth/finish", { state, code: "or-good" },
                                environment, [started.binding]);
    const text = await finished.text();
    expect(finished.status, text).toBe(200);
    expect(JSON.parse(text)).toMatchObject({ source: "openrouter_user_models_v1",
                                             kind: "oauth2", provider: "openrouter",
                                             status: "active", expires_at: null });
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

  it("answers a refused exchange with the gateway's fixed code, and commits nothing", async () => {
    const app = await signedIn();
    const started = await begin(app, "openrouter_user_models_v1");
    const state = started.url.searchParams.get("callback_url")!.split("/").at(-1)!;
    const finished = await post(app, "connections/oauth/finish", { state, code: "or-stolen" },
                                doorsEnv, [started.binding]);
    expect(finished.status).toBe(502);
    expect(await finished.json()).toEqual({ error: "oauth_rejected" });
    expect(await flowRows(app.owner)).toEqual([{ status: "used" }]);
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
});

describe("the connect callback", () => {
  it("refuses a request that is not a top-level navigation", async () => {
    const app = await signedIn();
    const started = await begin(app, "test_oauth");
    const state = started.url.searchParams.get("state")!;
    const answer = await callback(`code=good-code&state=${state}`, [started.binding], "", {});
    expect(answer.status).toBe(403);
    const posted = await call(new Request(`${CONNECT_CALLBACK}?state=${state}`,
                                          { method: "POST", headers: { ...NAVIGATE } }));
    expect(posted.status).toBe(405);
  });

  it("refuses a browser without the flow's binding, or with another flow's", async () => {
    const app = await signedIn();
    const first = await begin(app, "test_oauth");
    const second = await begin(app, "test_oauth");
    const state = first.url.searchParams.get("state")!;
    expect((await callback(`code=good-code&state=${state}`, [])).status).toBe(403);
    expect((await callback(`code=good-code&state=${state}`, [second.binding])).status).toBe(403);
    const forged = `${first.binding.split("=")[0]}=${"0".repeat(64)}`;
    expect((await callback(`code=good-code&state=${state}`, [forged])).status).toBe(403);
    const short = `${first.binding.slice(0, -1)}`;
    expect((await callback(`code=good-code&state=${state}`, [short])).status).toBe(403);
    expect((await callback(`code=good-code&state=${state}`, [first.binding, first.binding]))
      .status).toBe(403);
    expect(handed(await callback(`code=good-code&state=${state}`, [first.binding, second.binding])))
      .toEqual({ state, code: "good-code" });
  });

  it("refuses what is not a flow's state", async () => {
    const app = await signedIn();
    const started = await begin(app, "test_oauth");
    const state = started.url.searchParams.get("state")!;
    for (const [query, path] of [["code=good-code", ""], ["code=x&state=short", ""],
                                 [`code=x&state=${state}`, `/${state}`],
                                 ["code=x", "/a/b"]] as const) {
      expect((await callback(query, [started.binding], path)).status).toBe(400);
    }
  });

  it("hands the provider's refusal on as access_denied, never a code", async () => {
    const app = await signedIn();
    const started = await begin(app, "test_oauth");
    const state = started.url.searchParams.get("state")!;
    expect(handed(await callback(`error=access_denied&state=${state}`, [started.binding])))
      .toEqual({ state, error: "access_denied" });
    expect(handed(await callback(`code=good-code&error=server_error&state=${state}`,
                                 [started.binding]))).toEqual({ state, error: "access_denied" });
    expect(handed(await callback(`code=${encodeURIComponent("a b")}&state=${state}`,
                                 [started.binding]))).toEqual({ state, error: "access_denied" });
  });

  it("reaches no object", async () => {
    const app = await signedIn();
    const started = await begin(app, "test_oauth");
    const { environment, calls } = spied();
    const back = await call(new Request(
      `${CONNECT_CALLBACK}?code=good-code&state=${started.url.searchParams.get("state")}`,
      { headers: { ...NAVIGATE, Cookie: started.binding } }), environment);
    expect(back.status).toBe(302);
    expect(calls).toEqual([]);
  });
});

describe("a flow's finish", () => {
  async function begun() {
    const app = await signedIn();
    const started = await begin(app, "test_oauth");
    return { app, started, state: started.url.searchParams.get("state")! };
  }

  it("is refused without the flow's browser binding, before any object call", async () => {
    const { app, state } = await begun();
    const { environment, calls } = spied();
    const answer = await post(app, "connections/oauth/finish", { state, code: "good-code" },
                              environment);
    expect(answer.status).toBe(403);
    expect(calls).toEqual([]);
  });

  it("is used up: a replayed state is refused", async () => {
    const { app, started, state } = await begun();
    expect((await post(app, "connections/oauth/finish", { state, code: "good-code" }, doorsEnv,
                       [started.binding])).status).toBe(200);
    const replayed = await post(app, "connections/oauth/finish", { state, code: "good-code" },
                                doorsEnv, [started.binding]);
    expect(replayed.status).toBe(403);
    expect(await replayed.json()).toEqual({
      error: "no open connection flow for this state in this session" });
    expect((await received()).filter((request) => request.url.includes("/token"))).toHaveLength(1);
  });

  it("is refused in another session of the same owner", async () => {
    const { app, started, state } = await begun();
    const other = await signedIn(app.owner);
    const answer = await post(other, "connections/oauth/finish", { state, code: "good-code" },
                              doorsEnv, [started.binding]);
    expect(answer.status).toBe(403);
    expect(await answer.json()).toEqual({
      error: "no open connection flow for this state in this session" });
    expect(await received()).toEqual([]);
  });

  it("is refused when the flow has expired", async () => {
    const { app, started, state } = await begun();
    await runInDurableObject(env.OWNER.getByName(app.owner), (_instance: OwnerDO, storage) => {
      storage.storage.sql.exec("UPDATE connection_flows SET expires_at = ?", now() - 1);
    });
    const answer = await post(app, "connections/oauth/finish", { state, code: "good-code" },
                              doorsEnv, [started.binding]);
    expect(answer.status).toBe(403);
    expect(await received()).toEqual([]);
  });

  it("is refused after sessionCloseAll, the flow cancelled", async () => {
    const { app, started, state } = await begun();
    await env.OWNER.getByName(app.owner).sessionCloseAll(
      { owner: app.owner, caller: app.owner, origin: null });
    expect(await flowRows(app.owner)).toEqual([{ status: "cancelled" }]);
    for (const who of [app, await signedIn(app.owner)]) {
      const answer = await post(who, "connections/oauth/finish", { state, code: "good-code" },
                                doorsEnv, [started.binding]);
      expect(answer.status).toBe(403);
    }
    expect(await received()).toEqual([]);
  });

  it("is refused after the session that began it signs out", async () => {
    const { app, started, state } = await begun();
    const kept = await signedIn(app.owner);
    const kept2 = await begin(kept, "test_oauth");
    expect((await post(app, "session/close", {})).status).toBe(200);
    expect((await flowRows(app.owner)).map((row) => row.status).sort())
      .toEqual(["cancelled", "open"]);
    const fresh = await signedIn(app.owner);
    expect((await post(fresh, "connections/oauth/finish", { state, code: "good-code" }, doorsEnv,
                       [started.binding])).status).toBe(403);
    // The other session's flow stands.
    expect((await post(kept, "connections/oauth/finish",
                       { state: kept2.url.searchParams.get("state"), code: "good-code" },
                       doorsEnv, [kept2.binding])).status).toBe(200);
  });

  it.each([
    ["a forged connection", { connection: "c0123456789abcdef01234567" }],
    ["a forged version", { version: 2 }],
    ["an owner", { owner: "bob" }],
  ])("refuses %s in its body, before any object call", async (_what, extra) => {
    const { app, started, state } = await begun();
    const { environment, calls } = spied();
    const answer = await post(app, "connections/oauth/finish",
                              { state, code: "good-code", ...extra }, environment,
                              [started.binding]);
    expect(answer.status).toBe(400);
    expect(calls).toEqual([]);
  });

  it("refuses a malformed state or code, before any object call", async () => {
    const { app, started, state } = await begun();
    const { environment, calls } = spied();
    for (const body of [{ state: "short", code: "good-code" }, { state, code: "a b" },
                        { state, code: 1 }, { state }]) {
      expect((await post(app, "connections/oauth/finish", body, environment,
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
    expect(calls.map((made) => made.method)).toEqual(["connectionList"]);
  });

  it("revokes in the object, and the connection is unusable at once", async () => {
    const app = await signedIn();
    const connection = await connected(app);
    const { environment, calls } = spied();
    const answer = await post(app, "connections/revoke", { connection }, environment);
    expect(answer.status).toBe(200);
    expect(await answer.json()).toEqual({ connection, revoked: true, provider_revoke: false });
    expect(calls.map((made) => made.method)).toEqual(["connectionRevoke"]);
    const listed = await (await post(app, "connections/list", {})).json<{
      connections: { status: string }[] }>();
    expect(listed.connections.map((row) => row.status)).toEqual(["revoked"]);
    expect(await storageOf(app.owner)).not.toMatch(/v1\.k1\./);
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
