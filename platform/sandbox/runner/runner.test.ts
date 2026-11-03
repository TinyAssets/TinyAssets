/**
 * The runner (`runner.ts`, as the template carries it bundled) against fake CLIs that print
 * each record's own event shapes and a fake doors server: the first post is an empty
 * heartbeat; the frames rise from 1 and read by the record's matchers as the object reads
 * them; the CLI runs on the run's allow-list alone, from the record's argv, as its engine's
 * user; and every refusal the tasks item names holds: a project env file, a secret's bytes
 * in a synced file, a model id that would read as a flag. Cancel, `events_busy`, and the
 * renewal's held posts and resume are here too.
 */
import { existsSync, readFileSync } from "node:fs";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { bundledSources, runEnv } from "../../doors/src/sources";
import { readEvent } from "../../owner/src/run-events";

import { engineHome } from "./engine";
import { Doors, runnerBundle, Sandbox, startDoc } from "./harness";

const sources = bundledSources();

/** Each record's own event shapes: a session id, a tool call, the final text, usage. */
const SHAPES: Record<string, { lines: unknown[]; session: string | null; text: string;
                               tool?: false }> = {
  "claude-code": {
    lines: [{ type: "system", subtype: "init", session_id: "cs-1" },
            { type: "assistant", message: { content: [{ type: "tool_use", name: "Bash" }] } },
            { type: "result", subtype: "success", is_error: false, result: "all done",
              usage: { input_tokens: 3 } }],
    session: "cs-1", text: "all done" },
  codex: {
    lines: [{ type: "thread.started", thread_id: "th-1" },
            { type: "item.completed", item: { type: "command_execution", command: "ls" } },
            { type: "item.completed", item: { type: "agent_message", text: "all done" } },
            { type: "turn.completed", usage: { input_tokens: 3 } }],
    session: "th-1", text: "all done" },
  gemini: {
    lines: [{ type: "init", session_id: "g-1" }, { type: "tool_use", tool_name: "ls" },
            { type: "message", role: "assistant", content: "all " },
            { type: "message", role: "assistant", content: "done" },
            { type: "result", status: "success", stats: { total_tokens: 3 } }],
    session: "g-1", text: "all done" },
  kimi: {
    // Kimi's matchers read the same event as its result first: its tool calls and its
    // session are owed to the live check (tasks, "Kimi Code").
    lines: [{ role: "assistant", content: "all done", tool_calls: [{ id: "t" }] }],
    session: null, text: "all done", tool: false },
  pi: {
    lines: [{ type: "session", id: "p-1" }, { type: "tool_execution_start", tool: "bash" },
            { type: "message_end", message: { role: "assistant",
                                              content: [{ type: "text", text: "all done" }],
                                              usage: { input: 3 } } }],
    session: "p-1", text: "all done" },
};

let doors: Doors;
let box: Sandbox;

beforeEach(async () => {
  runnerBundle();
  doors = new Doors();
  await doors.listen();
  box = new Sandbox();
  doors.files.set("/workspace/agents/main/agent.json",
                  { text: JSON.stringify({ engine: "auto", scopes: [] }), version: 1 });
  doors.files.set("/workspace/agents/main/AGENTS.md", { text: "# main", version: 2 });
});

afterEach(() => {
  doors.close();
  box.remove();
});

/** A run's whole environment as the lease makes it (`runEnv`), for a signed-in run. */
function config(engine: string, credential = sources.get(engine)!.cli!.credential[0]!) {
  const made = runEnv(sources, engine, credential);
  return { generation: 1, env: made.env, inherit: ["PATH", "LANG"] };
}

