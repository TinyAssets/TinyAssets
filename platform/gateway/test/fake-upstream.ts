/**
 * The outside world for the gateway's tests: every global `fetch` the gateway Worker
 * makes in workerd is routed here by miniflare's `outboundService` (vitest.config.ts).
 *
 * It answers in each provider's own wire format, so pi-ai's real adapters parse it:
 * Anthropic Messages, OpenAI Responses and Google Generative AI, each as SSE. What it
 * does is chosen by a `cmd:` word in the request body (the test's user message):
 *
 * - `cmd:echo`: the reply's text is JSON of what it received (path, the credential
 *   headers, the output-token cap), so a test reads back what the gateway sent.
 * - `cmd:fail-<status>`: that status, with a body that quotes the credential.
 * - `cmd:redirect`: a 302 to another host.
 * - `cmd:slow-<id>`: one text delta, then holds the stream open; its cancel is recorded
 *   under `<id>`, read back from `https://status.test/cancelled?id=<id>`.
 * - `cmd:tool`: a tool call (Anthropic).
 *
 * `x-fake-mode` drives the raw passthrough tests (`echo`, `quote`, `hang`), and
 * `auth.test` and `auth.openai.com` are token endpoints. It is test code: its module
 * state (the cancel registry) is the fake's, never the gateway's.
 */
const cancelled = new Map<string, boolean>();
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
  const credential = credentialOf(request);
  const failing = /^fail-(\d{3})$/.exec(cmd);
  if (failing) {
    return Response.json({ error: { type: "fake", message: `bad key ${credential}` } },
                         { status: Number(failing[1]) });
  }
  if (cmd === "redirect") {
    return new Response(null, { status: 302, headers: { location: "https://evil.test/steal" } });
  }
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
  });
  const half = Math.floor(reply.length / 2);
  const payload = format.start(name) + format.text(reply.slice(0, half))
    + format.text(reply.slice(half)) + format.finish(reply);
  return new Response(payload, { headers: { "content-type": "text/event-stream" } });
}

function anthropicTool(request: Request, body: Record<string, unknown>): Response {
  const name = String(body["model"]);
  return new Response(sse([
    ["message_start", { type: "message_start", message: {
      id: "msg_tool", type: "message", role: "assistant", model: name, content: [],
      stop_reason: null, usage: { input_tokens: USAGE.input, output_tokens: 1 } } }],
    ["content_block_start", { type: "content_block_start", index: 0,
                              content_block: { type: "thinking", thinking: "", signature: "" } }],
    ["content_block_delta", { type: "content_block_delta", index: 0,
                              delta: { type: "thinking_delta", thinking: "Look it up." } }],
    ["content_block_delta", { type: "content_block_delta", index: 0,
                              delta: { type: "signature_delta", signature: "sig-1" } }],
    ["content_block_stop", { type: "content_block_stop", index: 0 }],
    ["content_block_start", { type: "content_block_start", index: 1,
                              content_block: { type: "tool_use", id: "toolu_1", name: "lookup",
                                               input: {} } }],
    ["content_block_delta", { type: "content_block_delta", index: 1,
                              delta: { type: "input_json_delta", partial_json: "{\"q\":" } }],
    ["content_block_delta", { type: "content_block_delta", index: 1,
                              delta: { type: "input_json_delta", partial_json: "\"pi\"}" } }],
    ["content_block_stop", { type: "content_block_stop", index: 1 }],
    ["message_delta", { type: "message_delta", delta: { stop_reason: "tool_use" },
                        usage: { output_tokens: USAGE.output } }],
    ["message_stop", { type: "message_stop" }]]),
    { headers: { "content-type": "text/event-stream", "x-request": request.url } });
}

async function token(request: Request): Promise<Response> {
  const form = new URLSearchParams(new TextDecoder().decode(await request.arrayBuffer()));
  const basic = request.headers.get("authorization");
  const client = basic !== null ? `basic:${atob(basic.replace(/^Basic /, ""))}`
    : form.has("client_secret") ? `post:${form.get("client_id")}:${form.get("client_secret")}`
      : `none:${form.get("client_id")}`;
  const grant = form.get("grant_type");
  if (grant === "authorization_code") {
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

async function forwardEcho(request: Request): Promise<Response> {
  const mode = request.headers.get("x-fake-mode");
  const url = new URL(request.url);
  if (mode === "hang") {
    // Answers only after 10 s (a timer, so workerd does not cut it as hung).
    return new Promise<Response>((resolve) =>
      setTimeout(() => resolve(new Response("late")), 10_000));
  }
  if (mode === "redirect") {
    return new Response(null, { status: 307, headers: { location: "https://evil.test/steal" } });
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

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.hostname === "status.test") {
      return Response.json({ cancelled: cancelled.get(url.searchParams.get("id") ?? "") ?? null });
    }
    if (url.hostname === "auth.test" || url.hostname === "auth.openai.com") {
      if (url.pathname === "/revoke") {
        const form = new URLSearchParams(new TextDecoder().decode(await request.arrayBuffer()));
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
      return request.headers.get("x-api-key") === "sk-good"
        ? Response.json({ data: [] }) : Response.json({ error: "no" }, { status: 401 });
    }
    if (url.hostname === "api.anthropic.com") {
      const text = await request.clone().text();
      if (command(text) === "tool") {
        return anthropicTool(request, JSON.parse(text) as Record<string, unknown>);
      }
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
