/**
 * The agent's one model provider, `gateway` (gateway research §2.3; agent research §2.1;
 * tasks 4 G2, 5 A1): pi-ai's own pi-messages client, its `fetch` the object's `GATEWAY`
 * service binding, inside the envelope wrapper that reserves, stamps and settles each call.
 *
 * pi resolves its model `{provider: "gateway", modelId}` at each request against
 * `getModels`, which lists, from the owner's active connections (`connections.ts`), each
 * connected model as `<connection>/<model>` with the metadata the gateway's setup check
 * gave, and `auto` (`auto.ts`) when there is at least one. With none, pi settles the turn
 * `unanswered: no_model`, loudly. The object loads no provider SDK and no catalog: provider
 * code is the gateway's, inside pi-ai.
 *
 * Each call, in order:
 * 1. The target: the model named, or `auto`'s route over the request's transcript.
 * 2. The turn: the submitted input at the origin of the request's causal chain, from the
 *    telemetry context pi gives it (`origin.ts`); none is the call's error, `no_run`, and
 *    nothing is reserved.
 * 3. An OAuth credential expiring soon is refreshed first, under the refresh lease.
 * 4. **Reserve**, in one unit of the write gate: the tools the request offers the model,
 *    read from its own transcript (pi carries them in its system entries), exactly
 *    `MODEL_TOOLS` or none, else `tool_surface`, so no widening of a conversation's tools,
 *    by any path, reaches a model; the authority the request's conversation
 *    runs under (found by its provider session, `OwnerAgent.modelAuthority`; a revoked
 *    client's is `outside_authority`, a request of no conversation the object made
 *    `no_conversation`, a conversation whose stored tools are not the five
 *    `tool_surface`), the connection's blob at its current version, the owner policy's
 *    generation and model caps, and the reservation (`usage.ts`), so the envelope carries
 *    exactly what was reserved under, and concurrent calls never pass a cap. A refusal is
 *    the call's error, named by its rule, and kept as a receipt.
 * 5. **Dispatched**, in a unit of its own: the row still reserved, the authority still
 *    standing, the policy at the same generation, the connection at the same version, the
 *    call not aborted.
 * 6. The call: pi-messages to `GATEWAY.fetch`, with the envelope in `x-ta-envelope`
 *    (owner, connection, version, blob, reservation id and output cap, policy generation,
 *    deadline; the gateway's `envelope.ts` reads it) and the model sent as the upstream's
 *    own id. The owner is the object's own name: the gateway never chooses one. The output
 *    is capped at the reservation, which the gateway clamps to as well. pi's abort signal
 *    rides the request: the runtime cancels the service binding's stream, and the gateway
 *    then the upstream's (the abort test watches the upstream see it).
 * 7. Every event goes to pi and, as it comes, to the live tee (`live-tee.ts`) under the
 *    request's `sessionId`, so the owner's views stream token by token while pi commits
 *    once a second.
 * 8. **Settle**, in a unit, before pi sees the end, so the turn's next reservation reads it
 *    (`outcomeOf`): `succeeded` with pi-ai's `Usage` from `done`; `failed` when the gateway
 *    answered with a refusal's status, its own or the provider's before any output;
 *    `unknown` for anything else, keeping the reservation. A blob the gateway resealed
 *    under a newer kid is stored by compare-and-swap. Once reserved, a call never leaves
 *    its row open in this instance: a dispatch or settle unit that throws, or anything
 *    else that does, abandons it (`not_sent` before dispatch, `unknown` after).
 *
 * Every refusal is a fixed code in the answer's `errorMessage`. Ours are none that pi-ai
 * reads as retryable. A refusal the gateway answered with a status is given to pi as the
 * gateway's own code, read from its answer's body, never as the pi-messages client's
 * `<status> <text>: ...` wording, whose status would make every one of them retryable. So
 * pi's own classifier (`isRetryableAssistantError`) decides on the gateway's words: it
 * retries a rate limit, a timeout, an overload and a server error, and no refusal of the
 * key or the request (the gateway's `upstreamCode`). No secret is ever in this module's
 * hands but the sealed blob it forwards.
 */
