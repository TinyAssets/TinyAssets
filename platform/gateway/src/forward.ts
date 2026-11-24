/**
 * Raw passthrough for sandbox egress (research §5.4 step 4.3), the credential check a
 * connection makes when it is set up (`verify`), the models its blob reaches by the catalog now
 * (`models`), and the reads of what its key can pay for (`credit`) and of the models its vendor
 * lists it served (`listing`).
 *
 * The sandbox's model clients speak each provider's own API to our egress door with
 * placeholder keys; the binder admits the request at the owner's object, which hands it
 * the blob, and calls `forward`. The gateway opens the blob, requires the request's
 * method, origin and path to be among the sealed endpoints, strips every credential the
 * request carried (the API-key query parameters; any other parameter its API's rules do not
 * allow is refused, `egressTarget`; every header its rules do not name is dropped,
 * `egressHeaders`, and named when the provider refuses the call, `logDropped`), injects the
 * sealed one by the sealed auth template, and sends it to the sealed origin. The provider's
 * own answer streams back, status and body, so the client sees its own API; only the secret
 * is redacted from it, in every spelling a provider could quote it back in (`redact.ts`). A
 * call is cut at `FORWARD_DEADLINE_MS`, and redirects are never followed. A silent answer is
 * its engine's to give up on: each engine's own client ends a stream that sends nothing for
 * 300 s or less (`limits-audit.md`, "Left to the engines"), and its leaving cancels the
 * answer here, which ends the provider's request.
 *
 * User CLIs speak their provider's wire format. An unmetered call (a read, a cited free
 * call) only moves bytes: its body streams through. A metered one is the one request the
 * gateway holds whole (egress shape research): under the pool's slots, and only while it
 * reads, rewrites and uploads it, it parses it once and asks the owner's object, through the
 * door's `reserve`, to reserve the call for the model and tier that parse read. The gateway
 * measures nothing it would keep: the binder counts the bytes it streams and settles the
 * owner's reservation.
 */
import type { Api, Model } from "@earendil-works/pi-ai";

import { type Catalog, modelInfos } from "./catalog";
import { bearer, type Credential } from "./credential";
import {
  allows, creditRead, egressHeaders, type EgressScope, egressTarget, freeOn, isService,
  listedBy, listingRead, type Meter, meteredModel, type ModelScope, paysBy, scopeApi,
  type ScopeApi, type Upstream,
} from "../../contract/src/egress-scope";
import {
  type KeyCredit, metering, MODEL_NAME, type MeterOutcome, type ModelListing, type Resealed,
  type Reservation, type Reserve, type SealedModels, succeeded, type Verified,
} from "../../contract/src/gateway";
import { taken } from "../../contract/src/handed";
import { boundedJson } from "./answer";
import { type GatewayCode, Refusal } from "../../contract/src/refusal";
import { codeOf, failureResponse, upstreamCode } from "./failure";
import {
  aliased, apiRules, fallbackList, manyChoices, paidTool, READS, servedPriced,
  tierMultiplier, unpricedOption,
} from "../../contract/src/api-rules";
import { keyed, valueAtPath } from "../../contract/src/checks";
import {
  clamped, meteredRecord, meteredUsage, Meters, type Pricing,
} from "./meter";
import { type Binding, type KeyringEnv, open, type Opened } from "./keyring";
import {
  gatewaySlots, heldDeadlineMs, Hold, raced, weighedBody, weightSlots,
} from "./held";
import { timeout } from "./deadline";
import { redaction } from "./redact";
import { RESEALED_HEADER } from "../../contract/src/egress";
import { EGRESS_BODY_MAX, EGRESS_DEADLINE_S, SLOT_WAIT_MS } from "../../contract/src/limits";

/**
 * The longest one forwarded request may run: the egress deadline (`EGRESS_DEADLINE_S`, a
 * day, the sandbox's own continuous-runtime ceiling), which the egress door and the owner's
 * object keep too. A policy write ends a stream sooner, through the caller's stop.
 */
const FORWARD_DEADLINE_MS = EGRESS_DEADLINE_S * 1000;

