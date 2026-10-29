/**
 * The model source records (`platform/sources/`) are data that fits (tasks 4 G3).
 *
 * - Every file is bundled by the doors, and every bundled record passes the schema
 *   (`sourceRecord`), whose gateway part is the gateway's own check: so a record naming an
 *   API outside the gateway's `SUPPORTED_APIS` fails here, from that one list.
 * - The nine Python records (`tinyassets/providers/source_records/`) are all ported.
 * - pi-ai's catalog supplies a known provider: a record of one restates its base URL and
 *   speaks an API its catalog has models for, so a record overrides only as `custom`.
 * - A record that breaks the schema fails loudly, naming itself.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { builtinProviders } from "@earendil-works/pi-ai/providers/all";
import { describe, expect, it } from "vitest";

import { SUPPORTED_APIS } from "../../gateway/src/credential";
import { PLATFORM } from "../../owner/guards/sources";
import {
  bundledRecords, catalogOf, sealedSource, type SourceRecord, SourceRecordError, sourceRecord,
} from "../src/sources";

const SOURCES = join(PLATFORM, "sources");
const PYTHON = join(PLATFORM, "..", "tinyassets", "providers", "source_records");

function files(dir: string): Map<string, unknown> {
  return new Map(readdirSync(dir).filter((name) => name.endsWith(".json")).sort()
    .map((name) => [name.slice(0, -".json".length),
                    JSON.parse(readFileSync(join(dir, name), "utf8")) as unknown]));
}

const records = files(SOURCES);
const groq = records.get("groq") as Record<string, unknown>;
const openrouter = records.get("openrouter_user_models_v1") as Record<string, unknown>;
const chatgpt = records.get("chatgpt") as Record<string, unknown>;
const claude = records.get("claude-code") as Record<string, unknown>;

function refused(record: unknown): string {
  try {
    sourceRecord(record);
  } catch (error) {
    expect(error).toBeInstanceOf(SourceRecordError);
    return (error as Error).message;
  }
  throw new Error("the record was accepted");
}

describe("the source records", () => {
  it("are every file, each named by its id, and each fits the schema", () => {
    expect([...records.keys()].sort())
      .toEqual(bundledRecords().map((record) => (record as { id: string }).id).sort());
    for (const [name, record] of records) {
      expect(sourceRecord(record).id).toBe(name);
    }
    expect(catalogOf(bundledRecords()).ids()).toHaveLength(records.size);
  });

  it("port every one of the Python's", () => {
    for (const id of files(PYTHON).keys()) {
      expect(records.has(id), id).toBe(true);
    }
  });

  it("restate pi-ai's catalog for a known provider, and speak an API it has models for", () => {
    const providers = builtinProviders();
    const gateway = [...records.values()].map(sourceRecord)
      .filter((record): record is SourceRecord & Required<Pick<SourceRecord, "upstream">> =>
        record.upstream !== undefined);
    expect(gateway.length).toBeGreaterThanOrEqual(6);
    for (const { id, upstream } of gateway) {
      expect(upstream.provider, id).not.toBe("custom");
      const provider = providers.find((entry) => entry.id === upstream.provider);
      expect(provider?.baseUrl, id).toBe(upstream.base_url);
      expect(provider!.getModels().filter((model) => model.api === upstream.api).length, id)
        .toBeGreaterThan(0);
      expect(SUPPORTED_APIS, id).toContain(upstream.api);
    }
  });

  it("hand the gateway what it seals for each way they connect", () => {
    const record = sourceRecord(openrouter);
    expect(sealedSource(record, "api_key")).toEqual({ upstream: record.upstream,
                                                      endpoints: record.endpoints });
    expect(sealedSource(record, "oauth")).toEqual({
      upstream: record.upstream, endpoints: record.endpoints,
      key_exchange: { endpoint: "https://openrouter.ai/api/v1/auth/keys" } });
    expect(sealedSource(sourceRecord(chatgpt), "oauth").oauth).toMatchObject({
      client_auth: { method: "none" }, refresh_with: "provider",
      token_endpoint: "https://auth.openai.com/api/accounts/oauth/token" });
    expect(() => sealedSource(sourceRecord(groq), "oauth")).toThrow(SourceRecordError);
    expect(() => sealedSource(sourceRecord(chatgpt), "api_key")).toThrow(SourceRecordError);
    expect(() => sealedSource(sourceRecord(claude), "api_key")).toThrow(SourceRecordError);
  });

  it("bound an authorize param at 128 code points, as the Python did", () => {
    const at = (value: string) => sourceRecord({ ...openrouter, oauth: {
      ...(openrouter["oauth"] as object), authorize_params: { key_label: value } } });
    expect(at("\u{1F511}".repeat(128)).oauth!.authorize_params["key_label"]).toHaveLength(256);
    expect(() => at("\u{1F511}".repeat(129))).toThrow(SourceRecordError);
  });

  it("take an issuer, bare or with a path, and its RFC 9207 flag", () => {
    for (const issuer of ["https://auth.openai.com", "https://auth.openai.com/tenant"]) {
      const record = sourceRecord({ ...chatgpt, oauth: {
        ...(chatgpt["oauth"] as object), issuer,
        authorization_response_iss_parameter_supported: true } });
      expect(record.oauth).toMatchObject({ issuer,
                                           authorization_response_iss_parameter_supported: true });
    }
    expect(sourceRecord(chatgpt).oauth).not.toHaveProperty("issuer");
  });

  it("keep Sign in with ChatGPT unwired, naming why", () => {
    expect(sourceRecord(chatgpt).unwired).toMatch(/OpenAI's approval/);
    const served = [...records.values()].map(sourceRecord)
      .filter((record) => record.unwired !== undefined).map((record) => record.id);
    expect(served).toEqual(["chatgpt"]);
  });

  it("are copies, so a reader cannot change the bundled data", () => {
    const first = catalogOf(bundledRecords()).get("groq")!;
    expect(Object.isFrozen(first)).toBe(true);
    expect(() => { (first.upstream as { base_url: string }).base_url = "https://evil.test"; })
      .toThrow();
    expect(catalogOf(bundledRecords()).get("groq")!.upstream!.base_url)
      .toBe("https://api.groq.com/openai/v1");
    expect(catalogOf(bundledRecords()).get("Groq")).toBeUndefined();
    expect(catalogOf(bundledRecords()).get("nope")).toBeUndefined();
  });
});

describe("a record that does not fit", () => {
  const endpoints = groq["endpoints"];
  it.each([
    ["an unsupported API", { ...groq, api: "bedrock-converse-stream" }],
    ["the legacy Codex API", { ...groq, api: "openai-codex-responses" }],
    ["an unknown API", { ...groq, api: "nonsense" }],
    ["an http base URL", { ...groq, base_url: "http://api.groq.com/openai/v1" }],
    ["a base URL outside its endpoints", { ...groq, base_url: "https://evil.test/v1" }],
    ["a setup check outside its endpoints", { ...groq, verify_url: "https://api.groq.com/x" }],
    ["no setup check", Object.fromEntries(Object.entries(groq)
      .filter(([key]) => key !== "verify_url"))],
    ["no endpoints", { ...groq, endpoints: [] }],
    ["an extra field", { ...groq, models_url: "https://api.groq.com/openai/v1/models" }],
    ["a missing field", Object.fromEntries(Object.entries(groq).filter(([key]) => key !== "auth"))],
    ["an id outside the grammar", { ...groq, id: "Groq!" }],
    ["no way to connect", { ...groq, connect: [] }],
    ["a method twice", { ...groq, connect: ["api_key", "api_key"] }],
    ["an unknown method", { ...groq, connect: ["password"] }],
    ["oauth with no flow", { ...groq, connect: ["oauth"] }],
    ["a CLI with an upstream", { ...claude, connect: ["cli", "api_key"] }],
    ["a CLI with gateway fields", { ...claude, endpoints }],
    ["a CLI unpinned", { ...claude, cli: { ...(claude["cli"] as object), install: {
      ...((claude["cli"] as Record<string, object>)["install"]), integrity: "sha1-x" } } }],
    ["a CLI host that is not a name", { ...claude, cli: { ...(claude["cli"] as object), hosts: {
      registry: ["registry.npmjs.org"], login: ["http://claude.ai"], provider: [] } } }],
    ["an unwired reason that is not text", { ...groq, unwired: 1 }],
    ["a suggestion that is not true", { ...groq, suggested: "yes" }],
    ["a help URL over http", { ...groq, help_url: "http://console.groq.com/keys" }],
    ["authorize params that set the state", { ...openrouter, oauth: {
      ...(openrouter["oauth"] as object), authorize_params: { state: "fixed" } } }],
    ["authorize params that set the callback", { ...openrouter, oauth: {
      ...(openrouter["oauth"] as object), authorize_params: { callback_url: "https://evil.test" } } }],
    ["an authorize param past 128 characters", { ...openrouter, oauth: {
      ...(openrouter["oauth"] as object), authorize_params: { key_label: "x".repeat(129) } } }],
    ["an empty authorize param", { ...openrouter, oauth: {
      ...(openrouter["oauth"] as object), authorize_params: { key_label: "" } } }],
    ["an http issuer", { ...chatgpt, oauth: {
      ...(chatgpt["oauth"] as object), issuer: "http://auth.openai.com" } }],
    ["an issuer with a query", { ...chatgpt, oauth: {
      ...(chatgpt["oauth"] as object), issuer: "https://auth.openai.com?x=1" } }],
    ["the RFC 9207 flag with no issuer", { ...chatgpt, oauth: {
      ...(chatgpt["oauth"] as object), authorization_response_iss_parameter_supported: true } }],
    ["the RFC 9207 flag false", { ...chatgpt, oauth: {
      ...(chatgpt["oauth"] as object), issuer: "https://auth.openai.com",
      authorization_response_iss_parameter_supported: false } }],
    ["an http exchange endpoint", { ...openrouter, oauth: {
      ...(openrouter["oauth"] as object), exchange_endpoint: "http://openrouter.ai/keys" } }],
    ["an unknown protocol", { ...openrouter, oauth: {
      ...(openrouter["oauth"] as object), protocol: "implicit" } }],
    ["a client secret in the data", { ...chatgpt, oauth: {
      ...(chatgpt["oauth"] as object), client_auth: { method: "client_secret_post", secret: "s" } } }],
    ["the provider's refresh for another token endpoint", { ...chatgpt, oauth: {
      ...(chatgpt["oauth"] as object), token_endpoint: "https://auth.test/token" } }],
    ["a refresh that is neither", { ...chatgpt, oauth: {
      ...(chatgpt["oauth"] as object), refresh_with: "magic" } }],
    ["a scope with a newline", { ...chatgpt, oauth: {
      ...(chatgpt["oauth"] as object), scope: "openid\nprofile" } }],
  ])("fails loudly: %s", (_what, record) => {
    expect(refused(record)).toMatch(/^source record "[^"]*": /);
  });

  it("fails loudly as a second record under one id", () => {
    expect(() => catalogOf([groq, openrouter, { ...groq, display_name: "Groq again" }]))
      .toThrow(/^source record "groq": its id is another record's$/);
    expect(() => catalogOf([null, null])).toThrow(SourceRecordError);
    expect(catalogOf([groq, openrouter]).ids()).toEqual(["groq", "openrouter_user_models_v1"]);
  });

  it("fails loudly when served, before any object is reached", () => {
    const broken = catalogOf([{ ...groq, api: "bedrock-converse-stream" }]);
    expect(() => broken.get("groq")).toThrow(SourceRecordError);
    expect(refused(null)).toMatch(/^source record "\?": /);
  });
});
