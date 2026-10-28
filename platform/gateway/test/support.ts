/**
 * Shared test helpers: keyrings, source records like the ones the binder will pass, an
 * envelope, and pi-ai's own pi-messages client pointed at the gateway.
 */
import {
  type Api, type AssistantMessage, type AssistantMessageEvent, type Context, createModels,
  createProvider, fauxAssistantMessage, fauxProvider, type FauxResponseStep, type Model,
} from "@earendil-works/pi-ai";
import { piMessagesApi } from "@earendil-works/pi-ai/api/pi-messages.lazy";

import type { Catalog } from "../src/catalog";
import { builtinCatalog } from "../src/catalog";
import type { Credential, Source } from "../src/credential";
import { encodeEnvelope } from "../src/envelope";
import { type Binding, seal } from "../src/keyring";
import { messages } from "../src/messages";

export type Env = { GATEWAY_KEYRING: string };

function randomKey(): string {
  return btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))));
}

/** A keyring env holding `kids` (each a fresh random key) with `active` active. */
export function ring(active: string, kids: readonly string[] = [active],
                     keys: Record<string, string> = {}): Env {
  const all: Record<string, string> = {};
  for (const kid of kids) {
    all[kid] = keys[kid] ?? randomKey();
  }
  return { GATEWAY_KEYRING: JSON.stringify({ active, keys: all }) };
}

/** The same keys as `env`, with `active` active. */
export function rotate(env: Env, active: string, extra: readonly string[] = []): Env {
  const parsed = JSON.parse(env.GATEWAY_KEYRING) as { keys: Record<string, string> };
  return ring(active, [...Object.keys(parsed.keys), ...extra], parsed.keys);
}

export const ALICE: Binding = Object.freeze({ owner: "alice", connection: "c1", version: 1 });

export const ANTHROPIC: Source = Object.freeze({
  upstream: { provider: "anthropic", api: "anthropic-messages", base_url: "https://api.anthropic.com",
              auth: { header: "x-api-key", scheme: "" },
              verify_url: "https://api.anthropic.com/v1/models" },
  endpoints: [{ origin: "https://api.anthropic.com", paths: ["/v1/messages", "/v1/models"],
                methods: ["GET", "POST"] }],
});

export const OPENAI: Source = Object.freeze({
  upstream: { provider: "openai", api: "openai-responses", base_url: "https://api.openai.com/v1",
              auth: { header: "authorization", scheme: "Bearer" } },
  endpoints: [{ origin: "https://api.openai.com", paths: ["/v1/responses", "/v1/models"],
                methods: ["GET", "POST"] }],
});

export const GOOGLE: Source = Object.freeze({
  upstream: { provider: "google", api: "google-generative-ai",
              base_url: "https://generativelanguage.googleapis.com/v1beta",
              auth: { header: "x-goog-api-key", scheme: "" } },
  endpoints: [{ origin: "https://generativelanguage.googleapis.com", paths: ["/v1beta/models/*"],
                methods: ["POST"] }],
});

export const FAUX_SOURCE: Source = Object.freeze({
  upstream: { provider: "faux", api: "faux", base_url: "https://faux.test/v1",
              auth: { header: "authorization", scheme: "Bearer" } },
  endpoints: [{ origin: "https://faux.test", paths: ["/v1/*"], methods: ["POST"] }],
});

/** An API-key credential for `record`. */
export function apiKey(secret: string, record: Source): Credential {
  return { kind: "api_key", secret, upstream: record.upstream, endpoints: record.endpoints,
           sealed_at: Date.now() };
}

export async function sealed(env: Env, bound: Binding, secret: string,
                             record: Source): Promise<string> {
  return (await seal(env, bound, apiKey(secret, record))).blob;
}

export interface EnvelopeFields {
  readonly blob: string;
  readonly bound?: Binding;
  readonly cap?: number;
  readonly deadline?: number;
}

