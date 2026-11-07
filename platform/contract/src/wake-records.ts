/**
 * Wake kinds as data (slice W, tasks 7; Direction 2: engine differences are data).
 *
 * Each vendor's way to wake its main agent is one record of `wake-kinds.json`: ChatGPT's
 * MCP Events, Claude's scheduled-task `/fire`, Grok Bot's webhook routines, and Muse,
 * which has no documented push. One sender serves every record: the object builds each
 * wake's headers and body from its record (`doorbellRequest`), and the gateway, which alone
 * holds the sealed secret, adds the record's auth and makes the send (`gateway/src/
 * doorbell.ts`). No code here, in the object or in the gateway names a vendor.
 *
 * - A record with a push names the agent registry's `delivery_method` (`ledger.ts`), one
 *   record each. A record without one never rings and names none: its agent registers as
 *   `pull` and catches up through `inbox_digest` and `board_digest` on its own schedule
 *   (`catch_up`); TinyAssets polls nothing.
 * - `callback` says who hands TinyAssets the callback: `subscribe`, the agent's own client
 *   over the MCP door's `events/subscribe` (its `events` block: the event it lists, the
 *   verification's error code and timeout, the default lifetime and the rotation window),
 *   or `owner`, the owner in session, pasting a token the vendor showed it.
 * - `url`: always `https` on port 443, a public DNS name (never an IP literal, a local or
 *   internal name, nor one of our own hosts: `callbackUrl`), no credentials or fragment;
 *   `hosts` and `path` pin it when the vendor documents them.
 * - `auth`: `bearer` (the secret as an `Authorization` bearer token) or
 *   `standard_webhooks` (the secret a `whsec_` key; `webhook-id`, `webhook-timestamp`
 *   and a `v1` HMAC-SHA256 `webhook-signature`), applied by the gateway.
 * - `headers` and `body` are templates: a JSON string that is exactly a placeholder
 *   (`{wake}`, `{text}`, `{event_id}`, `{timestamp}`, `{subscription_id}`) is replaced by
 *   its value; nothing is interpolated inside a string.
 * - `max_body_bytes`: the vendor's cited ceiling, or null where it states none.
 * - `token_dead`: the statuses that say the owner's pasted token no longer works, which
 *   leave the owner a notice (the callback stays: the owner pastes the new one).
 *
 * A wake carries identifiers, cursors and what kind of change it is, never content
 * (S31): the main agent reads the change itself, through its own grant.
 *
 * This module is the records and the callback checks alone, importing nothing but the
 * errors, the zone and the records' file, so the gateway applies the same
 * checks where it sends
 * (`gateway/src/doorbell.ts`; its isolation guard walks the closure). What builds a wake,
 * and a subscription's id, is `wake-kinds.ts`.
 */
import { ZONE } from "./egress";
import { Refusal } from "./refusal";
import raw from "../wake-kinds.json" with { type: "json" };

export interface Citation {
  readonly url: string;
  readonly read: string;
  readonly evidence: "V" | "VS" | "3P";
}

/** What a subscribing kind's client is offered and held to (MCP Events). */
export interface Events {
  readonly name: string;
  readonly description: string;
  /** The JSON-RPC code a failed callback verification is answered with. */
  readonly verify_error_code: number;
  readonly verify_timeout_ms: number;
  /** The lifetime granted when the client suggests none. */
  readonly default_ttl_ms: number;
  /** How long a refresh's replaced secret still signs beside the new one. */
  readonly rotation_grace_s: number;
}

export interface Push {
  readonly mechanism: string;
  readonly callback: "subscribe" | "owner";
  readonly url: { readonly hosts: readonly string[] | null; readonly path: string | null };
  readonly secret: string;
  readonly auth: "bearer" | "standard_webhooks";
  readonly headers: Readonly<Record<string, string>>;
  readonly body: unknown;
  readonly max_body_bytes: number | null;
  readonly token_dead: readonly number[];
  readonly events: Events | null;
}

export interface WakeKind {
  readonly id: string;
  readonly vendor: string;
  readonly agents: string;
  readonly delivery_method: string | null;
  readonly push: Push | null;
  readonly catch_up: string;
  readonly vendor_limits: readonly string[];
  readonly citations: readonly Citation[];
}

const PLACEHOLDERS: readonly string[] = Object.freeze(["{wake}", "{text}", "{event_id}",
                                                      "{timestamp}", "{subscription_id}"]);
