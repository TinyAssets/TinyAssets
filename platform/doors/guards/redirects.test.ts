/**
 * The OAuth sources' redirect registrations as data (tasks 2; slice L). Each RFC 6749
 * record registers exactly its own callback, matched exactly by its provider, never a prefix
 * (RFC 9700 §4.1); a key exchange registers none (its provider takes any callback) and its
 * state rides the callback's last segment; `registrations.ts` holds both to the URI the
 * doors derive. The data lives beside the records (`redirect-registrations.json`), never in
 * them: the records are digested into the sandbox template's tag. The key exchange's
 * residual mix-up is the source checker's rule (`contract/guards/sources.test.ts`).
 */
import { describe, expect, it } from "vitest";

import data from "../redirect-registrations.json" with { type: "json" };
import { allRegistrations, redirectOf, registrationOf } from "../src/redirects";
import { bundledRecords } from "../../sources/src/catalog";

type Json = Record<string, unknown>;

const records = bundledRecords() as Json[];
const oauthRecords = records.filter((record) => record["oauth"] !== undefined);
const exchange = oauthRecords.find((record) =>
  (record["oauth"] as Json)["protocol"] === "pkce_user_key_v1")!;
const rfc6749 = oauthRecords.find((record) =>
  (record["oauth"] as Json)["protocol"] === "rfc6749")!;
const rid = String(rfc6749["id"]);
const xid = String(exchange["id"]);

describe("a redirect registration", () => {
  it("is each OAuth record's own callback, as the doors derive it, and names no other", () => {
    expect(oauthRecords.length).toBeGreaterThanOrEqual(2);
    const all = allRegistrations(records);
    expect(Object.keys(all).sort()).toEqual(oauthRecords.map((r) => String(r["id"])).sort());
    for (const record of oauthRecords) {
      const id = String(record["id"]);
      expect(redirectOf(id)).toBe(`https://tinyassets.io/oauth/connect/callback/${id}`);
      expect(all[id]).toEqual(record === exchange
        ? { match: "any_callback", uri: `${redirectOf(id)}/{state}` }
        : { match: "exact", uri: redirectOf(id) });
    }
  });

  it.each([
    ["none at all", rfc6749, { ...data, [rid]: undefined }],
    ["a prefix", rfc6749, { ...data, [rid]: { match: "prefix", uri: redirectOf(rid) } }],
    ["another source's callback", rfc6749, { ...data, [rid]: { match: "exact",
                                                                uri: redirectOf("other") } }],
    ["a trailing slash", rfc6749, { ...data, [rid]: { match: "exact",
                                                       uri: `${redirectOf(rid)}/` } }],
    ["a key exchange matched exactly", exchange, { ...data, [xid]: { match: "exact",
                                                                      uri: redirectOf(xid) } }],
    ["a key exchange without its state's segment", exchange, {
      ...data, [xid]: { match: "any_callback", uri: redirectOf(xid) } }],
  ])("is refused as %s", (_what, record, changed) => {
    expect(() => registrationOf(record, changed as Json)).toThrow(/redirect registration/);
  });

  it("is refused for a source that serves no OAuth, or names no served source", () => {
    const keyOnly = records.find((record) => record["oauth"] === undefined
                                             && record["cli"] === undefined)!;
    expect(() => registrationOf(keyOnly, { [String(keyOnly["id"])]: { match: "exact",
                                                                       uri: "x" } }))
      .toThrow(/no OAuth/);
    expect(() => allRegistrations(records, { ...data, ghost: { match: "exact", uri: "x" } }))
      .toThrow(/no served source/);
  });
});