import {
  type Api, type AssistantMessage, type AssistantMessageEvent,
  type AssistantMessageEventStream, getCurrentTools, type Message, type Model,
  normalizeContext, type Provider,
  type SimpleStreamOptions, type TranscriptContext, type Usage as PiUsage,
} from "@earendil-works/pi-ai";
import { streamSimple as piMessages } from "@earendil-works/pi-ai/api/pi-messages";
import { estimateContextTokens } from "@earendil-works/pi-ai/utils/estimate";
import { createAssistantMessageEventStream } from "@earendil-works/pi-ai/utils/event-stream";

import { AUTHORITY_RULE, NO_CONVERSATION, offersFive, TOOL_SURFACE } from "./authority";
import { type Candidate, physicalId, ranked, route } from "./auto";
import type { Connections, Credential, GatewayService, Usable } from "./connections";
import type { Write } from "./gate";
import type { LiveTee } from "./live-tee";
import { NO_RUN, turnOf } from "./origin";
import { costMicros, type Outcome, type Reserved, type Usage } from "./usage";

/** The provider's id, and its virtual model's. */
export const GATEWAY_PROVIDER = "gateway";
export const AUTO_MODEL = "auto";
/** Where pi-messages posts; the service binding ignores the host, the gateway checks the path. */
export const GATEWAY_BASE_URL = "https://gateway.internal/v1";
/** The envelope's header (the gateway's `ENVELOPE_HEADER`). */
export const ENVELOPE_HEADER = "x-ta-envelope";
/** How long a call may run: inside the gateway's 15-minute bound, with room for skew. */
export const CALL_DEADLINE_MS = 14 * 60 * 1000;

/** What the provider is built from: the object's own parts. */
export interface ProviderParts {
  readonly connections: Connections;
  readonly usage: Usage;
  readonly write: Write;
  readonly gateway: () => GatewayService | undefined;
  /** The owner the object is named by. */
  readonly owner: () => string;
  readonly tee: LiveTee;
  /** The object's clock, in seconds. */
  readonly clock: () => number;
  /**
   * The authority the request's conversation runs under, by the request's provider
   * session: a check the reserve and dispatch units run, answering a refusal's rule or null
   * (`OwnerAgent.modelAuthority`).
   */
  readonly authority: (sessionId: string | undefined) => Promise<() => string | null>;
}

/** The fields of `x-ta-envelope`, exactly as the gateway's `envelope.ts` takes them. */
export interface EnvelopeFields {
  readonly owner: string;
  readonly connection: string;
  readonly version: number;
  readonly blob: string;
  readonly reservation: { readonly id: string; readonly max_output_tokens: number };
  readonly policy_generation: number;
  readonly deadline: number;
}

/** The header value: base64url of UTF-8 JSON, since an owner may be any printable Unicode. */
export function envelopeHeader(fields: EnvelopeFields): string {
  const value: EnvelopeFields = {
    owner: fields.owner, connection: fields.connection, version: fields.version,
    blob: fields.blob,
    reservation: { id: fields.reservation.id,
                   max_output_tokens: fields.reservation.max_output_tokens },
    policy_generation: fields.policy_generation, deadline: fields.deadline,
  };
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64url");
}

/** A connected model as pi lists it: pi-messages to the gateway, with its own metadata. */
export function listed(candidate: Candidate, id: string = physicalId(candidate),
                       name = `${candidate.display_name}: ${candidate.model.name}`):
    Model<"pi-messages"> {
  const model = candidate.model;
  return {
    id, name, api: "pi-messages", provider: GATEWAY_PROVIDER, baseUrl: GATEWAY_BASE_URL,
    reasoning: model.reasoning, input: [...model.input],
    cost: { input: model.cost.input, output: model.cost.output,
            cacheRead: model.cost.cacheRead, cacheWrite: model.cost.cacheWrite },
    contextWindow: model.contextWindow, maxTokens: model.maxTokens,
  };
}