describe.each(Object.keys(SHAPES))("a run of %s", (engine) => {
  it("posts a heartbeat first, then its frames in order, read by the record", async () => {
    const shape = SHAPES[engine]!;
    box.cli(engine, { lines: shape.lines });
    const credential = sources.get(engine)!.cli!.credential[0]!;
    const start = startDoc(doors, engine, { config: credential === "api_key" ? {
      generation: 1, inherit: ["PATH"], env: { ...config(engine, credential).env,
        ANTHROPIC_BASE_URL: `${doors.origin}/r/x/c/c${"0".repeat(24)}/`,
        ANTHROPIC_API_KEY: "ta_ph_placeholder" } } : config(engine) });
    box.start(doors, start);
    expect(box.runner("launch", start.run).code).toBe(0);
    const end = await doors.ended_();
    expect(end.body["end"]).toEqual({ exit_code: 0 });
    const posts = doors.events();
    // The first post is the empty heartbeat, before the sign-ins and the sync.
    expect(posts[0]!.body).toEqual({ frames: [] });
    expect(doors.posts.indexOf(posts[0]!)).toBe(0);
    expect(posts.every((post) => post.headers["x-ta-runner"] === "r".repeat(64))).toBe(true);
    const frames = posts.flatMap((post) => post.body["frames"] as
      { seq: number; at: number; event: unknown }[]);
    expect(frames.map((frame) => frame.seq)).toEqual(frames.map((_f, i) => i + 1));
    const events = sources.get(engine)!.cli!.engine.events;
    const reads = frames.map((frame) => readEvent(events, frame.event));
    expect(reads.find((read) => read.session_id !== null)?.session_id ?? null)
      .toBe(shape.session);
    const texts = reads.filter((read) => read.text !== null).map((read) => read.text!);
    const result = texts.some((text) => text.accumulate)
      ? texts.map((text) => text.value).join("") : texts.at(-1)?.value;
    expect(result).toBe(shape.text);
    expect(reads.some((read) => read.kind === "tool_call")).toBe(shape.tool !== false);
    // The CLI ran on the allow-list alone, as its engine's user, HOME its engine home.
    const seen = box.ran(engine).at(-1)!;
    const home = box.path(engineHome(engine));
    expect(seen.env["HOME"]).toBe(home);
    expect(Object.keys(seen.env).sort()).toEqual(
      [...new Set([...Object.keys(start.config.env), "PATH",
                   ...(process.env["LANG"] === undefined ? [] : ["LANG"])])].sort());
    expect(seen.uid).toBe(process.getuid!());
    const record = sources.get(engine)!.cli!;
    if (record.engine.stdin !== undefined) {
      expect(seen.stdin).toBe("do the thing");
    } else {
      expect(seen.argv).toContain("--prompt=do the thing");
    }
    expect(seen.cwd).toBe(record.engine.resume === "continue_in_cwd"
      ? `${home}/conversations/${start.conversation}` : box.path("/workspace/agents/main"));
    // The owner's files were synced in before the CLI ran.
    expect(readFileSync(box.path("/workspace/agents/main/AGENTS.md"), "utf8")).toBe("# main");
  });
});

