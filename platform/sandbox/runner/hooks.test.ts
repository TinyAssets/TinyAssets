/**
 * The agent's own hooks' dispatcher (D5): each matching hook run on the event as JSON, its
 * answer read by the format's rule for that event (a block on `pre_tool` and `prompt`, context
 * on `session_start` and `prompt`, nothing else), every failure on a blocking event a block,
 * and nothing a hook answers ever an allow or a rewrite.
 */
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { agentHooks, type HookEvent } from "../../contract/src/agent-definition";
import { served } from "../../sources/src/served";
import type { Decide } from "../../sources/src/schema";

import { runHooks, type RunHooks } from "./engine";
import { dispatch, unrunnable } from "./hooks";

let dir = "";
const CLAUDE = served().get("claude-code")!.cli.decide as Exclude<Decide, { unsupported: string }>;
const ENV = { PATH: process.env["PATH"] };

/** An executable `name` in the test's directory, its body a shell script. */
function script(name: string, body: string): string {
  const path = join(dir, name);
  writeFileSync(path, `#!/bin/sh\n${body}\n`);
  chmodSync(path, 0o755);
  return `./${name}`;
}

/**
 * The run's file of `event`'s hooks for `hooks.json` text, on Claude Code's tool names, in the
 * test's directory.
 */
function file(event: HookEvent, hooks: readonly unknown[]): RunHooks {
  return runHooks("claude-code", CLAUDE, dir,
                  agentHooks(JSON.stringify({ hooks: { [event]: hooks } })).hooks)[event]!;
}

const BASH = { session_id: "s-1", hook_event_name: "PreToolUse", tool_name: "Bash",
               tool_input: { command: "rm -rf build" } };
const PROMPT = { session_id: "s-1", hook_event_name: "UserPromptSubmit", prompt: "ship it" };

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), "ta-hooks-"));
});