/** How long the setup check waits for the provider. */
const VERIFY_DEADLINE_MS = 15 * 1000;

/** The bytes each write of a metered call's upload carries. */
const UPLOAD_SLICE = 64 * 1024;

/** A metered call's rewritten body on its way up, and its end (its slots let go). */
interface Upload {
  readonly body: ReadableStream<Uint8Array>;
  /** Stops the upload where it is: the provider's request ended first. */
  readonly stop: () => void;
}

/**
 * `whole` sent as a fixed-length stream, so the provider gets the `Content-Length` it always
 * did, in `UPLOAD_SLICE` writes; the bytes are held until the last write is taken (or the
 * upload is stopped), and `ended` runs then, once.
 */
function uploaded(whole: Uint8Array, ended: () => void): Upload {
  let bytes: Uint8Array | null = whole;
  const { readable, writable } = new FixedLengthStream(whole.byteLength);
  const writer = writable.getWriter();
  void (async () => {
    try {
      for (let at = 0; bytes !== null && at < bytes.byteLength; at += UPLOAD_SLICE) {
        await writer.write(bytes.subarray(at, at + UPLOAD_SLICE));
      }
      if (bytes !== null) {
        bytes = null;
        await writer.close();
      }
    } catch {
      // The provider's request ended before the upload did: nothing more to send.
    } finally {
      bytes = null;
      ended();
    }
  })();
  return {
    body: readable,
    stop: () => {
      if (bytes !== null) {
        bytes = null;
        writer.abort().catch(() => undefined);
      }
    },
  };
}

/** An answer's headers that belong to one hop, never passed back. */
const HOP_HEADERS: readonly string[] = Object.freeze([
  "connection", "keep-alive", "te", "trailer", "transfer-encoding", "upgrade", "content-length",
]);

/** Whether a header is ours or Cloudflare's: never passed back, nor a sandbox's drop. */
function internal(name: string): boolean {
  return name.startsWith("x-ta-") || name.startsWith("cf-");
}

/**
 * The most names one drop line shows, and the longest name it shows: the line stays a few
 * kilobytes whatever a request carries; a name past either is counted, not shown.
 */
const DROPS_SHOWN = 64;
const DROP_NAME_SHOWN = 64;

/** The provider's answers that refuse the call as sent (`upstreamCode`), not its own state. */
const AS_SENT: readonly GatewayCode[] = Object.freeze(["upstream_unauthorized",
                                                       "upstream_rejected"]);

/**
 * Logs which of the sandbox's request headers (`sent`) were not sent on (`forwarded`: what
 * `egressHeaders` kept and the gateway set), when the provider refused the call as sent
 * (`AS_SENT`: a 4xx, but a timeout's or a rate limit's). A header its API needs that its
 * rules do not name shows only as that refusal, and this line names it, for its API's data
 * to take (`api-rules.ts` `headers`). Names only (HTTP's tokens, lower-cased), never a value,
 * which may carry a secret; a call the provider served logs nothing, so an engine's telemetry
 * headers, dropped on every call, never flood the log.
 */
function logDropped(sent: Headers, forwarded: Headers, status: number, api: string): void {
  const dropped = [...sent.keys()].filter((name) => !forwarded.has(name) && !internal(name));
  if (!AS_SENT.includes(upstreamCode(status)) || dropped.length === 0) {
    return;
  }
  const shown = dropped.filter((name) => name.length <= DROP_NAME_SHOWN).slice(0, DROPS_SHOWN);
  console.warn(`a sealed ${api} call its provider refused (${status}) had ` +
               `${dropped.length} request headers dropped: ` +
               `${shown.join(" ")}${shown.length < dropped.length ? " ..." : ""}`);
}

/** The header value the sealed template makes of the credential's secret. */
function authValue(credential: Credential): string {
  const scheme = credential.upstream.auth.scheme;
  return scheme === "" ? bearer(credential) : `${scheme} ${bearer(credential)}`;
}

/** The secrets a credential holds, each redacted from what the provider sends back. */
function secretsOf(credential: Credential): string[] {
  if (credential.kind === "api_key") {
    return [credential.secret];
  }
  const { access, refresh, client_auth: auth } = credential.oauth;
  return [access, ...(refresh === undefined ? [] : [refresh]),
          ...(auth.method === "none" ? [] : [auth.secret])];
}

