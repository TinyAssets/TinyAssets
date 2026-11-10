/**
 * The doorbell's secrets and sends (slice W, ruling H1): like every credential, a wake's
 * secret (a Claude `/fire` token, a Grok routine key, an MCP Events `whsec_` key) is
 * sealed here and opened only here. The owner's object holds the blob; the doors hand the
 * plaintext straight to the seal; nothing else ever sees it.
 *
 * - `sealDoorbell`: the owner's pasted token (the doors' connect entrypoint), checked
 *   against its wake kind's record (`contract/wake-kinds.json`: its host, path and secret
 *   shape) and sealed with the callback URL and the record's auth, so the blob can only
 *   ever be sent where it was pasted for, the way it was meant to be.
 * - `verifyDoorbell`: an MCP Events subscribe's callback, verified before it is activated
 *   (OpenAI's MCP Events page): a signed `{"type": "verification", "challenge": ...}` with
 *   a fresh single-use challenge and a `msg_verification_` id, no redirect, bounded by the
 *   record's timeout; a 2xx whose JSON echoes the challenge, compared in constant time,
 *   read only to `ECHO_MAX_BYTES`. Then sealed as `sealDoorbell`. A failure is a category
 *   only (`timeout`, `connection_refused`, `http_4xx`, `http_5xx`, `challenge_failed`),
 *   never the endpoint's body.
 * - `ringDoorbell`: one wake, for the owner's object: the blob opened (and the replaced
 *   secret's, inside its rotation window), the object's headers and body sent as they are
 *   to the sealed URL alone, with the sealed auth: a bearer token, or Standard Webhooks
 *   (`webhook-id` the event id, the signing time, a `v1` HMAC-SHA256 of
 *   `id.timestamp.body`, both keys' signatures while the old one is in its window). It
 *   never follows a redirect and never reads the answer's body: only its status and
 *   `Retry-After` cross back. A blob that does not open, or a URL its record no longer
 *   admits, is `refused` before any byte; a send that failed after it may have left is
 *   `unknown`.
 *
 * Every fetch is the runtime's (`global_fetch_strictly_public`): never the egress
 * interposer's, since no model call's scope is open here.
 */
import { base64, base64url, fromBase64, hex } from "../../contract/src/envelope";
import { utf8Decoder } from "../../contract/src/text";
import { SUBSCRIPTION_ID } from "../../contract/src/ids";
import {
  AUTH_HEADER, callbackSecret, callbackUrl, kindNamed, type Push,
} from "../../contract/src/wake-records";
import { Refusal, refusalOf } from "../../contract/src/refusal";
import type {
  DoorbellVerified, Resealed, RingAnswer, RingRequest, Sealed,
} from "../../contract/src/gateway";
import { binding, type KeyringEnv, openValue, sealValue } from "./keyring";

/** What a doorbell blob holds: where it goes, how it is sent, and its secret. */
export interface DoorbellSecret {
  readonly kind: "doorbell";
  readonly record: string;
  readonly url: string;
  readonly auth: "bearer" | "standard_webhooks";
  readonly secret: string;
  readonly sealed_at: number;
}

/** The refusals a blob that cannot be opened for its subscription is answered with. */
const UNOPENED: readonly string[] = Object.freeze(["credential_refused", "invalid_binding"]);

/** How a request leaves: the runtime's fetch, or a test's recording one. */
export type Send = (request: Request) => Promise<Response>;

/**
 * The bytes of a verification answer read at most: `{"challenge": "<43 characters>"}` and
 * room for whitespace, a technical ceiling (the only answer body a doorbell ever reads).
 */
export const ECHO_MAX_BYTES = 1024;
const DOORBELL_KEYS = "auth,kind,record,sealed_at,secret,url";

/** A subscribed callback's push, by its record; `invalid_request` when it has none. */
function pushOf(record: unknown): Push {
  const kind = typeof record === "string" ? kindNamed(record) : null;
  if (kind === null || kind.push === null) {
    throw new Refusal("invalid_request");
  }
  return kind.push;
}

