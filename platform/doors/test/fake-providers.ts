/**
 * The model providers a connect reaches, for the doors' tests: every global `fetch` the
 * gateway Worker makes is routed here (vitest.config.ts), and the tests reach it as
 * `PROVIDERS`. Each request is logged, read back from `https://status.test/log`, so a
 * test sees what the gateway sent where.
 *
 * - Groq's model list (`api.groq.com/openai/v1/models`): `200` for a bearer key starting
 *   `gsk-good`, else `401` with a body that quotes the key.
 * - Google's (`generativelanguage.googleapis.com/v1beta/models`): `200` for an
 *   `x-goog-api-key` starting `AIza-good`.
 * - OpenRouter's key exchange (`openrouter.ai/api/v1/auth/keys`): JSON in; the code
 *   `or-good` buys `sk-or-bought`, `or-badkey` a key with a space; any other is `403`. Its model list: `200` for a bearer
 *   key starting `sk-or-`.
 * - A test OAuth server (`auth.test/token`): the code `good-code` gets `at-good`, any
 *   other `invalid_grant`. OpenAI's model list (`api.openai.com/v1/models`): `200` for
 *   the bearer `at-good`.
 * - Groq's chat completions (`api.groq.com/openai/v1/chat/completions`), for the egress
 *   door: `401` but for a bearer key starting `gsk-good`; else, by `x-fake-egress`, three
 *   SSE chunks (`stream`, the default, `EGRESS_CHUNKS`), the same without the usage chunk
 *   (`nousage`), the same from a dated snapshot of the named model (`snapshot`), a `429`
 *   (`busy`), or one chunk and
 *   then a stream held
 *   open, an SSE comment every 50 ms for at most 10 s, until its reader cancels it
 *   (`slow-<id>`; the cancel is recorded, read back from
 *   `https://status.test/cancelled?id=<id>`).
 *
 * It is test code: its module state (the log) is the fake's own.
 */
const log: unknown[] = [];
const cancelled = new Set<string>();

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
  const mode = request.headers.get("x-fake-egress") ?? "stream";
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
    if (url.hostname === "status.test") {
      if (url.pathname === "/cancelled") {
        return Response.json({ cancelled: cancelled.has(url.searchParams.get("id") ?? "") });
      }
      if (request.method === "DELETE") {
        log.length = 0;
        return new Response(null, { status: 204 });
      }
      return Response.json(log);
    }
    const body = request.method === "GET" ? "" : await request.clone().text();
    log.push({ method: request.method, url: request.url,
               headers: Object.fromEntries(request.headers), body });
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
        return listed(bearer(request).startsWith("sk-or-"), bearer(request));
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
