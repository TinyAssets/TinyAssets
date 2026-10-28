/**
 * `POST /v1/messages`: one model call, spoken as pi-ai's own `pi-messages` protocol
 * (research §2.2, §2.3), the protocol pi's Radius gateway speaks. The owner object's one
 * pi-ai provider (`gateway`, API `pi-messages`) posts `{model, context, options}` here
 * with the envelope (`envelope.ts`) and reads back server-sent pi-messages events.
 *
 * 1. Open the envelope's blob under the owner, connection and version it names.
 * 2. Take pi-ai's provider for the sealed `upstream.provider` (`catalog.ts`) and the
 *    requested model from its catalog, sent to the sealed `base_url`.
 * 3. The sealed endpoints hold the base URL's origin: `credential.ts` refuses, on every
 *    seal and every open, a credential whose base URL is off its endpoints.
 * 4. Clamp `maxTokens` to the reservation, on the request and on the model, so no
 *    adapter (a thinking budget among them) can ask for more.
 * 5. Run pi-ai's `streamSimple` with the opened secret, through a `fetch` that sends
 *    only to the sealed endpoints, with redirects never followed.
 * 6. Re-encode each event as pi-messages (`encode.ts`), `done` carrying pi-ai's `Usage`.
 *
 * Failures before the stream are answered with a fixed code (`failure.ts`); after it
 * starts, the stream ends with a pi-messages `error` event whose text is a fixed code,
 * classified from what the guarded fetch saw (the HTTP status), never from the
 * adapter's message, which can quote the provider's body.
 *
 * The call is cut when the caller stops reading (the response stream's cancel), and at
 * the envelope's deadline.
 */
