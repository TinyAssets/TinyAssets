/**
 * The outside world of the doors' tests: every global `fetch` the doors and gateway Workers
 * make is routed here (vitest.config.ts), and the tests reach it as `PROVIDERS`. Each
 * request but the sign-in's is logged, read back from `https://status.test/log`, so a test
 * sees what the gateway sent where.
 *
 * - The owner's sign-in, AuthKit's User Management as `UPSTREAM` names it (`api.workos.com`
 *   and the issuer `upstream.test`, which the browser alone visits): its token endpoint
 *   (`/user_management/authenticate`) takes a code `signInCode` made, checks its PKCE
 *   verifier against the code's challenge and that a client secret was sent, and answers an
 *   RS256 access token for the code's subject, signed under a key the fake makes once, with
 *   `auth_time` the code's (none when it is null); `upstream.test/oauth2/jwks` names that
 *   key. Its identity deletion (`DELETE /user_management/users/<sub>`) answers `204`, or
 *   `503` once for a subject armed by `POST https://status.test/fail-forget?sub=<sub>`; the
 *   statuses it answered a subject are read back from `https://status.test/forgotten?sub=`.
 * - A client's metadata document (CIMD), as its host serves it: a test publishes one at its
 *   URL (`POST https://status.test/document?url=<url>`, the document as the body), and a
 *   `GET` of that URL answers it.

 * - Groq's model list (`api.groq.com/openai/v1/models`): `200` for a bearer key starting
 *   `gsk-good`, else `401` with a body that quotes the key.
 * - Google's (`generativelanguage.googleapis.com/v1beta/models`): `200` for an
 *   `x-goog-api-key` starting `AIza-good`.
 * - OpenRouter's key exchange (`openrouter.ai/api/v1/auth/keys`): JSON in; the code
 *   `or-good` buys `sk-or-bought`, `or-badkey` a key with a space; any other is `403`. Its model list: `200` for a bearer
 *   key starting `sk-or-`, one model scored (the strength read); its key's limits
 *   (`openrouter.ai/api/v1/key`) the free tier for one starting `sk-or-free`, no limit for
 *   any other starting `sk-or-`.
 * - A test OAuth server (`auth.test/token`): the code `good-code` gets `at-good`, any
 *   other `invalid_grant`. OpenAI's model list (`api.openai.com/v1/models`): `200` for
 *   the bearer `at-good`.
 * - Groq's chat completions (`api.groq.com/openai/v1/chat/completions`), for the egress
 *   door: `401` but for a bearer key starting `gsk-good`; else, by its `user-agent` (the one
 *   free-text header the gateway passes on every call, `egressHeaders`), three
 *   SSE chunks (`stream`, the default, `EGRESS_CHUNKS`), the same without the usage chunk
 *   (`nousage`), the same from a dated snapshot of the named model (`snapshot`), a `429`
 *   (`busy`), or one chunk and
 *   then a stream held
 *   open, an SSE comment every 50 ms for at most 10 s, until its reader cancels it
 *   (`slow-<id>`; the cancel is recorded, read back from
 *   `https://status.test/cancelled?id=<id>`).
 *
 * It is test code: its module state (the log, the signing key, the deletions) is the fake's
 * own.
 */

/** The owner issuer the fake AuthKit stands for; the browser alone visits it. */
const ISSUER = "https://upstream.test";
/**
 * The doors' sign-in bindings whose endpoints the fake AuthKit below answers, as the deploy
 * kit sets them: the issuer and its `UPSTREAM` record (`contract/src/signin.ts`). The doors'
 * suite and the browser test configure the doors with them.
 */
export const AUTHKIT = Object.freeze({
  OWNER_ISSUER: ISSUER,
  UPSTREAM: JSON.stringify({
    client_id: "client_fake",
    authorization_endpoint: `${ISSUER}/authorize`,
    token_endpoint: "https://api.workos.com/user_management/authenticate",
    jwks_uri: `${ISSUER}/oauth2/jwks`,
    token_issuer: "https://api.workos.com/user_management/client_fake",
    user_delete: "https://api.workos.com/user_management/users/{sub}",
    authorize_params: { provider: "authkit" },
  }),
});

const log: unknown[] = [];
const cancelled = new Set<string>();
/** The client metadata documents a test published, by URL. */
const documents = new Map<string, unknown>();

/** A sign-in the fake AuthKit's code stands for: who, when (null: no `auth_time`), PKCE. */
export interface SignInCode {
  readonly subject: string;
  readonly authenticated_at: number | null;
  readonly challenge: string;
}

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_")
    .replaceAll("=", "");
}

function text64(value: unknown): string {
  return base64url(new TextEncoder().encode(JSON.stringify(value)));
}

/** The code the fake AuthKit issues for this sign-in, as its authorize page would. */
export function signInCode(code: SignInCode): string {
  return text64(code);
}

/** The fake issuer's one signing key, made at its first use, and its public JWK. */
let signing: Promise<{ key: CryptoKey; jwk: JsonWebKey }> | null = null;
const KID = "fake-authkit-1";