export function envelopeOf(fields: EnvelopeFields): string {
  const bound = fields.bound ?? ALICE;
  return encodeEnvelope({
    owner: bound.owner, connection: bound.connection, version: bound.version,
    blob: fields.blob, reservation: { id: "r-1", max_output_tokens: fields.cap ?? 1000 },
    policy_generation: 7, deadline: fields.deadline ?? Date.now() + 60_000,
  });
}

/** A faux catalog: pi-ai's faux provider as `faux`, answering `steps` in order. */
export function fauxCatalog(steps: FauxResponseStep[], tokensPerSecond?: number):
    { catalog: Catalog; faux: ReturnType<typeof fauxProvider> } {
  const faux = fauxProvider({ provider: "faux", api: "faux", tokenSize: { min: 3, max: 3 },
                              models: [{ id: "faux-1", maxTokens: 4096, reasoning: true }],
                              ...(tokensPerSecond === undefined ? {} : { tokensPerSecond }) });
  faux.setResponses(steps);
  return { catalog: { provider: (id) => (id === "faux" ? faux.provider : undefined) }, faux };
}

export const USER: Context = Object.freeze({
  messages: [{ role: "user" as const, content: "cmd:echo", timestamp: 1 }],
});

export function ask(text: string): Context {
  return { messages: [{ role: "user", content: text, timestamp: 1 }] };
}

/** The owner side: pi-ai's own pi-messages client, its fetch going to the gateway. */
export function client(handler: (request: Request) => Promise<Response>, header: string,
                       modelId: string) {
  const model: Model<Api> = {
    id: modelId, name: modelId, api: "pi-messages", provider: "gateway",
    baseUrl: "https://gateway.internal/v1", reasoning: true, input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 200_000, maxTokens: 64_000,
  };
  const models = createModels();
  models.setProvider(createProvider({
    id: "gateway",
    auth: { apiKey: { name: "TinyAssets gateway",
                      resolve: async () => ({ auth: { apiKey: "gateway" } }) } },
    models: [model],
    api: piMessagesApi(),
  }));
  return {
    model,
    stream: (context: Context, options: { maxTokens?: number; signal?: AbortSignal;
                                          reasoning?: "high" } = {}) =>
      models.streamSimple(model, context, {
        ...options,
        headers: { "x-ta-envelope": header },
        fetch: (input: RequestInfo | URL, init?: RequestInit) => throughFetch(handler, input, init),
      }),
  };
}

/**
 * The handler called as a subrequest would be: an abort of the caller's signal cancels
 * the response body, as a real fetch does, so the gateway sees its stream cancelled.
 */
export async function throughFetch(handler: (request: Request) => Promise<Response>,
                                   input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const response = await handler(new Request(input, init));
  const signal = init?.signal ?? undefined;
  if (response.body === null || signal === undefined) {
    return response;
  }
  const reader = response.body.getReader();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      signal.addEventListener("abort", () => {
        controller.error(signal.reason);
        void reader.cancel(signal.reason);
      }, { once: true });
    },
    async pull(controller) {
      const { done, value } = await reader.read();
      if (done) {
        controller.close();
      } else {
        controller.enqueue(value);
      }
    },
    cancel: (reason) => reader.cancel(reason),
  });
  return new Response(body, { status: response.status, headers: response.headers });
}

/** Encodes pi-ai `events` as the gateway does and plays them into the pi-messages client. */
export function replay(frames: string, modelId = "faux-1") {
  return client(async () => new Response(frames, {
    headers: { "content-type": "text/event-stream" },
  }), "unused", modelId);
}

/** Every event of a stream, and its final message. */
export async function drain(stream: AsyncIterable<AssistantMessageEvent> & {
  result(): Promise<AssistantMessage>;
}): Promise<{ events: AssistantMessageEvent[]; message: AssistantMessage }> {
  const events: AssistantMessageEvent[] = [];
  for await (const event of stream) {
    events.push(event);
  }
  return { events, message: await stream.result() };
}

/** The gateway's `/v1/messages` with `env` and `catalog`. */
export function gatewayHandler(env: Env, catalog: Catalog = builtinCatalog()) {
  return (request: Request) => messages(request, env, catalog);
}

export { fauxAssistantMessage };
