/**
 * The outside world for the gateway's tests: every global `fetch` the gateway Worker
 * makes in workerd is routed here by miniflare's `outboundService` (vitest.config.ts).
 *
 * It answers in each provider's own wire format, so pi-ai's real adapters parse it:
 * Anthropic Messages, OpenAI Responses and Google Generative AI, each as SSE. What it
 * does is chosen by a `cmd:` word in the request body (the test's user message):
 *
 * - `cmd:echo` (and any other word): the reply's text is JSON of what it received (path,
 *   the credential headers, the output-token cap, the service tier), so a test reads back
 *   what the gateway sent; `x-fake-served-tier` names the tier an Anthropic answer says it
 *   served.
 * - `cmd:slow-<id>`: one text delta, then holds the stream open; its cancel is recorded
 *   under `<id>`, read back from `https://status.test/cancelled?id=<id>`.
 *
 * `x-fake-mode` drives the raw passthrough tests (`echo`, `quote`, `hang`, `redirect`, `say`,
 * `decoded`, `hdr`, `jsonesc`; `held`: the body read to its end, its length answered after
 * 1.5 s, as a provider holds its answer until its first token; `stall`: some bytes, then
 * silence, its cancel recorded under `x-fake-id`); `auth.test` and `auth.openai.com` are
 * token endpoints; `keys.test` is a key-exchange endpoint (OpenRouter's PKCE flow); `evil.test` records everything it receives, read back from
 * `https://status.test/evil`, so a test sees whether a redirect was followed. It is test
 * code: its module state (the cancel registry, the evil log) is the fake's, never the
 * gateway's. `doorbell.test`, and Claude's `/fire` path on `api.anthropic.com`, are a
 * doorbell's receiver (slice W): each request is recorded, read back from
 * `https://status.test/doorbell`, and answered by its path: `status-<n>` that status
 * (`?retry=<v>` its `Retry-After`), `hang` never, `echo` the verification challenge it was
 * sent, else 202.
 */
const cancelled = new Map<string, boolean>();
const evil: unknown[] = [];
const USAGE = { input: 11, output: 7 };

function sse(frames: readonly (readonly [string | null, unknown])[]): string {
  return frames.map(([event, data]) =>
    `${event === null ? "" : `event: ${event}\n`}data: ${JSON.stringify(data)}\n\n`).join("");
}

function credentialOf(request: Request): string {
  return request.headers.get("x-api-key") ?? request.headers.get("x-goog-api-key")
    ?? (request.headers.get("authorization") ?? "").replace(/^Bearer /, "");
}

function command(body: string): string {
  const all = [...body.matchAll(/cmd:([a-z0-9-]+)/g)];
  return all.at(-1)?.[1] ?? "echo";
}

interface Format {
  readonly start: (model: string) => string;
  readonly text: (delta: string) => string;
  readonly finish: (text: string) => string;
}

function anthropicFormat(): Format {
  return {
    start: (model) => sse([
      ["message_start", { type: "message_start", message: {
        id: "msg_fake", type: "message", role: "assistant", model, content: [],
        stop_reason: null, usage: { input_tokens: USAGE.input, output_tokens: 1 } } }],
      ["content_block_start", { type: "content_block_start", index: 0,
                                content_block: { type: "text", text: "" } }]]),
    text: (delta) => sse([["content_block_delta", { type: "content_block_delta", index: 0,
                                                    delta: { type: "text_delta", text: delta } }]]),
    finish: () => sse([
      ["content_block_stop", { type: "content_block_stop", index: 0 }],
      ["message_delta", { type: "message_delta", delta: { stop_reason: "end_turn" },
                          usage: { output_tokens: USAGE.output } }],
      ["message_stop", { type: "message_stop" }]]),
  };
}

function openaiFormat(): Format {
  return {
    start: () => sse([
      ["response.created", { type: "response.created", response: { id: "resp_fake" } }],
      ["response.output_item.added", { type: "response.output_item.added", output_index: 0,
        item: { type: "message", id: "msg_fake", role: "assistant", content: [] } }]]),
    text: (delta) => sse([["response.output_text.delta", {
      type: "response.output_text.delta", output_index: 0, content_index: 0, delta }]]),
    finish: (text) => sse([
      ["response.output_item.done", { type: "response.output_item.done", output_index: 0,
        item: { type: "message", id: "msg_fake", role: "assistant",
                content: [{ type: "output_text", text }] } }],
      ["response.completed", { type: "response.completed", response: {
        id: "resp_fake", status: "completed",
        usage: { input_tokens: USAGE.input, output_tokens: USAGE.output,
                 total_tokens: USAGE.input + USAGE.output } } }]]),
  };
}