/** A doorbell blob's plaintext, checked against its record as it stands. */
export function doorbellSecret(value: unknown): DoorbellSecret {
  if (typeof value !== "object" || value === null || Array.isArray(value)
      || Object.keys(value).sort().join() !== DOORBELL_KEYS) {
    throw new Refusal("credential_refused");
  }
  const v = value as Record<string, unknown>;
  const push = pushOf(v["record"]);
  if (v["kind"] !== "doorbell" || v["auth"] !== push.auth || typeof v["sealed_at"] !== "number") {
    throw new Refusal("credential_refused");
  }
  try {
    callbackUrl(push, v["url"]);
    callbackSecret(push, v["secret"]);
  } catch {
    throw new Refusal("credential_refused");
  }
  return Object.freeze(v as unknown as DoorbellSecret);
}

/** A subscription's binding: its own key space, never a model connection's. */
function bound(owner: string, subscription: string, version: number) {
  if (typeof subscription !== "string" || !SUBSCRIPTION_ID.test(subscription)) {
    throw new Refusal("invalid_binding");
  }
  return binding(owner, `wake.${subscription}`, version);
}

/** The plaintext an input seals to, checked against its record; `invalid_request` if not. */
function plaintext(input: unknown): DoorbellSecret {
  if (typeof input !== "object" || input === null
      || Object.keys(input).sort().join() !== "record,secret,url") {
    throw new Refusal("invalid_request");
  }
  const { record, url, secret } = input as Record<string, unknown>;
  const push = pushOf(record);
  try {
    return { kind: "doorbell", record: record as string, url: callbackUrl(push, url),
             auth: push.auth, secret: callbackSecret(push, secret), sealed_at: Date.now() };
  } catch {
    throw new Refusal("invalid_request");
  }
}

export async function sealDoorbell(env: KeyringEnv, owner: string, subscription: string,
                                   version: number, input: unknown): Promise<Sealed> {
  return sealValue(env, bound(owner, subscription, version), plaintext(input), doorbellSecret);
}

function random(size: number): Uint8Array {
  return crypto.getRandomValues(new Uint8Array(size));
}

/** The Standard Webhooks signature of `id.stamp.body` under each `whsec_` key. */
async function signature(keys: readonly string[], id: string, stamp: number,
                         body: string): Promise<string> {
  const signed = new TextEncoder().encode(`${id}.${stamp}.${body}`);
  const each = await Promise.all([...new Set(keys)].map(async (key) => {
    const raw = fromBase64(key.slice("whsec_".length));
    const hmac = await crypto.subtle.importKey("raw", raw, { name: "HMAC", hash: "SHA-256" },
                                               false, ["sign"]);
    return "v1," + base64(new Uint8Array(await crypto.subtle.sign("HMAC", hmac, signed)));
  }));
  return each.join(" ");
}

/** At most `max` bytes of a body, then its stream released. */
async function bounded(response: Response, max: number): Promise<Uint8Array | null> {
  const reader = response.body?.getReader();
  if (reader === undefined) {
    return new Uint8Array();
  }
  const parts: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      size += value.byteLength;
      if (size > max) {
        return null;
      }
      parts.push(value);
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
  const all = new Uint8Array(size);
  let at = 0;
  for (const part of parts) {
    all.set(part, at);
    at += part.byteLength;
  }
  return all;
}

/** Whether `a` and `b` are the same bytes, in time that does not depend on where they differ. */
function same(a: string, b: string): boolean {
  const left = new TextEncoder().encode(a);
  const right = new TextEncoder().encode(b);
  return left.byteLength === right.byteLength && crypto.subtle.timingSafeEqual(left, right);
}

/**
 * Verifies a subscribed callback with a signed challenge, then seals it. `send` is the
 * runtime's fetch; a test's records.
 */
