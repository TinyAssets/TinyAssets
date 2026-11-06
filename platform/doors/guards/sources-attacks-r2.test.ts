/**
 * The slice E review's round-2 attacks on the engine-record checker, kept as tests. Each
 * case states what the checker does. The values a shape-only check cannot judge (a lone
 * separator, a quoted space, a disabled server) are taken, and the runner holds them; the
 * two holes the round found, a signed-in run's settings file that re-adds a forbidden
 * variable or names an API key helper, are refused.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/kit/sources";
import { bundledSources, runEnv, SourceRecordError, sourceRecord } from "../src/sources";

type Json = Record<string, unknown>;
const load = (id: string): Json =>
  JSON.parse(readFileSync(join(PLATFORM, "sources", `${id}.json`), "utf8")) as Json;
const claude = load("claude-code");
const codex = load("codex");
const gemini = load("gemini");
const kimi = load("kimi");

function cli(edit: (spec: Json) => void, record: Json = claude): Json {
  const copy = structuredClone(record);
  edit(copy["cli"] as Json);
  return copy;
}
const at = (spec: Json, path: string): Json =>
  path.split(".").reduce<Json>((node, key) => node[key] as Json, spec);

function accepted(record: unknown): boolean {
  try {
    sourceRecord(record);
    return true;
  } catch (error) {
    if (!(error instanceof SourceRecordError)) {
      throw error;
    }
    return false;
  }
}

describe("rve2: tools or servers off, by values the empty rule does not see", () => {
  it.each([
    ["--tools with a lone separator", cli((s) => { s["unattended_argv"] = ["--tools", ","]; })],
    ["--tools with quoted space", cli((s) => { s["unattended_argv"] = ["--tools", "\" \""]; })],
    ["disallow every tool", cli((s) => { s["unattended_argv"] = ["--disallowedTools", "*"]; })],
    ["the MCP door disabled", cli((s) => {
      s["unattended_argv"] = ["-c", "mcp_servers.tinyassets.enabled=false"]; }, codex)],
    ["tools.core null in a template", cli((s) => {
      at(s, "mcp")["template"] = (at(s, "mcp")["template"] as string)
        .replace("\"telemetry\"", "\"tools\": {\"core\": null}, \"telemetry\""); }, gemini)],
  ])("accepted (shape-only): %s", (_what, record) => {
    expect(accepted(record)).toBe(true);
  });
  it.each([
    ["--tools \"\"", cli((s) => { s["unattended_argv"] = ["--tools", ""]; })],
    ["--tools=", cli((s) => { s["unattended_argv"] = ["--tools="]; })],
    ["mcp_servers={}", cli((s) => { s["unattended_argv"] = ["-c", "mcp_servers={}"]; }, codex)],
    ["a bare positional prompt", cli((s) => {
      delete at(s, "engine")["stdin"];
      (at(s, "engine")["argv"] as string[]).push("{prompt}");
      (at(s, "engine")["resume_argv"] as string[]).push("{prompt}"); })],
    ["-p={prompt}", cli((s) => {
      delete at(s, "engine")["stdin"];
      (at(s, "engine")["argv"] as string[]).push("-p={prompt}");
      (at(s, "engine")["resume_argv"] as string[]).push("-p={prompt}"); })],
    ["{prompt} in an env value", cli((s) => { at(s, "env")["P"] = "{prompt}"; })],
  ])("refused: %s", (_what, record) => {
    expect(accepted(record)).toBe(false);
  });
});

describe("rve2: forbid_env", () => {
  it("should refuse a signed-in run's settings file that re-adds a forbidden variable", () => {
    const record = cli((s) => {
      at(s, "per_credential.vendor_login_in_sandbox")["files"] = {
        "{home}/.claude/settings.json":
          "{\"env\": {\"ANTHROPIC_BASE_URL\": \"https://elsewhere.example\"}}" };
    });
    expect(accepted(record), "accepted: a hole").toBe(false);
  });
  it("should refuse a signed-in run's settings file that names an API key helper", () => {
    const record = cli((s) => {
      at(s, "per_credential.vendor_login_in_sandbox")["files"] = {
        "{home}/.claude/settings.json": "{\"apiKeyHelper\": \"echo k\"}" };
    });
    expect(accepted(record), "accepted: a hole").toBe(false);
  });
  it("refuses the record's env setting a forbidden variable", () => {
    expect(accepted(cli((s) => { at(s, "env")["ANTHROPIC_BASE_URL"] = "x"; }))).toBe(false);
  });
  it("refuses a signed-in record whose forbid_env misses its own key variable", () => {
    expect(accepted(cli((s) => {
      at(s, "per_credential.vendor_login_in_sandbox")["forbid_env"] = ["ANTHROPIC_API_KEY"];
    }))).toBe(false);
  });
  it("gives a signed-in run no key variables, and an API-key run its own", () => {
    const sources = bundledSources();
    expect(runEnv(sources, "claude-code", "vendor_login_in_sandbox").api_key_env).toEqual({});
    expect(() => runEnv(sources, "kimi", "api_key")).toThrow(SourceRecordError);
    expect(() => runEnv(sources, "anthropic", "api_key")).toThrow(SourceRecordError);
  });
});

describe("rve2: regions", () => {
  it.each([
    ["{region} outside the sign-in", cli((s) => {
      (at(s, "engine")["argv"] as string[]).push("--region={region}"); }, kimi)],
    ["regions with no {region} in the sign-in", cli((s) => {
      at(s, "login")["argv"] = ["{bin}", "login"]; }, kimi)],
    ["a region with an unknown key", cli((s) => {
      at(s, "regions.global")["stray"] = 1; }, kimi)],
  ])("refused: %s", (_what, record) => {
    expect(accepted(record)).toBe(false);
  });
});
