/**
 * Raw passthrough for sandbox egress (research §5.4 step 4.3), and the credential check
 * a connection makes when it is set up (`verify`).
 *
 * The sandbox's model clients speak each provider's own API to our egress door with
 * placeholder keys; the binder admits the request at the owner's object, which hands it
 * the blob, and calls `forward`. The gateway opens the blob, requires the request's
 * method, origin and path to be among the sealed endpoints, strips every credential the
 * request carried (headers and the API-key query parameters), injects the sealed one by
 * the sealed auth template, and sends it to the sealed origin. The provider's own answer
 * streams back, status and body, so the client sees its own API; only the secret is
 * redacted from the body, in case a provider quotes it back. A call is cut at
 * `FORWARD_DEADLINE_MS`, and redirects are never followed.
 *
 * User CLIs speak their provider's wire format. An unmetered call (a read, a cited free
 * call) only moves bytes: its body streams through. A metered one is the one request the
 * gateway holds whole (egress shape research, A′): under the pool's slots, and only while it
 * reads, rewrites and uploads it, it parses it once and asks the owner's object, through the
 * door's `reserve`, to reserve the call for the model and tier that parse read. The gateway
 * measures nothing it would keep: the binder counts the bytes it streams and settles the
 * owner's reservation.
 */
import type { Api, Model } from "@earendil-works/pi-ai";

import { type Catalog, modelInfos } from "./catalog";
import {
  bearer, type Credential, type EgressScope, egressTarget, type Meter, meteredCall,
} from "./credential";
import type { ModelRecord } from "./model-record";
import { type Code, codeOf, Failure, failureResponse, upstreamCode } from "./failure";
import { nativeFetch } from "./egress";
import { aliased, apiRules, fallbackList, freeCall, manyChoices, paidTool, servedPriced,
         tierMultiplier, unpricedOption, valueAt } from "./api-rules";
import { clamped, learnMeter, meteredModel, meteredRecord, meteredUsage } from "./meter";
import { type Binding, type KeyringEnv, open, type Opened, type Resealed } from "./keyring";
import { BODY_MAX, heldDeadlineMs, Hold, HeldRefusal, raced, weighedBody, weightSlots }
  from "./held";
import { SLOT_WAIT_MS } from "../../owner/src/slots";

/** The longest one forwarded request may run (§6: a policy write's bound on a stream). */
export const FORWARD_DEADLINE_MS = 24 * 60 * 60 * 1000;

/** How long the setup check waits for the provider. */
const VERIFY_DEADLINE_MS = 15 * 1000;

export { BODY_MAX, GATEWAY_POOL_SLOTS } from "./held";

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

/** Headers that carry a credential, or route, in any provider's or proxy's convention. */
const CREDENTIAL_HEADERS: readonly string[] = Object.freeze([
  "authorization", "proxy-authorization", "x-api-key", "api-key", "x-goog-api-key", "cookie",
  "host", "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip",
  // A method override would let a sealed `POST` act as a method that is not sealed.
  "x-http-method-override", "x-http-method", "x-method-override",
]);
/** Query parameters that carry an API key (Google's `key` among them). */
const CREDENTIAL_PARAMS: readonly string[] = Object.freeze(["key", "api_key", "access_token"]);
/** Headers that belong to one hop, never forwarded either way. */
const HOP_HEADERS: readonly string[] = Object.freeze([
  "connection", "keep-alive", "te", "trailer", "transfer-encoding", "upgrade", "content-length",
]);

/** Whether a header is ours or Cloudflare's, never passed on either way. */
function internal(name: string): boolean {
  return name.startsWith("x-ta-") || name.startsWith("cf-");
}

/** The header value the sealed template makes of the credential's secret. */
export function authValue(credential: Credential): string {
  const scheme = credential.upstream.auth.scheme;
  return scheme === "" ? bearer(credential) : `${scheme} ${bearer(credential)}`;
}

const REDACTED = "[redacted]";

/**
 * Each secret as the provider could write it back: as it is, and JSON-escaped (a JSON
 * writer may escape `/` as `\/`).
 */
