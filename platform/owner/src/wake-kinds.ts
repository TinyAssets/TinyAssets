/**
 * Wake kinds as data (slice W, tasks 7; Direction 2: engine differences are data).
 *
 * Each vendor's way to wake its main agent is one record of `wake-kinds.json`: ChatGPT's
 * MCP Events, Claude's scheduled-task `/fire`, Grok Bot's webhook routines, and Muse,
 * which has no documented push. The doorbell (`wakes.ts`) is one sender for every record:
 * it checks a callback against its record's URL and secret rules, and builds the request
 * from its record's auth scheme, headers and body template. No code here or there names a
 * vendor.
 *
 * - A record's `delivery_method` is the agent registry's (`ledger.ts`), one record each.
 *   A record without `push` never rings: its agent catches up through `inbox_digest` and
 *   `board_digest` on its own schedule (`catch_up`), and TinyAssets polls nothing.
 * - `callback` says who hands TinyAssets the callback: `subscribe`, the agent's own
 *   client over the MCP door (MCP Events), or `owner`, the owner in session, pasting a
 *   token the vendor showed it.
 * - `url`: always `https` on port 443, a DNS name (never an IP literal, `network.ts`), no
 *   credentials or fragment; `hosts` and `path` narrow it when the vendor documents them.
 * - `auth`: `bearer` (the secret as an `Authorization` bearer token) or
 *   `standard_webhooks` (the secret a `whsec_` key; `webhook-id`, `webhook-timestamp`
 *   and a `v1` HMAC-SHA256 `webhook-signature`).
 * - `headers` and `body` are templates: a JSON string that is exactly a placeholder
 *   (`{wake}`, `{text}`, `{event_id}`, `{timestamp}`, `{subscription_id}`) is replaced by
 *   its value; nothing is interpolated inside a string.
 * - `gone`: the statuses that say the callback is dead, which erase it.
 *
 * A wake carries identifiers, cursors and what kind of change it is, never content
 * (S31): the main agent reads the change itself, through its own grant.
 */
import { Buffer } from "node:buffer";
import { createHmac } from "node:crypto";

import { Invalid } from "./errors";
import { checkDestination } from "./network";
import raw from "../wake-kinds.json" with { type: "json" };

/** What a wake says changed: mail to the agent, a room's rows, or the board's. */
export type Change = "mail" | "rooms" | "board";
export const CHANGES: readonly Change[] = Object.freeze(["mail", "rooms", "board"]);

export interface Citation {
  readonly url: string;
  readonly read: string;
  readonly evidence: "V" | "VS" | "3P";
}

export interface Push {
  readonly mechanism: string;
  readonly callback: "subscribe" | "owner";
  readonly url: { readonly hosts: readonly string[] | null; readonly path: string | null };
  readonly secret: string;
  readonly auth: "bearer" | "standard_webhooks";
  readonly headers: Readonly<Record<string, string>>;
  readonly body: unknown;
  readonly max_body_bytes: number;
  readonly gone: readonly number[];
}

export interface WakeKind {
  readonly id: string;
  readonly vendor: string;
  readonly agents: string;
  readonly delivery_method: string;
  readonly push: Push | null;
  readonly catch_up: string;
  readonly vendor_limits: readonly string[];
  readonly citations: readonly Citation[];
}

/** A stored callback, as the doorbell sends to it. */
export interface Callback {
  readonly kind: string;
  readonly url: string;
  readonly secret: string;
  readonly subscription_id: string | null;
}

/** What a wake says: identifiers, cursors and the kinds of change. Never content. */
export interface WakeContent {
  readonly agent_id: string;
  readonly command_center_id: string;
  readonly changes: readonly Change[];
  /** The agent's own acknowledged mailbox cursor: its unread mail is past it. */
  readonly cursor: number;
  /** The owner-wide change cursor to read rooms and the board after. */
  readonly after: number;
  /** The digests that answer just this change. */
  readonly read: readonly string[];
}

const PLACEHOLDERS: readonly string[] = Object.freeze(["{wake}", "{text}", "{event_id}",
                                                      "{timestamp}", "{subscription_id}"]);