import type {
  AssistantMessage, AssistantMessageEvent, Context, FetchFunction, SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import { createModels } from "@earendil-works/pi-ai/models";

import { type Catalog, NO_AMBIENT, providerFor } from "./catalog";
import { allows, bearer, type Endpoint } from "./credential";
import { encode, encodeError, frame } from "./encode";
import { ENVELOPE_HEADER, envelope } from "./envelope";
import { type Code, codeOf, Failure, failureResponse, upstreamCode } from "./failure";
import { type KeyringEnv, open } from "./keyring";

/** A request body's bound: a context with images can be large, a whole one never this. */
export const BODY_MAX = 16 * 1024 * 1024;

/**
 * The pi-ai 1.4.0 APIs whose adapters refuse a custom `fetch`
 * (`google-generative-ai.js:36-38`, `google-vertex.js:40`): their SDK takes no fetch.
 * They are sent with the global fetch, held only by the base URL's origin check (step 3).
 */
const GLOBAL_FETCH_APIS: readonly string[] = Object.freeze(["google-generative-ai", "google-vertex"]);

const THINKING: readonly string[] = Object.freeze(["minimal", "low", "medium", "high", "xhigh", "max"]);
const RETENTION: readonly string[] = Object.freeze(["none", "short", "long"]);
const OPTIONS: readonly string[] = Object.freeze(["temperature", "maxTokens", "reasoning",
                                                  "cacheRetention", "sessionId", "toolChoice"]);
const CONTEXT: readonly string[] = Object.freeze(["messages", "systemPrompt", "tools"]);
const SESSION = /^[\x21-\x7e]{1,256}$/;

/** What the guarded fetch saw, read when an error event is classified. */
interface Observed {
  status: number | undefined;
  refused: boolean;
  unreachable: boolean;
}

interface Call {
  readonly model: string;
  readonly context: Context;
  readonly options: Pick<SimpleStreamOptions, "temperature" | "maxTokens" | "reasoning"
                                              | "cacheRetention" | "sessionId" | "toolChoice">;
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The body, read up to `BODY_MAX` bytes; `too_large` past it, without reading the rest. */
async function boundedText(request: Request): Promise<string> {
  const declared = request.headers.get("content-length");
  if (declared !== null && !(/^\d+$/.test(declared) && Number(declared) <= BODY_MAX)) {
    throw new Failure("too_large");
  }
  if (request.body === null) {
    return "";
  }
  const reader = request.body.getReader();
  const parts: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    size += value.byteLength;
    if (size > BODY_MAX) {
      await reader.cancel();
      throw new Failure("too_large");
    }
    parts.push(value);
  }
  const whole = new Uint8Array(size);
  let at = 0;
  for (const part of parts) {
    whole.set(part, at);
    at += part.byteLength;
  }
  try {
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(whole);
  } catch {
    throw new Failure("invalid_request");
  }
}

/** The pi-messages request body, checked; the transcript itself is pi-ai's to read. */
function call(text: string): Call {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Failure("invalid_request");
  }
  if (!object(value) || Object.keys(value).some((key) => !["model", "context", "options"].includes(key))
      || typeof value["model"] !== "string" || value["model"].length === 0
      || value["model"].length > 256 || !object(value["context"])
      || !Array.isArray(value["context"]["messages"])
      || Object.keys(value["context"]).some((key) => !CONTEXT.includes(key))) {
    throw new Failure("invalid_request");
  }
  const options = value["options"] ?? {};
  if (!object(options) || Object.keys(options).some((key) => !OPTIONS.includes(key))) {
    throw new Failure("invalid_request");
  }
  const { temperature, maxTokens, reasoning, cacheRetention, sessionId, toolChoice } = options;
  if ((temperature !== undefined && (typeof temperature !== "number" || !Number.isFinite(temperature)))
      || (maxTokens !== undefined && (typeof maxTokens !== "number" || !Number.isSafeInteger(maxTokens)
                                      || maxTokens < 1))
      || (reasoning !== undefined && !THINKING.includes(reasoning as string))
      || (cacheRetention !== undefined && !RETENTION.includes(cacheRetention as string))
      || (sessionId !== undefined && (typeof sessionId !== "string" || !SESSION.test(sessionId)))
      || (toolChoice !== undefined && toolChoice !== "auto" && toolChoice !== "none")) {
    throw new Failure("invalid_request");
  }
  return {
    model: value["model"],
    context: value["context"] as unknown as Context,
    options: {
      ...(temperature === undefined ? {} : { temperature: temperature as number }),
      ...(maxTokens === undefined ? {} : { maxTokens: maxTokens as number }),
      ...(reasoning === undefined ? {} : { reasoning: reasoning as NonNullable<Call["options"]["reasoning"]> }),
      ...(cacheRetention === undefined
        ? {} : { cacheRetention: cacheRetention as NonNullable<Call["options"]["cacheRetention"]> }),
      ...(sessionId === undefined ? {} : { sessionId: sessionId as string }),
      ...(toolChoice === undefined ? {} : { toolChoice: toolChoice as "auto" | "none" }),
    },
  };
}

/**
 * A fetch that sends only where the credential is sealed for, never follows a redirect
 * (a redirect would carry the secret's header to wherever it points), and records the
 * status it saw for the error classification.
 */
export function guardedFetch(endpoints: readonly Endpoint[], observed: Observed): FetchFunction {
  return async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    if (!allows(endpoints, request.method, new URL(request.url))) {
      observed.refused = true;
      throw new Failure("endpoint_refused");
    }
    let response: Response;
    try {
      response = await fetch(request, { redirect: "manual" });
    } catch (error) {
      if (!request.signal.aborted) {
        observed.unreachable = true;
      }
      throw error;
    }
    observed.status = response.status;
    return response;
  };
}

/** The fixed code for a stream that ended in an error. */
function classify(observed: Observed, reason: "aborted" | "error", deadline: AbortSignal,
                  cancelled: AbortSignal): Code {
  if (deadline.aborted) {
    return "deadline";
  }
  if (reason === "aborted" || cancelled.aborted) {
    return "aborted";
  }
  if (observed.refused) {
    return "endpoint_refused";
  }
  if (observed.status !== undefined && observed.status >= 300) {
    return upstreamCode(observed.status);
  }
  return observed.unreachable ? "upstream_unreachable" : "upstream_error";
}

/** The response to `POST /v1/messages`. */
export async function messages(request: Request, env: KeyringEnv,
                               catalog: Catalog): Promise<Response> {
  try {
    return await serve(request, env, catalog);
  } catch (error) {
    return failureResponse(codeOf(error, "internal"));
  }
}

