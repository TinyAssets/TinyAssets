/**
 * The operator channel (d3 §G; `operator_research.md` §1.4): how the deploy kit asks the doors
 * for a platform act, and how the doors answer. One module, so the kit's sending side
 * (`deploy/operator.ts`) and the doors' consumer (`doors/src/operator.ts`) cannot disagree. It
 * shares nothing with the cutover's import messages, which go with the import whole.
 *
 * A message is `{v, op, owner, args, ask, at, kid, mac}`, sent to the operator queue
 * (`QUEUES.OPERATOR`):
 * - `v`, this format's version, 1;
 * - `op`, what the doors do (`OP`: dotted lowercase words); the doors' table of ops names
 *   each one they take, and answers any other refused;
 * - `owner`, the owner principal it is done to (`principal`);
 * - `args`, the op's own fields: a flat object of strings, safe integers and booleans, each
 *   key `ARG`, so its canonical text (`argsText`, its keys sorted) needs no general
 *   canonical JSON;
 * - `ask`, 32 hex the kit picks: the message's nonce, and the name of its acceptance record
 *   and its answer in the operator bucket (`acceptanceKey`, `answerKey`);
 * - `at`, the whole second the kit signed it;
 * - `kid`, the `OPERATOR_KEYRING` kid its MAC is under;
 * - `mac`, HMAC-SHA-256 under that kid's key over `MAC_DOMAIN`, the op, the owner, the
 *   SHA-256 of the args' canonical text, the ask and `at`, each after a NUL: base64url.
 *
 * The answer (`OperatorAnswer`) is the message less its MAC, with when the doors finished it
 * (`done`) and either the op's `result` or its `refused` refusal. The bucket keeps it, and
 * the acceptance record before it, under a lock (`deploy/platform.json` `bucket_locks`): the
 * channel's audit log.
 *
 * Pure: WebCrypto and the contract's own leaves, imported by path, so the kit runs it under
 * Node's type stripping as the doors bundle it.
 */
import { base64url, fromBase64url, hex, KID, type Ring } from "./envelope.ts";
import { HEX32 } from "./ids.ts";
import { REFUSALS, type RefusalCode } from "./refusal.ts";
import { principal } from "./text.ts";

/** The keyring the MAC is under, by name: a doors Worker secret and a `production` one. */
export const OPERATOR_KEYRING = "OPERATOR_KEYRING";
/** This format's version. */
export const OPERATOR_VERSION = 1;
/** The MAC's domain, so no other use of a key could make an operator message's MAC. */
const MAC_DOMAIN = "tinyassets-operator-v1";
/**
 * How far a message's `at` may be from the doors' clock when they first accept it: a message
 * unseen this long is refused, and one seen before goes on (`doors/src/operator.ts`).
 */
export const OPERATOR_WINDOW_S = 600;

/** An op: dotted lowercase words, at most 64 characters. */
const OP = /^(?=.{1,64}$)[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)*$/;
/** An arg's key: a lowercase word, at most 32 characters. */
const ARG = /^[a-z][a-z0-9_]{0,31}$/;
const MAC = /^[A-Za-z0-9_-]{43}$/;

/** An op's own fields. */
export type OperatorArgs = Readonly<Record<string, string | number | boolean>>;

export interface OperatorMessage {
  readonly v: typeof OPERATOR_VERSION;
  readonly op: string;
  readonly owner: string;
  readonly args: OperatorArgs;
  readonly ask: string;
  readonly at: number;
  readonly kid: string;
  readonly mac: string;
}

/** What the doors keep when they first accept a message: what its ask may mean from then on. */
export interface Acceptance {
  readonly op: string;
  readonly owner: string;
  readonly digest: string;
  readonly at: number;
  readonly kid: string;
}

/** A refusal, as an answer carries it: its code and its message. */
export interface Refused {
  readonly code: RefusalCode;
  readonly message: string;
}

/** The doors' answer to a message: its fields but the MAC, when it was done, and how. */
export type OperatorAnswer = Omit<OperatorMessage, "mac"> & { readonly done: number } &
  ({ readonly result: unknown } | { readonly refused: Refused });

const plain = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const whole = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

/** `body`'s keys are exactly `keys`, in any order. */
function exactly(body: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(body).sort().join() === [...keys].sort().join();
}

/** The args in their exact shape, or a throw. */
function argsOf(value: unknown): OperatorArgs {
  if (!plain(value) || Object.entries(value).some(([key, arg]) => !ARG.test(key)
      || !(typeof arg === "string" || typeof arg === "boolean"
           || (typeof arg === "number" && Number.isSafeInteger(arg))))) {
    throw new Error("an operator message's args are lowercase keys of strings, safe " +
                    "integers and booleans");
  }
  return Object.freeze({ ...value }) as OperatorArgs;
}

/** The args' canonical text: their JSON with the keys sorted (no key is integer-like). */
export function argsText(args: OperatorArgs): string {
  return JSON.stringify(Object.fromEntries(Object.entries(args)
    .sort(([a], [b]) => (a < b ? -1 : 1))));
}

/** The SHA-256 of the args' canonical text, as hex: what the MAC and the acceptance name. */
export async function argsDigest(args: OperatorArgs): Promise<string> {
  return hex(new Uint8Array(await crypto.subtle.digest("SHA-256",
                                                       new TextEncoder().encode(argsText(args)))));
}