/** A refusal's answer (`failure.ts`), carrying a resealed credential when there is one. */
function refusal(code: GatewayCode, resealed?: Resealed): Response {
  const extra: Record<string, string> = {};
  if (resealed !== undefined) {
    extra[RESEALED_HEADER] = JSON.stringify(resealed);
  }
  return failureResponse(code, extra);
}

/**
 * A sandbox's model call, metered, once the owner's object admitted it: held to pi-ai's
 * own model call for the sealed API it is one of (`scopeApi`, that API's `Meters`); its slots
 * taken as it arrives (`held.ts`), its body read to its `Content-Length` and parsed once;
 * refused for what no reservation bounds; its model and tier read from that parse and
 * reserved through
 * `reserve`; then its output cap clamped to the reservation's `max_output_tokens`, so what
 * it can cost is what the owner's object reserved, and forwarded as a read is. The outcome
 * comes once the answer ends: `failed` when nothing was sent or the provider refused before
 * any output, the usage pi-ai read from the answer, priced by the catalog (`meteredUsage`),
 * or `unknown`. `stop`, closed or errored by the caller, aborts the provider's request at
 * any point.
 */
export async function forwardMetered(env: KeyringEnv, bound: Binding, blob: unknown,
                                     request: Request, reserve: Reserve,
                                     stop: ReadableStream<Uint8Array>, catalog: Catalog):
    Promise<{ response: Response; outcome: Promise<MeterOutcome> }> {
  return send(env, bound, blob, request, stop, { reserve, catalog });
}

/** A metered call ready to send, or the code it is refused with. */
type Prepared = { readonly refused: GatewayCode }
  | { readonly upload: Upload; readonly model: Model<Api>; readonly pricing: Pricing };

/** A metered call's body serialized once it is checked, reserved and clamped, or its code. */
type Serialized = { readonly refused: GatewayCode }
  | { readonly text: string; readonly model: Model<Api>; readonly pricing: Pricing };

/**
 * The metered call's body, read (at most `length` bytes) and weighed, serialized
 * (`serialized`) and encoded once, each step's input unreachable once the next holds its
 * output (the text once this returns, as the upload starts); then uploading, `ended` run
 * when its last write is taken. The tree lives only in `serialized`'s frame, gone before the
 * encode: a running frame keeps what it passed to a call after its own variable lets it go
 * (measured in workerd: the egress memory leg, `gateway/memory/egress.mjs`, read the tree
 * beside the text and its bytes here).
 */
async function prepared(request: Request, length: number, upstream: Upstream,
                        row: ScopeApi, meters: Meters, path: string, catalog: Catalog,
                        reserve: Reserve, hold: Hold, signal: AbortSignal,
                        ended: () => void): Promise<Prepared> {
  // The body, read under slots taken as it arrives; then its weight's slots, before the parse.
  const read = await weighedBody(request, length, hold, signal);
  await hold.grow(weightSlots(read.weight));
  const call = await serialized(read, request.method, upstream, row, meters, path, catalog,
                                reserve, signal);
  if ("refused" in call) {
    return call;
  }
  const bytes = new TextEncoder().encode(call.text);
  return { upload: uploaded(bytes, ended), model: call.model, pricing: call.pricing };
}

/**
 * The weighed body parsed once, its text let go from `read` once parsed; checked, reserved,
 * clamped in place and serialized. Refused before the reserve for what the reservation cannot
 * bound, so a refused shape is never charged.
 */