async function serve(request: Request, env: KeyringEnv, catalog: Catalog): Promise<Response> {
  if (request.method !== "POST") {
    return failureResponse("method_not_allowed");
  }
  const sent = envelope(request.headers.get(ENVELOPE_HEADER), Date.now());
  const body = call(await boundedText(request));
  const opened = await open(env, sent.binding, sent.blob);
  const { upstream, endpoints } = opened.credential;
  const provider = providerFor(catalog, upstream);
  const found = provider.getModels().find((model) => model.id === body.model);
  if (found === undefined) {
    throw new Failure("unknown_model");
  }
  if (found.api !== upstream.api) {
    throw new Failure("unsupported_source");
  }
  const cap = sent.reservation.max_output_tokens;
  const model = { ...found, baseUrl: upstream.base_url, maxTokens: Math.min(found.maxTokens, cap) };
  const maxTokens = Math.min(body.options.maxTokens ?? cap, cap);

  const cancelled = new AbortController();
  const deadline = AbortSignal.timeout(Math.max(0, sent.deadline - Date.now()));
  const signal = AbortSignal.any([cancelled.signal, deadline]);
  const observed: Observed = { status: undefined, refused: false, unreachable: false };
  const models = createModels({ authContext: NO_AMBIENT });
  models.setProvider(provider);
  let events: AsyncIterator<AssistantMessageEvent>;
  try {
    events = models.streamSimple(model, body.context, {
      ...body.options,
      maxTokens,
      apiKey: bearer(opened.credential),
      signal,
      ...(GLOBAL_FETCH_APIS.includes(upstream.api)
        ? {} : { fetch: guardedFetch(endpoints, observed) }),
    })[Symbol.asyncIterator]();
  } catch {
    // pi-ai normalizes the transcript before the stream exists; a malformed one throws.
    throw new Failure("invalid_request");
  }

  const encoder = new TextEncoder();
  let ended = false;
  const end = (controller: ReadableStreamDefaultController<Uint8Array>, message: AssistantMessage,
               reason: "aborted" | "error", code: Code) => {
    ended = true;
    controller.enqueue(encoder.encode(frame(encodeError(message, reason, code))));
    controller.close();
  };
  let last: AssistantMessage | undefined;
  const stream = new ReadableStream<Uint8Array>({
    async pull(controller) {
      if (ended) {
        return;
      }
      let next: IteratorResult<AssistantMessageEvent>;
      try {
        next = await events.next();
      } catch {
        // pi-ai's stream never throws; if it did, its text is not shown.
        cancelled.abort();
        end(controller, last ?? emptyMessage(model), "error", "internal");
        return;
      }
      if (next.done) {
        // pi-ai always ends a stream with `done` or `error`; one that does not is a bug there.
        cancelled.abort();
        end(controller, last ?? emptyMessage(model), "error", "upstream_error");
        return;
      }
      const event = next.value;
      if (event.type === "error") {
        end(controller, event.error, event.reason,
            classify(observed, event.reason, deadline, cancelled.signal));
        return;
      }
      last = event.type === "done" ? event.message : event.partial;
      let wire;
      try {
        wire = encode(event);
      } catch (error) {
        cancelled.abort();
        end(controller, last, "error", codeOf(error, "upstream_error"));
        return;
      }
      controller.enqueue(encoder.encode(frame(wire)));
      if (event.type === "done") {
        ended = true;
        controller.close();
      }
    },
    cancel() {
      ended = true;
      cancelled.abort();
    },
  });
  const headers = new Headers({
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-store",
    "x-ta-reservation": sent.reservation.id,
    "x-ta-policy-generation": String(sent.policy_generation),
  });
  if (opened.resealed !== undefined) {
    headers.set("x-ta-resealed", JSON.stringify(opened.resealed));
  }
  return new Response(stream, { status: 200, headers });
}

function emptyMessage(model: { api: string; provider: string; id: string }): AssistantMessage {
  return {
    role: "assistant", content: [], api: model.api, provider: model.provider, model: model.id,
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
             cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    stopReason: "error", timestamp: Date.now(),
  };
}
