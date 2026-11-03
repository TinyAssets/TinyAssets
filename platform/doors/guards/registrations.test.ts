/**
 * The OAuth sources' redirect registrations as data, and the key exchange's residual
 * mix-up (tasks 2; slice L). Each RFC 6749 record registers exactly its own callback,
 * matched exactly by its provider, never a prefix (RFC 9700 §4.1); a key exchange registers
 * none (its provider takes any callback) and its state rides the callback's last segment;
 * the checker holds both to the URI the doors derive. And no catalog serves a source on an
 * authorization server the platform does not curate beside a key exchange.
 */
import { describe, expect, it } from "vitest";

import { bundledRecords, catalogOf, redirectOf, sourceRecord } from "../src/sources";

type Json = Record<string, unknown>;

const records = bundledRecords() as Json[];
const oauthRecords = records.filter((record) => (record["oauth"] as Json | undefined) !== undefined);
const exchange = oauthRecords.find((record) =>
  (record["oauth"] as Json)["protocol"] === "pkce_user_key_v1")!;
const rfc6749 = oauthRecords.find((record) =>
  (record["oauth"] as Json)["protocol"] === "rfc6749")!;

function withOauth(record: Json, change: Json): Json {
  return { ...record, oauth: { ...(record["oauth"] as Json), ...change } };
}

describe("a redirect registration", () => {
  it("is each OAuth record's own callback, as the doors derive it", () => {
    expect(oauthRecords.length).toBeGreaterThanOrEqual(2);
    for (const record of oauthRecords) {
      const checked = sourceRecord(record);
      const registered = checked.oauth!.redirect_registration;
      const own = redirectOf(checked.id);
      expect(own).toBe(`https://tinyassets.io/oauth/connect/callback/${checked.id}`);
      expect(registered).toEqual(checked.oauth!.protocol === "rfc6749"
        ? { match: "exact", uri: own } : { match: "any_callback", uri: `${own}/{state}` });
    }
  });

  it.each([
    ["none at all", rfc6749, { redirect_registration: undefined }],
    ["a prefix", rfc6749, { redirect_registration: { match: "prefix",
                                                      uri: redirectOf(String(rfc6749["id"])) } }],
    ["another source's callback", rfc6749, { redirect_registration: {
      match: "exact", uri: redirectOf("other") } }],
    ["a trailing slash", rfc6749, { redirect_registration: {
      match: "exact", uri: `${redirectOf(String(rfc6749["id"]))}/` } }],
    ["a key exchange matched exactly", exchange, { redirect_registration: {
      match: "exact", uri: redirectOf(String(exchange["id"])) } }],
    ["a key exchange without its state's segment", exchange, { redirect_registration: {
      match: "any_callback", uri: redirectOf(String(exchange["id"])) } }],
  ])("is refused as %s", (_what, record, change) => {
    expect(() => sourceRecord(withOauth(record, change))).toThrow(/redirect_registration/);
  });
});

describe("the key exchange's residual mix-up", () => {
  const uncurated = { ...withOauth(rfc6749, { redirect_registration: {
    match: "exact", uri: redirectOf("mcp_server_x") } }), id: "mcp_server_x", curated: false };

  it("refuses a catalog serving an uncurated authorization server beside a key exchange", () => {
    expect(() => catalogOf([...records, uncurated])).toThrow(/does not curate/);
  });

  it("allows one where no key exchange is served", () => {
    const without = records.filter((record) => record !== exchange);
    expect(catalogOf([...without, uncurated]).get("mcp_server_x")?.curated).toBe(false);
  });

  it("holds curated to false or absent", () => {
    expect(() => sourceRecord({ ...rfc6749, curated: true })).toThrow(/curated/);
  });
});
