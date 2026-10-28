/**
 * The envelope the owner object's `gateway` provider sends with each model call
 * (research §1.4, §2.3): one private header, `x-ta-envelope`, holding base64url of UTF-8
 * JSON with exactly these fields:
 *
 * - `owner`, `connection`, `version`, `blob`: the sealed credential and what it is bound
 *   to. The owner is the object's own name (`ctx.id.name`); the gateway takes it from here
 *   and the seal holds the call to it, so it never chooses an owner.
 * - `reservation`: the owner's spend reservation for this call (`id`, and
 *   `max_output_tokens`, which bounds the request's `maxTokens`).
 * - `policy_generation`: the owner policy the reservation was made under, echoed back so
 *   the settlement matches it (§6: the gateway makes no call back to the object).
 * - `deadline`: epoch ms after which the call is cut, at most `DEADLINE_MAX_MS` ahead.
 *
 * Base64url, because an owner name may be any printable Unicode and a header value is
 * Latin-1. Anything else is refused as `invalid_envelope`.
 */
import { type Binding, binding } from "./keyring";
import { Failure } from "./failure";

export const ENVELOPE_HEADER = "x-ta-envelope";

/** The longest a call may be given: the owner object's alarm budget (FV 1.7). */
export const DEADLINE_MAX_MS = 15 * 60 * 1000;

const HEADER_MAX = 96 * 1024;
const RESERVATION_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const B64URL = /^[A-Za-z0-9_-]+$/;

export interface Reservation {
  readonly id: string;
  readonly max_output_tokens: number;
}

export interface Envelope {
  readonly binding: Binding;
  readonly blob: string;
  readonly reservation: Reservation;
  readonly policy_generation: number;
  readonly deadline: number;
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function keys(value: Record<string, unknown>): string {
  return Object.keys(value).sort().join();
}

function decode(header: string): unknown {
  if (header.length > HEADER_MAX || !B64URL.test(header) || header.length % 4 === 1) {
    throw new Failure("invalid_envelope");
  }
  const padded = header.replace(/-/g, "+").replace(/_/g, "/");
  try {
    const binary = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes));
  } catch {
    throw new Failure("invalid_envelope");
  }
}

function positive(value: unknown, min: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min) {
    throw new Failure("invalid_envelope");
  }
  return value;
}

/** The envelope a request carries, checked against `now`; `invalid_envelope` otherwise. */
export function envelope(header: string | null, now: number): Envelope {
  if (header === null) {
    throw new Failure("invalid_envelope");
  }
  const value = decode(header);
  if (!object(value) || keys(value)
      !== "blob,connection,deadline,owner,policy_generation,reservation,version") {
    throw new Failure("invalid_envelope");
  }
  const reservation = value["reservation"];
  if (!object(reservation) || keys(reservation) !== "id,max_output_tokens"
      || typeof reservation["id"] !== "string" || !RESERVATION_ID.test(reservation["id"])) {
    throw new Failure("invalid_envelope");
  }
  let bound: Binding;
  try {
    bound = binding(value["owner"], value["connection"], value["version"]);
  } catch {
    throw new Failure("invalid_envelope");
  }
  const deadline = positive(value["deadline"], 0);
  if (deadline <= now) {
    throw new Failure("deadline");
  }
  if (deadline > now + DEADLINE_MAX_MS) {
    throw new Failure("invalid_envelope");
  }
  if (typeof value["blob"] !== "string") {
    throw new Failure("invalid_envelope");
  }
  return Object.freeze({
    binding: bound,
    blob: value["blob"],
    reservation: Object.freeze({ id: reservation["id"],
                                 max_output_tokens: positive(reservation["max_output_tokens"], 1) }),
    policy_generation: positive(value["policy_generation"], 0),
    deadline,
  });
}

/** The header value carrying `fields` (the owner object's side of the wire, and tests'). */
export function encodeEnvelope(fields: {
  readonly owner: string; readonly connection: string; readonly version: number;
  readonly blob: string; readonly reservation: Reservation;
  readonly policy_generation: number; readonly deadline: number;
}): string {
  let binary = "";
  for (const byte of new TextEncoder().encode(JSON.stringify(fields))) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