const ID = /^[a-z][a-z0-9_]{0,63}$/;
const HEADER = /^[A-Za-z0-9-]{1,64}$/;
const HOST = /^[a-z0-9.-]{1,253}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const KEYS = "agents,catch_up,citations,delivery_method,id,push,vendor,vendor_limits";
const PUSH_KEYS = "auth,body,callback,gone,headers,max_body_bytes,mechanism,secret,url";
/** What a URL may be at most, and a Standard Webhooks key's decoded bytes. */
export const CALLBACK_URL_CHARS = 2048;
export const CALLBACK_SECRET_CHARS = 512;
const WHSEC = Object.freeze({ low: 24, high: 64 });

function record(value: unknown, keys: string, what: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)
      || Object.keys(value).sort().join() !== keys) {
    throw new Error(`wake kinds: ${what} is not exactly {${keys}}`);
  }
  return value as Record<string, unknown>;
}

function text(value: unknown, what: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`wake kinds: ${what} is not text`);
  }
  return value;
}

function strings(value: unknown, what: string): string[] {
  if (!Array.isArray(value)) {
    throw new Error(`wake kinds: ${what} is not a list`);
  }
  return value.map((item, n) => text(item, `${what}[${n}]`));
}

/** Every placeholder a template holds is a known one, as a whole string. */
function template(value: unknown, what: string): unknown {
  if (typeof value === "string") {
    if (value.includes("{") && !PLACEHOLDERS.includes(value)) {
      throw new Error(`wake kinds: ${what} names an unknown placeholder`);
    }
  } else if (Array.isArray(value)) {
    value.forEach((item, n) => template(item, `${what}[${n}]`));
  } else if (typeof value === "object" && value !== null) {
    for (const [key, item] of Object.entries(value)) {
      template(item, `${what}.${key}`);
    }
  } else if (!(value === null || typeof value === "number" || typeof value === "boolean")) {
    throw new Error(`wake kinds: ${what} is not JSON`);
  }
  return value;
}

function push(value: unknown, what: string): Push | null {
  if (value === null) {
    return null;
  }
  const p = record(value, PUSH_KEYS, what);
  const url = record(p["url"], "hosts,path", `${what}.url`);
  const hosts = url["hosts"] === null ? null : strings(url["hosts"], `${what}.url.hosts`);
  if (hosts !== null && (!hosts.length || !hosts.every((host) => HOST.test(host)))) {
    throw new Error(`wake kinds: ${what}.url.hosts are not host names`);
  }
  const path = url["path"] === null ? null : text(url["path"], `${what}.url.path`);
  for (const pattern of [path, p["secret"]]) {
    if (pattern !== null && (typeof pattern !== "string" || !pattern.startsWith("^")
                             || !pattern.endsWith("$"))) {
      throw new Error(`wake kinds: ${what}'s patterns are anchored`);
    }
    if (pattern !== null) {
      new RegExp(pattern as string);  // a pattern that does not compile fails here, loudly
    }
  }
  if (p["callback"] !== "subscribe" && p["callback"] !== "owner") {
    throw new Error(`wake kinds: ${what}.callback is subscribe or owner`);
  }
  if (p["auth"] !== "bearer" && p["auth"] !== "standard_webhooks") {
    throw new Error(`wake kinds: ${what}.auth is bearer or standard_webhooks`);
  }
  const headers = p["headers"];
  if (typeof headers !== "object" || headers === null || Array.isArray(headers)
      || !Object.entries(headers).every(([name, v]) => HEADER.test(name)
                                        && typeof v === "string"
                                        && !/^authorization$|^webhook-/i.test(name))) {
    throw new Error(`wake kinds: ${what}.headers are names to text, never the auth's own`);
  }
  template(headers, `${what}.headers`);
  const max = p["max_body_bytes"];
  if (typeof max !== "number" || !Number.isSafeInteger(max) || max < 1024) {
    throw new Error(`wake kinds: ${what}.max_body_bytes is a byte count`);
  }
  const gone = p["gone"];
  if (!Array.isArray(gone) || !gone.every((s) => Number.isSafeInteger(s) && s >= 400 && s < 600)) {
    throw new Error(`wake kinds: ${what}.gone are error statuses`);
  }
  return Object.freeze({
    mechanism: text(p["mechanism"], `${what}.mechanism`), callback: p["callback"],
    url: Object.freeze({ hosts: hosts === null ? null : Object.freeze(hosts), path }),
    secret: p["secret"] as string, auth: p["auth"],
    headers: Object.freeze({ ...(headers as Record<string, string>) }),
    body: template(p["body"], `${what}.body`), max_body_bytes: max,
    gone: Object.freeze([...gone] as number[]),
  });
}