function spellings(secrets: readonly string[]): string[] {
  const all = secrets.filter((secret) => secret.length > 0).flatMap((secret) => [
    secret, secret.replace(/\\/g, "\\\\").replace(/"/g, "\\\"").replace(/\//g, "\\/"),
  ]);
  return [...new Set(all)].sort((a, b) => b.length - a.length);
}

/** `value` with every spelling of each of `secrets` replaced by `[redacted]`. */
export function redactText(value: string, secrets: readonly string[]): string {
  let out = value;
  for (const spelling of spellings(secrets)) {
    out = out.split(spelling).join(REDACTED);
  }
  return out;
}

/**
 * Replaces every occurrence of each spelling of each of `secrets` in a byte stream with
 * `[redacted]`, across chunk boundaries: it holds back the last `longest - 1` bytes of
 * each chunk until the next one shows whether they start a secret.
 */
export function redact(body: ReadableStream<Uint8Array>,
                       secrets: readonly string[]): ReadableStream<Uint8Array> {
  const needles = spellings(secrets).map((secret) => new TextEncoder().encode(secret));
  const longest = Math.max(0, ...needles.map((needle) => needle.byteLength));
  if (longest === 0) {
    return body;
  }
  const mark = new TextEncoder().encode(REDACTED);
  let held = new Uint8Array(0);
  const scrub = (bytes: Uint8Array, final: boolean): Uint8Array[] => {
    const out: Uint8Array[] = [];
    let start = 0;
    let at = 0;
    const limit = final ? bytes.byteLength : Math.max(0, bytes.byteLength - (longest - 1));
    while (at < limit) {
      const hit = needles.find((needle) => needle.byteLength <= bytes.byteLength - at
        && needle.every((byte, index) => bytes[at + index] === byte));
      if (hit === undefined) {
        at++;
        continue;
      }
      out.push(bytes.subarray(start, at), mark);
      at += hit.byteLength;
      start = at;
    }
    out.push(bytes.subarray(start, Math.max(start, limit)));
    held = bytes.slice(Math.max(start, limit));
    return out;
  };
  return body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      const joined = new Uint8Array(held.byteLength + chunk.byteLength);
      joined.set(held);
      joined.set(chunk, held.byteLength);
      for (const part of scrub(joined, false)) {
        if (part.byteLength > 0) {
          controller.enqueue(part);
        }
      }
    },
    flush(controller) {
      for (const part of scrub(held, true)) {
        if (part.byteLength > 0) {
          controller.enqueue(part);
        }
      }
    },
  }));
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
function refusal(code: Code, resealed?: Resealed): Response {
  return failureResponse(code, resealed === undefined ? {}
    : { "x-ta-resealed": JSON.stringify(resealed) });
}

/**
 * Forwards `request` (its method, path, query, headers and body; its host is ignored) to
 * the credential's sealed origin under `bound`. Refusals are a fixed-code JSON answer the
 * sandbox client sees as an error; the provider's own answers pass through.
 */
export async function forward(env: KeyringEnv, bound: Binding, blob: unknown,
                              request: Request,
                              deadlineMs: number = FORWARD_DEADLINE_MS): Promise<Response> {
  return (await send(env, bound, blob, request, deadlineMs, null, null)).response;
}

/**
 * What a metered call is held to: the output cap its reservation leaves, its body's size,
 * and the model and tier (its tier option's value, or null) it was reserved for.
 */
export interface Metering {
  readonly max_output_tokens: number;
  readonly input_bytes: number;
  readonly model: string;
  readonly tier: string | null;
}

/** How a metered call ended, as the owner's object settles it. */
export type MeterOutcome =
  | { readonly state: "succeeded"; readonly tokens: number; readonly cost: number;
      readonly served?: string }
  | { readonly state: "failed" }
  | { readonly state: "unknown" };

/** What the owner's object answered a reserve: the call's metering, or its refusal's code. */
export type Reservation = { readonly metering: Metering } | { readonly refused: string };

/**
 * The egress door's callback (over RPC, a stub to the door): the owner's object reserves the
 * admitted call for the model and tier its body names, as the gateway's one parse read them.
 */
export type Reserve = (call: { readonly model: string; readonly tier: string | null }) =>
  Promise<Reservation>;

/** A model id or tier as the owner's object takes one: visible ASCII. */
const NAMED = /^[\x21-\x7e]{1,256}$/;