function signingKey(): Promise<{ key: CryptoKey; jwk: JsonWebKey }> {
  signing ??= (async () => {
    const pair = await crypto.subtle.generateKey(
      { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
        publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
      true, ["sign", "verify"]) as CryptoKeyPair;
    return { key: pair.privateKey,
             jwk: await crypto.subtle.exportKey("jwk", pair.publicKey) as JsonWebKey };
  })();
  return signing;
}

/** The subjects armed to fail their next deletion, and the statuses each one's were answered. */
const failForget = new Set<string>();
const forgotten = new Map<string, number[]>();

async function authenticate(request: Request): Promise<Response> {
  const body = await request.json<Record<string, string>>();
  let code: SignInCode;
  try {
    const bytes = Uint8Array.from(atob(body["code"]!.replaceAll("-", "+").replaceAll("_", "/")),
                                  (char) => char.charCodeAt(0));
    code = JSON.parse(new TextDecoder().decode(bytes)) as SignInCode;
  } catch {
    return Response.json({ error: "invalid_grant" }, { status: 400 });
  }
  const digest = new Uint8Array(await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(body["code_verifier"] ?? "")));
  if (body["grant_type"] !== "authorization_code" || !body["client_secret"]
      || base64url(digest) !== code.challenge) {
    return Response.json({ error: "invalid_grant" }, { status: 400 });
  }
  const { key } = await signingKey();
  const at = Math.floor(Date.now() / 1000);
  const claims = { iss: `https://api.workos.com/user_management/${body["client_id"]}`,
                   client_id: body["client_id"], sub: code.subject, sid: "session_fake",
                   iat: at, exp: at + 300,
                   ...(code.authenticated_at === null ? {} : { auth_time: code.authenticated_at }) };
  const head = `${text64({ alg: "RS256", kid: KID })}.${text64(claims)}`;
  const signature = new Uint8Array(await crypto.subtle.sign(
    "RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(head)));
  return Response.json({ access_token: `${head}.${base64url(signature)}`,
                         user: { id: code.subject } });
}

async function authKit(request: Request, url: URL): Promise<Response | null> {
  if (url.hostname === "api.workos.com" && url.pathname === "/user_management/authenticate") {
    return authenticate(request);
  }
  if (url.hostname === "upstream.test" && url.pathname === "/oauth2/jwks") {
    const { jwk } = await signingKey();
    return Response.json({ keys: [{ ...jwk, kid: KID, use: "sig", alg: "RS256" }] });
  }
  const deleted = /^\/user_management\/users\/([^/]+)$/.exec(url.pathname);
  if (url.hostname === "api.workos.com" && request.method === "DELETE" && deleted !== null) {
    const subject = decodeURIComponent(deleted[1]!);
    const status = failForget.delete(subject) ? 503 : 204;
    forgotten.set(subject, [...forgotten.get(subject) ?? [], status]);
    return new Response(null, { status });
  }
  if (url.hostname === "status.test" && url.pathname === "/fail-forget") {
    failForget.add(url.searchParams.get("sub") ?? "");
    return new Response(null, { status: 204 });
  }
  if (url.hostname === "status.test" && url.pathname === "/forgotten") {
    return Response.json(forgotten.get(url.searchParams.get("sub") ?? "") ?? []);
  }
  return null;
}

/** The usage the egress fake's stream reports, as OpenAI's chat completions report it. */
export const EGRESS_USAGE = Object.freeze({ prompt_tokens: 1000, completion_tokens: 500,
                                           total_tokens: 1500 });

function chunk(data: unknown): string {
  return `data: ${JSON.stringify(data)}\n\n`;
}

/** What the egress fake streams, in order: OpenAI's chat completions stream, with usage. */
export const EGRESS_CHUNKS: readonly string[] = Object.freeze([
  chunk({ id: "c1", object: "chat.completion.chunk", choices: [
    { index: 0, delta: { role: "assistant", content: "one " }, finish_reason: null }] }),
  chunk({ id: "c1", object: "chat.completion.chunk", choices: [
    { index: 0, delta: { content: "two" }, finish_reason: "stop" }] }),
  chunk({ id: "c1", object: "chat.completion.chunk", choices: [], usage: EGRESS_USAGE }),
  "data: [DONE]\n\n",
]);

async function completions(request: Request): Promise<Response> {
  if (!bearer(request).startsWith("gsk-good")) {
    return Response.json({ error: { message: `invalid key ${bearer(request)}` } }, { status: 401 });
  }
  const mode = request.headers.get("user-agent") ?? "stream";
  if (mode === "busy") {
    return Response.json({ error: { message: "rate limited" } },
                         { status: 429, headers: { "retry-after": "7" } });
  }
  const encoder = new TextEncoder();
  const headers = { "content-type": "text/event-stream", "x-upstream": "fake" };
  if (mode === "snapshot") {
    // Answered by a dated snapshot of the model the call named, as providers often answer.
    const { model } = await request.json<{ model: string }>();
    return new Response(EGRESS_CHUNKS.map((chunk) => chunk.replace(
      /^data: \{"id":"c1",/, `data: {"id":"c1","model":"${model}-2026-01-01",`)).join(""),
                        { headers });
  }
  if (mode === "nousage") {
    // What a provider streams when the call did not ask for its usage report.
    return new Response(EGRESS_CHUNKS.filter((chunk) => !chunk.includes("\"usage\"")).join(""),
                        { headers });
  }
  if (mode.startsWith("slow-")) {
    const id = mode.slice("slow-".length);
    // A comment every 50 ms keeps the stream open without ending it (a stream with nothing
    // pending would be cut by workerd's hang detection), for at most 10 s.
    let timer: ReturnType<typeof setInterval> | undefined;
    return new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(EGRESS_CHUNKS[0]!));
        let ticks = 0;
        timer = setInterval(() => {
          if (++ticks > 200) {
            clearInterval(timer);
            controller.close();
            return;
          }
          controller.enqueue(encoder.encode(": ping\n\n"));
        }, 50);
      },
      cancel() {
        clearInterval(timer);
        cancelled.add(id);
      },
    }), { headers });
  }
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of EGRESS_CHUNKS) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  }), { headers });
}