function googleFormat(): Format {
  return {
    start: () => "",
    text: (delta) => sse([[null, { responseId: "resp_fake", candidates: [{
      content: { role: "model", parts: [{ text: delta }] } }] }]]),
    finish: () => sse([[null, { responseId: "resp_fake", candidates: [{
      content: { role: "model", parts: [{ text: "" }] }, finishReason: "STOP" }],
      usageMetadata: { promptTokenCount: USAGE.input, candidatesTokenCount: USAGE.output,
                       totalTokenCount: USAGE.input + USAGE.output } }]]),
  };
}

function outputCap(body: Record<string, unknown>): unknown {
  const config = body["generationConfig"] as Record<string, unknown> | undefined;
  return body["max_tokens"] ?? body["max_output_tokens"] ?? config?.["maxOutputTokens"] ?? null;
}

async function model(request: Request, format: Format): Promise<Response> {
  const text = await request.text();
  const body = JSON.parse(text) as Record<string, unknown>;
  const cmd = command(text);
  const url = new URL(request.url);
  const name = String(body["model"] ?? url.pathname.split("/").at(-1)?.split(":")[0] ?? "");
  const encoder = new TextEncoder();
  const slow = /^slow-([a-z0-9]+)$/.exec(cmd);
  if (slow) {
    const id = slow[1]!;
    cancelled.set(id, false);
    // An SSE comment every 50 ms keeps the stream open without ending it (a stream with
    // nothing pending would be cut by workerd's hang detection), for at most 10 s.
    let timer: ReturnType<typeof setInterval> | undefined;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(format.start(name) + format.text("first")));
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
        cancelled.set(id, true);
      },
    });
    return new Response(stream, { headers: { "content-type": "text/event-stream" } });
  }
  const reply = JSON.stringify({
    path: url.pathname, query: url.search,
    auth: { "x-api-key": request.headers.get("x-api-key"),
            authorization: request.headers.get("authorization"),
            "x-goog-api-key": request.headers.get("x-goog-api-key") },
    envelope: request.headers.get("x-ta-envelope"),
    max: outputCap(body),
    tier: body["service_tier"] ?? null,
  });
  const half = Math.floor(reply.length / 2);
  const tier = typeof body["service_tier"] === "string" ? body["service_tier"] : null;
  // OpenAI's Responses answer names the tier it served the call at, as the API does; an
  // Anthropic answer names its served tier in its usage when `x-fake-served-tier` asks.
  const served = request.headers.get("x-fake-served-tier");
  const payload = (format.start(name) + format.text(reply.slice(0, half))
    + format.text(reply.slice(half)) + format.finish(reply))
    .replace('"status":"completed",', tier === null ? '"status":"completed",'
      : `"status":"completed","service_tier":${JSON.stringify(tier === "auto" ? "default" : tier)},`)
    // As OpenAI's does, its first event echoes the tier the call asked for, and the last
    // names the one it served at (`auto` resolved to `default`).
    .replace('"response":{"id":"resp_fake"}', tier === null ? '"response":{"id":"resp_fake"}'
      : `"response":{"id":"resp_fake","service_tier":"auto"}`)
    .replace('"usage":{"input_tokens":', served === null ? '"usage":{"input_tokens":'
      : `"usage":{"service_tier":${JSON.stringify(served)},"input_tokens":`);
  return new Response(payload, { headers: { "content-type": "text/event-stream" } });
}

