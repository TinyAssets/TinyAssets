/**
 * The slice E review's attacks on the engine-record checker (review round 1), kept as
 * tests. Each case states what the checker does with a record an attacker or a careless
 * edit could write. The holes the review found are closed, so each now expects a refusal:
 * a session path that sweeps a credential (`/*` + `/`, `/root/`), a secret path reached
 * through `/proc`, an engine configured to nothing (`--tools ""`, `mcp_servers={}`), and a
 * bare positional prompt.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import { bundledRecords, catalogOf } from "../src/catalog";
import { sourceRecord } from "../src/check";
import { SourceRecordError } from "../src/schema";

type Json = Record<string, unknown>;
const load = (id: string): Json =>
  JSON.parse(readFileSync(join(PLATFORM, "sources", "records", `${id}.json`), "utf8")) as Json;
const claude = load("claude-code");
const codex = load("codex");
const pi = load("pi");
const gemini = load("gemini");
const kimi = load("kimi");
const anthropic = load("anthropic");

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

describe("unknown keys at every level", () => {
  it.each([
    ["top level", { ...claude, stray: 1 }],
    ["cli", cli((s) => { s["stray"] = 1; })],
    ["install", cli((s) => { at(s, "install")["stray"] = 1; })],
    ["login", cli((s) => { at(s, "login")["stray"] = 1; })],
    ["login.state_checks[0]", cli((s) => {
      (at(s, "login")["state_checks"] as Json[])[0]!["stray"] = 1; }, codex)],
    ["regions entry", cli((s) => { at(s, "regions.global")["stray"] = 1; }, kimi)],
    ["regions hosts", cli((s) => { at(s, "regions.global.hosts")["stray"] = []; }, kimi)],
    ["api_key_env entry", cli((s) => { at(s, "api_key_env.anthropic")["stray"] = 1; })],
    ["unattended entry (allowed)", cli((s) => {
      at(s, "unattended.vendor_login_in_sandbox")["stray"] = 1; })],
    ["unattended entry (refused)", cli((s) => {
      at(s, "unattended.vendor_login_in_sandbox")["stray"] = 1; }, codex)],
    ["unattended map", cli((s) => { at(s, "unattended")["stray"] = { allowed: false,
      reason: "x" }; })],
    ["per_credential map", cli((s) => { at(s, "per_credential")["stray"] = {}; })],
    ["per_credential entry", cli((s) => {
      at(s, "per_credential.vendor_login_in_sandbox")["stray"] = 1; })],
    ["an invoke nested in per_credential", cli((s) => {
      at(s, "per_credential.vendor_login_in_sandbox")["invoke"] = {}; })],
    ["engine", cli((s) => { at(s, "engine")["stray"] = 1; })],
    ["engine.events", cli((s) => { at(s, "engine.events")["stray"] = []; })],
    ["event matcher", cli((s) => {
      (at(s, "engine.events")["final_text"] as Json[])[0]!["stray"] = 1; })],
    ["instructions", cli((s) => { at(s, "instructions")["stray"] = 1; })],
    ["mcp (file)", cli((s) => { at(s, "mcp")["stray"] = 1; })],
    ["mcp.trust", cli((s) => { at(s, "mcp.trust")["stray"] = 1; })],
    ["mcp (unsupported)", cli((s) => { s["mcp"] = { unsupported: "none", stray: 1 }; }, pi)],
    ["tools", cli((s) => { at(s, "tools")["stray"] = {}; })],
    ["tools.allow (join)", cli((s) => { at(s, "tools.allow")["stray"] = 1; })],
    ["tools.deny (each)", cli((s) => { at(s, "tools.deny")["stray"] = 1; }, codex)],
    ["tools.allow (file)", cli((s) => { at(s, "tools.allow")["stray"] = 1; }, gemini)],
    ["no_update (env)", cli((s) => { at(s, "no_update")["stray"] = 1; })],
    ["no_update (reason)", cli((s) => { s["no_update"] = { reason: "none", stray: 1 }; }, pi)],
    ["hosts", cli((s) => { at(s, "hosts")["stray"] = []; })],
    ["gateway record", { ...anthropic, stray: 1 }],
  ])("refuses an unknown key in %s", (_what, record) => {
    expect(accepted(record)).toBe(false);
  });
});

describe("secret and session paths", () => {
  // Each of these sweeps, or can sweep, the record's own credential file into a sync.
  it.each([
    ["an absolute wildcard session path", cli((s) => { s["session_paths"] = ["/*/"]; })],
    ["an absolute session path holding every home", cli((s) => {
      s["session_paths"] = ["/root/"]; })],
    ["a secret path reaching /workspace through /proc", cli((s) => {
      s["secret_paths"] = ["/proc/self/cwd/.credentials.json"]; })],
    ["a secret path reaching /workspace through another process's root", cli((s) => {
      s["secret_paths"] = ["/proc/1/root/workspace/x"]; })],
    ["a session path that is the agent directory's parent", cli((s) => {
      s["session_paths"] = ["/*/agents/"]; })],
    ["a wildcard first segment under home", cli((s) => { s["session_paths"] = ["{home}/*/"]; })],
    ["/workspace with a trailing slash", cli((s) => { s["secret_paths"] = ["/workspace/"]; })],
    ["/workspace exactly", cli((s) => { s["secret_paths"] = ["/workspace"]; })],
    ["a double slash", cli((s) => { s["secret_paths"] = ["//workspace/x"]; })],
    ["a double slash under home", cli((s) => { s["secret_paths"] = ["{home}//x"]; })],
    ["a relative path", cli((s) => { s["secret_paths"] = ["workspace/x"]; })],
    ["a tilde", cli((s) => { s["secret_paths"] = ["~/.claude/.credentials.json"]; })],
    ["a glob inside a segment", cli((s) => { s["secret_paths"] = ["{home}/work*/x"]; })],
    ["a parent segment", cli((s) => { s["secret_paths"] = ["{home}/a/../../workspace/x"]; })],
    ["a dot segment", cli((s) => { s["secret_paths"] = ["{home}/./x"]; })],
    ["a session path equal to the secret", cli((s) => {
      s["session_paths"] = ["{home}/.claude/.credentials.json"]; })],
    ["a secret holding the session", cli((s) => { s["secret_paths"] = ["{home}/.claude/"]; })],
    ["a wildcard secret root", cli((s) => { s["secret_paths"] = ["/*"]; })],
    ["home itself as the secret", cli((s) => { s["secret_paths"] = ["{home}"]; })],
    ["home as a session path", cli((s) => { s["session_paths"] = ["{home}/"]; })],
  ])("refuses: %s", (_what, record) => {
    expect(accepted(record)).toBe(false);
  });

  it("takes a wildcard below the first segment, as Gemini's chats are kept", () => {
    expect(accepted(gemini)).toBe(true);
    expect((gemini["cli"] as Json)["session_paths"]).toEqual(["{home}/.gemini/tmp/*/chats/"]);
  });
});