function emptyUsage(): PiUsage {
  return { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
           cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };
}

/** A call that ends before any answer: its error, named by a fixed code. */
function failed(model: Model<Api>, code: string, aborted: boolean): AssistantMessageEvent {
  const error: AssistantMessage = {
    role: "assistant", content: [], api: model.api, provider: model.provider, model: model.id,
    usage: emptyUsage(), stopReason: aborted ? "aborted" : "error", errorMessage: code,
    timestamp: Date.now(),
  };
  return { type: "error", reason: aborted ? "aborted" : "error", error };
}

/** A refusal's fixed code: our own refusals' words, and nothing else's. */
function codeOf(error: unknown, fallback: string): string {
  const message = error instanceof Error ? error.message : "";
  return /^[a-z_]{1,40}(?:: [a-z_]{1,40})?$/.test(message) ? message : fallback;
}

/** What the call to the gateway saw: its status, the code of a refusal, a blob it resealed. */
export interface Seen {
  status?: number;
  code?: string;
  resealed?: unknown;
}

/** A gateway code, as its failure answers carry it. */
const GATEWAY_CODE = /^[a-z_]{1,40}$/;

/**
 * How a dispatched call settles: `succeeded` from its `done`; `failed` when the gateway
 * answered with a refusal's status (nothing was produced, so nothing is owed); `unknown`
 * for anything else, a stream lost or an error after it started included.
 */
export function outcomeOf(final: AssistantMessageEvent | undefined, seen: Seen): Outcome {
  if (final?.type === "done") {
    return { state: "succeeded", tokens: tokensOf(final.message.usage),
             cost: costMicros(final.message.usage.cost.total) };
  }
  return seen.status !== undefined && seen.status !== 200 ? { state: "failed" }
    : { state: "unknown" };
}

export class GatewayProvider implements Provider<Api> {
  readonly id = GATEWAY_PROVIDER;
  readonly name = "TinyAssets gateway";
  /** The gateway takes no bearer of ours: the envelope is the call's authority. */
  readonly auth = Object.freeze({ apiKey: Object.freeze({
    name: "TinyAssets gateway",
    resolve: async () => ({ auth: { apiKey: GATEWAY_PROVIDER } }),
  }) });
  readonly #parts: ProviderParts;
  /** The ranking of the connections as last read: re-ranked only when they change. */
  #ranked: { usable: readonly Usable[]; candidates: readonly Candidate[] } | null = null;

  constructor(parts: ProviderParts) {
    this.#parts = parts;
  }