describe("the invocation", () => {
  it("resumes by session id with resume_argv, the model joined to its flag", async () => {
    box.cli("claude-code", { lines: SHAPES["claude-code"]!.lines });
    const start = startDoc(doors, "claude-code", { session_id: "cs-9", model: "-weird-model",
                                                   unattended: true,
                                                   config: config("claude-code") });
    box.start(doors, start);
    box.runner("launch", start.run);
    await doors.ended_();
    const argv = box.ran("claude-code").at(-1)!.argv;
    expect(argv.slice(-2)).toEqual(["--resume", "cs-9"]);
    expect(argv).toContain("--model=-weird-model");
    expect(argv).not.toContain("-weird-model");
    expect(argv.join(" ")).toContain("--permission-mode auto --permission-prompts none");
    // The MCP config holds the bare door and the run's secret as its header, no bearer.
    const mcp = JSON.parse(readFileSync(box.path(`${engineHome("claude-code")}/run/mcp.json`),
                                        "utf8"));
    expect(mcp.mcpServers.tinyassets.url).toBe(start.urls.mcp_bare);
    expect(mcp.mcpServers.tinyassets.headers).toEqual({ "x-ta-run": "e".repeat(64) });
    expect(argv).toEqual(expect.arrayContaining(["--mcp-config", "--strict-mcp-config"]));
  });

  it("passes codex its MCP door with the secret in the path", async () => {
    box.cli("codex", { lines: SHAPES["codex"]!.lines });
    const start = startDoc(doors, "codex", { config: config("codex") });
    box.start(doors, start);
    box.runner("launch", start.run);
    await doors.ended_();
    const argv = box.ran("codex").at(-1)!.argv;
    expect(argv).toContain(`mcp_servers.tinyassets.url="${start.urls.mcp}"`);
    expect(argv).toContain("check_for_update_on_startup=false");
    expect(argv).toContain("forced_login_method=\"chatgpt\"");
  });

  it("refuses a run whose project files set a variable its environment never takes", async () => {
    for (const [file, text] of [["/workspace/agents/main/.env", "ANTHROPIC_API_KEY=sk-x\n"],
                                ["/workspace/agents/main/.claude/settings.json",
                                 JSON.stringify({ apiKeyHelper: "echo key" })],
                                ["/workspace/agents/main/.gemini/.env",
                                 "GEMINI_API_KEY=abc\n"]] as const) {
      const local = new Doors();
      await local.listen();
      local.files = new Map(doors.files);
      local.files.set(file, { text, version: 9 });
      const start = startDoc(local, "claude-code", { config: config("claude-code") });
      box.start(local, start);
      box.runner("launch", start.run);
      const end = await local.ended_();
      expect(end.body["end"]).toEqual({ exit_code: 1 });
      const frames = local.events().flatMap((post) => post.body["frames"] as
        { event: { type?: string; reason?: string } }[]);
      expect(frames.find((frame) => frame.event.type === "ta.refused")!.event.reason)
        .toMatch(/^project_env_refused/);
      local.close();
    }
    expect(box.ran("claude-code")).toEqual([]);  // the CLI never ran
  });
});

describe("the sync", () => {
  it("never syncs a file that holds a secret's bytes; syncs the sessions and the rest", async () => {
    const token = "sk-ant-oat01-" + "Z".repeat(40);
    box.write(`${engineHome("claude-code")}/.claude/.credentials.json`,
              JSON.stringify({ claudeAiOauth: { accessToken: token } }));
    box.cli("claude-code", {
      lines: SHAPES["claude-code"]!.lines,
      writes: { "notes.md": "plain notes", "leak.txt": `copied: ${token}`,
                [box.path(`${engineHome("claude-code")}/.claude/projects/main/cs-1.jsonl`)]:
                  "{\"type\":\"user\"}\n" } });
    const start = startDoc(doors, "claude-code", { config: config("claude-code") });
    box.start(doors, start);
    box.runner("launch", start.run);
    await doors.ended_();
    const commits = doors.posts.filter((post) => post.path === "/sync/commit");
    const ops = commits.flatMap((post) => post.body["ops"] as { path: string; op: string }[]);
    const paths = ops.map((op) => op.path);
    expect(paths).toContain("/workspace/agents/main/notes.md");
    expect(paths).not.toContain("/workspace/agents/main/leak.txt");
    expect(JSON.stringify(commits)).not.toContain(token);
    // The engine's session, into the owner's files under its authority and engine.
    expect(paths).toContain("/workspace/.sessions/owner/claude-code/.claude/projects/main/cs-1.jsonl");
    // Nothing of the engine's home but its sessions: never its credential file.
    expect(paths.some((path) => path.includes("credentials"))).toBe(false);
    expect(commits.every((post) => post.headers["x-ta-sync"] === "sync-token")).toBe(true);
    expect(commits[0]!.body).toMatchObject({ lease_id: "b".repeat(32), epoch: 1 });
    const frames = doors.events().flatMap((post) => post.body["frames"] as
      { event: { type?: string; path?: string } }[]);
    expect(frames.find((frame) => frame.event.type === "ta.sync_refused")!.event.path)
      .toBe("/workspace/agents/main/leak.txt");
  });

  it("keeps a client's sandbox off the owner's trees, read-only there", async () => {
    box.cli("claude-code", { lines: SHAPES["claude-code"]!.lines,
                             writes: { "/tmp/ignored": "x" } });
    const start = startDoc(doors, "claude-code", { authority: "a".repeat(64),
                                                   config: config("claude-code") });
    box.start(doors, start, "a".repeat(64));
    box.runner("launch", start.run);
    await doors.ended_();
    const ops = doors.posts.filter((post) => post.path === "/sync/commit")
      .flatMap((post) => post.body["ops"] as { path: string }[]);
    expect(ops.filter((op) => op.path.startsWith("/workspace/agents/"))).toEqual([]);
    expect(readFileSync(box.path("/workspace/agents/main/AGENTS.md"), "utf8")).toBe("# main");
  });
});

