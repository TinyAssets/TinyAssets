/**
 * Inbound proof (tasks 2, `/hooks/inbound`; slice L), ported from the Python's
 * `broker/inbound_verification.py`: a verification profile, as data, and the check of a
 * request's raw bytes against it. The binder checks a shared app's (the platform's own
 * signing secret, `doors/src/inbound.ts`); the owner's object checks an owner's own
 * capability hook's, against the secret the owner holds there (`inbound.ts`).
 *
 * - `hmac-sha256`: the signature in `signature_header`, after `prefix`, in `encoding`, over
 *   `version:timestamp:body` (each part present only when the profile names it), the
 *   timestamp in `timestamp_header` within `max_skew_seconds` of now, so a replay past the
 *   skew is refused: Slack's `v0` scheme, GitHub's `sha256=` one and their kin as data.
 * - `token`: a header whose value's SHA-256 is the profile's digest.
 *
 * Every comparison is constant-time; a profile is checked whole before it is used.
 */
import { createHash, createHmac, timingSafeEqual } from "node:crypto";

import { Invalid } from "./errors";
import { HEX64 } from "./ids";

const HEADER = /^[a-z0-9][a-z0-9-]{0,127}$/;
const STAMP = /^[0-9]{1,12}$/;
/** The longest skew a profile may allow: Slack's own five minutes. */
export const MAX_SKEW_S = 300;

export interface HmacProfile {
  readonly method: "hmac-sha256";
  readonly signature_header: string;
  readonly encoding: "hex" | "base64";
  readonly prefix: string;
  readonly version?: string;
  readonly timestamp_header?: string;
  readonly max_skew_seconds?: number;
}

export interface TokenProfile {
  readonly method: "token";
  readonly header: string;
  readonly digest: string;
}

export type Profile = HmacProfile | TokenProfile;

function header(value: unknown): string {
  if (typeof value !== "string" || !HEADER.test(value)) {
    throw new Invalid("a proof header is a lowercase header name");
  }
  return value;
}

/** A profile checked whole, or `Invalid`. */
export function profile(value: unknown): Profile {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Invalid("a proof profile is an object");
  }
  const fields = value as Record<string, unknown>;
  const keys = Object.keys(fields);
  if (fields["method"] === "token") {
    if (keys.sort().join() !== "digest,header,method" || typeof fields["digest"] !== "string"
        || !HEX64.test(fields["digest"])) {
      throw new Invalid("a token profile is its header and its value's SHA-256");
    }
    return Object.freeze({ method: "token", header: header(fields["header"]),
                           digest: fields["digest"] });
  }
  if (fields["method"] !== "hmac-sha256") {
    throw new Invalid("a proof profile's method is hmac-sha256 or token");
  }
  const allowed = ["method", "signature_header", "encoding", "prefix", "version",
                   "timestamp_header", "max_skew_seconds"];
  if (keys.some((key) => !allowed.includes(key))
      || !["signature_header", "encoding", "prefix"].every((key) => keys.includes(key))) {
    throw new Invalid("an HMAC profile names its signature header, encoding and prefix");
  }
  const { encoding, prefix, version } = fields;
  if (encoding !== "hex" && encoding !== "base64") {
    throw new Invalid("an HMAC profile's encoding is hex or base64");
  }
  if (typeof prefix !== "string" || prefix.length > 32 || !/^[\x21-\x7e]*$/.test(prefix)) {
    throw new Invalid("an HMAC profile's prefix is at most 32 visible ASCII characters");
  }
  if (version !== undefined && (typeof version !== "string" || !/^[A-Za-z0-9]{1,16}$/.test(version))) {
    throw new Invalid("an HMAC profile's version is 1 to 16 letters or digits");
  }
  const timed = fields["timestamp_header"] !== undefined;
  if (timed !== (fields["max_skew_seconds"] !== undefined)) {
    throw new Invalid("an HMAC profile's timestamp has a finite skew, and only with one");
  }
  const skew = fields["max_skew_seconds"];
  if (timed && (typeof skew !== "number" || !Number.isSafeInteger(skew) || skew < 1
                || skew > MAX_SKEW_S)) {
    throw new Invalid(`an HMAC profile's skew is 1 to ${MAX_SKEW_S} seconds`);
  }
  return Object.freeze({
    method: "hmac-sha256", signature_header: header(fields["signature_header"]), encoding,
    prefix, ...(version === undefined ? {} : { version: version as string }),
    ...(timed ? { timestamp_header: header(fields["timestamp_header"]),
                  max_skew_seconds: skew as number } : {}),
  });
}

function equal(a: Uint8Array, b: Uint8Array): boolean {
  return a.byteLength === b.byteLength && timingSafeEqual(a, b);
}

/** The request's headers by lowercase name. */
export type Headers = Readonly<Record<string, string>>;

/**
 * Whether `body`, with `headers`, proves `checked` under `secret` (an HMAC's key), at `now`
 * (whole seconds). False for anything short of proof; never a throw for a caller's bytes.
 */
export function proves(checked: Profile, headers: Headers, body: Uint8Array,
                       secret: string | null, now: number): boolean {
  if (checked.method === "token") {
    const value = headers[checked.header];
    if (typeof value !== "string" || value.length > 4096) {
      return false;
    }
    return equal(createHash("sha256").update(value, "utf8").digest(),
                 Buffer.from(checked.digest, "hex"));
  }
  const signature = headers[checked.signature_header];
  if (typeof signature !== "string" || !signature.startsWith(checked.prefix) || secret === null
      || secret === "") {
    return false;
  }
  let stamp: string | null = null;
  if (checked.timestamp_header !== undefined) {
    stamp = headers[checked.timestamp_header] ?? null;
    if (stamp === null || !STAMP.test(stamp)
        || Math.abs(now - Number(stamp)) > checked.max_skew_seconds!) {
      return false;
    }
  }
  const encoded = signature.slice(checked.prefix.length);
  const valid = checked.encoding === "hex" ? /^(?:[0-9a-f]{2})+$/i.test(encoded)
    : /^[A-Za-z0-9+/]+={0,2}$/.test(encoded);
  if (!valid) {
    return false;
  }
  const supplied = Buffer.from(encoded, checked.encoding);
  const mac = createHmac("sha256", secret);
  if (checked.version !== undefined) {
    mac.update(`${checked.version}:`, "utf8");
  }
  if (stamp !== null) {
    mac.update(`${stamp}:`, "utf8");
  }
  mac.update(body);
  return equal(supplied, mac.digest());
}