/** The metering a reserve answered, its whole numbers and its model and tier, or a bug. */
function meteringOf(value: unknown): Metering {
  const fields = value as Partial<Metering> | null;
  const whole = (n: unknown) => typeof n === "number" && Number.isSafeInteger(n) && n >= 1;
  const named = (s: unknown) => typeof s === "string" && NAMED.test(s);
  if (typeof value !== "object" || value === null
      || Object.keys(value).sort().join() !== "input_bytes,max_output_tokens,model,tier"
      || !whole(fields!.input_bytes) || !whole(fields!.max_output_tokens)
      || !named(fields!.model) || (fields!.tier !== null && !named(fields!.tier))) {
    throw new Failure("internal");
  }
  return { input_bytes: fields!.input_bytes!, max_output_tokens: fields!.max_output_tokens!,
           model: fields!.model!, tier: fields!.tier ?? null };
}

/**
 * A sandbox's model call, metered (G4), once the owner's object admitted it: held to pi-ai's
 * own model call for the sealed API (`learnMeter`); its slots taken as it arrives
 * (`held.ts`), its body read to its `Content-Length` and parsed once; refused for what no
 * reservation
 * bounds; its model and tier read from that parse and reserved through `reserve`; then its
 * output cap clamped to the reservation's `max_output_tokens`, so what it can cost is what
 * the owner's object reserved, and forwarded as `forward` forwards. The outcome comes once
 * the answer ends: `failed` when nothing was sent or the provider refused before any output,
 * the usage pi-ai read from the answer, priced by the catalog (`meteredUsage`), or
 * `unknown`. `stop`, closed or errored by the caller, aborts the provider's request at any
 * point.
 */
export async function forwardMetered(env: KeyringEnv, bound: Binding, blob: unknown,
                                     request: Request, reserve: Reserve,
                                     stop: ReadableStream<Uint8Array> | null,
                                     catalog: Catalog,
                                     deadlineMs: number = FORWARD_DEADLINE_MS):
    Promise<{ response: Response; outcome: Promise<MeterOutcome> }> {
  return send(env, bound, blob, request, deadlineMs, { reserve, catalog }, stop);
}

/** A metered call ready to send, or the code it is refused with. */
type Prepared = { readonly refused: Code }
  | { readonly upload: Upload; readonly model: Model<Api>; readonly listed: boolean };

/**
 * The metered call's body, read and parsed once (at most `length` bytes), checked, reserved,
 * clamped in place, serialized and encoded once, each step's input unreachable once the next
 * holds its output; then uploading, `ended` run when its last write is taken. Refused before
 * the reserve for what the reservation cannot bound, so a refused shape is never charged.
 */
