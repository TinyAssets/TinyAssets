/**
 * The agent definition's JSON files (`src/agent-definition.ts`): each read whole or refused
 * `agent_file_invalid` naming the file, and the paths that are one of them.
 */
import { describe, expect, it } from "vitest";

import {
  AGENT_FILES, agentFile, agentHooks, agentMcp, agentPath, checkDefinitionFile,
  definitionFileOf, definitionWidening, HOOK_EVENTS, skillUnion,
} from "../src/agent-definition";
import { Refusal } from "../src/refusal";

type Json = Record<string, unknown>;

/** The refusal `read` throws on `value`'s JSON, its message. */
function refusal(read: (text: string) => unknown, value: unknown): string {
  try {
    read(typeof value === "string" ? value : JSON.stringify(value));
  } catch (error) {
    expect(error).toBeInstanceOf(Refusal);
    expect((error as Refusal).code).toBe("agent_file_invalid");
    return (error as Refusal).message;
  }
  throw new Error("the file was accepted");
}

describe("agent.json", () => {
  it("reads an agent, with its defaults", () => {
    expect(agentFile(JSON.stringify({ engine: "auto", scopes: ["read_files"] }))).toEqual({
      engine: "auto", credential: null, connection: null, model: null,
      scopes: ["read_files"], enabled: true });
  });

  it.each<readonly [string, unknown]>([
    ["not JSON", "{"],
    ["a list", []],
    ["a key it does not hold", { engine: "auto", scopes: [], mcp: {} }],
    ["no engine", { scopes: [] }],
    ["an engine that is no record's id", { engine: "Claude Code", scopes: [] }],
    ["an unknown credential", { engine: "auto", scopes: [], credential: "cookie" }],
    ["a connection on the vendor's own sign-in", { engine: "codex", scopes: [],
      credential: "vendor_login_in_sandbox", connection: `c${"0".repeat(24)}` }],
    ["a scope twice", { engine: "auto", scopes: ["read_files", "read_files"] }],
    ["a scope no run holds", { engine: "auto", scopes: ["admin"] }],
    ["an enabled that is not true or false", { engine: "auto", scopes: [], enabled: "yes" }],
  ])("refuses %s", (_what, value) => {
    expect(refusal(agentFile, value)).toMatch(/^agent\.json: /);
  });
});

describe("mcp.json", () => {
  it("reads servers by URL and by command, a command's args and env present", () => {
    expect(agentMcp(JSON.stringify({ mcpServers: {
      docs: { url: "https://mcp.example.com/mcp?profile=ro" },
      "local-fs_2": { command: "uvx", args: ["some-mcp", "--root=/workspace"],
                      env: { MODE: "ro", lower_case: "1" } },
      bare: { command: "/usr/bin/server" },
    } }))).toEqual({ servers: {
      docs: { url: "https://mcp.example.com/mcp?profile=ro" },
      "local-fs_2": { command: "uvx", args: ["some-mcp", "--root=/workspace"],
                      env: { MODE: "ro", lower_case: "1" } },
      bare: { command: "/usr/bin/server", args: [], env: {} },
    } });
    expect(agentMcp(JSON.stringify({ mcpServers: {} }))).toEqual({ servers: {} });
  });

  const server = (entry: unknown, name = "docs"): Json => ({ mcpServers: { [name]: entry } });
  it.each<readonly [string, unknown]>([
    ["not JSON", "mcpServers"],
    ["a key beside the servers", { mcpServers: {}, inputs: [] }],
    ["servers as a list", { mcpServers: [] }],
    ["the run's own door's name", server({ url: "https://x.example/mcp" }, "tinyassets")],
    ["a name with a dot", server({ url: "https://x.example/mcp" }, "a.b")],
    ["a name with a wildcard", server({ url: "https://x.example/mcp" }, "a*")],
    ["a name past 64 characters", server({ url: "https://x.example/mcp" }, "a".repeat(65))],
    ["a server over plain http", server({ url: "http://x.example/mcp" })],
    ["a URL with a password", server({ url: "https://user:pass@x.example/mcp" })],
    ["a URL with a variable", server({ url: "https://x.example/mcp/${TOKEN}" })],
    ["a URL that is not one", server({ url: "x.example/mcp" })],
    ["headers, which would carry a credential", server({ url: "https://x.example/mcp",
                                                         headers: { Authorization: "Bearer k" } })],
    ["a transport type", server({ type: "http", url: "https://x.example/mcp" })],
    ["both a URL and a command", server({ url: "https://x.example/mcp", command: "x" })],
    ["no command", server({ args: ["x"] })],
    ["an empty command", server({ command: "" })],
    ["a command pi would run for its value", server({ command: "!cat ~/.ssh/id_rsa" })],
    ["an argument with a variable", server({ command: "x", args: ["--token=$TOKEN"] })],
    ["an argument with a braced variable", server({ command: "x", args: ["${HOME}"] })],
    ["args as one line", server({ command: "x", args: "--a --b" })],
    ["an argument with a NUL", server({ command: "x", args: ["a\0b"] })],
    ["a credential-shaped variable", server({ command: "x", env: { GITHUB_TOKEN: "t" } })],
    ["a lower-case credential-shaped variable", server({ command: "x", env: { api_key: "t" } })],
    ["a proxy variable", server({ command: "x", env: { HTTPS_PROXY: "http://p" } })],
    ["a variable that is not a name", server({ command: "x", env: { "A-B": "1" } })],
    ["a variable's value read from the environment", server({ command: "x",
                                                              env: { MODE: "${MODE}" } })],
    ["a variable that is not text", server({ command: "x", env: { MODE: 1 } })],
  ])("refuses %s", (_what, value) => {
    expect(refusal(agentMcp, value)).toMatch(/^mcp\.json: /);
  });
});