/** The records as the file holds them, checked whole: a bad record fails loudly. */
export function checkKinds(value: unknown): readonly WakeKind[] {
  const file = record(value, "about,kinds", "the file");
  if (!Array.isArray(file["kinds"]) || !file["kinds"].length) {
    throw new Error("wake kinds: kinds is not a list of records");
  }
  const kinds = file["kinds"].map((item, n): WakeKind => {
    const k = record(item, KEYS, `kinds[${n}]`);
    const id = text(k["id"], `kinds[${n}].id`);
    if (!ID.test(id) || !ID.test(text(k["delivery_method"], `${id}.delivery_method`))) {
      throw new Error(`wake kinds: ${id}'s id and delivery_method are identifiers`);
    }
    if (!Array.isArray(k["citations"]) || !k["citations"].length) {
      throw new Error(`wake kinds: ${id} cites no source`);
    }
    const citations = k["citations"].map((c, m): Citation => {
      const cite = record(c, "evidence,read,url", `${id}.citations[${m}]`);
      if (typeof cite["url"] !== "string" || !cite["url"].startsWith("https://")
          || typeof cite["read"] !== "string" || !DATE.test(cite["read"])
          || !["V", "VS", "3P"].includes(cite["evidence"] as string)) {
        throw new Error(`wake kinds: ${id}.citations[${m}] is an https URL, a date, a label`);
      }
      return Object.freeze(cite as unknown as Citation);
    });
    return Object.freeze({
      id, vendor: text(k["vendor"], `${id}.vendor`), agents: text(k["agents"], `${id}.agents`),
      delivery_method: k["delivery_method"] as string, push: push(k["push"], `${id}.push`),
      catch_up: text(k["catch_up"], `${id}.catch_up`),
      vendor_limits: Object.freeze(strings(k["vendor_limits"], `${id}.vendor_limits`)),
      citations: Object.freeze(citations),
    });
  });
  for (const field of ["id", "delivery_method"] as const) {
    if (new Set(kinds.map((kind) => kind[field])).size !== kinds.length) {
      throw new Error(`wake kinds: two records share a ${field}`);
    }
  }
  return Object.freeze(kinds);
}

/**
 * The bundled records (`wake-kinds.json`), checked whole at each read (four records; a
 * module keeps no state of its own at top level).
 */
export function wakeKinds(): readonly WakeKind[] {
  return checkKinds(raw);
}

/** The record for a delivery method; null when none (`a2a`, which no slice admits yet). */
export function kindFor(deliveryMethod: string): WakeKind | null {
  return wakeKinds().find((kind) => kind.delivery_method === deliveryMethod) ?? null;
}

/** The record named `id`, which a stored callback names. */
export function kindNamed(id: string): WakeKind | null {
  return wakeKinds().find((kind) => kind.id === id) ?? null;
}

/** The delivery methods whose agents ring: a record with a push. */
export function ringingMethods(kinds: readonly WakeKind[] = wakeKinds()): string[] {
  return kinds.filter((kind) => kind.push !== null).map((kind) => kind.delivery_method);
}

/** Every delivery method a record admits. */
export function admittedMethods(kinds: readonly WakeKind[] = wakeKinds()): string[] {
  return kinds.map((kind) => kind.delivery_method);
}

