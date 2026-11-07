/**
 * The OAuth sources' redirect registrations, as data (tasks 2; slice L).
 *
 * `redirect-registrations.json` names, for each OAuth source record, the redirect URI its
 * provider holds: an RFC 6749 source's own callback, registered exactly and matched exactly
 * (RFC 9700 §4.1), never a prefix; a key exchange's, which its provider takes as any
 * callback the flow names (OpenRouter's), its state riding the last segment. Each is held
 * here to the URI the doors derive for the source (`connect.ts`'s per-source callback), so
 * the data can never name another, and the deploy checklist lists them for the founder.
 * The data sits beside the records, not in them: the records are bundled into the
 * sandbox's runner, whose template tag digests them, and a registration is the doors'
 * concern alone.
 */
import registrations from "../redirect-registrations.json" with { type: "json" };

import { CONNECT_CALLBACK } from "./config";
import { sourceRecord } from "../../sources/src/check";
import { SourceRecordError } from "../../sources/src/schema";

export type RedirectRegistration =
  | { readonly match: "exact"; readonly uri: string }
  | { readonly match: "any_callback"; readonly uri: string };

/** The redirect URI the doors derive for a source: one per source. */
export function redirectOf(id: string): string {
  return `${CONNECT_CALLBACK}/${id}`;
}

/** A record's registration, checked against the URI the doors derive, or a throw. */
export function registrationOf(record: unknown,
                               data: Readonly<Record<string, unknown>> = registrations):
    RedirectRegistration | null {
  const checked = sourceRecord(record);
  if (checked.oauth === undefined) {
    if (Object.hasOwn(data, checked.id)) {
      throw new SourceRecordError(`${checked.id}: a registration for a source with no OAuth`);
    }
    return null;
  }
  const value = data[checked.id] as Record<string, unknown> | undefined;
  const own = redirectOf(checked.id);
  const keyExchange = checked.oauth.protocol === "pkce_user_key_v1";
  const expected = keyExchange ? { match: "any_callback", uri: `${own}/{state}` }
    : { match: "exact", uri: own };
  if (typeof value !== "object" || value === null
      || Object.keys(value).sort().join() !== "match,uri"
      || value["match"] !== expected.match || value["uri"] !== expected.uri) {
    throw new SourceRecordError(`${checked.id}: its redirect registration is ${
      keyExchange ? "any_callback at its own callback with its state's segment"
        : "its own callback, matched exactly"}`);
  }
  return Object.freeze(expected) as RedirectRegistration;
}

/** Every OAuth record's registration, each checked; a registration naming no record throws. */
export function allRegistrations(records: readonly unknown[],
                               data: Readonly<Record<string, unknown>> = registrations):
    Readonly<Record<string, RedirectRegistration>> {
  const found: Record<string, RedirectRegistration> = {};
  for (const record of records) {
    const registration = registrationOf(record, data);
    if (registration !== null) {
      found[sourceRecord(record).id] = registration;
    }
  }
  const stray = Object.keys(data).filter((id) => !Object.hasOwn(found, id));
  if (stray.length > 0) {
    throw new SourceRecordError(`registrations for no served source: ${stray.join(", ")}`);
  }
  return Object.freeze(found);
}