const ID = /^[a-z][a-z0-9_]{0,63}$/;
const HEADER = /^[A-Za-z0-9-]{1,64}$/;
const HOST = /^[a-z0-9.-]{1,253}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const EVENT_NAME = /^[a-z][a-z0-9_.]{0,63}$/;
const KEYS = "agents,catch_up,citations,delivery_method,id,push,vendor,vendor_limits";
const PUSH_KEYS = "auth,body,callback,events,headers,max_body_bytes,mechanism,secret," +
  "token_dead,url";
const EVENTS_KEYS = "default_ttl_ms,description,name,rotation_grace_s,verify_error_code," +
  "verify_timeout_ms";
/**
 * A callback URL's and secret's characters at most (a subscription's canonical arguments'
 * are `ARGUMENTS_CHARS`, a frozen bound of the owner's schema, in `limits.ts`): technical
 * ceilings, the subscriptions' share of the owner's database (the doorbell's subscriptions,
 * the owner-bound guard; `limits-audit.md`).
 */
export const CALLBACK_URL_CHARS = 2048;
export const CALLBACK_SECRET_CHARS = 512;
/** A Standard Webhooks key's decoded bytes (OpenAI's page: 24 to 64). */
const WHSEC = Object.freeze({ low: 24, high: 64 });
/**
 * Host names a callback never reaches, whatever DNS says: single-label names, local and
 * internal names (`localhost`, `.local`, `.internal`, `.arpa`) and our own hosts (our zone,
 * where `mcp.` is internal, and `workers.dev`, where our Workers answer). An address a
 * public name resolves to is the platform's to refuse (owed live probe, `tasks.md`).
 */
const REFUSED_SUFFIXES: readonly string[] = Object.freeze([
  "localhost", "local", "internal", "arpa", ZONE, "workers.dev",
]);
/** A header a record's or a wake's own may never be: the auth's, which the gateway sets. */
export const AUTH_HEADER = /^authorization$|^webhook-/i;
/** A DNS label, and a dotted IPv4 address as the URL parser writes every spelling of one. */
const LABEL = /^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$/;
const IPV4 = /^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+$/;

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

function whole(value: unknown, low: number, what: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < low) {
    throw new Error(`wake kinds: ${what} is a whole number from ${low}`);
  }
  return value;
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

