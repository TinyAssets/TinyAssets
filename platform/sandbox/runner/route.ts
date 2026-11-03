/**
 * The route probe's verdict (`ta-runner route-probe`, at every template build;
 * codexurl_research.md Q4): what a keyed engine's CLI did, run as a run runs it with its
 * egress door on a loopback capture server, judged here. Pure: no process, no socket.
 *
 * It passes only when every model request reached the door's own path for its run and
 * connection, each carrying the run's placeholder key and its run secret where the record
 * puts it; no request carried any byte of the fake sign-in seeded in the engine's store; and
 * the CLI connected to nothing but loopback and the pinned resolver. The capture server
 * answers each model request with a minimal valid reply in its sealed API's wire format
 * (`reply`), so a CLI completes its turn, and its resume and compaction are judged the same.
 */

/** One request the capture server took. */
export interface Captured {
  readonly method: string;
  /** The request's path and query, as sent. */
  readonly url: string;
  readonly headers: Readonly<Record<string, string>>;
  readonly body: string;
}

/** What one probe run is judged by. */
export interface RouteExpect {
  /** The door's base path for this run and connection (no origin), its slashes trimmed. */
  readonly prefix: string;
  /** The run's MCP door's paths, which are not model requests. */
  readonly mcp: readonly string[];
  readonly placeholder: string;
  readonly secret: string;
  /** Whether the record sends the run secret as the `x-ta-run` header, not in the path. */
  readonly secretInHeader: boolean;
  /** The fake sign-in's marker, which no request may carry. */
  readonly marker: string;
  /** The resolvers a CLI may reach besides loopback. */
  readonly resolvers: readonly string[];
}

/** Whether `path` is `prefix` or under it. */
export function underPrefix(path: string, prefix: string): boolean {
  const bare = prefix.replace(/\/+$/, "");
  const only = path.split("?")[0]!;
  return only === bare || only.startsWith(`${bare}/`);
}

function loopback(address: string): boolean {
  return /^127\./.test(address) || address === "::1" || /^::ffff:127\./.test(address);
}

/**
 * The connects an strace log (`strace -f -e trace=connect`) shows to anything but loopback
 * and the resolvers, as `address:port`: an IPv4 or IPv6 socket's, a Unix socket's never.
 */
export function outsideConnects(trace: string, resolvers: readonly string[]): string[] {
  const out: string[] = [];
  for (const line of trace.split("\n")) {
    if (!line.includes("connect(")) {
      continue;
    }
    const v4 = /sa_family=AF_INET,\s*sin_port=htons\((\d+)\),\s*sin_addr=inet_addr\("([^"]+)"\)/
      .exec(line);
    const v6 = /sa_family=AF_INET6,\s*sin6_port=htons\((\d+)\).*?inet_pton\(AF_INET6,\s*"([^"]+)"/
      .exec(line);
    const found = v4 ?? v6;
    if (found === null) {
      continue;
    }
    const [, port, address] = found;
    if (loopback(address!) || resolvers.includes(address!)) {
      continue;
    }
    out.push(`${address}:${port}`);
  }
  return [...new Set(out)];
}

/** Why the probe run failed, in plain words, or null when it passed. */
export function routeVerdict(requests: readonly Captured[], connects: readonly string[],
                             expect: RouteExpect): string | null {
  const leaked = requests.find((request) => [request.url, request.body,
                                             ...Object.values(request.headers)]
    .some((text) => text.includes(expect.marker)));
  if (leaked !== undefined) {
    return `a request carried the fake sign-in's bytes (${leaked.method} ${leaked.url})`;
  }
  if (connects.length > 0) {
    return `the CLI connected outside the sandbox: ${connects.join(", ")}`;
  }
  const model = requests.filter((request) =>
    !expect.mcp.some((path) => underPrefix(request.url, path)));
  if (model.length === 0) {
    return "no model request reached the door";
  }
  for (const request of model) {
    const what = `${request.method} ${request.url.split("?")[0]}`;
    if (!underPrefix(request.url, expect.prefix)) {
      return `a model request went to ${what}, not under ${expect.prefix}`;
    }
    if (![request.url, ...Object.values(request.headers)].some((text) =>
      text.includes(expect.placeholder))) {
      return `a model request carried no placeholder key (${what})`;
    }
    if (expect.secretInHeader ? request.headers["x-ta-run"] !== expect.secret
      : !request.url.includes(`/${expect.secret}/`)) {
      return `a model request carried no run secret where the record puts it (${what})`;
    }
  }
  return null;
}