async function serialized(read: Awaited<ReturnType<typeof weighedBody>>, method: string,
                          upstream: Upstream, row: ScopeApi, meters: Meters, path: string,
                          catalog: Catalog, reserve: Reserve, signal: AbortSignal):
    Promise<Serialized> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(read.text!);
  } catch {
    return { refused: "invalid_request" };
  }
  read.text = null;
  const rules = apiRules(row.api);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)
      || rules === null) {
    return { refused: "unmetered" };
  }
  // A key the rules read, spelled another way, could say what they did not read.
  if (aliased(rules, parsed)) {
    return { refused: "egress_aliased_key" };
  }
  // The model the call names, read where pi-ai names it; then pi-ai's own call for that
  // model, which says where its output cap goes (a model whose record cannot cap it is
  // never metered).
  const id = meteredModel(row.meter!, method, path, parsed);
  if (id === null) {
    return { refused: "unmetered" };
  }
  const model = MODEL_NAME.test(id) ? meteredRecord(catalog, upstream, row, id) : null;
  if (model === null) {
    return { refused: "unknown_model" };
  }
  const meter = await meters.of(model);
  if (meter === null || meteredModel(meter, method, path, parsed) !== id) {
    return { refused: "unmetered" };
  }
  // What the reservation cannot bound: several choices, or a server-side tool the
  // provider bills beyond the tokens pi-ai prices, or an option it does not price.
  if (manyChoices(rules, parsed)) {
    return { refused: "egress_multiple_choices" };
  }
  if (paidTool(rules, parsed)) {
    return { refused: "egress_unmetered_tool" };
  }
  if (unpricedOption(rules, parsed)) {
    return { refused: "egress_unpriced_option" };
  }
  const tier = rules.tier === null ? null : valueAtPath(parsed, [rules.tier.key]) ?? null;
  if (tier !== null && (typeof tier !== "string" || !MODEL_NAME.test(tier))) {
    return { refused: "egress_unpriced_option" };
  }
  if (tierMultiplier(rules, tier) === null) {
    return { refused: "egress_unpriced_option" };
  }
  // The call reserved for the model and tier this parse read, the only reading there is.
  // The reserve, to the caller's stop and the pool's wait: an object that does not answer
  // within it leaves the call `busy`, its units let go. A reserve already in flight when the
  // caller stops may still commit at the owner's object, which then keeps the charge (the
  // door settles a stopped call `unknown`).
  const reserving = new AbortController();
  const late = setTimeout(() => reserving.abort(new Refusal("busy")), SLOT_WAIT_MS);
  let reserved: Reservation;
  try {
    reserved = await raced(reserve({ model: id, tier }),
                           AbortSignal.any([signal, reserving.signal]));
  } finally {
    clearTimeout(late);
  }
  if (!("metering" in reserved)) {
    return { refused: "egress_reserve_refused" };
  }
  const terms = metering(reserved.metering);
  if (terms.model !== id || terms.tier !== tier) {
    throw new Refusal("internal");  // the object reserved another call than it was asked
  }
  if (read.bytes > terms.input_bytes) {
    return { refused: "invalid_request" };
  }
  const pricing = { fallbacks: fallbackList(rules, parsed), routed: terms.routed };
  const text = JSON.stringify(clamped(meter, parsed as Record<string, unknown>,
                                      terms.max_output_tokens,
                                      { provider: upstream.provider, api: row.api }));
  return { text, model, pricing };
}

/**
 * A sandbox's free call on a sealed endpoint, forwarded unmetered: a read (`GET`, `HEAD`: a
 * model list), which carries no model use, or a call its API's rules cite as free
 * (`freeCall`: a token count). Any other is `unmetered`, so no call the owner's object did
 * not charge reaches a provider's model. A service's every call on its sealed URL is free:
 * it reaches no model (`isService`). Its request (its method, path, query, the headers its
 * rules name, and body; its host is ignored) goes to the credential's sealed origin; the
 * provider's own answer passes through.
 */
export async function forwardRead(env: KeyringEnv, bound: Binding, blob: unknown,
                                  request: Request, stop: ReadableStream<Uint8Array>):
    Promise<{ response: Response; outcome: Promise<MeterOutcome> }> {
  return send(env, bound, blob, request, stop, null);
}

const FAILED: MeterOutcome = Object.freeze({ state: "failed" });
const UNKNOWN: MeterOutcome = Object.freeze({ state: "unknown" });