describe("hooks.json", () => {
  it("reads hooks by event, every event listed, a match on tool events alone", () => {
    const read = agentHooks(JSON.stringify({ hooks: {
      pre_tool: [{ match: { kinds: ["shell"], tools: ["mcp:github:*"] },
                   run: ["./hooks/guard.sh", "--strict"], timeout_s: 30 }],
      prompt: [{ run: ["python3", "hooks/context.py"] }],
      stop: [],
    } }));
    expect(read.hooks).toEqual({
      pre_tool: [{ match: { kinds: ["shell"], tools: ["mcp:github:*"] },
                   run: ["./hooks/guard.sh", "--strict"], timeout_s: 30 }],
      post_tool: [], prompt: [{ run: ["python3", "hooks/context.py"] }], session_start: [],
      stop: [], session_end: [], pre_compact: [] });
    expect(Object.keys(read.hooks)).toEqual(Object.keys(HOOK_EVENTS));
    expect(agentHooks(JSON.stringify({ hooks: {} })).hooks.pre_tool).toEqual([]);
  });

  const on = (event: string, entry: unknown): Json => ({ hooks: { [event]: [entry] } });
  it.each<readonly [string, unknown]>([
    ["not JSON", "hooks:"],
    ["a key beside the hooks", { hooks: {}, disabled: true }],
    ["an event no engine-neutral hook has", on("PreToolUse", { run: ["x"] })],
    ["an event's hooks not a list", { hooks: { stop: { run: ["x"] } } }],
    ["a shell line", on("pre_tool", { run: "./guard.sh --strict" })],
    ["nothing to run", on("pre_tool", { run: [] })],
    ["an empty program", on("pre_tool", { run: ["", "x"] })],
    ["an argument that is not text", on("pre_tool", { run: ["x", 1] })],
    ["an argument with a NUL", on("pre_tool", { run: ["x\0y"] })],
    ["a decision to allow", on("pre_tool", { run: ["x"], decision: "allow" })],
    ["an input rewrite", on("pre_tool", { run: ["x"], updatedInput: {} })],
    ["a match on an event that is no tool call's", on("prompt", { run: ["x"],
                                                                 match: { kinds: ["shell"] } })],
    ["an empty match", on("pre_tool", { run: ["x"], match: {} })],
    ["a kind acts.json does not define", on("pre_tool", { run: ["x"],
                                                         match: { kinds: ["web_fetch"] } })],
    ["a kind twice", on("pre_tool", { run: ["x"], match: { kinds: ["shell", "shell"] } })],
    ["no kinds", on("pre_tool", { run: ["x"], match: { kinds: [] } })],
    ["a tool that is not an MCP tool's matcher", on("post_tool", { run: ["x"],
                                                                  match: { tools: ["Bash"] } })],
    ["a matcher by regular expression", on("post_tool", { run: ["x"],
                                                         match: { tools: ["mcp:.*:.*"] } })],
    ["a timeout of none", on("pre_tool", { run: ["x"], timeout_s: 0 })],
    ["a timeout in fractions", on("pre_tool", { run: ["x"], timeout_s: 1.5 })],
  ])("refuses %s", (_what, value) => {
    expect(refusal(agentHooks, value)).toMatch(/^hooks\.json: /);
  });
});