describe("the posts", () => {
  it("post again after events_busy, the same frames, never twice", async () => {
    doors.busy = 2;
    box.cli("codex", { lines: SHAPES["codex"]!.lines });
    const start = startDoc(doors, "codex", { config: config("codex") });
    box.start(doors, start);
    box.runner("launch", start.run);
    await doors.ended_();
    const taken = doors.events().slice(2);
    const seqs = taken.flatMap((post) => (post.body["frames"] as { seq: number }[])
      .map((frame) => frame.seq));
    expect(new Set(seqs).size).toBe(seqs.length);
    expect(seqs).toEqual(seqs.map((_s, i) => i + 1));
  });

  it("cut an event past a frame's bound, marked", async () => {
    box.cli("codex", { lines: [...SHAPES["codex"]!.lines,
                               { type: "item.completed", item: { type: "reasoning",
                                                                 text: "x".repeat(100_000) } }] });
    const start = startDoc(doors, "codex", { config: config("codex") });
    box.start(doors, start);
    box.runner("launch", start.run);
    await doors.ended_();
    const frames = doors.events().flatMap((post) => post.body["frames"] as
      { event: { ta_cut?: boolean; type?: string } }[]);
    const cut = frames.find((frame) => frame.event.ta_cut === true)!;
    expect(cut.event.type).toBe("item.completed");
    expect(doors.events().every((post) => JSON.stringify(post.body).length < 65_536)).toBe(true);
  });

  it("stop the CLI when the object ended the run", async () => {
    box.cli("codex", { lines: [], sleepMs: 20_000 });
    const start = startDoc(doors, "codex", { config: config("codex") });
    box.start(doors, start);
    box.runner("launch", start.run);
    await waitFor(() => box.ran("codex").length > 0);
    doors.ended = true;
    // The next post is refused; the runner stops its CLI and ends.
    await waitFor(() => !existsSync(box.path(`/var/lib/ta/runs/${start.run}/state.json`))
      || JSON.parse(readFileSync(box.path(`/var/lib/ta/runs/${start.run}/state.json`), "utf8"))
        .ended === true, 25_000);
  }, 40_000);
});

describe("cancel and the runner's state", () => {
  it("sends the CLI SIGTERM, and its end goes to the object", async () => {
    box.cli("claude-code", { lines: [{ type: "system", subtype: "init", session_id: "s" }],
                             sleepMs: 20_000 });
    const start = startDoc(doors, "claude-code", { config: config("claude-code") });
    box.start(doors, start);
    box.runner("launch", start.run);
    await waitFor(() => box.ran("claude-code").length > 0);
    await waitFor(() => box.runner("state").out.includes("\"live\":1"));
    expect(JSON.parse(box.runner("state").out)).toEqual({ live: 1, quiet: false });
    expect(box.runner("status", start.run).code).toBe(0);
    expect(box.runner("cancel", start.run).code).toBe(0);
    const end = await doors.ended_();
    expect(end.body["end"]).toEqual({ exit_code: 143 });
    await waitFor(() => box.runner("status", start.run).code === 3);
    expect(box.runner("cancel", start.run).code).toBe(3);
  });

  it("kills a CLI that ignores SIGTERM, after its grace", async () => {
    box.cli("codex", { lines: [], sleepMs: 60_000, stubborn: true });
    const start = startDoc(doors, "codex", { config: config("codex") });
    box.start(doors, start);
    box.runner("launch", start.run);
    await waitFor(() => box.ran("codex").length > 0);
    box.runner("cancel", start.run);
    const end = await doors.ended_(25_000);
    expect((end.body["end"] as { exit_code: number }).exit_code).toBeGreaterThan(128);
  }, 40_000);
});