function events(value: unknown, what: string): Events | null {
  if (value === null) {
    return null;
  }
  const e = record(value, EVENTS_KEYS, what);
  if (typeof e["name"] !== "string" || !EVENT_NAME.test(e["name"])) {
    throw new Error(`wake kinds: ${what}.name is an event name`);
  }
  const code = e["verify_error_code"];
  if (typeof code !== "number" || !Number.isSafeInteger(code) || code > -32000
      || code < -32099) {
    throw new Error(`wake kinds: ${what}.verify_error_code is a server error code`);
  }
  return Object.freeze({
    name: e["name"], description: text(e["description"], `${what}.description`),
    verify_error_code: code, verify_timeout_ms: whole(e["verify_timeout_ms"], 1, what),
    default_ttl_ms: whole(e["default_ttl_ms"], 1, what),
    rotation_grace_s: whole(e["rotation_grace_s"], 0, what),
  });
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
                                        && !AUTH_HEADER.test(name))) {
    throw new Error(`wake kinds: ${what}.headers are names to text, never the auth's own`);
  }
  template(headers, `${what}.headers`);
  const max = p["max_body_bytes"];
  if (max !== null) {
    whole(max, 1024, `${what}.max_body_bytes`);
  }
  const dead = p["token_dead"];
  if (!Array.isArray(dead) || !dead.every((s) => Number.isSafeInteger(s) && s >= 400 && s < 600)) {
    throw new Error(`wake kinds: ${what}.token_dead are error statuses`);
  }
  const subscribed = events(p["events"], `${what}.events`);
  if ((subscribed !== null) !== (p["callback"] === "subscribe")
      || (subscribed !== null && p["auth"] !== "standard_webhooks")) {
    throw new Error(`wake kinds: ${what}.events is a subscribing kind's, signed by webhook`);
  }
  return Object.freeze({
    mechanism: text(p["mechanism"], `${what}.mechanism`), callback: p["callback"],
    url: Object.freeze({ hosts: hosts === null ? null : Object.freeze(hosts), path }),
    secret: p["secret"] as string, auth: p["auth"],
    headers: Object.freeze({ ...(headers as Record<string, string>) }),
    body: template(p["body"], `${what}.body`), max_body_bytes: max as number | null,
    token_dead: Object.freeze([...dead] as number[]), events: subscribed,
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
    const method = k["delivery_method"];
    if (!ID.test(id) || (method !== null && (typeof method !== "string" || !ID.test(method)))) {
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
    const pushed = push(k["push"], `${id}.push`);
    if ((pushed === null) !== (method === null) || method === "pull") {
      throw new Error(`wake kinds: ${id} names a delivery method exactly when it pushes`);
    }
    return Object.freeze({
      id, vendor: text(k["vendor"], `${id}.vendor`), agents: text(k["agents"], `${id}.agents`),
      delivery_method: method as string | null, push: pushed,
      catch_up: text(k["catch_up"], `${id}.catch_up`),
      vendor_limits: Object.freeze(strings(k["vendor_limits"], `${id}.vendor_limits`)),
      citations: Object.freeze(citations),
    });
  });
  const methods = kinds.map((kind) => kind.delivery_method).filter((m) => m !== null);
  const names = kinds.map((kind) => kind.push?.events?.name).filter((m) => m !== undefined);
  if (new Set(kinds.map((kind) => kind.id)).size !== kinds.length
      || new Set(methods).size !== methods.length || new Set(names).size !== names.length) {
    throw new Error("wake kinds: two records share an id, a delivery method or an event");
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

/** The record for a delivery method; null for one no record pushes (`pull`, `a2a`). */
export function kindFor(deliveryMethod: string): WakeKind | null {
  return wakeKinds().find((kind) => kind.delivery_method === deliveryMethod) ?? null;
}

/** The record named `id`, which a stored callback names. */
export function kindNamed(id: string): WakeKind | null {
  return wakeKinds().find((kind) => kind.id === id) ?? null;
}

/** The subscribing record whose event is `name` (MCP Events), or null. */
export function eventKind(name: string): WakeKind | null {
  return wakeKinds().find((kind) => kind.push?.events?.name === name) ?? null;
}

/** The delivery methods whose agents ring: a record with a push. */
export function ringingMethods(kinds: readonly WakeKind[] = wakeKinds()): string[] {
  return kinds.flatMap((kind) => kind.delivery_method === null ? [] : [kind.delivery_method]);
}

/** Every delivery method an agent may register with: `pull`, and each pushing record's. */
export function admittedMethods(kinds: readonly WakeKind[] = wakeKinds()): string[] {
  return ["pull", ...ringingMethods(kinds)];
}

/** Whether `host` is one a callback never reaches (`REFUSED_SUFFIXES`, a single label). */
function refusedHost(host: string): boolean {
  const name = host.toLowerCase().replace(/\.$/, "");
  return !name.includes(".")
    || REFUSED_SUFFIXES.some((suffix) => name === suffix || name.endsWith(`.${suffix}`));
}

/** A callback URL this record's push accepts, as given; refused (`invalid`) otherwise. */
export function callbackUrl(push: Push, value: unknown): string {
  if (typeof value !== "string" || value.length > CALLBACK_URL_CHARS
      || !/^[!-~]+$/.test(value) || value.includes("\\")) {
    throw new Refusal("invalid", "invalid callback url");
  }
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Refusal("invalid", "invalid callback url");
  }
  if (url.protocol !== "https:" || url.port !== "" || url.username !== "" || url.password !== ""
      || url.hash !== "" || value.includes("#")) {
    throw new Refusal("invalid", "a callback is https on port 443, with no credentials or fragment");
  }
  const host = url.hostname;  // the parser's: any spelling of an IPv4 address comes back dotted
  if (host.includes(":") || host.startsWith("[") || host.length > 253 || IPV4.test(host)
      || !host.split(".").every((label) => LABEL.test(label))) {
    throw new Refusal("invalid", "a callback is a DNS name, never an IP literal");
  }
  if (refusedHost(url.hostname)) {
    throw new Refusal("invalid", "a callback is a public host, never a local, internal or our own");
  }
  if (push.url.hosts !== null && !push.url.hosts.includes(url.hostname)) {
    throw new Refusal("invalid", "this kind's callback is on its vendor's own host");
  }
  if (push.url.path !== null && !new RegExp(push.url.path).test(url.pathname)) {
    throw new Refusal("invalid", "this kind's callback is its vendor's own path");
  }
  return value;
}

/** A callback secret this record's push accepts; refused (`invalid`) otherwise. */
export function callbackSecret(push: Push, value: unknown): string {
  if (typeof value !== "string" || value.length > CALLBACK_SECRET_CHARS
      || !new RegExp(push.secret).test(value)) {
    throw new Refusal("invalid", "invalid callback secret");
  }
  if (push.auth === "standard_webhooks") {
    let bytes: number;
    try {
      bytes = atob(value.slice("whsec_".length)).length;
    } catch {
      throw new Refusal("invalid", "invalid callback secret");
    }
    if (bytes < WHSEC.low || bytes > WHSEC.high) {
      throw new Refusal("invalid", "invalid callback secret");
    }
  }
  return value;
}