/** A message in its exact shape, or a throw: no field more, none missing, each its grammar. */
export function operatorMessage(value: unknown): OperatorMessage {
  if (!plain(value) || !exactly(value, ["v", "op", "owner", "args", "ask", "at", "kid", "mac"])) {
    throw new Error("an operator message is exactly v, op, owner, args, ask, at, kid and mac");
  }
  const { v, op, ask, at, kid, mac } = value;
  if (v !== OPERATOR_VERSION || typeof op !== "string" || !OP.test(op)
      || typeof ask !== "string" || !HEX32.test(ask) || !whole(at)
      || typeof kid !== "string" || !KID.test(kid) || typeof mac !== "string" || !MAC.test(mac)) {
    throw new Error("an operator message's v, op, ask, at, kid or mac is out of shape");
  }
  return Object.freeze({ v, op, owner: principal(value["owner"]), args: argsOf(value["args"]),
                         ask, at, kid, mac });
}

/** The bytes the MAC is over. */
async function macInput(message: Omit<OperatorMessage, "v" | "kid" | "mac">): Promise<Uint8Array> {
  return new TextEncoder().encode([MAC_DOMAIN, message.op, message.owner,
                                   await argsDigest(message.args), message.ask,
                                   String(message.at)].join("\0"));
}

function macKey(key: Uint8Array, use: "sign" | "verify"): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, [use]);
}

/**
 * The kit's message: `op` on `owner` with `args`, under the ring's active kid, at `at`, with a
 * fresh ask.
 */
export async function signOperator(ring: Ring, op: string, owner: string, args: OperatorArgs,
                                   at: number): Promise<OperatorMessage> {
  const ask = hex(crypto.getRandomValues(new Uint8Array(16)));
  const key = ring.keys.get(ring.active);
  if (key === undefined) {
    throw new Error("a ring's active kid is among its keys");
  }
  const unsigned = { op, owner, args, ask, at };
  const mac = base64url(new Uint8Array(await crypto.subtle.sign(
    "HMAC", await macKey(key, "sign"), await macInput(unsigned))));
  return operatorMessage({ v: OPERATOR_VERSION, ...unsigned, kid: ring.active, mac });
}

/** Whether `message`'s kid is in `ring` and its MAC is that key's, compared in constant time. */
export async function macHolds(ring: Ring, message: OperatorMessage): Promise<boolean> {
  const key = ring.keys.get(message.kid);
  if (key === undefined) {
    return false;
  }
  const mac = fromBase64url(message.mac);
  return mac !== null
    && crypto.subtle.verify("HMAC", await macKey(key, "verify"), mac, await macInput(message));
}

/** Where the doors keep a message's acceptance record, in the operator bucket. */
export const acceptanceKey = (ask: string): string => `asks/${ask}.json`;
/** Where the doors write a message's answer, which the kit reads. */
export const answerKey = (ask: string): string => `answers/${ask}.json`;

/** What an acceptance record holds of `message`. */
export async function acceptanceOf(message: OperatorMessage): Promise<Acceptance> {
  return { op: message.op, owner: message.owner, digest: await argsDigest(message.args),
           at: message.at, kid: message.kid };
}

/** An acceptance record as the bucket holds it, or a throw: a malformed one is a bug. */
export function acceptance(value: unknown): Acceptance {
  if (!plain(value) || !exactly(value, ["op", "owner", "digest", "at", "kid"])
      || typeof value["op"] !== "string" || typeof value["owner"] !== "string"
      || typeof value["digest"] !== "string" || !whole(value["at"])
      || typeof value["kid"] !== "string") {
    throw new Error("an operator acceptance record is malformed");
  }
  return value as unknown as Acceptance;
}

/** `message`'s answer, done at `done`: the op's `result`, or its refusal. */
export function answerOf(message: OperatorMessage, done: number,
                         outcome: { readonly result: unknown } | { readonly refused: Refused }):
    OperatorAnswer {
  const { mac: _mac, ...fields } = message;
  return { ...fields, done, ...outcome };
}

/**
 * The answer to `message` as the kit reads it from the bucket, or a throw: its exact shape,
 * naming the message's own ask, op, owner, args, `at` and kid.
 */
export function operatorAnswer(value: unknown, message: OperatorMessage): OperatorAnswer {
  const outcome = plain(value) && Object.hasOwn(value, "refused") ? "refused" : "result";
  if (!plain(value) || !exactly(value, ["v", "op", "owner", "args", "ask", "at", "kid", "done",
                                        outcome]) || !whole(value["done"])) {
    throw new Error(`the answer to ${message.ask} is out of shape`);
  }
  const named = (["v", "op", "owner", "ask", "at", "kid"] as const)
    .every((field) => value[field] === message[field]);
  if (!named || !plain(value["args"])
      || argsText(value["args"] as OperatorArgs) !== argsText(message.args)) {
    throw new Error(`the answer to ${message.ask} names another message`);
  }
  const refused = value["refused"];
  if (outcome === "refused" && (!plain(refused) || !exactly(refused, ["code", "message"])
      || typeof refused["code"] !== "string" || !Object.hasOwn(REFUSALS, refused["code"])
      || typeof refused["message"] !== "string")) {
    throw new Error(`the answer to ${message.ask} carries a refusal out of shape`);
  }
  return value as unknown as OperatorAnswer;
}