async function token(request: Request): Promise<Response> {
  const form = new URLSearchParams(new TextDecoder().decode(await request.arrayBuffer()));
  const basic = request.headers.get("authorization");
  const client = basic !== null ? `basic:${atob(basic.replace(/^Basic /, ""))}`
    : form.has("client_secret") ? `post:${form.get("client_id")}:${form.get("client_secret")}`
      : `none:${form.get("client_id")}`;
  const grant = form.get("grant_type");
  if (grant === "authorization_code") {
    if (form.get("code") === "redirect-code") {
      return new Response(null, { status: 307, headers: { location: "https://evil.test/token" } });
    }
    if (form.get("code") === "mac-code") {
      return Response.json({ access_token: "at-mac", token_type: "mac", expires_in: 60 });
    }
    if (form.get("code") === "huge-code") {
      return Response.json({ access_token: "at-huge", token_type: "Bearer",
                             padding: "x".repeat(70 * 1024) });
    }
    if (form.get("code") !== "good-code" || form.get("code_verifier") === null) {
      return Response.json({ error: "invalid_grant",
                             error_description: `code ${form.get("code")} is not valid` },
                           { status: 400 });
    }
    return Response.json({ access_token: `at-1|${client}|${form.get("resource") ?? ""}`,
                           token_type: "Bearer", expires_in: 3600, refresh_token: "rt-1",
                           scope: "models.read" });
  }
  if (grant === "refresh_token") {
    const refresh = form.get("refresh_token") ?? "";
    if (refresh === "rt-dead") {
      return Response.json({ error: "invalid_grant", error_description: `token ${refresh}` },
                           { status: 400 });
    }
    if (refresh === "rt-hop") {
      return new Response(null, { status: 307, headers: { location: "https://evil.test/refresh" } });
    }
    if (refresh === "rt-down") {
      return new Response(`down; token ${refresh}`, { status: 503 });
    }
    const next = Number(refresh.replace(/^rt-/, "")) + 1;
    return Response.json({ access_token: `at-${next}|${client}`, token_type: "bearer",
                           expires_in: 1800,
                           ...(refresh === "rt-keep" ? {} : { refresh_token: `rt-${next}` }),
                           scope: "openid offline_access chatgpt.tokens.use.direct" });
  }
  return Response.json({ error: "unsupported_grant_type" }, { status: 400 });
}

/** A key-exchange endpoint (OpenRouter's PKCE flow): JSON in, `{key}` out. */
async function keyExchange(request: Request): Promise<Response> {
  const body = JSON.parse(await request.text()) as Record<string, string>;
  if (request.method !== "POST" || request.headers.get("content-type") !== "application/json"
      || body["code_challenge_method"] !== "S256") {
    return Response.json({ error: "bad request" }, { status: 400 });
  }
  switch (body["code"]) {
    case "key-code":
      return Response.json({ key: `sk-bought-${body["code_verifier"]}`, user_id: "u1" });
    case "nokey-code":
      return Response.json({ user_id: "u1" });
    case "spacekey-code":
      return Response.json({ key: "sk-bought with a space" });
    case "redirect-code":
      return new Response(null, { status: 307, headers: { location: "https://evil.test/keys" } });
    case "down-code":
      return new Response(`down; code ${body["code"]}`, { status: 503 });
    default:
      return Response.json({ error: { message: `code ${body["code"]} is not valid` } },
                           { status: 403 });
  }
}

async function forwardEcho(request: Request): Promise<Response> {
  const mode = request.headers.get("x-fake-mode");
  const url = new URL(request.url);
  if (mode === "hang") {
    // Answers only after 10 s (a timer, so workerd does not cut it as hung).
    return new Promise<Response>((resolve) =>
      setTimeout(() => resolve(new Response("late")), 10_000));
  }
  if (mode === "stall") {
    // Some bytes, then silence; a timer holds the stream open (so workerd does not cut it
    // as hung) and ends it after 5 s.
    const id = request.headers.get("x-fake-id") ?? "";
    cancelled.set(id, false);
    let timer: ReturnType<typeof setTimeout> | undefined;
    return new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("x".repeat(64)));
        timer = setTimeout(() => controller.close(), 5_000);
      },
      cancel() {
        clearTimeout(timer);
        cancelled.set(id, true);
      },
    }));
  }
  if (mode === "held") {
    const length = (await request.arrayBuffer()).byteLength;
    return new Promise<Response>((resolve) =>
      setTimeout(() => resolve(Response.json({ length })), 1500));
  }
  if (mode === "redirect") {
    return new Response(null, { status: 307, headers: { location: "https://evil.test/steal" } });
  }
  if (mode === "say") {
    return new Response(request.headers.get("x-fake-say") ?? "", { status: 200 });
  }
  if (mode === "decoded") {
    // A body the runtime has already decoded keeps its Content-Encoding header.
    return new Response("plain text, decoded on the way", {
      headers: { "content-encoding": "gzip", "content-type": "text/plain" },
    });
  }
  if (mode === "hdr") {
    return new Response("{}", { status: 200, headers: { "x-echo-key": credentialOf(request) } });
  }
  if (mode === "jsonesc") {
    return new Response('{"k":"' + credentialOf(request).replace("Bearer ", "").split("/").join("\\/") + '"}', { status: 200 });
  }
  if (mode === "quote") {
    const secret = credentialOf(request);
    const encoder = new TextEncoder();
    const bytes = encoder.encode(`{"error":"bad key ${secret} and again ${secret}"}`);
    const cut = 22;
    return new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, cut));
        controller.enqueue(bytes.slice(cut));
        controller.close();
      },
    }), { status: 401, headers: { "content-type": "application/json", "x-ta-resealed": "forged" } });
  }
  // The credential headers come back as digests: the gateway redacts the secret itself
  // from every answer, so an echo of it would read `[redacted]`.
  const digests: Record<string, string> = {};
  for (const name of ["authorization", "x-api-key"]) {
    const value = request.headers.get(name);
    if (value !== null) {
      const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
      digests[name] = [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    }
  }
  return Response.json({
    method: request.method, host: url.host, path: url.pathname, query: url.search,
    headers: Object.fromEntries(request.headers), digests,
    body: request.method === "GET" ? null : await request.text(),
  }, { headers: { "x-upstream": "fake", "cf-ray": "fake-ray" } });
}