export async function verifyDoorbell(env: KeyringEnv, owner: string, subscription: string,
                                     version: number, input: unknown,
                                     send: Send = (request) => fetch(request)):
    Promise<DoorbellVerified> {
  const plain = plaintext(input);
  const events = pushOf(plain.record).events;
  if (events === null || plain.auth !== "standard_webhooks") {
    throw new Refusal("invalid_request");
  }
  const target = bound(owner, subscription, version);
  const challenge = base64url(random(32));
  const id = `msg_verification_${hex(random(16))}`;
  const stamp = Math.floor(Date.now() / 1000);
  const body = JSON.stringify({ type: "verification", challenge });
  let response: Response;
  try {
    response = await send(new Request(plain.url, {
      method: "POST", redirect: "manual", signal: AbortSignal.timeout(events.verify_timeout_ms),
      headers: { "Content-Type": "application/json", "webhook-id": id,
                 "webhook-timestamp": String(stamp),
                 "webhook-signature": await signature([plain.secret], id, stamp, body),
                 "X-MCP-Subscription-Id": subscription },
      body,
    }));
  } catch (error) {
    const name = (error as { name?: unknown } | null)?.name;
    return { verified: false,
             reason: name === "TimeoutError" || name === "AbortError" ? "timeout"
               : "connection_refused" };
  }
  if (response.status < 200 || response.status >= 300) {
    await response.body?.cancel().catch(() => undefined);
    return { verified: false, reason: response.status >= 500 ? "http_5xx" : "http_4xx" };
  }
  const bytes = await bounded(response, ECHO_MAX_BYTES).catch(() => null);
  let echoed: unknown;
  try {
    echoed = bytes === null ? null
      : (JSON.parse(utf8Decoder("strip").decode(bytes)) as
         { challenge?: unknown }).challenge;
  } catch {
    echoed = null;
  }
  if (typeof echoed !== "string" || !same(echoed, challenge)) {
    return { verified: false, reason: "challenge_failed" };
  }
  return { verified: true, sealed: await sealValue(env, target, plain, doorbellSecret) };
}

/** The headers a ring adds for its sealed auth. */
async function auth(secret: DoorbellSecret, previous: DoorbellSecret | null,
                    request: RingRequest, stamp: number): Promise<Record<string, string>> {
  if (secret.auth === "bearer") {
    return { Authorization: `Bearer ${secret.secret}` };
  }
  const keys = previous === null ? [secret.secret] : [secret.secret, previous.secret];
  return { "webhook-id": request.event_id, "webhook-timestamp": String(stamp),
           "webhook-signature": await signature(keys, request.event_id, stamp, request.body) };
}

function ringRequest(value: unknown): RingRequest {
  const r = value as Record<string, unknown> | null;
  const headers = r?.["headers"];
  if (typeof value !== "object" || r === null
      || Object.keys(r).sort().join() !== "body,event_id,headers"
      || typeof r["body"] !== "string" || typeof r["event_id"] !== "string"
      || !/^[A-Za-z0-9_-]{1,128}$/.test(r["event_id"])
      || typeof headers !== "object" || headers === null || Array.isArray(headers)
      || !Object.entries(headers).every(([name, v]) => typeof v === "string"
                                        && !AUTH_HEADER.test(name))) {
    throw new Refusal("invalid_request");
  }
  return r as unknown as RingRequest;
}

/**
 * One wake's send: `blob` opened for its subscription at `version` (and `previous`, the
 * replaced secret's, while its rotation window lasts), the request sent to the sealed URL.
 */
export async function ringDoorbell(env: KeyringEnv, owner: string, subscription: string,
                                   version: number, blob: string,
                                   previous: { readonly version: number; readonly blob: string }
                                     | null,
                                   request: unknown, send: Send = (r) => fetch(r)):
    Promise<RingAnswer> {
  const checked = ringRequest(request);
  let opened: Awaited<ReturnType<typeof openValue<DoorbellSecret>>>;
  let old: DoorbellSecret | null = null;
  try {
    opened = await openValue(env, bound(owner, subscription, version), blob, doorbellSecret);
    if (previous !== null) {
      old = (await openValue(env, bound(owner, subscription, previous.version), previous.blob,
                             doorbellSecret)).value;
      if (old.url !== opened.value.url) {
        old = null;  // a replaced secret signs only for the URL it was given for
      }
    }
  } catch (error) {
    // A blob that does not open, or a binding it was never sealed for, is this wake's own
    // refusal; anything else (a keyring the config lost) fails the call loudly.
    if (UNOPENED.includes(refusalOf(error)?.code ?? "")) {
      return { state: "refused" };
    }
    throw error;
  }
  const secret = opened.value;
  const stamp = Math.floor(Date.now() / 1000);
  let response: Response;
  try {
    response = await send(new Request(secret.url, {
      method: "POST", redirect: "manual",
      headers: { ...checked.headers, ...await auth(secret, old, checked, stamp) },
      body: checked.body,
    }));
  } catch {
    return { state: "unknown" };  // a byte may have left
  }
  const retry = response.headers.get("Retry-After");
  await response.body?.cancel().catch(() => undefined);
  return { state: "answered", status: response.status, retry_after: retry,
           ...(opened.resealed === undefined ? {} : { resealed: opened.resealed }) };
}