/** What the capture server answers. */
export interface Reply {
  readonly status: number;
  readonly type: string;
  readonly body: string;
}

const TEXT = "OK";

function sse(events: readonly [string | null, unknown][]): string {
  return events.map(([name, data]) => `${name === null ? "" : `event: ${name}\n`}data: ${
    typeof data === "string" ? data : JSON.stringify(data)}\n\n`).join("");
}

function bodyOf(request: Captured): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(request.body);
    return typeof parsed === "object" && parsed !== null ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

const NOT_FOUND: Reply = { status: 404, type: "application/json",
                           body: JSON.stringify({ error: { message: "no such route" } }) };

/** Anthropic's Messages API: a message, streamed when asked; a token count. */
function messages(request: Captured, path: string): Reply {
  const body = bodyOf(request);
  const model = String(body["model"] ?? "probe");
  if (path.endsWith("/count_tokens")) {
    return { status: 200, type: "application/json", body: JSON.stringify({ input_tokens: 1 }) };
  }
  if (!path.endsWith("/messages")) {
    return NOT_FOUND;
  }
  const usage = { input_tokens: 1, output_tokens: 1 };
  if (body["stream"] !== true) {
    return { status: 200, type: "application/json", body: JSON.stringify({
      id: "msg_probe", type: "message", role: "assistant", model,
      content: [{ type: "text", text: TEXT }], stop_reason: "end_turn", stop_sequence: null,
      usage }) };
  }
  return { status: 200, type: "text/event-stream", body: sse([
    ["message_start", { type: "message_start", message: {
      id: "msg_probe", type: "message", role: "assistant", model, content: [],
      stop_reason: null, stop_sequence: null, usage } }],
    ["content_block_start", { type: "content_block_start", index: 0,
                              content_block: { type: "text", text: "" } }],
    ["content_block_delta", { type: "content_block_delta", index: 0,
                              delta: { type: "text_delta", text: TEXT } }],
    ["content_block_stop", { type: "content_block_stop", index: 0 }],
    ["message_delta", { type: "message_delta",
                        delta: { stop_reason: "end_turn", stop_sequence: null },
                        usage: { output_tokens: 1 } }],
    ["message_stop", { type: "message_stop" }],
  ]) };
}

/** OpenAI's Responses API: a response with one message, streamed when asked. */
function responses(request: Captured, path: string): Reply {
  if (!path.endsWith("/responses")) {
    return NOT_FOUND;
  }
  const body = bodyOf(request);
  const model = String(body["model"] ?? "probe");
  const item = { id: "msg_probe", type: "message", status: "completed", role: "assistant",
                 content: [{ type: "output_text", text: TEXT, annotations: [] }] };
  const usage = { input_tokens: 1, input_tokens_details: { cached_tokens: 0 },
                  output_tokens: 1, output_tokens_details: { reasoning_tokens: 0 },
                  total_tokens: 2 };
  const done = { id: "resp_probe", object: "response", created_at: 0, status: "completed",
                 model, output: [item], usage };
  if (body["stream"] !== true) {
    return { status: 200, type: "application/json", body: JSON.stringify(done) };
  }
  return { status: 200, type: "text/event-stream", body: sse([
    ["response.created", { type: "response.created", sequence_number: 0,
                           response: { ...done, status: "in_progress", output: [],
                                       usage: null } }],
    ["response.output_item.added", { type: "response.output_item.added", sequence_number: 1,
                                     output_index: 0,
                                     item: { ...item, status: "in_progress", content: [] } }],
    ["response.content_part.added", { type: "response.content_part.added", sequence_number: 2,
                                      item_id: item.id, output_index: 0, content_index: 0,
                                      part: { type: "output_text", text: "",
                                              annotations: [] } }],
    ["response.output_text.delta", { type: "response.output_text.delta", sequence_number: 3,
                                     item_id: item.id, output_index: 0, content_index: 0,
                                     delta: TEXT }],
    ["response.output_text.done", { type: "response.output_text.done", sequence_number: 4,
                                    item_id: item.id, output_index: 0, content_index: 0,
                                    text: TEXT }],
    ["response.content_part.done", { type: "response.content_part.done", sequence_number: 5,
                                     item_id: item.id, output_index: 0, content_index: 0,
                                     part: item.content[0] }],
    ["response.output_item.done", { type: "response.output_item.done", sequence_number: 6,
                                    output_index: 0, item }],
    ["response.completed", { type: "response.completed", sequence_number: 7,
                             response: done }],
  ]) };
}