describe("the definition's files", () => {
  it("are an agent's own JSON files, directly in its directory", () => {
    expect(definitionFileOf(agentPath("main", AGENT_FILES.mcp))).toBe("mcp.json");
    expect(definitionFileOf("/workspace/agents/writer.v2/hooks.json")).toBe("hooks.json");
    expect(definitionFileOf("/workspace/agents/main/agent.json")).toBe("agent.json");
    expect([
      "/workspace/agents/main/AGENTS.md", "/workspace/agents/main/sub/mcp.json",
      "/workspace/.agents/skills/x/mcp.json", "/workspace/agents/.x/mcp.json",
      "/workspace/agents/mcp.json", "/workspace/mcp.json",
    ].map(definitionFileOf)).toEqual([null, null, null, null, null, null]);
  });

  it("are each checked by its own reader, and any other path is not", () => {
    expect(() => checkDefinitionFile("/workspace/agents/main/hooks.json", "{}"))
      .toThrow(/^hooks\.json: /);
    expect(() => checkDefinitionFile("/workspace/agents/main/mcp.json", "{\"mcpServers\": {}}"))
      .not.toThrow();
    expect(() => checkDefinitionFile("/workspace/.agents/skills/x/mcp.json", "{")).not.toThrow();
  });
});

describe("an agent's skills", () => {
  it("are the union of its own, the shared abilities and the owner's, the first holding a " +
     "name winning it; a hidden directory or one with no SKILL.md is none", () => {
    const files = new Set([
      "/workspace/agents/main/.agents/skills/mine/SKILL.md",
      "/workspace/agents/main/.agents/skills/both/SKILL.md",
      "/workspace/.agents/skills/both/SKILL.md", "/workspace/.agents/skills/shared/SKILL.md",
      "/workspace/.agents/skills/.hidden/SKILL.md", "/workspace/skills/shared/SKILL.md",
      "/workspace/skills/owners/SKILL.md", "/workspace/skills/notes/README.md",
    ]);
    const dirs = (dir: string) => [...new Set([...files].filter((path) =>
      path.startsWith(`${dir}/`)).map((path) => path.slice(dir.length + 1).split("/")[0]!))];
    expect(Object.fromEntries(skillUnion("main", { dirs, file: (path) => files.has(path) })))
      .toEqual({
        mine: "/workspace/agents/main/.agents/skills/mine",
        both: "/workspace/agents/main/.agents/skills/both",
        shared: "/workspace/.agents/skills/shared",
        owners: "/workspace/skills/owners",
      });
  });
});

describe("what a new version of a definition file adds", () => {
  const mcp = (servers: Json) => JSON.stringify({ mcpServers: servers });
  const hooks = (events: Json) => JSON.stringify({ hooks: events });
  const guard = { run: ["./guard.sh"] };
  it.each<readonly [string, string, string | null, string, string | null]>([
    ["no scope", "agent.json", JSON.stringify({ engine: "auto", scopes: ["read_files"] }),
     JSON.stringify({ engine: "codex", scopes: [] }), null],
    ["a scope", "agent.json", JSON.stringify({ engine: "auto", scopes: ["read_files"] }),
     JSON.stringify({ engine: "auto", scopes: ["read_files", "run_graph"] }),
     "agent.json adds the scopes run_graph"],
    ["no server, from none", "mcp.json", null, mcp({}), null],
    ["a server dropped", "mcp.json", mcp({ a: { url: "https://a.example/mcp" } }), mcp({}),
     null],
    ["a server, over a file that does not read", "mcp.json", "{", mcp({ a: { command: "x" } }),
     "mcp.json adds or changes the servers a"],
    ["a server changed", "mcp.json", mcp({ a: { command: "x" }, b: { command: "y" } }),
     mcp({ a: { command: "x", args: ["--write"] }, b: { command: "y" } }),
     "mcp.json adds or changes the servers a"],
    ["the same hooks, written another way", "hooks.json", hooks({ stop: [guard] }),
     hooks({ stop: [guard], prompt: [] }), null],
    ["a hook", "hooks.json", hooks({ stop: [guard] }),
     hooks({ stop: [guard], pre_tool: [guard] }), "hooks.json adds or changes hooks on pre_tool"],
    ["a hook narrowed", "hooks.json", hooks({ pre_tool: [guard] }),
     hooks({ pre_tool: [{ ...guard, match: { kinds: ["shell"] } }] }),
     "hooks.json adds or changes hooks on pre_tool"],
    ["another file", "AGENTS.md", "a", "b", null],
  ])("%s", (_what, file, before, after, said) => {
    expect(definitionWidening(file, before, after)).toBe(said);
  });
});