describe("renewal", () => {
  it("posts renewing, holds its posts, then renewed; a CLI that failed is resumed", async () => {
    box.cli("claude-code", { lines: [{ type: "system", subtype: "init", session_id: "cs-r" },
                                     { type: "assistant", message: { content: [] } }],
                             sleepMs: 3_000, exit: 1 });
    const start = startDoc(doors, "claude-code", { config: config("claude-code") });
    box.start(doors, start);
    box.runner("launch", start.run);
    await waitFor(() => box.ran("claude-code").length > 0);
    // The doors answer from this process: the command runs beside it, not blocking it.
    expect(await box.runnerAsync("renewing")).toBe(0);
    const held = doors.events().length;
    await new Promise((resolve) => setTimeout(resolve, 800));
    expect(doors.events().length).toBe(held);  // nothing posted while paused
    box.cli("claude-code", { lines: SHAPES["claude-code"]!.lines });
    expect(box.runner("renewed").code).toBe(0);
    const end = await doors.ended_();
    expect(end.body["end"]).toEqual({ exit_code: 0 });
    const types = doors.events().flatMap((post) => post.body["frames"] as
      { event: { type?: string } }[]).map((frame) => frame.event.type);
    expect(types).toEqual(expect.arrayContaining(["ta.renewing", "ta.renewed", "ta.resumed"]));
    expect(types.indexOf("ta.renewing")).toBeLessThan(types.indexOf("ta.renewed"));
    // Resumed from its session, on the record's resume_argv.
    const resumed = box.ran("claude-code").at(-1)!;
    expect(resumed.argv.slice(-2)).toEqual(["--resume", "cs-r"]);
  }, 40_000);
});

describe("the sign-in", () => {
  it("is signed in where the status matches the record, and reported each start", async () => {
    box.cli("claude-code", { lines: [], status: {
      out: JSON.stringify({ loggedIn: true, authMethod: "claude.ai" }), exit: 0 } });
    expect(box.runner("signin-check", "claude-code").code).toBe(0);
    box.cli("claude-code", { lines: [], status: {
      out: JSON.stringify({ loggedIn: true, authMethod: "console" }), exit: 0 } });
    expect(box.runner("signin-check", "claude-code").code).toBe(3);
    box.cli("codex", { lines: [], status: { out: "Not logged in", exit: 1 } });
    expect(box.runner("signin-check", "codex").code).toBe(3);
    box.cli("claude-code", { lines: SHAPES["claude-code"]!.lines, status: {
      out: JSON.stringify({ loggedIn: true, authMethod: "claude.ai" }), exit: 0 } });
    const start = startDoc(doors, "claude-code", { config: config("claude-code") });
    box.start(doors, start);
    box.runner("launch", start.run);
    await doors.ended_();
    const report = doors.posts.find((post) => post.path === "/signin")!;
    expect(report.headers["x-ta-runner"]).toBe("r".repeat(64));
    expect(report.body["signins"]).toEqual(expect.arrayContaining([
      { engine: "claude-code", signed_in: true }, { engine: "codex", signed_in: false }]));
    // Presence only: no credential, no status output.
    expect(JSON.stringify(report.body)).not.toContain("authMethod");
  });
});

async function waitFor(check: () => boolean, ms = 15_000): Promise<void> {
  const until = Date.now() + ms;
  while (!check()) {
    if (Date.now() > until) {
      throw new Error("timed out");
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}