/** The Chat Completions API (and Mistral's, its shape): a choice, streamed when asked. */
function completions(request: Captured, path: string): Reply {
  if (!path.endsWith("/chat/completions")) {
    return NOT_FOUND;
  }
  const body = bodyOf(request);
  const model = String(body["model"] ?? "probe");
  const usage = { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 };
  if (body["stream"] !== true) {
    return { status: 200, type: "application/json", body: JSON.stringify({
      id: "chatcmpl-probe", object: "chat.completion", created: 0, model,
      choices: [{ index: 0, message: { role: "assistant", content: TEXT },
                  finish_reason: "stop" }], usage }) };
  }
  const chunk = (delta: unknown, finish: string | null, extra: object = {}) => ({
    id: "chatcmpl-probe", object: "chat.completion.chunk", created: 0, model,
    choices: [{ index: 0, delta, finish_reason: finish }], ...extra });
  return { status: 200, type: "text/event-stream", body: sse([
    [null, chunk({ role: "assistant", content: TEXT }, null)],
    [null, chunk({}, "stop", { usage })],
    [null, "[DONE]"],
  ]) };
}

/**
 * The smallest value a JSON schema (an OpenAPI-style `responseSchema` or a JSON Schema
 * `responseJsonSchema`) admits: each required property, an enum's first value, a type's
 * plainest value.
 */
export function fromSchema(schema: unknown): unknown {
  if (typeof schema !== "object" || schema === null) {
    return TEXT;
  }
  const spec = schema as Record<string, unknown>;
  if (Array.isArray(spec["enum"]) && spec["enum"].length > 0) {
    return spec["enum"][0];
  }
  const type = String(Array.isArray(spec["type"]) ? spec["type"][0] : spec["type"] ?? "")
    .toLowerCase();
  if (type === "object" || (type === "" && typeof spec["properties"] === "object")) {
    const properties = (spec["properties"] ?? {}) as Record<string, unknown>;
    const required = Array.isArray(spec["required"]) ? spec["required"] as string[]
      : Object.keys(properties);
    return Object.fromEntries(required.map((key) => [key, fromSchema(properties[key])]));
  }
  if (type === "array") {
    return [];
  }
  if (type === "integer" || type === "number") {
    return 0;
  }
  if (type === "boolean") {
    return false;
  }
  return TEXT;
}

/** Google's Generative Language API: a candidate, streamed as SSE when asked; a count. */
function generative(request: Captured, path: string): Reply {
  const model = /models\/([^:/]+)/.exec(path)?.[1] ?? "probe";
  if (path.endsWith(":countTokens")) {
    return { status: 200, type: "application/json", body: JSON.stringify({ totalTokens: 1 }) };
  }
  // A request for JSON by a schema gets the schema's smallest object, as text.
  const config = (bodyOf(request)["generationConfig"] ?? {}) as Record<string, unknown>;
  const schema = config["responseJsonSchema"] ?? config["responseSchema"];
  const text = schema !== undefined || config["responseMimeType"] === "application/json"
    ? JSON.stringify(fromSchema(schema)) : TEXT;
  const answer = {
    candidates: [{ content: { role: "model", parts: [{ text }] }, finishReason: "STOP",
                   index: 0 }],
    usageMetadata: { promptTokenCount: 1, candidatesTokenCount: 1, totalTokenCount: 2 },
    modelVersion: model };
  if (path.endsWith(":streamGenerateContent")) {
    return { status: 200, type: "text/event-stream", body: sse([[null, answer]]) };
  }
  if (path.endsWith(":generateContent")) {
    return { status: 200, type: "application/json", body: JSON.stringify(answer) };
  }
  return NOT_FOUND;
}

/** Each sealed API's minimal valid reply, by the gateway record's `api` (data, no vendor). */
const REPLIES: Readonly<Record<string, (request: Captured, path: string) => Reply>> =
  Object.freeze({
    "anthropic-messages": messages,
    "openai-responses": responses,
    "openai-completions": completions,
    "mistral-conversations": completions,
    "google-generative-ai": generative,
  });

/**
 * The capture server's answer to a model request of a sealed `api`: a minimal valid reply,
 * so the CLI completes its turn; a request no reply fits is answered 404, and an API the
 * table lacks fails loudly.
 */
export function reply(api: string, request: Captured): Reply {
  const make = REPLIES[api];
  if (make === undefined) {
    throw new Error(`the route probe has no reply for the ${api} API`);
  }
  return make(request, request.url.split("?")[0]!);
}