async function prepared(request: Request, length: number, credential: Credential, any: Meter,
                        path: string, catalog: Catalog, reserve: Reserve, hold: Hold,
                        signal: AbortSignal, ended: () => void): Promise<Prepared> {
  // The body, read under slots taken as it arrives; then its weight's slots, before the parse.
  let read: Awaited<ReturnType<typeof weighedBody>> | null =
    await weighedBody(request, length, hold, signal);
  await hold.grow(weightSlots(read.weight));
  let parsed: unknown;
  try {
    parsed = JSON.parse(read.text!);
  } catch {
    return { refused: "invalid_request" };
  }
  read.text = null;
  const rules = apiRules(credential.upstream.api);
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
  const id = meteredModel(any, request.method, path, parsed);
  if (id === null) {
    return { refused: "unmetered" };
  }
  const model = NAMED.test(id) ? meteredRecord(catalog, credential.upstream, id) : null;
  if (model === null) {
    return { refused: "unknown_model" };
  }
  const meter = await learnMeter(catalog, credential.upstream, bearer(credential), model);
  if (meter === null || meteredModel(meter, request.method, path, parsed) !== id) {
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
  const tier = rules.tier === null ? null : valueAt(parsed, [rules.tier.key]) ?? null;
  if (tier !== null && (typeof tier !== "string" || !NAMED.test(tier))) {
    return { refused: "egress_unpriced_option" };
  }
  if (tierMultiplier(rules, tier) === null) {
    return { refused: "egress_unpriced_option" };
  }
  // The call reserved for the model and tier this parse read, the only reading there is.
  // The reserve, to the caller's stop and the pool's wait: an object that does not answer
  // within it leaves the call `busy`, its units let go. A reserve already in flight when the
  // caller stops may still commit at the owner's object, which then keeps the charge (the
  // door settles a stopped call `unknown`), as before A′.
  const reserving = new AbortController();
  const late = setTimeout(() => reserving.abort(new HeldRefusal("busy")), SLOT_WAIT_MS);
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
  const metering = meteringOf(reserved.metering);
  if (metering.model !== id || metering.tier !== tier) {
    throw new Failure("internal");  // the object reserved another call than it was asked
  }
  if (read.bytes > metering.input_bytes) {
    return { refused: "invalid_request" };
  }
  read = null;
  const listed = fallbackList(rules, parsed);
  let text: string | null = JSON.stringify(clamped(meter, parsed as Record<string, unknown>,
                                                   metering.max_output_tokens,
                                                   credential.upstream.api));
  parsed = null;
  const bytes = new TextEncoder().encode(text);
  text = null;
  return { upload: uploaded(bytes, ended), model, listed };
}

/**
 * A sandbox's free call on a sealed endpoint, forwarded unmetered as `forward` forwards: a
 * read (`GET`, `HEAD`: a model list), which carries no model use, or a call its API's rules
 * cite as free (`freeCall`: a token count). Any other is `unmetered`, so no call the
 * owner's object did not charge reaches a provider's model.
 */
export async function forwardRead(env: KeyringEnv, bound: Binding, blob: unknown,
                                  request: Request, stop: ReadableStream<Uint8Array> | null,
                                  deadlineMs: number = FORWARD_DEADLINE_MS):
    Promise<{ response: Response; outcome: Promise<MeterOutcome> }> {
  return send(env, bound, blob, request, deadlineMs, null, stop, true);
}

const FAILED: MeterOutcome = Object.freeze({ state: "failed" });
const UNKNOWN: MeterOutcome = Object.freeze({ state: "unknown" });

async function send(env: KeyringEnv, bound: Binding, blob: unknown, request: Request,
                    deadlineMs: number,
                    metered: { reserve: Reserve; catalog: Catalog } | null,
                    stop: ReadableStream<Uint8Array> | null, free = false):
    Promise<{ response: Response; outcome: Promise<MeterOutcome> }> {
  const refused = (code: Code, resealed?: Resealed) =>
    ({ response: refusal(code, resealed), outcome: Promise.resolve(FAILED) });
  const cancelled = new AbortController();
  stop?.getReader().read().then(() => cancelled.abort(), () => cancelled.abort());
  let opened: Opened;
  try {
    opened = await open(env, bound, blob);
  } catch (error) {
    return refused(codeOf(error, "internal"));
  }
  const { credential, resealed } = opened;
  let incoming: URL;
  try {
    incoming = new URL(request.url);
  } catch {
    return refused("invalid_request", resealed);
  }
  const target = egressTarget(scopeOf(credential), request.method,
                              `${incoming.pathname}${incoming.search}`);
  if (target === null) {
    return refused("endpoint_refused", resealed);
  }
  // A sandbox's unmetered call (`free`): only a read, or a call its API's rules cite as free.
  if (free && request.method !== "GET" && request.method !== "HEAD"
      && !freeCall(apiRules(credential.upstream.api), credential.upstream.provider,
                   request.method, target.pathname)) {
    return refused("unmetered", resealed);
  }
  // Every spelling of each credential parameter, as some servers read them case-blind.
  for (const name of [...target.searchParams.keys()]) {
    if (CREDENTIAL_PARAMS.includes(name.toLowerCase())) {
      target.searchParams.delete(name);
    }
  }
  let body: BodyInit | null = request.method === "GET" || request.method === "HEAD"
    ? null : request.body;
  let model: Model<Api> | null = null;
  let listed = false;
  let upload: Upload | null = null;
  let held: AbortSignal | null = null;
  if (metered !== null) {
    const { reserve, catalog } = metered;
    // Only pi-ai's own model call for the sealed API is metered; nothing else is read. A throw
    // here sent nothing, so it is `failed` (M3), as every end before the fetch is.
    const any = await learnMeter(catalog, credential.upstream, bearer(credential))
      .catch(() => undefined);
    if (any === undefined) {
      return refused("internal", resealed);
    }
    if (any === null || !meteredCall(scopeOf(credential, any), request.method,
                                     target.pathname)) {
      return refused("unmetered", resealed);
    }
    // Its declared length, which the owner's object admitted it by: read exactly it.
    const declared = request.headers.get("content-length");
    if (declared === null || !/^[0-9]{1,15}$/.test(declared) || Number(declared) > BODY_MAX) {
      return refused("invalid_request", resealed);
    }
    // Held from its first unit to its upload's last write, to its stop; its sender's deadline
    // is the read's (`readHeld`), its reserve's the pool's wait, its upload's its own.
    const hold = new Hold(bound.owner, cancelled.signal);
    let ready: Prepared;
    try {
      ready = await prepared(request, Number(declared), credential, any, target.pathname,
                             catalog, reserve, hold, cancelled.signal, () => hold.release());
    } catch (error) {
      hold.release();
      // Nothing was sent: the call is `failed`, whatever stopped it (M3).
      return refused(cancelled.signal.aborted ? "aborted"
        : error instanceof HeldRefusal ? error.code : "internal", resealed);
    }
    if ("refused" in ready) {
      hold.release();
      return refused(ready.refused, resealed);
    }
    ({ upload, model, listed } = ready);
    body = upload.body;
    // Past its deadline, or stopped, the upload ends there.
    held = AbortSignal.any([cancelled.signal,
                            AbortSignal.timeout(heldDeadlineMs(Number(declared)))]);
    held.addEventListener("abort", () => upload?.stop(), { once: true });
  }
  const headers = new Headers();
  let deadline: AbortSignal;
  let signal: AbortSignal;
  try {
    // A metered call's provider beta headers go only where its API's rules account for them.
    const betas = metered === null ? null : apiRules(credential.upstream.api)?.betas ?? [];
    for (const [name, value] of request.headers) {
      if (!CREDENTIAL_HEADERS.includes(name) && !HOP_HEADERS.includes(name) && !internal(name)
          && !(betas !== null && /-beta$/.test(name) && !betas.includes(name))) {
        headers.append(name, value);
      }
    }
    // `set` replaces every value the request sent under the sealed header's name.
    headers.set(credential.upstream.auth.header, authValue(credential));
    // Plain bytes back, so the redaction reads what the provider wrote.
    headers.set("accept-encoding", "identity");
    if (metered !== null) {
      headers.delete("content-length");
    }
    deadline = AbortSignal.timeout(deadlineMs);
    signal = AbortSignal.any([request.signal, deadline, cancelled.signal]);
  } catch {
    // Nothing was sent: the upload's slots go with it (L2), and the call is `failed` (M3).
    upload?.stop();
    return refused("internal", resealed);
  }
  // A call its caller stopped before it was sent is never sent (L3).
  if (cancelled.signal.aborted) {
    upload?.stop();
    return refused("aborted", resealed);
  }
  let upstream: Response;
  try {
    upstream = await nativeFetch(target, {
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
  const answer = new Headers();
  const secrets = secretsOf(credential);
  for (const [name, value] of upstream.headers) {
    if (!HOP_HEADERS.includes(name) && !internal(name)) {
      answer.append(name, redactText(value, secrets));
    }
  }
  // The body is rewritten when redacted, so its encoding headers no longer describe it.
  answer.delete("content-encoding");
  if (resealed !== undefined) {
    answer.set("x-ta-resealed", JSON.stringify(resealed));
  }
  let outcome: Promise<MeterOutcome> = Promise.resolve(UNKNOWN);
  let source = upstream.body;
  if (metered !== null && model !== null) {
    if (upstream.status < 200 || upstream.status >= 300) {
      // A refusal before any output: the provider produced nothing, as the loop settles it.
      outcome = Promise.resolve(FAILED);
    } else if (source !== null) {
      const [toCaller, toMeters] = source.tee();
      const [toMeter, toTier] = toMeters.tee();
      source = toCaller;
      const rules = apiRules(credential.upstream.api)!;
      // An answer served at a tier pi-ai does not price is unknown, whatever pi-ai read (and
      // one whose tier cannot be read, a cut, is unknown by the rejection below).
      const tierPriced = servedPriced(rules, toTier);
      outcome = Promise.all([meteredUsage(metered.catalog, credential.upstream,
                                          bearer(credential), model, upstream.status,
                                          new Headers(upstream.headers), toMeter, listed),
                             tierPriced])
        .then(([usage, tier]): MeterOutcome => (usage === null || !tier.priced ? UNKNOWN
          : { state: "succeeded", tokens: usage.tokens,
              // An answer naming no tier was served at the account's default: the dearest.
              cost: usage.cost * (tier.seen ? 1 : rules.served?.absent ?? 1),
              ...(usage.served === undefined ? {} : { served: usage.served }) }), () => UNKNOWN);
    }
  }
  // The caller's cancel ends the provider's request too, whatever else reads the answer; the
  // answer's end ends an upload the provider never finished reading.
  const passed = source === null ? null : abortable(source, cancelled, () => upload?.stop());
  if (source === null) {
    upload?.stop();
  }
  const redacted = passed === null ? null : redact(passed, secrets);
  return { response: new Response(redacted, { status: upstream.status,
                                              statusText: upstream.statusText, headers: answer }),
           outcome };
}

/**
 * `body`, whose cancel by its reader also aborts the provider's request; `ended` at its end.
 * When the caller stopped the call (`cancelled`, its stop stream or its cancel), the answer
 * ends there: closed, not errored with the abort the stop caused. The caller already knows
 * why it stopped, and an abort error carried back over the RPC stream to a door that no
 * longer reads it is a rejection nothing can handle (workerd logs each one as uncaught). A
 * provider's own failure, or the gateway's deadline, still errors the answer.
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

/** Where a credential may be forwarded: its sealed base URL and endpoints, no secret. */
function scopeOf(credential: Credential, meter: Meter | null = null): EgressScope {
  return Object.freeze({ api: credential.upstream.api, base_url: credential.upstream.base_url,
                         endpoints: credential.endpoints, meter });
}

/**
 * What a setup check found: the credential works, the models it can be asked for, and
 * where sandbox egress may forward it (`egress`, its sealed base URL and endpoints), which
 * the owner's object keeps to refuse an endpoint not sealed before any forward (G4).
 */
export interface Verified {
  readonly models: readonly ModelRecord[];
  readonly egress: EgressScope;
  readonly resealed?: Resealed;
}

/**
 * Checks an opened credential against its sealed `verify_url` (a `GET` among its
 * endpoints, sent like `forward` sends): a 2xx answer is a working credential, and the
 * models are pi-ai's catalog for its provider (or its declared models), with their
 * metadata, and its egress scope, which the binder hands the owner object's
 * `connectionCommit`. Anything else is
 * the upstream outcome's fixed code; the answer's body is never read.
 */
export async function verify(env: KeyringEnv, bound: Binding, blob: unknown,
                             catalog: Catalog): Promise<Verified> {
  const { credential, resealed } = await open(env, bound, blob);
  const url = credential.upstream.verify_url;
  if (url === undefined) {
    throw new Failure("unsupported_source");
  }
  // A sealed `verify_url` is one of the endpoints (`credential.ts`, on every open).
  const target = new URL(url);
  let answer: Response;
  try {
    answer = await nativeFetch(target, {
      method: "GET",
      headers: new Headers([[credential.upstream.auth.header, authValue(credential)],
                            ["accept", "application/json"]]),
      redirect: "manual",
      signal: AbortSignal.timeout(VERIFY_DEADLINE_MS),
    });
  } catch {
    throw new Failure("upstream_unreachable");
  }
  await answer.body?.cancel();
  if (answer.status < 200 || answer.status >= 300) {
    throw new Failure(upstreamCode(answer.status));
  }
  return Object.freeze({ models: Object.freeze(modelInfos(catalog, credential.upstream)),
                         egress: scopeOf(credential, await learnMeter(catalog, credential.upstream,
                                                                bearer(credential))),
                         ...(resealed === undefined ? {} : { resealed }) });
}