/** One admitted call: metered (its reserve and catalog), or, with none, a free call. */
async function send(env: KeyringEnv, bound: Binding, blob: unknown, request: Request,
                    stop: ReadableStream<Uint8Array>,
                    metered: { reserve: Reserve; catalog: Catalog } | null):
    Promise<{ response: Response; outcome: Promise<MeterOutcome> }> {
  const refused = (code: GatewayCode, resealed?: Resealed) =>
    ({ response: refusal(code, resealed), outcome: Promise.resolve(FAILED) });
  const cancelled = new AbortController();
  stop.getReader().read().then(() => cancelled.abort(), () => cancelled.abort());
  let opened: Opened;
  try {
    opened = await open(env, bound, blob);
  } catch (error) {
    return refused(codeOf(error, "internal"));
  }
  const { credential, resealed } = opened;
  // A model source's upstream; null for a service's, which is never metered.
  const priced = isService(credential.upstream) ? null : credential.upstream;
  let incoming: URL;
  try {
    incoming = new URL(request.url);
  } catch {
    return refused("invalid_request", resealed);
  }
  // A metered call is held to the one API whose model call it is (`scopeApi`), so each API's
  // meter is learned first (pi-ai's own call, run with no network); a free call's scope has
  // none, so it is held to every API's rules, as the owner's object holds a read. A throw
  // here sent nothing, so it is `failed`, as every end before the fetch is.
  let learned: Learned | null = null;
  if (metered !== null && priced !== null) {
    try {
      learned = await learnedScope(metered.catalog, priced, credential.endpoints,
                                   bearer(credential));
    } catch {
      return refused("internal", resealed);
    }
  }
  const scope = learned?.scope ?? scopeOf(credential);
  const target = egressTarget(scope, request.method, `${incoming.pathname}${incoming.search}`);
  if (target === null) {
    return refused("endpoint_refused", resealed);
  }
  if (metered === null && !("kind" in scope)
      && !freeOn(scope, priced!.provider, request.method, target.pathname)) {
    return refused("unmetered", resealed);
  }
  let body: BodyInit | null = READS.includes(request.method) ? null : request.body;
  // The metered call's model and what prices it, once it is ready to send.
  let call: { readonly model: Model<Api>; readonly pricing: Pricing } | null = null;
  let upload: Upload | null = null;
  let held: AbortSignal | null = null;
  if (metered !== null) {
    if (priced === null) {
      return refused("unmetered", resealed);
    }
    const { reserve, catalog } = metered;
    // Only pi-ai's own model call for a sealed API is metered; nothing else is read.
    const row = scopeApi(learned!.scope, request.method, target.pathname);
    if (row === null) {
      return refused("unmetered", resealed);
    }
    const meters = learned!.meters.get(row.api)!;
    // Its declared length, which the owner's object admitted it by: read exactly it.
    const declared = request.headers.get("content-length");
    if (declared === null || !/^[0-9]{1,15}$/.test(declared)
        || Number(declared) > EGRESS_BODY_MAX) {
      return refused("invalid_request", resealed);
    }
    // The parse reads plain bytes: a coded body is refused before it is read.
    if (request.headers.has("content-encoding")) {
      return refused("egress_body_encoded", resealed);
    }
    // Held from its first unit to its upload's last write, to its stop; its sender's deadline
    // is the read's (`readHeld`), its reserve's the pool's wait, its upload's its own.
    const hold = new Hold(gatewaySlots(), bound.owner, cancelled.signal);
    let ready: Prepared;
    try {
      ready = await prepared(request, Number(declared), priced, row, meters,
                             target.pathname, catalog, reserve, hold, cancelled.signal,
                             () => hold.release());
    } catch (error) {
      hold.release();
      // Nothing was sent: the call is `failed`, whatever stopped it.
      return refused(cancelled.signal.aborted ? "aborted" : codeOf(error, "internal"),
                     resealed);
    }
    if ("refused" in ready) {
      hold.release();
      return refused(ready.refused, resealed);
    }
    ({ upload } = ready);
    call = ready;
    body = upload.body;
    // Past its deadline, or stopped, the upload ends there.
    held = AbortSignal.any([cancelled.signal,
                            timeout(heldDeadlineMs(Number(declared)))]);
    held.addEventListener("abort", () => upload?.stop(), { once: true });
  }
  let headers: Headers;
  let deadline: AbortSignal;
  let signal: AbortSignal;
  try {
    headers = egressHeaders(scope, request.method, target.pathname, metered === null,
                            request.headers);
    headers.set(credential.upstream.auth.header, authValue(credential));
    // Plain bytes back, so the redaction reads what the provider wrote.
    headers.set("accept-encoding", "identity");
    deadline = timeout(FORWARD_DEADLINE_MS);
    signal = AbortSignal.any([request.signal, deadline, cancelled.signal]);
  } catch {
    // Nothing was sent: the upload's slots go with it, and the call is `failed`.
    upload?.stop();
    return refused("internal", resealed);
  }
  // A call its caller stopped before it was sent is never sent.
  if (cancelled.signal.aborted) {
    upload?.stop();
    return refused("aborted", resealed);
  }
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method, headers, body, redirect: "manual", signal,
    });
  } catch {
    upload?.stop();
    // Sent or not, nothing came back: a metered call whose request may have reached the
    // provider is unknown, and keeps its whole reservation.
    return { response: refusal(deadline.aborted ? "deadline" : "upstream_unreachable",
                               resealed),
             outcome: Promise.resolve(metered === null ? FAILED : UNKNOWN) };
  }
  logDropped(request.headers, headers, upstream.status,
             call?.model.api ?? priced?.apis.map((row) => row.api).join(", ") ?? "service");
  const answer = new Headers();
  const redacted = redaction(secretsOf(credential));
  for (const [name, value] of upstream.headers) {
    if (!HOP_HEADERS.includes(name) && !internal(name)) {
      answer.append(name, redacted.text(value));
    }
  }
  // The body is rewritten when redacted, so its encoding headers no longer describe it.
  answer.delete("content-encoding");
  if (resealed !== undefined) {
    answer.set(RESEALED_HEADER, JSON.stringify(resealed));
  }
  let outcome: Promise<MeterOutcome> = Promise.resolve(UNKNOWN);
  let source = upstream.body;
  if (metered !== null && call !== null && priced !== null) {
    if (upstream.status < 200 || upstream.status >= 300) {
      // A refusal before any output: the provider produced nothing.
      outcome = Promise.resolve(FAILED);
    } else if (source !== null) {
      const [toCaller, toMeters] = source.tee();
      const [toMeter, toTier] = toMeters.tee();
      source = toCaller;
      const rules = apiRules(call.model.api)!;
      // An answer served at a tier pi-ai does not price is unknown, whatever pi-ai read (and
      // one whose tier cannot be read, a cut, is unknown by the rejection below).
      const tierPriced = servedPriced(rules, toTier);
      outcome = Promise.all([meteredUsage(metered.catalog, priced,
                                          bearer(credential), call.model, upstream.status,
                                          new Headers(upstream.headers), toMeter, call.pricing),
                             tierPriced])
        .then(([usage, tier]): MeterOutcome => (usage === null || !tier.priced ? UNKNOWN
          // An answer naming no tier was served at the account's default: the dearest.
          : succeeded(usage.tokens, usage.cost * (tier.seen ? 1 : rules.served?.absent ?? 1),
                      usage.served)), () => UNKNOWN);
    }
  }
  // The caller's cancel ends the provider's request too, whatever else reads the answer; the
  // answer's end ends an upload the provider never finished reading.
  const passed = source === null ? null : abortable(source, cancelled, () => upload?.stop());
  if (source === null) {
    upload?.stop();
  }
  return { response: new Response(passed === null ? null : redacted.stream(passed),
                                  { status: upstream.status, statusText: upstream.statusText,
                                    headers: answer }),
           outcome };
}