function bearer(request: Request): string {
  return (request.headers.get("authorization") ?? "").replace(/^Bearer /, "");
}

function listed(ok: boolean, credential: string): Response {
  return ok ? Response.json({ data: [] })
    : Response.json({ error: `invalid key ${credential}` }, { status: 401 });
}

async function token(request: Request): Promise<Response> {
  const form = new URLSearchParams(await request.text());
  if (form.get("grant_type") !== "authorization_code" || form.get("code") !== "good-code"
      || form.get("code_verifier") === null || form.get("client_id") !== "tinyassets-test") {
    return Response.json({ error: "invalid_grant",
                           error_description: `code ${form.get("code")} is not valid` },
                         { status: 400 });
  }
  return Response.json({ access_token: "at-good", token_type: "Bearer", expires_in: 3600,
                         refresh_token: "rt-good", scope: "models.read" });
}

async function keyExchange(request: Request): Promise<Response> {
  const body = JSON.parse(await request.text()) as Record<string, string>;
  if (body["code"] === "or-badkey") {
    return Response.json({ key: "sk-or-has a space" });
  }
  if (body["code"] !== "or-good" || body["code_challenge_method"] !== "S256"
      || typeof body["code_verifier"] !== "string") {
    return Response.json({ error: { message: `code ${body["code"]} is not valid` } },
                         { status: 403 });
  }
  return Response.json({ key: "sk-or-bought", user_id: "u1" });
}

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const signedIn = await authKit(request, url);
    if (signedIn !== null) {
      return signedIn;
    }
    if (url.hostname === "status.test") {
      if (url.pathname === "/document") {
        documents.set(url.searchParams.get("url") ?? "", await request.json());
        return new Response(null, { status: 204 });
      }
      if (url.pathname === "/cancelled") {
        return Response.json({ cancelled: cancelled.has(url.searchParams.get("id") ?? "") });
      }
      if (request.method === "DELETE") {
        log.length = 0;
        return new Response(null, { status: 204 });
      }
      return Response.json(log);
    }
    if (request.method === "GET" && documents.has(request.url)) {
      return Response.json(documents.get(request.url));
    }
    const body = request.method === "GET" ? "" : await request.clone().text();
    log.push({ method: request.method, url: request.url,
               headers: Object.fromEntries(request.headers), body });
    if (url.hostname === "callback.test") {
      // An MCP Events receiver: `/echo...` echoes the verification's challenge,
      // `/wrong...` answers another, anything else a 404.
      const sent = body === "" ? {} : JSON.parse(body) as { challenge?: string };
      if (url.pathname.startsWith("/echo")) {
        return Response.json({ challenge: sent.challenge });
      }
      if (url.pathname.startsWith("/wrong")) {
        return Response.json({ challenge: "not it" });
      }
      return new Response("no such receiver", { status: 404 });
    }
    const where = `${url.hostname}${url.pathname}`;
    switch (where) {
      case "api.groq.com/openai/v1/models":
        return listed(bearer(request).startsWith("gsk-good"), bearer(request));
      case "generativelanguage.googleapis.com/v1beta/models": {
        const key = request.headers.get("x-goog-api-key") ?? "";
        return listed(key.startsWith("AIza-good"), key);
      }
      case "openrouter.ai/api/v1/auth/keys":
        return keyExchange(request);
      case "openrouter.ai/api/v1/models/user":
        return bearer(request).startsWith("sk-or-")
          ? Response.json({ data: [{ id: "openrouter/auto", benchmarks: {
            artificial_analysis: { intelligence_index: 50 } } }] })
          : listed(false, bearer(request));
      case "openrouter.ai/api/v1/key":
        return bearer(request).startsWith("sk-or-")
          ? Response.json({ data: { is_free_tier: bearer(request).startsWith("sk-or-free"),
                                    limit_remaining: null } })
          : listed(false, bearer(request));
      case "auth.test/token":
        return token(request);
      case "api.openai.com/v1/models":
        return listed(bearer(request) === "at-good", bearer(request));
      case "api.groq.com/openai/v1/chat/completions":
        return completions(request);
      default:
        return new Response(`no fake for ${where}`, { status: 599 });
    }
  },
};