/** A callback URL this record's push accepts, as given; refused (`Invalid`) otherwise. */
export function callbackUrl(push: Push, value: unknown): string {
  if (typeof value !== "string" || value.length > CALLBACK_URL_CHARS
      || !/^[!-~]+$/.test(value) || value.includes("\\")) {
    throw new Invalid("invalid callback url");
  }
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Invalid("invalid callback url");
  }
  if (url.protocol !== "https:" || url.port !== "" || url.username !== "" || url.password !== ""
      || url.hash !== "" || value.includes("#")) {
    throw new Invalid("a callback is https on port 443, with no credentials or fragment");
  }
  checkDestination({ host: url.hostname, port: 443 });  // a DNS name, never an IP literal
  if (push.url.hosts !== null && !push.url.hosts.includes(url.hostname)) {
    throw new Invalid("this kind's callback is on its vendor's own host");
  }
  if (push.url.path !== null && !new RegExp(push.url.path).test(url.pathname)) {
    throw new Invalid("this kind's callback is its vendor's own path");
  }
  return value;
}

/** A callback secret this record's push accepts; refused (`Invalid`) otherwise. */
export function callbackSecret(push: Push, value: unknown): string {
  if (typeof value !== "string" || value.length > CALLBACK_SECRET_CHARS
      || !new RegExp(push.secret).test(value)) {
    throw new Invalid("invalid callback secret");
  }
  if (push.auth === "standard_webhooks") {
    const key = Buffer.from(value.slice("whsec_".length), "base64");
    if (key.length < WHSEC.low || key.length > WHSEC.high) {
      throw new Invalid("invalid callback secret");
    }
  }
  return value;
}

/** The wake's one line, for a kind whose body is text: the change and where to read it. */
export function wakeText(wake: WakeContent): string {
  return `TinyAssets: new ${wake.changes.join(", ")} for agent ${wake.agent_id} in ` +
    `${wake.command_center_id}. Read just the change: ` +
    `${wake.read.map((digest) => `${digest} with after=${wake.after}`).join("; ")}.`;
}

function filled(value: unknown, values: Readonly<Record<string, unknown>>): unknown {
  if (typeof value === "string") {
    return Object.hasOwn(values, value) ? values[value] : value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => filled(item, values));
  }
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(Object.entries(value).map(([k, item]) => [k, filled(item, values)]));
  }
  return value;
}

/** The request one wake sends: the record's own shape, to the callback. */
export interface DoorbellRequest {
  readonly url: string;
  readonly headers: Readonly<Record<string, string>>;
  readonly body: string;
}

/**
 * Build one wake's request from its record. `eventId` is the wake's own id (a consumer
 * deduplicates by it); `now` is in seconds. Refused (`Invalid`) before any byte when the
 * callback no longer passes the record, or the body is past its record's size.
 */
export function doorbellRequest(kind: WakeKind, callback: Callback, wake: WakeContent,
                                eventId: string, now: number): DoorbellRequest {
  const p = kind.push;
  if (p === null || callback.kind !== kind.id) {
    throw new Invalid("this kind has no push");
  }
  const url = callbackUrl(p, callback.url);
  const secret = callbackSecret(p, callback.secret);
  const stamp = Math.floor(now);
  const values: Record<string, unknown> = {
    "{wake}": wake, "{text}": wakeText(wake), "{event_id}": eventId,
    "{timestamp}": new Date(stamp * 1000).toISOString(),
    "{subscription_id}": callback.subscription_id ?? "",
  };
  const body = JSON.stringify(filled(p.body, values));
  if (new TextEncoder().encode(body).byteLength > p.max_body_bytes) {
    throw new Invalid("the wake is past its kind's body size");
  }
  const headers: Record<string, string> = {};
  for (const [name, value] of Object.entries(p.headers)) {
    headers[name] = String(filled(value, values));
  }
  if (p.auth === "bearer") {
    headers["Authorization"] = `Bearer ${secret}`;
  } else {
    const key = Buffer.from(secret.slice("whsec_".length), "base64");
    const signature = createHmac("sha256", key).update(`${eventId}.${stamp}.${body}`, "utf8")
      .digest("base64");
    Object.assign(headers, { "webhook-id": eventId, "webhook-timestamp": String(stamp),
                             "webhook-signature": `v1,${signature}` });
  }
  return { url, headers: Object.freeze(headers), body };
}
