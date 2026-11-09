/**
 * The vendor's own sign-in, by its record's `cli.login` (`LoginSpec`): the hosts it may send
 * the owner to, the states and refusals a sign-in's frames carry, the shape its URL and code
 * must have, and whether it is switched on. Checked in the sandbox's VM before a prompt
 * leaves it, and again on the trusted side.
 */
import type { ControlRead } from "../../contract/src/control";
import { SIGNIN_CODE_MAX, SIGNIN_URL_MAX } from "../../contract/src/limits";

import type { SourceRecord, Sources } from "./schema";

/** The hosts a record's sign-in may send the owner to: its login hosts, and its region's. */
export function signinHosts(rec: SourceRecord, region: string | null): string[] {
  const cli = rec.cli;
  if (cli === undefined) {
    return [];
  }
  return [...cli.hosts.login, ...(region === null ? [] : cli.regions?.[region]?.hosts.login ?? [])];
}

/**
 * A sign-in's states, the only ones a frame from the VM may carry (review M1): no text the
 * VM chose leaves it but the record's own URL and code.
 */
export const SIGNIN_STATES = Object.freeze(["starting", "open", "code_sent", "signed_in",
                                            "signed_out", "failed", "cancelled", "refused",
                                            "expired"] as const);
export type SigninState = (typeof SIGNIN_STATES)[number];

/**
 * Why a sign-in was refused, each in plain words (review L6): the reason a refusal carries,
 * one of these keys, never a text the VM chose.
 */
export const SIGNIN_REFUSALS = Object.freeze({
  url: "it showed a link that is not the vendor's own sign-in page",
  code: "it showed a code that is not the vendor's kind of code",
  credential: "it showed something that looked like a credential",
  state: "it reported something a sign-in never reports",
} as const);
export type SigninRefusal = keyof typeof SIGNIN_REFUSALS;

/**
 * A URL parameter or fragment key named like a credential, anywhere in its name (so a
 * suffixed or prefixed one too: `refresh_token_x`, `x_api_key`).
 */
const CREDENTIAL_PARAM = /token|secret|passw|api_?key|credential|session/i;
/** A JSON Web Token: three base64url segments, the first a JSON object's (`eyJ`). */
const JWT = /eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/;

/**
 * Whether `value` looks like a credential: a JSON Web Token, or, as a URL, a parameter or
 * fragment key named like one. Checked in the VM (beside the engines' own secrets' atoms)
 * and again on the trusted side.
 */
export function credentialLike(value: string): boolean {
  if (JWT.test(value)) {
    return true;
  }
  let url: URL | null = null;
  try {
    url = /^https?:\/\//.test(value) ? new URL(value) : null;
  } catch {
    url = null;
  }
  return url !== null && [...url.searchParams.keys(),
                          ...new URLSearchParams(url.hash.replace(/^#/, "")).keys()]
    .some((key) => CREDENTIAL_PARAM.test(key));
}

/** A free parameter's value: RFC 3986 section 2.3's unreserved characters, so never a URL. */
const UNRESERVED = /^[A-Za-z0-9._~-]{1,128}$/;

/**
 * Why a sign-in's prompt is not the record's own shape (a `SigninRefusal`), or null. The URL:
 * visible ASCII at most `SIGNIN_URL_MAX`, matching the record's `url_pattern` whole,
 * `https` on one of the record's `url.hosts` that is among its login hosts (and the chosen
 * region's), no user, password, port or fragment, its path the record's `url.path` exactly, each of the record's `url.params`
 * present with exactly its value, the record's `url.free` parameters each at most once with
 * an unreserved value (a state, a PKCE challenge, a user code: never a URL, so no redirect
 * chain), and no other parameter (so no other OAuth client's page). The code: a device
 * code's characters, at most `SIGNIN_CODE_MAX`. Neither credential-shaped
 * (`credentialLike`). Checked in the VM before a prompt leaves it, and again by the owner's
 * object before the owner sees it.
 */
export function signinShape(rec: SourceRecord, region: string | null, url: string | null,
                            code: string | null): SigninRefusal | null {
  const login = rec.cli?.login;
  if (login === undefined) {
    return "state";
  }
  if (url !== null) {
    if (url.length > SIGNIN_URL_MAX || !/^[\x21-\x7e]+$/.test(url)) {
      return "url";
    }
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      return "url";
    }
    const whole = new RegExp(login.url_pattern).exec(url);
    if (whole === null || whole[1] !== url || parsed.protocol !== "https:"
        || parsed.username !== "" || parsed.password !== "" || parsed.port !== ""
        || parsed.hash !== "" || url.includes("#")
        || !signinHosts(rec, region).includes(parsed.hostname)
        || !login.url.hosts.includes(parsed.hostname) || parsed.pathname !== login.url.path) {
      return "url";
    }
    const seen = new Set<string>();
    for (const [key, value] of parsed.searchParams) {
      if (seen.has(key)) {
        return "url";
      }
      seen.add(key);
      const pinned = login.url.params[key];
      if (pinned !== undefined ? value !== pinned
          : !login.url.free.includes(key) || !UNRESERVED.test(value)) {
        return "url";
      }
    }
    if (Object.keys(login.url.params).some((key) => !seen.has(key))) {
      return "url";
    }
    if (credentialLike(url)) {
      return "credential";
    }
  }
  // A device code: letters, digits and hyphens (RFC 8628 section 6.1), from a record that
  // shows one at all; the runner took it from the record's `code_pattern` group.
  if (code !== null) {
    if (login.code_pattern === undefined || code.length > SIGNIN_CODE_MAX
        || !/^[A-Za-z0-9-]+$/.test(code)) {
      return "code";
    }
    if (credentialLike(code)) {
      return "credential";
    }
  }
  return null;
}

/**
 * Whether a source's vendor sign-in is switched on: the control record's switch for it
 * (`signins`), or else its record's verdict on the vendor's terms for hosted use (on only
 * when `allowed`).
 */
export function signinOn(rec: SourceRecord,
                         switches: Readonly<Record<string, boolean>> | null): boolean {
  const login = rec.cli?.login;
  return login !== undefined && rec.unwired === undefined
    && (switches?.[rec.id] ?? login.hosted_use.verdict === "allowed");
}

/** The served records whose sign-in is switched on, sorted. */
export function signinsOn(sources: Sources,
                          switches: Readonly<Record<string, boolean>> | null): string[] {
  return sources.ids().filter((id) => signinOn(sources.get(id)!, switches)).sort();
}

/**
 * The served records whose sign-in the control record, as read, switches on: its `signins`
 * when it reads well formed, each record's verdict when there is none, and none at all when it
 * is malformed or unreadable (fail-closed: a switch is never taken as on unless the record
 * says so).
 */
export function signinsRead(sources: Sources, read: ControlRead): string[] {
  return read.kind === "read" ? signinsOn(sources, read.control.signins)
    : read.kind === "missing" ? signinsOn(sources, null) : [];
}