/**
 * `body`, whose cancel by its reader also aborts the provider's request; `ended` at its end.
 * When the caller stopped the call (`cancelled`, its stop stream or its cancel), the answer
 * ends there: closed, not errored with the abort the stop caused. The caller already knows
 * why it stopped, and an abort error carried back over the RPC stream to a door that no
 * longer reads it is a rejection nothing can handle (workerd logs each one as uncaught). A
 * provider's own failure or the gateway's deadline still errors the answer, and cancels the
 * provider's, which ends its request.
 */
function abortable(body: ReadableStream<Uint8Array>, cancelled: AbortController,
                   ended: () => void): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { done, value } = await reader.read();
        if (done) {
          ended();
          controller.close();
        } else {
          controller.enqueue(value);
        }
      } catch (error) {
        ended();
        if (cancelled.signal.aborted) {
          controller.close();
        } else {
          controller.error(error);
          // Through the fetch's signal, so a metered answer's tee (its meter's readers) ends too.
          cancelled.abort();
        }
      }
    },
    cancel(reason) {
      ended();
      cancelled.abort();
      return reader.cancel(reason);
    },
  });
}

/**
 * Where a credential may be forwarded: its sealed base URLs and endpoints, no secret; a model
 * source's with no meter learned (`learnedScope` learns them).
 */
