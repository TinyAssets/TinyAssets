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
 * - An engine record (`cli`) pins its install by a lock committed beside the records
 *   (`sources/locks/<id>/`), keeps every secret path outside `/workspace` and outside its
 *   synced session paths, allows unattended runs only with a citation, and has no
 *   single-inference `invoke` (ADR-033 decision 2). Every key's `engines` are CLI records
 *   that run on that provider's key.
 */
import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { builtinProviders } from "@earendil-works/pi-ai/providers/all";
import { describe, expect, it } from "vitest";

import { SUPPORTED_APIS } from "../../gateway/src/credential";
import { PLATFORM } from "../../owner/guards/sources";
import {
  bundledRecords, catalogOf, forbids, type Install, RUN_BASICS, runEnv, sealedSource,
  type SourceRecord,
  SourceRecordError, sourceRecord,
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
const anthropic = records.get("anthropic") as Record<string, unknown>;
const pi = records.get("pi") as Record<string, unknown>;
type Json = Record<string, unknown>;
const claudeCli = claude["cli"] as Json;

/** `claude` with its `cli` changed by `edit`, a deep copy. */
function cli(edit: (spec: Json) => void, record: Json = claude): Json {
  const copy = structuredClone(record);
  edit(copy["cli"] as Json);
  return copy;
}
const at = (spec: Json, path: string): Json =>
  path.split(".").reduce<Json>((node, key) => node[key] as Json, spec);

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

  it("park Sign in with ChatGPT, unwired, naming why", () => {
    expect(sourceRecord(chatgpt).unwired).toMatch(/^Parked: .*in-object loop.*OpenAI's approval/);
    expect(sourceRecord(chatgpt).engines).toEqual([]);
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

describe("the engine records", () => {
  const engines = [...records.values()].map(sourceRecord)
    .filter((record) => record.cli !== undefined);
  const keys = [...records.values()].map(sourceRecord)
    .filter((record) => record.connect.includes("api_key"));

  it("are the four vendor CLIs and pi's, none with an invoke", () => {
    expect(engines.map((record) => record.id).sort())
      .toEqual(["claude-code", "codex", "gemini", "kimi", "pi"]);
    for (const record of engines) {
      expect(record.cli, record.id).not.toHaveProperty("invoke");
      expect(record.cli, record.id).not.toHaveProperty("state_paths");
    }
  });

  it("pin each npm install by the lock committed beside the records", () => {
    const LOCKS = join(SOURCES, "locks");
    const npm = engines.filter((record) => record.cli!.install.manager === "npm");
    expect(readdirSync(LOCKS).sort()).toEqual(npm.map((record) => record.id).sort());
    for (const record of npm) {
      const install = record.cli!.install as Extract<Install, { manager: "npm" }>;
      const raw = readFileSync(join(LOCKS, record.id, "package-lock.json"));
      expect(createHash("sha256").update(raw).digest("hex"), record.id).toBe(install.lock_sha256);
      const manifest = JSON.parse(readFileSync(join(LOCKS, record.id, "package.json"), "utf8")) as Json;
      expect(manifest["dependencies"], record.id).toEqual({ [install.package]: install.version });
      const lock = JSON.parse(raw.toString("utf8")) as { packages: Record<string, Json> };
      const entry = lock.packages[`node_modules/${install.package}`]!;
      expect(entry["version"], record.id).toBe(install.version);
      expect(entry["integrity"], record.id).toBe(install.integrity);
      expect(Object.keys((entry["bin"] ?? {}) as Json), record.id).toContain(install.bin);
      expect(Object.values(lock.packages).every((item) => item["resolved"] === undefined
        || String(item["resolved"]).startsWith("https://registry.npmjs.org/")), record.id)
        .toBe(true);
      // The record runs install scripts exactly when its lock holds a package with one.
      const scripted = Object.values(lock.packages).some((item) =>
        item["hasInstallScript"] === true);
      expect(install.lifecycle_scripts === true, record.id).toBe(scripted);
    }
  });

  it("cite a vendor page for every unattended run they allow, and say why for the rest", () => {
    const verdicts = Object.fromEntries(engines.map((record) => [record.id,
      Object.fromEntries(Object.entries(record.cli!.unattended).map(([kind, entry]) =>
        [kind, entry!.allowed]))]));
    expect(verdicts).toEqual({
      "claude-code": { vendor_login_in_sandbox: true, api_key: true },
      codex: { vendor_login_in_sandbox: false, api_key: true },
      gemini: { vendor_login_in_sandbox: false, api_key: true },
      kimi: { vendor_login_in_sandbox: false },
      pi: { api_key: true },
    });
    expect(sourceRecord(claude).cli!.unattended.vendor_login_in_sandbox).toEqual({
      allowed: true, cite: "https://code.claude.com/docs/en/legal-and-compliance",
      read: "2026-11-01" });
    // A page read only through a search index says so.
    expect(sourceRecord(records.get("codex")).cli!.unattended.api_key)
      .toMatchObject({ allowed: true, read_note: expect.stringMatching(/search index/) });
    // Gemini's refusal says what Google's terms leave open, not that they forbid it.
    const gemini = sourceRecord(records.get("gemini")).cli!.unattended.vendor_login_in_sandbox!;
    expect(gemini.allowed).toBe(false);
    expect(gemini.allowed === false && gemini.reason).toMatch(/^Unclear: /);
    expect(gemini.allowed === false && gemini.reason).not.toMatch(/forbid/);
  });

  it("give pi an API key only, never a subscription's sign-in (ADR-034 decision 1.3)", () => {
    const spec = sourceRecord(pi).cli!;
    expect(spec.credential).toEqual(["api_key"]);
    expect(spec).not.toHaveProperty("login");
    expect(spec.hosts.login).toEqual([]);
    expect(() => sourceRecord(cli((s) => { s["credential"] = ["vendor_login_in_sandbox",
      "api_key"]; }, pi))).toThrow(SourceRecordError);
  });

  it("send Claude's token refresh to platform.claude.com, and never run it bare signed in", () => {
    const spec = sourceRecord(claude).cli!;
    expect(spec.hosts.provider).toContain("platform.claude.com");
    expect(spec.per_credential?.vendor_login_in_sandbox?.forbid_argv).toContain("--bare");
  });

  it("are each key's engines, each one running on that provider's key", () => {
    const catalog = catalogOf(bundledRecords());
    const runs = Object.fromEntries(keys.map((record) => [record.id, catalog.get(record.id)!.engines]));
    expect(runs).toMatchObject({ anthropic: ["claude-code", "pi"], openai: ["codex", "pi"],
                                 xai: ["pi"], google_ai_studio: ["gemini", "pi"] });
    for (const record of keys) {
      for (const engine of record.engines!) {
        expect(sourceRecord(records.get(engine)).cli!.api_key_env![record.upstream!.provider],
               `${record.id} → ${engine}`).toBeDefined();
      }
    }
    for (const engine of ["kimi", "nope", "groq"]) {
      expect(() => catalogOf([...bundledRecords().filter((record) =>
        (record as Json)["id"] !== "anthropic"), { ...anthropic, engines: [engine] }])
        .get("anthropic")).toThrow(/^source record "anthropic": engine /);
    }
  });

  it("name what an API-key run's environment sets, and nothing for a signed-in run", () => {
    const catalog = catalogOf(bundledRecords());
    expect(runEnv(catalog, "claude-code", "api_key").api_key_env["anthropic"])
      .toEqual({ base_url: "ANTHROPIC_BASE_URL", api_key: "ANTHROPIC_API_KEY", base: "sealed" });
    expect(runEnv(catalog, "codex", "api_key").api_key_env["openai"])
      .toEqual({ base_url: "OPENAI_BASE_URL", api_key: "CODEX_API_KEY", base: "sealed" });
    expect(runEnv(catalog, "gemini", "api_key").api_key_env["google"]).toEqual({
      base_url: "GOOGLE_GEMINI_BASE_URL", api_key: "GEMINI_API_KEY", base: "connection" });
    for (const engine of ["claude-code", "codex", "gemini", "kimi"]) {
      const signedIn = runEnv(catalog, engine, "vendor_login_in_sandbox");
      expect(signedIn.api_key_env, engine).toEqual({});
      expect(signedIn.forbid_env.length, engine).toBeGreaterThan(0);
    }
    expect(runEnv(catalog, "claude-code", "vendor_login_in_sandbox").forbid_env)
      .toEqual(expect.arrayContaining(["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
                                       "ANTHROPIC_AUTH_TOKEN"]));
    // Only a served CLI record, on a credential it runs on.
    for (const [engine, credential] of [["kimi", "api_key"], ["pi", "vendor_login_in_sandbox"],
                                        ["anthropic", "api_key"], ["nope", "api_key"]] as const) {
      expect(() => runEnv(catalog, engine, credential), engine).toThrow(SourceRecordError);
    }
    expect(() => runEnv(catalogOf([...bundledRecords().filter((record) =>
      (record as Json)["id"] !== "codex"), { ...(records.get("codex") as Json),
      unwired: "parked" }]), "codex", "vendor_login_in_sandbox")).toThrow(SourceRecordError);
  });

  it("build every run's environment from an allow-list, nothing credential-shaped in it",
     () => {
    const catalog = catalogOf(bundledRecords());
    expect(RUN_BASICS).toEqual(["PATH", "LANG", "LC_ALL", "TERM", "TZ", "TMPDIR", "USER",
                                "SHELL", "NODE_EXTRA_CA_CERTS"]);
    for (const record of engines) {
      for (const credential of record.cli!.credential) {
        const run = runEnv(catalog, record.id, credential);
        const what = `${record.id} ${credential}`;
        expect(run.env["HOME"], what).toBe("{home}");
        expect(run.inherit, what).toBe(RUN_BASICS);
        const own = new Set(["HOME", ...Object.keys(record.cli!.home_env),
          ...Object.keys(record.cli!.env ?? {}),
          ...Object.keys("env" in record.cli!.no_update ? record.cli!.no_update.env ?? {} : {}),
          ...Object.keys(record.cli!.per_credential?.[credential]?.env ?? {})]);
        expect(new Set(Object.keys(run.env)), what).toEqual(own);
        for (const name of [...Object.keys(run.env), ...run.inherit]) {
          expect(name, what).not.toMatch(/(?:^|_)(?:API_?KEY|KEY|TOKEN|SECRET|PASSWORD|AUTH|BASE_URL|ENDPOINT)(?:_|$)/);
          expect(forbids(run.forbid_env, name), `${what}: ${name}`).toBe(false);
        }
      }
    }
  });

  it("give a run its credential's own variables, and no other credential's", () => {
    const codex = structuredClone(records.get("codex")) as Json;
    at(codex["cli"] as Json, "per_credential.api_key")["env"] = { CODEX_EXTRA_FLAG: "1" };
    const catalog = catalogOf([...bundledRecords().filter((record) =>
      (record as Json)["id"] !== "codex"), codex]);
    expect(runEnv(catalog, "codex", "api_key").env["CODEX_EXTRA_FLAG"]).toBe("1");
    expect(runEnv(catalog, "codex", "vendor_login_in_sandbox").env)
      .not.toHaveProperty("CODEX_EXTRA_FLAG");
  });

  it("forbid a signed-in run every variable its CLI reads to move it off its sign-in", () => {
    const forbidden = (id: string) => sourceRecord(records.get(id)).cli!.per_credential!
      .vendor_login_in_sandbox!.forbid_env!;
    expect(forbidden("claude-code")).toEqual(expect.arrayContaining([
      "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
      "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
      "CLAUDE_CODE_USE_GATEWAY", "CLAUDE_CODE_GATEWAY_TOKEN", "CLAUDE_CODE_USE_MANTLE",
      "ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_AUTH_TOKEN", "ANTHROPIC_AWS_API_KEY",
      "ANTHROPIC_AWS_BASE_URL", "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL",
      "CLAUDE_CODE_OAUTH_TOKEN"]));
    expect(forbidden("codex")).toEqual(expect.arrayContaining([
      "CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_ACCESS_TOKEN"]));
    expect(forbidden("gemini")).toEqual(expect.arrayContaining([
      "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GEMINI_BASE_URL", "CODE_ASSIST_ENDPOINT",
      "GOOGLE_GENAI_USE_GCA", "GOOGLE_CLOUD_ACCESS_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS",
      "GOOGLE_VERTEX_BASE_URL", "GEMINI_CLI_AUTH_OVERRIDE", "GEMINI_DEFAULT_AUTH_TYPE"]));
    expect(forbidden("kimi")).toEqual(expect.arrayContaining([
      "KIMI_API_KEY", "KIMI_BASE_URL", "MOONSHOT_API_KEY", "KIMI_CODE_BASE_URL",
      "KIMI_MODEL_*", "KIMI_OAUTH_HOST", "KIMI_CODE_OAUTH_HOST", "OPENAI_*", "ANTHROPIC_*"]));
    // A pattern names every variable with its prefix, and nothing else.
    expect(forbids(forbidden("kimi"), "KIMI_MODEL_API_KEY")).toBe(true);
    expect(forbids(forbidden("kimi"), "OPENAI_BASE_URL")).toBe(true);
    expect(forbids(forbidden("kimi"), "KIMI_CODE_HOME")).toBe(false);
    expect(forbids(["OPENAI_*"], "XOPENAI_KEY")).toBe(false);
  });

  it("list every credential file each CLI writes among its secret paths (M1)", () => {
    const secrets = (id: string) => sourceRecord(records.get(id)).cli!.secret_paths;
    expect(secrets("claude-code")).toEqual(expect.arrayContaining([
      "{home}/.claude/.credentials.json", "{home}/.claude/.claude.json"]));
    expect(secrets("codex")).toEqual(expect.arrayContaining([
      "{home}/.codex/auth.json", "{home}/.codex/.credentials.json"]));
    expect(secrets("gemini")).toEqual(expect.arrayContaining([
      "{home}/.gemini/oauth_creds.json", "{home}/.gemini/mcp-oauth-tokens.json",
      "{home}/.gemini/a2a-oauth-tokens.json", "{home}/.gemini/gemini-credentials.json"]));
    expect(secrets("pi")).toEqual(expect.arrayContaining([
      "{home}/.pi/agent/auth.json", "{home}/.pi/agent/mcp-auth.json"]));
  });

  it("give pi its MCP door and its agent's own skills in JSON mode (H2)", () => {
    const spec = sourceRecord(pi).cli!;
    // Its tools declared to the model directly, so a `--tools` filter of plain names keeps
    // them (pi's default exposure reaches them through codemode only).
    expect(spec.mcp).toEqual(expect.objectContaining({ file: "{home}/.pi/agent/mcp.json",
      template: "{\"mcpServers\": {\"tinyassets\": {\"url\": \"{mcp_url}\", " +
                "\"exposure\": \"direct\"}}}" }));
    // JSON mode cannot show the trust prompt: the run approves the agent's own folder.
    expect(spec.unattended_argv).toContain("--approve");
    expect(spec.no_update).toEqual({ env: { PI_SKIP_VERSION_CHECK: "1" } });
  });

  it("let a Kimi member sign in from either region, its hosts by the choice (M2)", () => {
    const spec = sourceRecord(records.get("kimi")).cli!;
    expect(spec.login!["argv"]).toEqual(["{bin}", "login", "--region", "{region}"]);
    expect(Object.keys(spec.regions!).sort()).toEqual(["global", "mainland-cn"]);
    expect(spec.regions!["global"]!.hosts.login).toContain("auth.kimi.ai");
    expect(spec.regions!["mainland-cn"]!.hosts.login).toContain("auth.kimi.com");
    const pattern = new RegExp(String(spec.login!["url_pattern"]));
    for (const url of ["https://www.kimi.ai/device?x=1", "https://auth.kimi.com/device"]) {
      expect(pattern.exec(url)?.[1], url).toBe(url);
    }
    expect(spec.no_update).toEqual({ env: { KIMI_CODE_NO_AUTO_UPDATE: "1" } });
  });

  it("never pass a prompt as a bare argument (M3)", () => {
    for (const record of engines) {
      const { engine } = record.cli!;
      const carried = [...engine.argv, ...engine.resume_argv].filter((item) =>
        item.includes("{prompt}"));
      if (engine.stdin === "{prompt}") {
        expect(carried, record.id).toEqual([]);
      } else {
        expect(carried.every((item) => /^--[a-z-]+=\{prompt\}$/.test(item)), record.id)
          .toBe(true);
      }
    }
    expect(engines.filter((record) => record.cli!.engine.stdin === undefined)
      .map((record) => record.id)).toEqual(["kimi"]);
  });
});

describe("an engine record that does not fit", () => {
  it.each([
    ["an unknown key", cli((s) => { s["stray"] = 1; })],
    ["an unknown engine key", cli((s) => { at(s, "engine")["timeout_s"] = 60; })],
    ["an unknown event kind", cli((s) => { at(s, "engine.events")["thinking"] = []; })],
    ["an unknown verdict key", cli((s) => {
      at(s, "unattended.api_key")["expires"] = "2027-01-01"; })],
    ["the old state_paths", cli((s) => { s["state_paths"] = [".claude/.credentials.json"]; })],
    ["a secret path inside /workspace", cli((s) => {
      s["secret_paths"] = ["/workspace/agents/a/.credentials.json"]; })],
    ["/workspace itself as a secret path", cli((s) => { s["secret_paths"] = ["/workspace/"]; })],
    ["a secret path under a wildcard root", cli((s) => { s["secret_paths"] = ["/*/x.json"]; })],
    ["a secret path that climbs out", cli((s) => {
      s["secret_paths"] = ["{home}/../workspace/x.json"]; })],
    ["a relative secret path", cli((s) => { s["secret_paths"] = [".claude/.credentials.json"]; })],
    ["a secret path inside a session path", cli((s) => {
      s["secret_paths"] = ["{home}/.claude/projects/p/creds.json"]; })],
    ["a secret path a wildcard session path covers", cli((s) => {
      s["session_paths"] = ["{home}/*/"]; })],
    ["a session path inside a secret path", cli((s) => {
      s["session_paths"] = ["{home}/.claude/.credentials.json/x"]; })],
    ["no secret path", cli((s) => { s["secret_paths"] = []; })],
    ["no session path", cli((s) => { s["session_paths"] = []; })],
    ["allowed with no citation", cli((s) => {
      s["unattended"] = { ...(s["unattended"] as Json), vendor_login_in_sandbox: { allowed: true } };
    })],
    ["allowed with no read date", cli((s) => { delete at(s, "unattended.api_key")["read"]; })],
    ["allowed citing an http page", cli((s) => {
      at(s, "unattended.api_key")["cite"] = "http://code.claude.com/docs"; })],
    ["allowed with a date that is not one", cli((s) => {
      at(s, "unattended.api_key")["read"] = "2026-13-01"; })],
    ["allowed as a string", cli((s) => { at(s, "unattended.api_key")["allowed"] = "yes"; })],
    ["refused with no reason", cli((s) => {
      s["unattended"] = { ...(s["unattended"] as Json), api_key: { allowed: false } }; })],
    ["refused with an empty reason", cli((s) => {
      s["unattended"] = { ...(s["unattended"] as Json), api_key: { allowed: false,
        reason: "" } }; })],
    ["refused citing a page with no date", cli((s) => {
      s["unattended"] = { ...(s["unattended"] as Json), api_key: { allowed: false,
        reason: "unclear", cite: "https://example.com/terms" } }; })],
    ["refused with a date and no page", cli((s) => {
      s["unattended"] = { ...(s["unattended"] as Json), api_key: { allowed: false,
        reason: "unclear", read: "2026-11-01" } }; })],
    ["a verdict for a credential it does not run on", cli((s) => {
      s["credential"] = ["vendor_login_in_sandbox"]; delete s["api_key_env"]; })],
    ["no verdict for a credential it runs on", cli((s) => {
      delete at(s, "unattended")["api_key"]; })],
    ["an unknown credential", cli((s) => { s["credential"] = ["password"]; })],
    ["no credential", cli((s) => {
      s["credential"] = []; s["unattended"] = {}; delete s["login"]; delete s["api_key_env"];
      delete s["per_credential"]; })],
    ["a credential twice", cli((s) => { s["credential"] = ["api_key", "api_key"]; }, pi)],
    ["API-key variables with no API key", cli((s) => {
      s["api_key_env"] = { moonshot: { base_url: "KIMI_BASE_URL", api_key: "KIMI_API_KEY",
                                       base: "sealed" } }; }, records.get("kimi") as Json)],
    ["a sign-in with no vendor login", cli((s) => {
      s["credential"] = ["api_key"]; delete at(s, "unattended")["vendor_login_in_sandbox"];
      delete s["per_credential"]; })],
    ["an API key with no variables", cli((s) => { delete s["api_key_env"]; })],
    ["a key variable that is not a name", cli((s) => {
      at(s, "api_key_env.anthropic")["api_key"] = "anthropic-key"; })],
    ["no base-URL variable and no template carrying it", cli((s) => {
      at(s, "api_key_env.anthropic")["base_url"] = null; })],
    ["a per-credential part for another credential", cli((s) => {
      s["per_credential"] = { vendor_login_in_sandbox: {} }; }, pi)],
    ["a forbidden argument in the engine argv", cli((s) => {
      (at(s, "engine")["argv"] as string[]).push("--bare"); })],
    ["a forbidden argument in the unattended argv", cli((s) => {
      (s["unattended_argv"] as string[]).push("--bare"); })],
    ["an unknown placeholder", cli((s) => { (at(s, "engine")["argv"] as string[]).push("{system}"); })],
    ["a placeholder in an env value it is not", cli((s) => {
      at(s, "env")["X"] = "{config}"; })],
    ["a session id outside the resume argv", cli((s) => {
      (at(s, "engine")["argv"] as string[]).push("{sid}"); })],
    ["a resume by id with no {sid}", cli((s) => {
      at(s, "engine")["resume_argv"] = ["{bin}", "-p", "--continue"]; })],
    ["a resume in the cwd that names a session", cli((s) => {
      at(s, "engine")["resume"] = "continue_in_cwd"; })],
    ["a resume in the cwd still matching a session id", cli((s) => {
      at(s, "engine")["resume"] = "continue_in_cwd";
      at(s, "engine")["resume_argv"] = ["{bin}", "-p", "--continue"]; })],
    ["an unknown resume", cli((s) => { at(s, "engine")["resume"] = "magic"; })],
    ["an engine that runs something else", cli((s) => {
      (at(s, "engine")["argv"] as string[])[0] = "/bin/sh"; })],
    ["a prompt both on stdin and in the argv", cli((s) => {
      (at(s, "engine")["argv"] as string[]).push("{prompt}"); })],
    ["no prompt anywhere", cli((s) => { delete at(s, "engine")["stdin"]; })],
    ["no final text", cli((s) => { at(s, "engine.events")["final_text"] = []; })],
    ["an event matcher with no match", cli((s) => {
      at(s, "engine.events")["usage"] = [{ match: {}, path: "usage" }]; })],
    ["an event path that is not dotted", cli((s) => {
      at(s, "engine.events")["usage"] = [{ match: { type: "result" }, path: "a..b" }]; })],
    ["an event format that is not one", cli((s) => { at(s, "engine.events")["format"] = "xml"; })],
    ["an MCP config with no door URL", cli((s) => {
      at(s, "mcp")["template"] = "{\"mcpServers\": {\"t\": {\"url\": \"https://example.com/mcp\"}}}";
    })],
    ["an MCP config flag with no file", cli((s) => {
      delete at(s, "mcp")["file"]; delete at(s, "mcp")["template"];
      (at(s, "mcp")["argv"] as string[]).push("--url", "{mcp_url}"); })],
    ["an MCP file inside /workspace", cli((s) => {
      at(s, "mcp")["file"] = "/workspace/agents/a/.mcp.json"; })],
    ["the engine home itself as a file", cli((s) => { at(s, "mcp")["file"] = "{home}"; })],
    ["an MCP trust with no note", cli((s) => { delete at(s, "mcp.trust")["note"]; })],
    ["a tool filter with no {tools}", cli((s) => {
      at(s, "tools.allow")["argv"] = ["--tools"]; })],
    ["a tool filter on a file the record does not write", cli((s) => {
      at(s, "tools")["allow"] = { file: "{home}/other.json", key: "tools.core" }; })],
    ["a home outside {home}", cli((s) => { at(s, "home_env")["CLAUDE_CONFIG_DIR"] = "/root/.claude"; })],
    ["no home", cli((s) => { s["home_env"] = {}; })],
    ["a no-update with nothing in it", cli((s) => { s["no_update"] = {}; })],
    ["a no-update file the record does not write", cli((s) => {
      s["no_update"] = { file: "{home}/settings.json" }; })],
    ["skills outside the agent's directory", cli((s) => { s["skills_paths"] = ["/etc/skills"]; })],
    ["skills that climb out", cli((s) => { s["skills_paths"] = ["../skills"]; })],
    ["instructions shadowed by themselves", cli((s) => {
      at(s, "instructions")["shadowed_by"] = ["AGENTS.md"]; })],
    ["a login state check outside the secret paths", cli((s) => {
      at(s, "login")["state_checks"] = [{ path: "{home}/state.json", present: ["token"] }]; })],
    ["a login pattern that is not a regular expression", cli((s) => {
      at(s, "login")["url_pattern"] = "(unclosed"; })],
    ["an npm install unpinned", cli((s) => { at(s, "install")["integrity"] = "sha1-x"; })],
    ["an npm install with no lock", cli((s) => { delete at(s, "install")["lock_sha256"]; })],
    ["an unknown install manager", cli((s) => { at(s, "install")["manager"] = "brew"; })],
    ["a pip install with no lock", cli((s) => { s["install"] = { manager: "pip",
      package: "mistral-vibe", version: "1.2.3", bin: "vibe", verify_argv: ["{bin}"] }; })],
    ["a binary over http", cli((s) => { s["install"] = { manager: "binary",
      url: "http://example.com/grok", sha256: "a".repeat(64), version: "1.0.0", bin: "grok",
      verify_argv: ["{bin}", "--version"] }; })],
    ["a binary with no digest", cli((s) => { s["install"] = { manager: "binary",
      url: "https://example.com/grok", version: "1.0.0", bin: "grok",
      verify_argv: ["{bin}", "--version"] }; })],
    // H1: every secret and session path is under {home}, compared in one namespace.
    ["an absolute session path holding every top-level directory", cli((s) => {
      s["session_paths"] = ["/*/"]; })],
    ["an absolute session path holding the home", cli((s) => { s["session_paths"] = ["/root/"]; })],
    ["an absolute secret path", cli((s) => { s["secret_paths"] = ["/root/.claude/.credentials.json"]; })],
    ["a secret path through /proc", cli((s) => {
      s["secret_paths"] = ["/proc/self/cwd/.credentials.json"]; })],
    ["a session path through /proc", cli((s) => {
      s["session_paths"] = ["/proc/1/root/workspace/x/"]; })],
    ["a session path with * first under home", cli((s) => { s["session_paths"] = ["{home}/*/x/"]; })],
    ["a secret path with * first under home", cli((s) => { s["secret_paths"] = ["{home}/*"]; })],
    ["the home itself as a session path", cli((s) => { s["session_paths"] = ["{home}/"]; })],
    ["a session path that climbs out", cli((s) => {
      s["session_paths"] = ["{home}/a/../../root/"]; })],
    ["a written file outside the home", cli((s) => {
      at(s, "mcp")["file"] = "/etc/claude/mcp.json"; })],
    ["a written file with a wildcard", cli((s) => {
      s["files"] = { "{home}/run/*": "model = \"m\"" }; })],
    ["a home variable outside {home}", cli((s) => {
      at(s, "home_env")["CLAUDE_CONFIG_DIR"] = "/proc/self/cwd"; })],
    // M3: the prompt is never a bare argument.
    ["a bare positional prompt", cli((s) => {
      delete at(s, "engine")["stdin"];
      (at(s, "engine")["argv"] as string[]).push("{prompt}");
      (at(s, "engine")["resume_argv"] as string[]).push("{prompt}"); })],
    ["a two-token --prompt {prompt}", cli((s) => {
      delete at(s, "engine")["stdin"];
      (at(s, "engine")["argv"] as string[]).push("--prompt", "{prompt}");
      (at(s, "engine")["resume_argv"] as string[]).push("--prompt", "{prompt}"); })],
    ["a short option joined to the prompt", cli((s) => {
      delete at(s, "engine")["stdin"];
      (at(s, "engine")["argv"] as string[]).push("-p={prompt}");
      (at(s, "engine")["resume_argv"] as string[]).push("-p={prompt}"); })],
    ["a prompt inside a longer argument", cli((s) => {
      delete at(s, "engine")["stdin"];
      (at(s, "engine")["argv"] as string[]).push("--prompt=say {prompt}");
      (at(s, "engine")["resume_argv"] as string[]).push("--prompt=say {prompt}"); })],
    ["a prompt in an env value", cli((s) => { at(s, "env")["PROMPT"] = "{prompt}"; })],
    ["a prompt in the unattended argv", cli((s) => {
      (s["unattended_argv"] as string[]).push("--prompt={prompt}"); })],
    // M4: never configured to nothing.
    ["an empty argument", cli((s) => { (s["unattended_argv"] as string[]).push("--tools", ""); })],
    ["an argument of bare quotes", cli((s) => {
      (s["unattended_argv"] as string[]).push("--tools", "\"\""); })],
    ["an option set to nothing", cli((s) => { (s["unattended_argv"] as string[]).push("--tools="); })],
    ["an option set to empty quotes", cli((s) => {
      (s["unattended_argv"] as string[]).push("-c", "model=\"\""); })],
    ["an empty MCP map in an argument", cli((s) => {
      (at(s, "engine")["argv"] as string[]).push("-c", "mcp_servers={}"); })],
    ["an empty list in an argument", cli((s) => {
      (at(s, "engine")["argv"] as string[]).push("-c", "tools = [ ]"); })],
    ["an empty tool list in a template", cli((s) => {
      at(s, "mcp")["template"] = "{\"mcpServers\": {\"t\": {\"url\": \"{mcp_url}\"}}, " +
        "\"tools\": {\"core\": []}}"; })],
    ["an empty map in an env value", cli((s) => { at(s, "env")["SETTINGS"] = "{ }"; })],
    // M5: a signed-in run carries none of its key's variables.
    ["a sign-in with no forbidden variables", cli((s) => {
      delete at(s, "per_credential.vendor_login_in_sandbox")["forbid_env"]; })],
    ["a sign-in whose forbidden variables miss its key variable", cli((s) => {
      at(s, "per_credential.vendor_login_in_sandbox")["forbid_env"] = ["ANTHROPIC_BASE_URL"]; })],
    ["a sign-in whose forbidden variables miss its base-URL variable", cli((s) => {
      at(s, "per_credential.vendor_login_in_sandbox")["forbid_env"] = ["ANTHROPIC_API_KEY"]; })],
    ["a forbidden variable that is not a name", cli((s) => {
      (at(s, "per_credential.vendor_login_in_sandbox")["forbid_env"] as string[]).push("x y"); })],
    ["a record that sets a variable its sign-in forbids", cli((s) => {
      at(s, "env")["ANTHROPIC_BASE_URL"] = "https://example.com"; })],
    ["a sign-in part that sets a variable it forbids", cli((s) => {
      at(s, "per_credential.vendor_login_in_sandbox")["env"] = { ANTHROPIC_API_KEY: "x" }; })],
    ["a Kimi sign-in with no forbidden variables", cli((s) => { delete s["per_credential"]; },
                                                      records.get("kimi") as Json)],
    // M2: regions as data.
    ["regions on a record that does not sign in", cli((s) => {
      s["regions"] = { global: { display_name: "G", hosts: { login: [], provider: [] } } }; }, pi)],
    ["a region with an id outside the grammar", cli((s) => {
      s["regions"] = { "Global!": { display_name: "G", hosts: { login: [], provider: [] } } };
    }, records.get("kimi") as Json)],
    ["a region host that is not a name", cli((s) => {
      at(s, "regions.global.hosts")["login"] = ["https://auth.kimi.ai"]; },
    records.get("kimi") as Json)],
    ["a region with an unknown key", cli((s) => { at(s, "regions.global")["stray"] = 1; },
                                         records.get("kimi") as Json)],
    ["no regions", cli((s) => { s["regions"] = {}; }, records.get("kimi") as Json)],
    ["regions the sign-in does not use", cli((s) => {
      at(s, "login")["argv"] = ["{bin}", "login"]; }, records.get("kimi") as Json)],
    ["a region placeholder with no regions", cli((s) => { delete s["regions"]; },
                                                 records.get("kimi") as Json)],
    ["a region placeholder outside the sign-in", cli((s) => {
      (s["unattended_argv"] as string[]).push("--region={region}"); },
    records.get("kimi") as Json)],
    // Round 2: the run's environment is an allow-list, and no settings file carries a
    // credential, an environment block or a forbidden variable.
    ["a record variable shaped like a key", cli((s) => { at(s, "env")["MY_API_KEY"] = "x"; })],
    ["a record variable shaped like a token", cli((s) => { at(s, "env")["SOME_TOKEN"] = "x"; })],
    ["a record variable shaped like an endpoint", cli((s) => {
      at(s, "env")["UPSTREAM_BASE_URL"] = "https://example.com"; })],
    ["a record that sets HOME", cli((s) => { at(s, "env")["HOME"] = "{home}"; })],
    ["a record that sets PATH", cli((s) => { at(s, "env")["PATH"] = "/usr/bin"; })],
    ["a credential part that sets a token", cli((s) => {
      at(s, "per_credential.api_key")["env"] = { EXTRA_TOKEN: "x" }; }, records.get("codex") as Json)],
    ["a forbidden pattern's variable in the record", cli((s) => {
      s["env"] = { OPENAI_ORG: "x" }; }, records.get("kimi") as Json)],
    ["a forbid pattern that is not one", cli((s) => {
      (at(s, "per_credential.vendor_login_in_sandbox")["forbid_env"] as string[])
        .push("OPENAI*_X"); })],
    ["a settings file with an env block", cli((s) => {
      at(s, "per_credential.vendor_login_in_sandbox")["files"] = {
        "{home}/.claude/settings.json": "{\"env\": {\"FOO\": \"1\"}}" }; })],
    ["a settings file with a credential helper", cli((s) => {
      at(s, "per_credential.vendor_login_in_sandbox")["files"] = {
        "{home}/.claude/settings.json": "{\"apiKeyHelper\": \"echo k\"}" }; })],
    ["a settings file holding a token", cli((s) => {
      at(s, "per_credential.vendor_login_in_sandbox")["files"] = {
        "{home}/.claude/settings.json": "{\"oauth\": {\"token\": \"t\"}}" }; })],
    ["a TOML settings file with an env table", cli((s) => {
      at(s, "per_credential.vendor_login_in_sandbox")["files"] = {
        "{home}/.codex/config.toml": "[env]\nX = \"1\"" }; }, records.get("codex") as Json)],
    ["a signed-in settings file that moves the endpoint", cli((s) => {
      at(s, "per_credential.vendor_login_in_sandbox")["files"] = {
        "{home}/.gemini/settings.json": "{\"baseUrl\": \"https://elsewhere.example\"}" };
    }, records.get("gemini") as Json)],
    ["a shared settings file that moves a signed-in run's endpoint", cli((s) => {
      at(s, "mcp")["template"] = String(at(s, "mcp")["template"])
        .replace("\"telemetry\"", "\"endpoint\": \"https://x.example\", \"telemetry\"");
    }, records.get("gemini") as Json)],
    ["a settings file naming a forbidden variable", cli((s) => {
      at(s, "per_credential.vendor_login_in_sandbox")["files"] = {
        "{home}/.claude/notes.json": "{\"note\": \"ANTHROPIC_BASE_URL\"}" }; })],
    ["a settings file naming a forbidden pattern's variable", cli((s) => {
      at(s, "mcp")["template"] = String(at(s, "mcp")["template"])
        .replace("\"transport\"", "\"note\": \"KIMI_MODEL_NAME\", \"transport\""); },
    records.get("kimi") as Json)],
    // The survivors of this round's own mutation run.
    ["a wildcard session path that covers a secret below the first segment", cli((s) => {
      s["session_paths"] = ["{home}/.claude/*/"]; })],
    ["a prompt with something after it", cli((s) => {
      delete at(s, "engine")["stdin"];
      (at(s, "engine")["argv"] as string[]).push("--prompt={prompt}.");
      (at(s, "engine")["resume_argv"] as string[]).push("--prompt={prompt}."); })],
    ["an empty list inside an argument's JSON", cli((s) => {
      (s["unattended_argv"] as string[]).push("--settings",
                                              "{\"permissions\": {\"allow\": []}}"); })],
    ["an MCP template with no file, its door URL in the argv", cli((s) => {
      delete at(s, "mcp")["file"]; at(s, "mcp")["argv"] = ["--mcp-url={mcp_url}"]; }, pi)],
    // The three mutants the reviewer's run left alive.
    ["an MCP template with no file", cli((s) => { delete at(s, "mcp")["file"]; }, pi)],
    ["an npm lock digest that is not one", cli((s) => { at(s, "install")["lock_sha256"] = "xyz"; })],
    ["a login state check on a secret path's parent", cli((s) => {
      (at(s, "login")["state_checks"] as Json[])[0]!["path"] = "{home}/.codex"; },
    records.get("codex") as Json)],
    // Citations.
    ["a read note on a refusal that cites nothing", cli((s) => {
      at(s, "unattended.vendor_login_in_sandbox")["read_note"] = "from memory"; },
    records.get("kimi") as Json)],
    ["a read note that is not text", cli((s) => { at(s, "unattended.api_key")["read_note"] = 1; })],
    ["a gateway source with no engines", Object.fromEntries(Object.entries(anthropic)
      .filter(([key]) => key !== "engines"))],
    ["a gateway source naming an engine twice", { ...anthropic, engines: ["pi", "pi"] }],
  ])("fails loudly: %s", (_what, record) => {
    expect(refused(record)).toMatch(/^source record "[^"]*": /);
  });

  it.each([
    ["an invoke backend", cli((s) => { s["invoke"] = { argv: ["{bin}", "-p"] }; })],
    ["an invoke inside the engine", cli((s) => { at(s, "engine")["invoke"] = {}; })],
  ])("fails loudly, never a backend: %s", (_what, record) => {
    expect(refused(record)).toMatch(/^source record "claude-code": cli(?:\.engine)? has no invoke: a CLI is an engine, never a backend/);
  });

  it("takes the other pinned installs and an MCP-less engine", () => {
    for (const install of [
      { manager: "pip", package: "mistral-vibe", version: "1.2.3", bin: "vibe",
        verify_argv: ["{bin}", "--version"], lock_sha256: "b".repeat(64) },
      { manager: "binary", url: "https://example.com/grok", sha256: "a".repeat(64),
        version: "1.0.0", bin: "grok", verify_argv: ["{bin}", "--version"] },
    ]) {
      expect(sourceRecord(cli((s) => { s["install"] = install; })).cli!.install).toEqual(install);
    }
    const unsupported = { unsupported: "No MCP client: TinyAssets is reached another way." };
    expect(sourceRecord(cli((s) => { s["mcp"] = unsupported; }, pi)).cli!.mcp)
      .toEqual(unsupported);
    expect(claudeCli["mcp"]).toHaveProperty("file");
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

  it("refuses a key whose engine is unwired", () => {
    expect(() => catalogOf([...bundledRecords().filter((record) =>
      (record as Json)["id"] !== "claude-code"), { ...claude, unwired: "parked" }])
      .get("anthropic")).toThrow(/^source record "anthropic": engine "claude-code" is unwired/);
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