afterAll(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("the dispatcher", () => {
  it("hands each hook the event as JSON, its tool's kind by the CLI's own names", async () => {
    const seen = join(dir, "seen.json");
    const hook = script("seen.sh", `cat > ${seen}`);
    expect(await dispatch(file("pre_tool", [{ run: [hook] }]), "pre_tool", BASH, ENV))
      .toEqual({ code: 0, out: "", err: "" });
    expect(JSON.parse(readFileSync(seen, "utf8"))).toEqual({
      event: "pre_tool", engine: "claude-code", session_id: "s-1",
      tool: { kind: "shell", name: "Bash", input: { command: "rm -rf build" } } });
    await dispatch(file("prompt", [{ run: [hook] }]), "prompt", PROMPT, ENV);
    expect(JSON.parse(readFileSync(seen, "utf8"))).toEqual({
      event: "prompt", engine: "claude-code", session_id: "s-1", prompt: "ship it" });
  });

  it("blocks by a hook's exit 2, its stderr the reason, or by its block answer", async () => {
    const exit2 = script("exit2.sh", "echo 'no rm in build' >&2; exit 2");
    const said = script("said.sh", `echo '{"block": "not on Fridays"}'`);
    expect(await dispatch(file("pre_tool", [{ run: [exit2] }, { run: [said] }]), "pre_tool",
                          BASH, ENV))
      .toEqual({ code: 2, out: "", err: `the agent's pre_tool hook ${exit2} blocked it: no rm ` +
        `in build\nthe agent's pre_tool hook ${said} blocked it: not on Fridays\n` });
    expect((await dispatch(file("prompt", [{ run: [said] }]), "prompt", PROMPT, ENV)).code)
      .toBe(2);
  });

  it("adds context where the event takes it, under the engine's own name of the event",
     async () => {
    const context = script("context.sh", `echo '{"context": "the branch is main"}'`);
    for (const [event, input, name] of [["prompt", PROMPT, "UserPromptSubmit"],
      ["session_start", { hook_event_name: "SessionStart" }, "SessionStart"]] as const) {
      expect(await dispatch(file(event, [{ run: [context] }, { run: [context] }]), event,
                            input, ENV))
        .toEqual({ code: 0, err: "", out: `${JSON.stringify({ hookSpecificOutput: {
          hookEventName: name,
          additionalContext: "the branch is main\n\nthe branch is main" } })}\n` });
    }
  });

  it("drops what an event does not take: a block after the call, context on a call",
     async () => {
    const exit2 = script("late.sh", "echo 'too late' >&2; exit 2");
    const context = script("ctx.sh", `echo '{"context": "x", "block": ""}'`);
    expect(await dispatch(file("post_tool", [{ run: [exit2] }]), "post_tool", BASH, ENV))
      .toEqual({ code: 0, out: "", err: "" });
    expect(await dispatch(file("stop", [{ run: [exit2] }]), "stop", {}, ENV))
      .toEqual({ code: 0, out: "", err: "" });
    expect(await dispatch(file("pre_tool", [{ run: [context] }]), "pre_tool", BASH, ENV))
      .toEqual({ code: 0, out: "", err: "" });
  });

  it("drops an allow and a rewrite, in any engine's words, so the decide hook decides",
     async () => {
    const allow = script("allow.sh", `echo '${JSON.stringify({
      allow: true, input: { command: "true" }, decision: "allow",
      hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "allow",
                            updatedInput: { command: "rm -rf /" }, tool_input: { a: 1 } } })}'`);
    for (const event of ["pre_tool", "prompt"] as const) {
      expect(await dispatch(file(event, [{ run: [allow] }]), event,
                            event === "prompt" ? PROMPT : BASH, ENV))
        .toEqual({ code: 0, out: "", err: "" });
    }
    // Gemini takes a hook's stderr as its answer when its stdout is empty.
    const said = script("stderr.sh", `echo '${JSON.stringify({
      hookSpecificOutput: { tool_input: { command: "rm -rf /" } } })}' >&2; exit 2`);
    const blocked = await dispatch(file("pre_tool", [{ run: [said] }]), "pre_tool", BASH, ENV);
    expect(blocked.code).toBe(2);
    expect(() => JSON.parse(blocked.err)).toThrow();
  });

  it("blocks when a hook on a blocking event fails, and says why; elsewhere it exits 1",
     async () => {
    const slow = script("slow.sh", "sleep 30");
    const one = script("one.sh", "echo broken >&2; exit 1");
    const prose = script("prose.sh", "echo looks fine");
    const failing: [string, RegExp][] = [
      [slow, /ran past its 1 s/], [one, /exited 1: broken/], [prose, /answered no JSON object/],
      ["./missing.sh", /could not start/]];
    for (const [run, why] of failing) {
      const blocked = await dispatch(file("pre_tool", [{ run: [run], timeout_s: 1 }]),
                                     "pre_tool", BASH, ENV);
      expect([blocked.code, blocked.out]).toEqual([2, ""]);
      expect(blocked.err).toMatch(/^the agent's pre_tool hook \S+ failed, so it holds: /);
      expect(blocked.err).toMatch(why);
    }
    const started = Date.now();
    const late = await dispatch(file("session_start", [{ run: [slow], timeout_s: 1 }]),
                                "session_start", {}, ENV);
    expect(Date.now() - started).toBeLessThan(10_000);
    expect([late.code, late.out]).toEqual([1, ""]);
    expect(late.err).toMatch(/^the agent's session_start hook \S+ failed: ran past its 1 s/);
  });

  it("runs a tool event's hook only on the calls its match names", async () => {
    const count = join(dir, "count");
    const hook = script("count.sh", `echo x >> ${count}`);
    const matched = file("pre_tool", [{ match: { kinds: ["shell"], tools: ["mcp:gmail:send_*"] },
                                        run: [hook] }]);
    for (const tool_name of ["Bash", "PowerShell", "Read", "mcp__gmail__send_mail",
                             "mcp__gmail__read", "Bashful"]) {
      await dispatch(matched, "pre_tool", { ...BASH, tool_name }, ENV);
    }
    expect(readFileSync(count, "utf8")).toBe("x\nx\nx\n");
  });

  it("checks at the start that each hook's program is there to run", () => {
    const there = script("there.sh", "exit 0");
    writeFileSync(join(dir, "plain.sh"), "not executable");
    expect(unrunnable("pre_tool", file("pre_tool", [{ run: [there] }, { run: ["sh", "-c", "x"] }]),
                      ENV)).toEqual([]);
    expect(unrunnable("prompt",
                      file("prompt", [{ run: ["./plain.sh"] }, { run: ["no-such-program"] }]),
                      ENV)).toEqual([
      "the agent's prompt hook ./plain.sh is no program it can run",
      "the agent's prompt hook no-such-program is no program it can run"]);
  });
});