describe("engine, not backend", () => {
  it.each([
    ["a literal tools-off flag in the unattended argv", cli((s) => {
      s["unattended_argv"] = ["--tools", ""]; })],
    ["a literal empty MCP set", cli((s) => {
      s["unattended_argv"] = ["-c", "mcp_servers={}"]; }, codex)],
    ["an empty core tool list in a settings template", cli((s) => {
      at(s, "mcp")["template"] = String(at(s, "mcp")["template"])
        .replace("\"telemetry\"", "\"tools\": {\"core\": []}, \"telemetry\""); }, gemini)],
  ])("refuses: %s", (_what, record) => {
    expect(accepted(record)).toBe(false);
  });
});

describe("citations", () => {
  it.each([
    ["allowed citing a non-vendor page", cli((s) => {
      at(s, "unattended.vendor_login_in_sandbox")["cite"] = "https://example.com/"; })],
    ["allowed read in the future", cli((s) => {
      at(s, "unattended.vendor_login_in_sandbox")["read"] = "2099-12-31"; })],
    ["allowed read on 31 February", cli((s) => {
      at(s, "unattended.vendor_login_in_sandbox")["read"] = "2026-02-31"; })],
  ])("holds shape only, so takes: %s", (_what, record) => {
    expect(accepted(record)).toBe(true);
  });
  it("refuses allowed: true with cite: null", () => {
    expect(accepted(cli((s) => {
      at(s, "unattended.vendor_login_in_sandbox")["cite"] = null; }))).toBe(false);
  });
  it("refuses allowed: 1", () => {
    expect(accepted(cli((s) => {
      at(s, "unattended.vendor_login_in_sandbox")["allowed"] = 1; }))).toBe(false);
  });
});

describe("prompt injection by argv", () => {
  it("passes pi's prompt on stdin, never as a positional argument", () => {
    const engine = (pi["cli"] as Json)["engine"] as Json;
    expect(engine["stdin"]).toBe("{prompt}");
    expect(JSON.stringify([engine["argv"], engine["resume_argv"]])).not.toContain("{prompt}");
  });
  it.each([
    ["a bare positional {prompt}", ["{bin}", "--mode", "json", "{prompt}"]],
    ["a {prompt} after --", ["{bin}", "--mode", "json", "--", "{prompt}"]],
    ["a two-token --prompt {prompt}", ["{bin}", "--prompt", "{prompt}"]],
  ])("refuses %s", (_what, argv) => {
    expect(accepted(cli((s) => {
      delete at(s, "engine")["stdin"];
      at(s, "engine")["argv"] = argv;
      at(s, "engine")["resume_argv"] = [...argv, "--session", "{sid}"]; }, pi))).toBe(false);
  });
});

describe("engines cross-check", () => {
  it("refuses a key naming an engine of another provider", () => {
    expect(() => catalogOf([...bundledRecords().filter((r) => (r as Json)["id"] !== "anthropic"),
      { ...anthropic, engines: ["codex"] }]).get("anthropic")).toThrow(SourceRecordError);
  });
});