  /** The connected models, strongest first (`usable` is the same list until a change). */
  #candidates(): readonly Candidate[] {
    const usable = this.#parts.connections.usable();
    if (this.#ranked?.usable !== usable) {
      this.#ranked = { usable, candidates: ranked(usable) };
    }
    return this.#ranked.candidates;
  }

  /** The owner's connected models and, with any, `auto` mirroring the strongest. */
  getModels(): readonly Model<Api>[] {
    const candidates = this.#candidates();
    const strongest = candidates[0];
    if (strongest === undefined) {
      return [];
    }
    return [listed(strongest, AUTO_MODEL, `Auto (${strongest.model.name})`),
            ...candidates.map((candidate) => listed(candidate))];
  }

  /** A full-options request is served as a simple one: pi-messages carries the same. */
  readonly stream: Provider<Api>["stream"] = (model, context, options) =>
    this.streamSimple(model, context, options as SimpleStreamOptions | undefined);

  streamSimple(model: Model<Api>, context: TranscriptContext,
               options: SimpleStreamOptions = {}): AssistantMessageEventStream {
    const out = createAssistantMessageEventStream();
    this.#call(model, context, options, out).catch(() => {
      console.error("a gateway model call failed in the object");  // no ids or content
      out.push(failed(model, "gateway_provider: internal", options.signal?.aborted ?? false));
    });
    return out;
  }

  /** One model call, from its target to its settlement; every outcome ends `out`. */
  async #call(model: Model<Api>, context: TranscriptContext, options: SimpleStreamOptions,
              out: AssistantMessageEventStream): Promise<void> {
    const { connections, usage, write, tee, clock } = this.#parts;
    const session = options.sessionId;
    const end = (event: AssistantMessageEvent) => {
      tee.publish(session, event);
      out.push(event);
    };
    const aborted = () => options.signal?.aborted ?? false;
    let owner: string;
    try {
      owner = this.#parts.owner();
    } catch {
      return end(failed(model, "no_owner", aborted()));
    }
    const candidates = this.#candidates();
    const target = model.id === AUTO_MODEL ? route(candidates, context.messages)
      : candidates.find((candidate) => physicalId(candidate) === model.id);
    if (target === undefined) {
      return end(failed(model, "no_connected_model", aborted()));
    }
    const physical = listed(target);
    let turn: string;
    try {
      turn = turnOf(options.telemetry);
    } catch (error) {
      return end(failed(physical, codeOf(error, NO_RUN), aborted()));
    }
    // What the request offers the model, read from the messages `#send` sends and nothing
    // else: a refusal is the reserve unit's (its authority check, which `usage.reserve` runs
    // before any cap, answers it first), kept as a refused receipt; it refreshes nothing.
    const surface = offersFive(getCurrentTools(context.messages).map((tool) => tool.name))
      ? null : TOOL_SURFACE;
    let authority: () => string | null;
    try {
      const standing = await this.#parts.authority(session);
      authority = () => surface ?? standing();
    } catch {
      return end(failed(physical, AUTHORITY_RULE, aborted()));
    }
    try {
      if (surface === null) {
        await connections.fresh(target.connection);
      }
    } catch (error) {
      return end(failed(physical, codeOf(error, "credential_refresh_failed"), aborted()));
    }
    const asked = Math.min(options.maxTokens ?? physical.maxTokens, physical.maxTokens);
    let call: { credential: Credential; reserved: Reserved };
    try {
      call = await write(() => {
        const credential = connections.credential(target.connection, Date.now());
        const reserved = usage.reserve({
          turn, connection: target.connection, source: target.source,
          model: target.model.id, inputTokens: estimateContextTokens(context).tokens,
          maxOutputTokens: Math.max(1, asked),
          reasoning: options.reasoning !== undefined && physical.reasoning,
          price: { input: target.model.cost.input, output: target.model.cost.output },
        }, clock(), authority);
        return { credential, reserved };
      });
    } catch (error) {
      return end(failed(physical, codeOf(error, "connection_unavailable"), aborted()));
    }
    const { credential, reserved } = call;
    if (!reserved.ok) {
      return end(failed(physical, reserved.rule === AUTHORITY_RULE
        || reserved.rule === NO_CONVERSATION || reserved.rule === TOOL_SURFACE ? reserved.rule
        : `model_budget: ${reserved.rule}`, aborted()));
    }
    // From here the row is this instance's to close: settled, or not sent, or abandoned.
    let closed = false;
    try {
      const sent = await write(() => usage.dispatched(reserved.id, clock(),
        aborted() ? "aborted"
          : authority() ?? (connections.current(credential.connection, credential.version)
            ? null : "connection_changed")));
      if (!sent.sent) {
        closed = true;
        return end(failed(physical, `model_call_not_sent: ${sent.rule}`, aborted()));
      }
      const { final, seen } = await this.#send(physical, target, context, options, out,
                                               credential, owner, reserved);
      try {
        await write(() => usage.settle(reserved.id, outcomeOf(final, seen), clock()));
      } catch {
        console.error("a model call's settlement could not be kept; it is abandoned");
        await this.#abandon(reserved.id);
      }
      closed = true;
      if (seen.resealed !== undefined) {
        const resealed = seen.resealed;
        await write(() => connections.resealed(credential.connection, credential.version,
                                               resealed, Date.now()))
          .catch(() => console.error("a resealed blob could not be kept"));
      }
      end(seen.status !== undefined && seen.status !== 200 && seen.code !== undefined
        ? failed(physical, seen.code, aborted())
        : final ?? failed(physical, "gateway_provider: stream_lost", aborted()));
    } finally {
      if (!closed) {
        await this.#abandon(reserved.id);
      }
    }
  }

  /** A reservation abandoned in a unit of its own (`Usage.abandon`); a failure is logged. */
  async #abandon(id: string): Promise<void> {
    const { usage, write, clock } = this.#parts;
    await write(() => usage.abandon(id, clock()))
      .catch(() => console.error("a model call could not be abandoned"));
  }

  /**
   * The call to the gateway, its events streamed to pi and the tee as they come, up to
   * its terminal event (undefined when the stream was lost without one).
   */
  async #send(physical: Model<"pi-messages">, target: Candidate, context: TranscriptContext,
              options: SimpleStreamOptions, out: AssistantMessageEventStream,
              credential: Credential, owner: string,
              reserved: { readonly id: string; readonly max_output_tokens: number;
                          readonly policy_generation: number }):
      Promise<{ final: AssistantMessageEvent | undefined; seen: Seen }> {
    const { tee } = this.#parts;
    const session = options.sessionId;
    const seen: Seen = {};
    let final: AssistantMessageEvent | undefined;
    try {
      const header = envelopeHeader({
        owner, connection: credential.connection,
        version: credential.version, blob: credential.blob,
        reservation: { id: reserved.id, max_output_tokens: reserved.max_output_tokens },
        policy_generation: reserved.policy_generation,
        deadline: Date.now() + CALL_DEADLINE_MS,
      });
      const upstreamId = target.model.id;
      // Exactly the messages whose offered tools the reservation checked: no other context
      // field, and no caller's payload hook, which could offer what no check saw (the
      // gateway refuses a context carrying anything but `messages`).
      const events = piMessages(physical, normalizeContext({ messages: context.messages }), {
        ...options,
        apiKey: GATEWAY_PROVIDER,
        maxTokens: reserved.max_output_tokens,
        headers: { ...options.headers, "x-ta-envelope": header },
        fetch: (input, init) => this.#fetch(seen, input, init),
        onPayload: async (payload) => ({ ...(payload as Record<string, unknown>),
                                         model: upstreamId }),
      });
      for await (const event of events) {
        if (event.type === "done" || event.type === "error") {
          final = event;
          break;
        }
        tee.publish(session, event);
        out.push(event);
      }
    } catch {
      final = undefined;  // settled unknown; pi-ai's stream itself never throws
    }
    return { final, seen };
  }

  /** One request to the gateway, through the service binding only. */
  async #fetch(seen: Seen, input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const gateway = this.#parts.gateway();
    if (gateway === undefined) {
      throw new Error("gateway_unbound");
    }
    const response = await gateway.fetch(new Request(input, init));
    seen.status = response.status;
    if (response.status !== 200) {
      const code = await response.clone().json()
        .then((body) => (body as { error?: { code?: unknown } }).error?.code, () => undefined);
      if (typeof code === "string" && GATEWAY_CODE.test(code)) {
        seen.code = code;
      }
    }
    const resealed = response.headers.get("x-ta-resealed");
    if (resealed !== null) {
      try {
        seen.resealed = JSON.parse(resealed);
      } catch {
        seen.resealed = undefined;
      }
    }
    return response;
  }
}

/** The tokens a settled call used: pi-ai's total, else its parts. */
function tokensOf(usage: PiUsage): number {
  const parts = usage.input + usage.output + usage.cacheRead + usage.cacheWrite;
  const total = Number.isSafeInteger(usage.totalTokens) && usage.totalTokens > 0
    ? usage.totalTokens : parts;
  return Number.isSafeInteger(total) && total > 0 ? total : 0;
}