function scopeOf(credential: Credential): EgressScope {
  const { upstream, endpoints } = credential;
  return isService(upstream)
    ? Object.freeze({ kind: "service", base_url: upstream.base_url, endpoints })
    : modelScope(upstream, endpoints, upstream.apis.map(() => null));
}

/** A model source's scope, with pi-ai's own model call for each of its APIs, or none. */
function modelScope(upstream: Upstream, endpoints: Credential["endpoints"],
                    meters: readonly (Meter | null)[]): ModelScope {
  return Object.freeze({
    apis: Object.freeze(upstream.apis.map((row, index) =>
      Object.freeze({ ...row, meter: meters[index] ?? null }))),
    endpoints });
}

/** A model source's scope with each API's meter learned, and each API's `Meters`, by API. */
interface Learned {
  readonly scope: ModelScope;
  readonly meters: ReadonlyMap<string, Meters>;
}

/** Each of `upstream`'s APIs' meters, learned with the call's own credential (`Meters`). */
async function learnedScope(catalog: Catalog, upstream: Upstream,
                            endpoints: Credential["endpoints"], apiKey: string):
    Promise<Learned> {
  const meters = new Map(upstream.apis.map((row) =>
    [row.api, new Meters(catalog, upstream, row, apiKey)] as const));
  const learned = await Promise.all(upstream.apis.map((row) => meters.get(row.api)!.any()));
  return Object.freeze({ scope: modelScope(upstream, endpoints, learned), meters });
}

/**
 * Checks an opened credential against its sealed `verify_url` (a `GET` among its
 * endpoints, sent like `forward` sends): a 2xx answer is a working credential, and the
 * models are pi-ai's catalog for its provider (or its declared models), with their
 * metadata, and its egress scope, which the binder hands the owner object's
 * `connectionCommit`. Anything else is the upstream outcome's fixed code; the answer's body
 * is never read. A service's key has no models and no check a service shares (an MCP server
 * answers only its own protocol, with a session), so it opens and its scope is all there
 * is: a wrong key is the server's own refusal at its first call, which the run sees.
 */
export async function verify(env: KeyringEnv, bound: Binding, blob: unknown,
                             catalog: Catalog): Promise<Verified> {
  const { credential, resealed } = await open(env, bound, blob);
  const kept = resealed === undefined ? {} : { resealed };
  const upstream = credential.upstream;
  if (isService(upstream)) {
    return Object.freeze({ models: Object.freeze([]), egress: scopeOf(credential), ...kept });
  }
  const url = upstream.verify_url;
  if (url === undefined) {
    throw new Refusal("unsupported_source");
  }
  // A sealed `verify_url` is one of the endpoints (`credential.ts`, on every open).
  const target = new URL(url);
  let answer: Response;
  try {
    answer = await fetch(target, {
      method: "GET",
      headers: new Headers([[credential.upstream.auth.header, authValue(credential)],
                            ["accept", "application/json"]]),
      redirect: "manual",
      signal: timeout(VERIFY_DEADLINE_MS),
    });
  } catch {
    throw new Refusal("upstream_unreachable");
  }
  await answer.body?.cancel();
  if (answer.status < 200 || answer.status >= 300) {
    throw new Refusal(upstreamCode(answer.status));
  }
  const { scope } = await learnedScope(catalog, upstream, credential.endpoints,
                                      bearer(credential));
  return Object.freeze({ models: Object.freeze(modelInfos(catalog, upstream)), egress: scope,
                         ...kept });
}