/** Every request a doorbell receiver took (slice W), oldest first. */
const rung: { url: string; method: string; redirect: string; headers: Record<string, string>;
              body: string }[] = [];

async function doorbellReceiver(request: Request, url: URL): Promise<Response> {
  const body = await request.text();
  rung.push({ url: request.url, method: request.method, redirect: request.redirect,
              headers: Object.fromEntries(request.headers), body });
  if (url.pathname.includes("hang")) {
    return new Promise<Response>(() => undefined);
  }
  if (url.pathname.includes("echo")) {
    return Response.json({ challenge: (JSON.parse(body) as { challenge?: string }).challenge });
  }
  const status = /status[-_](\d{3})/.exec(url.pathname);
  const retry = url.searchParams.get("retry");
  return new Response("a receiver's own words", {
    status: status === null ? 202 : Number(status[1]),
    headers: retry === null ? {} : { "Retry-After": retry } });
}

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.hostname === "doorbell.test" || (url.hostname === "api.anthropic.com"
                                             && url.pathname.startsWith("/v1/claude_code/"))) {
      return doorbellReceiver(request, url);
    }
    if (url.hostname === "status.test" && url.pathname === "/doorbell") {
      return Response.json(rung);
    }
    if (url.hostname === "evil.test") {
      evil.push({ url: request.url, headers: Object.fromEntries(request.headers) });
      return new Response("stolen", { status: 200 });
    }
    if (url.hostname === "status.test" && url.pathname === "/evil") {
      return Response.json(evil);
    }
    if (url.hostname === "status.test") {
      return Response.json({ cancelled: cancelled.get(url.searchParams.get("id") ?? "") ?? null });
    }
    if (url.hostname === "keys.test") {
      return keyExchange(request);
    }
    if (url.hostname === "auth.test" || url.hostname === "auth.openai.com") {
      if (url.pathname === "/revoke") {
        const form = new URLSearchParams(new TextDecoder().decode(await request.arrayBuffer()));
        if (form.get("token") === "rt-redirect") {
          return new Response(null, { status: 307, headers: { location: "https://evil.test/revoke" } });
        }
        return form.get("token") === "rt-down" ? new Response("down", { status: 503 })
          : new Response(null, { status: 200,
                                 headers: { "x-revoked": `${form.get("token")}:${form.get("token_type_hint")}` } });
      }
      return token(request);
    }
    if (request.headers.has("x-fake-mode")) {
      return forwardEcho(request);
    }
    if (url.hostname === "api.anthropic.com" && url.pathname === "/v1/models") {
      if (request.headers.get("x-api-key") === "sk-redirect") {
        return new Response(null, { status: 307, headers: { location: "https://evil.test/models" } });
      }
      return request.headers.get("x-api-key") === "sk-good"
        ? Response.json({ data: [] }) : Response.json({ error: "no" }, { status: 401 });
    }
    if (url.hostname === "api.anthropic.com") {
      return model(request, anthropicFormat());
    }
    if (url.hostname === "api.openai.com") {
      return model(request, openaiFormat());
    }
    if (url.hostname === "generativelanguage.googleapis.com") {
      return model(request, googleFormat());
    }
    return new Response(`no fake for ${url.hostname}`, { status: 599 });
  },
};