/**
 * The models an opened model credential's upstream serves by `catalog` now, on every API it is
 * sealed for (`modelInfos`, each naming its API): the setup check's list, as the catalog has
 * moved since. No call leaves the gateway. A service's key has none (`unsupported_source`).
 */
export async function models(env: KeyringEnv, bound: Binding, blob: unknown,
                             catalog: Catalog): Promise<SealedModels> {
  const { credential } = await open(env, bound, blob);
  if (isService(credential.upstream)) {
    throw new Refusal("unsupported_source");
  }
  return Object.freeze({ models: Object.freeze(modelInfos(catalog, credential.upstream)) });
}

/** The most of a key's limits answer read (`credit`). */
const CREDIT_ANSWER_MAX = 64 * 1024;
/**
 * The most of a listing read (`listing`): a vendor's whole model listing, about 0.9 MB for
 * OpenRouter's 477 models on 2026-11-23, with room for it to grow.
 */
const LISTING_ANSWER_MAX = 4 * 1024 * 1024;

/**
 * What an opened model credential's key can pay for (`credit_read`, checked here), read by
 * `sealedRead` to `CREDIT_ANSWER_MAX` and held to the read's tests; an answer of another
 * shape is `upstream_error`.
 */
export async function credit(env: KeyringEnv, bound: Binding, blob: unknown,
                             value: unknown): Promise<KeyCredit> {
  if (!keyed(value, ...taken("credit_read"))) {
    throw new Refusal("invalid_request");
  }
  const read = creditRead(value);
  const pays = paysBy(read, await sealedRead(env, bound, blob, read.url, CREDIT_ANSWER_MAX));
  if (pays === null) {
    throw new Refusal("upstream_error");
  }
  return Object.freeze({ pays });
}

/**
 * The models the opened model credential's vendor lists its key served, with their scores
 * (`listing_read`, checked here), read by `sealedRead` to `LISTING_ANSWER_MAX` and held to the
 * read's paths (`listedBy`); an answer of another shape is `upstream_error`.
 */
export async function listing(env: KeyringEnv, bound: Binding, blob: unknown,
                              value: unknown): Promise<ModelListing> {
  if (!keyed(value, ...taken("listing_read"))) {
    throw new Refusal("invalid_request");
  }
  const read = listingRead(value);
  const models = listedBy(read, await sealedRead(env, bound, blob, read.url,
                                                 LISTING_ANSWER_MAX));
  if (models === null) {
    throw new Refusal("upstream_error");
  }
  return Object.freeze({ models: Object.freeze(models) });
}

/**
 * A model credential's `GET` of `url`, only where its sealed endpoints allow one
 * (`endpoint_refused`), sent as `verify` sends: a 2xx answer's JSON, read to `max`. Anything
 * else is the upstream outcome's fixed code. A service's key has no such read
 * (`unsupported_source`).
 */
async function sealedRead(env: KeyringEnv, bound: Binding, blob: unknown, url: string,
                          max: number): Promise<unknown> {
  const { credential } = await open(env, bound, blob);
  if (isService(credential.upstream)) {
    throw new Refusal("unsupported_source");
  }
  const target = new URL(url);
  if (!allows(credential.endpoints, "GET", target)) {
    throw new Refusal("endpoint_refused");
  }
  let answer: Response;
  try {
    answer = await fetch(target, {
      method: "GET",
      headers: new Headers([[credential.upstream.auth.header, authValue(credential)],
                            ["accept", "application/json"]]),
      redirect: "manual",
      signal: timeout(VERIFY_DEADLINE_MS),
    });
  } catch {
    throw new Refusal("upstream_unreachable");
  }
  if (answer.status < 200 || answer.status >= 300) {
    await answer.body?.cancel();
    throw new Refusal(upstreamCode(answer.status));
  }
  return boundedJson(answer, max);
}
