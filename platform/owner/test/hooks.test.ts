/**
 * A package's executable hooks, remote (tasks 5, H1; agent research §6.3-6.5), in workerd
 * with the real hooks Worker behind `HOOKS` and miniflare's Worker Loader: a `beforeTool`
 * hook that blocks, rewrites, times out (and blocks); an `afterTool` failure failing
 * closed; `onYield` and `beforeCompact` taking effect and falling back; a crash between the
 * decision and the tool's intent replaying the memo, not the hook; no call where no
 * package declares a point or a tool; no client planting a hook; `pi.extensions` still
 * refused beside hooks; the bundle one per owner, its id the owner's. The wrappers' rules
 * are also held one by one over a fake service (the owner-side deadline among them).
 */
import { runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT, type Context } from "@earendil-works/chord/context";
import {
  type AssistantMessage, fauxAssistantMessage, type FauxContentBlock, fauxToolCall,
  type Message,
} from "@earendil-works/pi-ai";
import {
  CompactionTask, type CompactionHooks, type Extension, GenerationTask, type GenerationHooks,
  type HookApi, type ToolExecutionResult, type ToolHooks, ToolTask,
} from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import { bundleId as workerBundleId } from "../../hooks/src/protocol";
import {
  bundleOf, callHook, failedClosed, HOOK_DECISION_MAX, HOOK_FAILED, HOOK_FAILED_CODE,
  HOOK_SLACK_MS, hookExtension, type HookParts, type HookReply, type HookRequest, memoName,
  OBSERVE_DEADLINE_MS,
} from "../src/hooks";
import type { Binding } from "../src/owner-do";
import { AGENTS_OWNER_ONLY } from "../src/paths";
import { PACKAGE_EXECUTABLE, PACKAGE_INVALID, PACKAGE_REFUSED } from "../src/resources";
import { MUSE, MCP, ledgerOn, signedIn } from "./ledger-support";
import { failure } from "./support";
import { type FauxOwnerDO, says, storeScript } from "./worker";

const BG = BACKGROUND_CONTEXT;
const P = "/workspace/.agents/packages";
const text = (value: string): Uint8Array => new TextEncoder().encode(value);

function calls(...blocks: FauxContentBlock[]): AssistantMessage {
  return fauxAssistantMessage(blocks, { stopReason: "toolUse" });
}

/** The tool results the model was shown, by tool name: their text, and whether in error. */
function results(messages: readonly Message[]): Record<string, { text: string; error: boolean }> {
  const out: Record<string, { text: string; error: boolean }> = {};
  for (const message of messages) {
    if (message.role === "toolResult") {
      out[message.toolName] = {
        text: message.content.map((part) => (part.type === "text" ? part.text : "")).join(""),
        error: message.isError,
      };
    }
  }
  return out;
}

/** An answer that repeats what the tools returned. */
const echo = (messages: Message[]): AssistantMessage => says(JSON.stringify(results(messages)));

async function until(check: () => Promise<boolean>, what: string): Promise<void> {
  for (let tries = 0; tries < 500; tries++) {
    if (await check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timed out waiting for ${what}`);
}

/** An owner of its own on the faux-model class, signed in, with a session for policy. */
async function rig() {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub = env.AGENT.getByName(owner);
  const as = (): Binding => ({ owner, caller: owner, origin: null });
  const at = signedIn();
  const { session } = await stub.sessionOpen({ ...as(), authenticated_at: at });
  const write = (path: string, content: string) =>
    stub.fileWrite(as(), { path, expected_etag: null, content: text(content) });
  return {
    owner, stub, as, write,
    /** A package of `hooks` entries, its modules' sources by path in the package. */
    pack: async (name: string, hooks: unknown[], modules: Record<string, string>,
                 extra: Record<string, unknown> = {}) => {
      await write(`${P}/${name}/package.json`,
                  JSON.stringify({ name, ...extra, tinyassets: { hooks } }));
      for (const [path, source] of Object.entries(modules)) {
        await write(`${P}/${name}/${path}`, source);
      }
    },
    /** The owner selects `packages`, with `hooks` policy when given. */
    select: async (packages: (string | { name: string; guard: boolean })[],
                   hooks?: Record<string, unknown>) => {
      const { generation, policy } = await stub.policyRead(as());
      return stub.policyWrite({ ...as(), session }, {
        expected_generation: generation,
        policy: { ...policy, packages: packages.map((name) => (typeof name === "string"
          ? `${P}/${name}` : { source: `${P}/${name.name}`, guard: name.guard })),
                  ...(hooks === undefined ? {} : { hooks }) } as never });
    },
    client: (): Binding => ({ owner, caller: owner, origin: MUSE, source: MCP,
                              authenticated_at: at }),
    in: <R>(act: (instance: FauxOwnerDO, state: DurableObjectState) => R | Promise<R>) =>
      runInDurableObject(stub, act),
  };
}

/** The owner's notices of `type`, their details. */
function noticesOf(instance: FauxOwnerDO, type: string): string[] {
  return instance.parts.notices.list().filter((notice) => notice.type === type)
    .map((notice) => notice.detail);
}

const GUARD = "export function beforeTool({ call }) {\n" +
  "  if (call.name !== 'write') return null;\n" +
  "  if (call.arguments.path.includes('secret')) return { block: 'no secrets here' };\n" +
  "  return { arguments: { ...call.arguments, content: call.arguments.content.toUpperCase() } };\n" +
  "}\n";

describe("a package's remote hooks", () => {
  it("block a call a beforeTool hook blocks and rewrite one it rewrites, the code sent " +
     "once on need_code; no call for a tool or point the package does not name", async () => {
    const r = await rig();
    await r.pack("guard", [{ module: "hooks/guard.js", points: ["beforeTool"],
                             tools: ["write"] }], { "hooks/guard.js": GUARD });
    await r.select(["guard"]);
    await r.write("/workspace/a.md", "alpha");
    const out = await r.in(async (instance) => {
      instance.script = [
        calls(fauxToolCall("write", { path: "/workspace/secret.md", content: "x" })),
        calls(fauxToolCall("write", { path: "/workspace/ok.md", content: "quiet" })),
        calls(fauxToolCall("read", { path: "/workspace/a.md" })),
        echo,
      ];
      const answer = await instance.harness.prompt("go");
      return { answer, calls: [...instance.probes.hookCalls],
               bundle: instance.agent.resources.index.bundle };
    });
    const shown = JSON.parse(out.answer.text!) as Record<string, { text: string }>;
    expect(shown["read"]!.text).toContain("alpha");
    expect(await r.stub.fileStat(r.as(), { path: "/workspace/secret.md" }))
      .toMatchObject({ entry: null });
    const ok = await r.stub.fileRead(r.as(), { path: "/workspace/ok.md" });
    expect(new TextDecoder().decode(ok.inline)).toBe("QUIET");
    // Two writes, each asked once; the first without the code, which the runner then asked
    // for; the read, which the entry does not name, never asked; no other point at all.
    expect(out.calls).toEqual([
      { point: "beforeTool", extension: "pkg:guard", sent: false },
      { point: "beforeTool", extension: "pkg:guard", sent: true },
      { point: "beforeTool", extension: "pkg:guard", sent: false },
    ]);
    expect(out.bundle!.id.startsWith(`${r.owner}/`)).toBe(true);
    const messages = (await r.in((instance) => instance.harness.messages()))
      .flatMap((entry) => (entry.model ?? []) as Message[]);
    expect(JSON.stringify(messages)).toContain("Tool call blocked: no secrets here");
  });

  it("blocks a call whose beforeTool hook times out, with a notice, at the policy's " +
     "deadline whatever its manifest asks", async () => {
    const r = await rig();
    await r.pack("slow", [{ module: "slow.js", points: ["beforeTool"], timeout_ms: 5_000 }], {
      "slow.js": "export function beforeTool() {\n" +
        "  return new Promise((resolve) => setTimeout(() => resolve(null), 3000));\n}\n" });
    await r.select(["slow"], { timeout_ms: 100 });
    const out = await r.in(async (instance) => {
      instance.script = [calls(fauxToolCall("write", { path: "/workspace/late.md",
                                                       content: "x" })), echo];
      const started = Date.now();
      const answer = await instance.harness.prompt("go");
      return { answer, took: Date.now() - started, notices: noticesOf(instance, HOOK_FAILED) };
    });
    const shown = JSON.parse(out.answer.text!) as Record<string, { text: string; error: boolean }>;
    expect(shown["write"]).toEqual({ error: true, text: expect.stringContaining(
      `Tool call blocked: ${HOOK_FAILED_CODE}: pkg:slow: timeout`) });
    expect(out.took).toBeLessThan(3_000);
    expect(out.notices).toEqual(["pkg:slow beforeTool: timeout"]);
    expect(await r.stub.fileStat(r.as(), { path: "/workspace/late.md" }))
      .toMatchObject({ entry: null });
  });

  it("fails an afterTool hook's failure closed, the tool's output withheld; redacts as " +
     "its hook says", async () => {
    const r = await rig();
    await r.pack("redact", [{ module: "redact.js", points: ["afterTool"], tools: ["read"] }], {
      "redact.js": "export function afterTool({ call, result }) {\n" +
        "  if (call.arguments.path.endsWith('boom.md')) throw new Error('the hook broke');\n" +
        "  return { output: [{ type: 'text', text: '[redacted]' }] };\n}\n" });
    await r.select(["redact"]);
    await r.write("/workspace/boom.md", "SECRET-ONE");
    await r.write("/workspace/fine.md", "SECRET-TWO");
    const out = await r.in(async (instance) => {
      instance.script = [calls(fauxToolCall("read", { path: "/workspace/boom.md" })),
                         (messages) => says(JSON.stringify(results(messages))),
      ];
      const first = await instance.harness.prompt("boom");
      instance.script = [calls(fauxToolCall("read", { path: "/workspace/fine.md" })),
                         (messages) => says(JSON.stringify(results(messages)))];
      const second = await instance.harness.prompt("fine", {
        session: (await instance.agent.thread("main", null, { kind: "run" })).thread_id });
      return { first, second, notices: noticesOf(instance, HOOK_FAILED) };
    });
    expect(out.first.text).not.toContain("SECRET-ONE");
    expect(JSON.parse(out.first.text!)).toEqual({ read: { error: true, text: expect.stringMatching(
      /^hook failed[^]*pkg:redact's afterTool hook failed, so the tool's result is withheld/) } });
    expect(out.second.text).not.toContain("SECRET-TWO");
    expect(JSON.parse(out.second.text!)).toEqual({ read: { text: "[redacted]", error: false } });
    expect(out.notices).toEqual(["pkg:redact afterTool: threw"]);
  });

  it("continues a run an onYield hook continues, and continues nothing when it fails",
     async () => {
    const r = await rig();
    await r.pack("more", [{ module: "more.js", points: ["onYield"] }], {
      "more.js": "export function onYield({ answer }) {\n" +
        "  const said = answer.content.map((part) => part.text ?? '').join('');\n" +
        "  if (said === 'first') return { continue: 'say the second' };\n" +
        "  if (said === 'broken') throw new Error('no');\n" +
        "  return null;\n}\n" });
    await r.select(["more"]);
    const out = await r.in(async (instance) => {
      instance.script = [says("first"), says("second")];
      const continued = await instance.harness.prompt("go");
      instance.script = [says("broken"), says("never")];
      const stopped = await instance.harness.prompt("again", {
        session: (await instance.agent.thread("main", null, { kind: "run" })).thread_id });
      return { continued, stopped, notices: noticesOf(instance, HOOK_FAILED) };
    });
    expect(out.continued.text).toBe("second");
    expect(JSON.stringify(out.continued.messages)).toContain("say the second");
    expect(out.stopped.text).toBe("broken");
    expect(out.notices).toEqual(["pkg:more onYield: threw"]);
  });

  it("summarizes a compaction as a beforeCompact hook says, and leaves pi's own summary " +
     "when it fails", async () => {
    const compacting = (messages: readonly Message[]) => JSON.stringify(messages)
      .includes("context summarization assistant");
    for (const [source, summary] of [
      ["export function beforeCompact() { return { summary: 'the hook summary' }; }",
       "the hook summary"],
      ["export function beforeCompact() { throw new Error('no'); }", "pi summary"],
    ] as const) {
      const r = await rig();
      await r.pack("brief", [{ module: "brief.js", points: ["beforeCompact"] }],
                   { "brief.js": source });
      await r.select(["brief"]);
      const out = await r.in(async (instance) => {
        instance.script = Array.from({ length: 6 }, () => (messages: Message[]) =>
          compacting(messages) ? says("## Goal\npi summary") : says("noted"));
        // Past the 20,000 tokens a compaction keeps, twice: the first is summarized.
        await instance.harness.prompt(`early ${"word ".repeat(20_000)}`);
        await instance.harness.prompt(`later ${"word ".repeat(20_000)}`);
        const pi = await instance.harness.pi();
        const root = await pi.root(BG);
        const task = await root.compact(undefined, BG);
        const done = await pi.waitForTask(task, BG);
        const entries = await instance.harness.messages();
        return { status: done.state.outcome.status,
                 compaction: JSON.stringify(entries.filter((entry) =>
                   entry.kind === "pi.compaction")),
                 notices: noticesOf(instance, HOOK_FAILED) };
      });
      expect(out.status).toBe("completed");
      expect(out.compaction).toContain(summary);
      expect(out.notices).toEqual(summary === "pi summary"
        ? ["pkg:brief beforeCompact: threw"] : []);
    }
  });

  it("replays a decision a crash cut off before the tool's intent from its memo, never " +
     "asking the hook again", async () => {
    const r = await rig();
    await r.pack("guard", [{ module: "hooks/guard.js", points: ["beforeTool"] }],
                 { "hooks/guard.js": GUARD });
    await r.select(["guard"]);
    const operation = await r.in(async (instance, state) => {
      storeScript(state, [calls(fauxToolCall("write", { path: "/workspace/kept.md",
                                                        content: "decided" })),
                          says("done")]);
      // The test's own gate runs after the package's: the decision is made and kept, and
      // the call waits here, before its intent, until the object dies.
      instance.probes.toolGate = new Promise(() => undefined);
      return (await instance.harness.submit("go")).operationId;
    });
    await until(() => r.in((instance) => instance.probes.gated.length === 1),
                "the decision, then the test's gate");
    expect(await r.in((instance) => instance.probes.hookCalls.length)).toBe(2);
    expect(await r.in((_instance, state) => state.abort("evicted mid-decision"))
      .then(() => "alive", (error: Error) => error.message)).toBe("evicted mid-decision");
    const fresh = env.AGENT.getByName(r.owner);
    expect(await runDurableObjectAlarm(fresh)).toBe(true);
    const out = await runInDurableObject(fresh, async (instance: FauxOwnerDO) => ({
      settled: await instance.harness.wait(operation),
      calls: [...instance.probes.hookCalls],
    }));
    expect([out.settled.status, out.settled.text]).toEqual(["done", "done"]);
    // The rerun found the decision in its memo: the hook was not asked on the new instance,
    // and the call ran with the arguments it rewrote.
    expect(out.calls).toEqual([]);
    const kept = await fresh.fileRead(r.as(), { path: "/workspace/kept.md" });
    expect(new TextDecoder().decode(kept.inline)).toBe("DECIDED");
  });

  it("are the owner's alone: no client plants a module, and one its writer is not the " +
     "owner's refuses the package", async () => {
    const r = await rig();
    await r.pack("guard", [{ module: "hooks/guard.js", points: ["beforeTool"] }],
                 { "hooks/guard.js": GUARD });
    await r.stub.outsideAgents(r.client());
    await runInDurableObject(r.stub, (_instance, state) => ledgerOn(state.storage).register(
      MUSE, MCP, { command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
                   recipe_id: null }));
    for (const path of [`${P}/guard/hooks/guard.js`, `${P}/guard/hooks/more.js`,
                        `${P}/guard/package.json`, `${P}/planted/package.json`]) {
      expect((await failure(() => r.stub.fileWrite(r.client(), {
        path, expected_etag: null, content: text("export function beforeTool() {}") })))
        .message).toBe(AGENTS_OWNER_ONLY);
    }
    // A module whose writer is a client (planted around the doors) refuses the package.
    await r.in((instance) => instance.parts.files.write(
      `${P}/guard/hooks/guard.js`, text("export function beforeTool() { return null; }"),
      { expected: instance.parts.files.stat(`${P}/guard/hooks/guard.js`)!.etag,
        writer: `client:${MUSE.client}` }));
    await r.select(["guard"]);
    const out = await r.in(async (instance) => {
      instance.script = [calls(fauxToolCall("write", { path: "/workspace/x.md",
                                                       content: "x" })), echo];
      await instance.harness.prompt("go");
      return { calls: instance.probes.hookCalls.length,
               refused: noticesOf(instance, PACKAGE_REFUSED),
               hooks: instance.agent.resources.index.hooks };
    });
    expect(out.calls).toBe(0);
    expect(out.hooks).toEqual([]);
    expect(out.refused).toEqual([`${P}/guard: ${PACKAGE_INVALID}: the hook module ` +
                                 `${P}/guard/hooks/guard.js is not written by the owner`]);
  });

  it("still refuses pi.extensions, and any in-process code, beside remote hooks", async () => {
    const r = await rig();
    await r.pack("both", [{ module: "hooks/guard.js", points: ["beforeTool"] }],
                 { "hooks/guard.js": GUARD, "index.ts": "export default () => {};" },
                 { pi: { extensions: ["./index.ts"] } });
    await r.pack("main", [{ module: "hooks/guard.js", points: ["beforeTool"] }],
                 { "hooks/guard.js": GUARD }, { main: "hooks/guard.js" });
    await r.pack("folder", [{ module: "hooks/guard.js", points: ["beforeTool"] }],
                 { "hooks/guard.js": GUARD, "extensions/x.ts": "export default 1;" });
    await r.pack("request", [{ module: "hooks/guard.js", points: ["beforeRequest"] }],
                 { "hooks/guard.js": GUARD });
    // A module of another package (which exists, the owner's): outside this one.
    await r.write(`${P}/other/hooks/guard.js`, GUARD);
    await r.pack("outside", [{ module: "../other/hooks/guard.js", points: ["beforeTool"] }],
                 {});
    await r.select(["both", "main", "folder", "request", "outside"]);
    const out = await r.in(async (instance) => {
      instance.script = [says("hi")];
      await instance.harness.prompt("hi");
      return { refused: noticesOf(instance, PACKAGE_REFUSED),
               hooks: instance.agent.resources.index.hooks };
    });
    expect(out.hooks).toEqual([]);
    const by = Object.fromEntries(out.refused.map((detail) => [detail.split(": ")[0],
                                                               detail.split(": ")[1]]));
    expect(by).toEqual({ [`${P}/both`]: PACKAGE_EXECUTABLE, [`${P}/main`]: PACKAGE_EXECUTABLE,
                         [`${P}/folder`]: PACKAGE_EXECUTABLE, [`${P}/request`]: PACKAGE_INVALID,
                         [`${P}/outside`]: PACKAGE_INVALID });
  });

  it("refuses a package whose modules would take the owner's bundle past a MiB", async () => {
    const r = await rig();
    const big = (n: number) => `// ${String(n).repeat(600_000)}\n` +
      "export function beforeTool() { return null; }\n";
    await r.pack("first", [{ module: "a.js", points: ["beforeTool"] }], { "a.js": big(1) });
    await r.pack("second", [{ module: "b.js", points: ["beforeTool"] }], { "b.js": big(2) });
    await r.select(["first", "second"]);
    const out = await r.in(async (instance) => {
      instance.script = [says("hi")];
      await instance.harness.prompt("hi");
      return { refused: noticesOf(instance, PACKAGE_REFUSED),
               hooks: instance.agent.resources.index.hooks.map((hooked) => hooked.extension) };
    });
    expect(out.hooks).toEqual(["pkg:first"]);
    expect(out.refused).toEqual([`${P}/second: ${PACKAGE_INVALID}: its hook modules take the ` +
                                 "owner's bundle past 1048576 bytes"]);
  });

  it("makes one bundle per owner, its id the owner's and its code's, as the hooks Worker " +
     "computes it", async () => {
    const ids: string[] = [];
    for (let at = 0; at < 2; at++) {
      const r = await rig();
      await r.pack("guard", [{ module: "hooks/guard.js", points: ["beforeTool"] }],
                   { "hooks/guard.js": GUARD });
      await r.select(["guard"]);
      const bundle = await r.in(async (instance) => {
        instance.script = [says("hi")];
        await instance.harness.prompt("hi");
        return instance.agent.resources.index.bundle!;
      });
      expect(bundle.id).toBe(await workerBundleId(r.owner, bundle.modules));
      expect(bundle.id.startsWith(`${r.owner}/`)).toBe(true);
      ids.push(bundle.id);
    }
    expect(ids[0]).not.toBe(ids[1]);
    expect(ids[0]!.split("/")[1]).toBe(ids[1]!.split("/")[1]);
  });

  it("runs a guard package's hooks after every other package's, so a later rewrite " +
     "cannot follow its approval", async () => {
    const r = await rig();
    await r.pack("fence", [{ module: "fence.js", points: ["beforeTool"], tools: ["write"] }], {
      "fence.js": "export function beforeTool({ call }) {\n" +
        "  return call.arguments.path.includes('secret') ? { block: 'no secrets' } : null;\n}" });
    await r.pack("loose", [{ module: "loose.js", points: ["beforeTool"], tools: ["write"] }], {
      "loose.js": "export function beforeTool({ call }) {\n" +
        "  return { arguments: { ...call.arguments, path: '/workspace/secret.md' } };\n}" });
    const attempt = (session?: string) => r.in(async (instance) => {
      instance.probes.hookCalls.length = 0;
      instance.script = [calls(fauxToolCall("write", { path: "/workspace/ok.md",
                                                       content: "x" })), echo];
      await instance.agent.resources.settled();
      const where = session ?? (await instance.agent.thread("main", null, { kind: "run" }))
        .thread_id;
      const answer = await instance.harness.prompt("go", { session: where });
      const order = instance.probes.hookCalls.map((call) => call.extension);
      return { order: order.filter((name, at) => order.indexOf(name) === at),
               write: (JSON.parse(answer.text!) as Record<string, { text: string;
                                                                     error: boolean }>)["write"] };
    });
    // In selection order the fence approves, and the later rewrite lands where it refused.
    await r.select(["fence", "loose"]);
    const plain = await attempt();
    expect(plain.order).toEqual(["pkg:fence", "pkg:loose"]);
    expect(plain.write!.error).toBe(false);
    expect((await r.stub.fileStat(r.as(), { path: "/workspace/secret.md" })).entry)
      .not.toBeNull();
    // In the guard position the fence runs last, sees the rewrite, and blocks it.
    await r.select([{ name: "fence", guard: true }, "loose"]);
    const guarded = await attempt();
    expect(guarded.order).toEqual(["pkg:loose", "pkg:fence"]);
    expect(guarded.write).toEqual({ error: true, text: expect.stringContaining(
      "Tool call blocked: no secrets") });
    for (const guard of ["yes", 1]) {
      expect((await failure(() => r.select([{ name: "fence", guard: guard as never }])))
        .message).toMatch(/invalid packages policy/);
    }
  });

  it("removes a package's wrappers when it is no longer selected", async () => {
    const r = await rig();
    await r.pack("guard", [{ module: "hooks/guard.js", points: ["beforeTool"] }],
                 { "hooks/guard.js": GUARD });
    await r.select(["guard"]);
    await r.in(async (instance) => {
      instance.script = [says("hi")];
      await instance.harness.prompt("hi");
    });
    await r.select([]);
    const seen = await r.in(async (instance) => {
      await instance.agent.resources.settled();
      return { hooks: instance.agent.resources.index.hooks.length,
               builds: instance.agent.resources.builds,
               notices: instance.parts.notices.list().map((notice) => notice.detail) };
    });
    expect(seen).toEqual({ hooks: 0, builds: 2, notices: [] });
    const out = await r.in(async (instance) => {
      instance.script = [calls(fauxToolCall("write", { path: "/workspace/free.md",
                                                       content: "free" })), echo];
      await instance.harness.prompt("go", {
        session: (await instance.agent.thread("main", null, { kind: "run" })).thread_id });
      return instance.probes.hookCalls.length;
    });
    expect(out).toBe(0);
  }, 20_000);
});

describe("the owner's hooks policy", () => {
  it("bounds a hook's deadline and CPU, and takes code mode as a switch", async () => {
    const r = await rig();
    for (const hooks of [{ timeout_ms: 50 }, { timeout_ms: 20_000 }, { cpu_ms: 10 },
                         { cpu_ms: 2_000 }, { codemode: "yes" }, { codemode: 1 },
                         { other: true }, { timeout_ms: 1.5 }]) {
      expect((await failure(() => r.select([], hooks))).message)
        .toMatch(/^invalid hooks policy/);
    }
    await r.select([], { timeout_ms: 100, cpu_ms: 1_000, codemode: true });
    await r.select([], { timeout_ms: 10_000, cpu_ms: 50, codemode: false });
    expect((await r.stub.policyRead(r.as())).policy.hooks)
      .toEqual({ timeout_ms: 10_000, cpu_ms: 50, codemode: false });
  });
});

// -- The wrappers' rules, one by one, over a fake service.

const BUNDLE = bundleOf("alice", { "/m.js": "export {}" });

type Answer = HookReply | "hang" | { readonly after: number; readonly reply: HookReply };

/** Wrapper parts over a service that answers `answers` in turn; what it was asked. */
function fake(answers: Answer[], options: { timeout?: number; bound?: boolean } = {}) {
  const asked: HookRequest[] = [];
  const notices: string[] = [];
  const parts: HookParts = {
    service: () => (options.bound === false ? undefined : {
      run: async (request) => {
        asked.push(request);
        const answer = answers.shift() ?? { ok: true, result: null };
        if (answer === "hang") {
          return new Promise<HookReply>(() => undefined);
        }
        if ("after" in answer) {
          await new Promise((resolve) => setTimeout(resolve, answer.after));
          return answer.reply;
        }
        return answer;
      },
      codemodeExecute: () => { throw new Error("no code mode here"); },
    }),
    owner: () => "alice",
    settings: () => ({ timeout_ms: options.timeout ?? 2_000, cpu_ms: 75 }),
    notice: (type, detail) => notices.push(`${type} ${detail}`),
    authority: async () => "owner",
  };
  return { parts, asked, notices };
}

/**
 * A hook API with its memos in memory; `memo` says which of its reads or stores reject, as
 * a store that fails (`SQLITE_TOOBIG` past 2 MB, a transient error) rejects them.
 */
function api(memo: "ok" | "reject-read" | "reject-store" = "ok"):
    HookApi & { memos: Map<string, unknown> } {
  const memos = new Map<string, unknown>();
  return {
    taskId: "7" as never, conversationId: 0 as never, models: undefined as never, memos,
    snapshot: undefined as never, snapshotAsOf: undefined as never,
    memo: (async (name: string, ...rest: unknown[]) => {
      if (rest.length === 2) {
        if (memo === "reject-store") {
          throw new Error("SQLITE_TOOBIG: string or blob too big");
        }
        if (!memos.has(name)) {
          memos.set(name, rest[0]);
        }
        return memos.get(name);
      }
      if (memo === "reject-read") {
        throw new Error("the store's read failed");
      }
      return memos.get(name);
    }) as never,
  } as HookApi & { memos: Map<string, unknown> };
}

/** pi's `hooks.each` for `afterTool`: a hook that throws is reported and skipped. */
async function piKeeps(hook: ToolHooks["afterTool"], memo: HookApi): Promise<ToolExecutionResult> {
  try {
    return (await hook(CALL, RESULT, memo, ctx)) ?? RESULT;
  } catch {
    return RESULT;  // skipped, as pi skips it: the unredacted result is committed
  }
}

/** A pi task's name, as a hook registration names it. */
function taskName(task: unknown): string {
  return (task as { definition: { name: string } }).definition.name;
}

function handlers<T>(extension: Extension, task: unknown): T {
  return extension.hooks!.find((registration) => registration.task === taskName(task))!
    .handlers as T;
}

const CALL = { type: "toolCall" as const, id: "call-1", name: "write",
               arguments: { path: "/workspace/x.md", content: "x" } };
const RESULT: ToolExecutionResult = { output: [{ type: "text", text: "the secret" }] };
const ctx = BG as Context;

function tools(parts: HookParts, points = ["beforeTool", "afterTool"], only?: string[]) {
  return handlers<ToolHooks>(hookExtension(parts, BUNDLE, { extension: "pkg:p", entries: [
    { module: "/m.js", points, ...(only === undefined ? {} : { tools: only }) }] }), ToolTask);
}

describe("the remote wrappers' rules", () => {
  it("sends the code only once the runner asks for it, within one deadline", async () => {
    const { parts, asked } = fake([{ ok: false, error: "need_code" }, { ok: true, result: 3 }]);
    expect(await callHook(parts, BUNDLE, { point: "beforeTool", extension: "pkg:p",
      module: "/m.js", input: {}, context: { conversation_id: 0, task_id: "7",
                                             authority: "owner" }, deadline_ms: 500 }))
      .toBe(3);
    expect(asked.map((request) => request.bundle.modules !== undefined)).toEqual([false, true]);
    expect(asked[0]).toMatchObject({ owner: "alice", bundle: { id: BUNDLE.id }, cpu_ms: 75 });
    // A second need_code is a failure, never a third call.
    const again = fake([{ ok: false, error: "need_code" }, { ok: false, error: "need_code" }]);
    expect((await failure(() => callHook(again.parts, BUNDLE, { point: "beforeTool",
      extension: "pkg:p", module: "/m.js", input: {}, context: { conversation_id: 0,
        task_id: "7", authority: "owner" }, deadline_ms: 500 }))).message).toBe("need_code");
    expect(again.asked).toHaveLength(2);
  });

  it("waits a hook's deadline and its slack, and no longer", async () => {
    const request = { point: "beforeTool", extension: "pkg:p", module: "/m.js", input: {},
                      context: { conversation_id: 0, task_id: "7", authority: "owner" },
                      deadline_ms: 100 };
    expect(HOOK_SLACK_MS).toBe(250);
    const inside = fake([{ after: 250, reply: { ok: true, result: "in time" } }]);
    expect(await callHook(inside.parts, BUNDLE, request)).toBe("in time");
    const hung = fake(["hang"]);
    const started = Date.now();
    expect((await failure(() => callHook(hung.parts, BUNDLE, request))).message)
      .toBe("timeout");
    const took = Date.now() - started;
    expect(took).toBeGreaterThanOrEqual(340);
    expect(took).toBeLessThan(1_500);
  });

  it.each([
    ["it times out", ["hang"] as Answer[], "timeout"],
    ["it throws", [{ ok: false, error: "threw", message: "no" }] as Answer[], "threw"],
    ["it answers out of shape", [{ ok: true, result: { block: "x", arguments: {} } }] as Answer[],
     "invalid"],
    ["it answers a block that is no text", [{ ok: true, result: { block: 7 } }] as Answer[],
     "invalid"],
    ["the runner refuses the request", [{ ok: false, error: "invalid" }] as Answer[], "invalid"],
  ])("blocks a beforeTool call when %s, the decision kept", async (_what, answers, code) => {
    const { parts, notices, asked } = fake(answers, { timeout: 100 });
    const hooks = tools(parts);
    const memo = api();
    const decided = await hooks.beforeTool(CALL, memo, ctx);
    expect(decided).toEqual({ block: expect.stringMatching(
      new RegExp(`^${HOOK_FAILED_CODE}: pkg:p: ${code}`)) });
    expect(notices).toEqual([`${HOOK_FAILED} pkg:p beforeTool: ${code}`]);
    // A rerun takes the kept decision: the hook is not asked again.
    expect(await hooks.beforeTool(CALL, memo, ctx)).toEqual(decided);
    expect(asked).toHaveLength(1);
    expect([...memo.memos.keys()]).toEqual([memoName("pkg:p", "beforeTool", "call-1")]);
  });

  it("blocks a beforeTool call when no hooks Worker is bound", async () => {
    const { parts } = fake([], { bound: false });
    expect(await tools(parts).beforeTool(CALL, api(), ctx))
      .toEqual({ block: expect.stringContaining(`${HOOK_FAILED_CODE}: pkg:p: unbound`) });
  });

  it("takes a beforeTool block or rewrite, and passes a call its entry does not name " +
     "without asking", async () => {
    const blocking = fake([{ ok: true, result: { block: "no" } }]);
    expect(await tools(blocking.parts).beforeTool(CALL, api(), ctx)).toEqual({ block: "no" });
    const rewriting = fake([{ ok: true, result: { arguments: { path: "/y.md" } } }]);
    expect(await tools(rewriting.parts).beforeTool(CALL, api(), ctx))
      .toEqual({ arguments: { path: "/y.md" } });
    const passing = fake([{ ok: true, result: null }]);
    expect(await tools(passing.parts).beforeTool(CALL, api(), ctx)).toBeUndefined();
    const unnamed = fake([{ ok: true, result: { block: "no" } }]);
    expect(await tools(unnamed.parts, ["beforeTool"], ["bash"]).beforeTool(CALL, api(), ctx))
      .toBeUndefined();
    expect(unnamed.asked).toEqual([]);
  });

  it.each([
    ["it throws", [{ ok: false, error: "threw" }] as Answer[]],
    ["it times out", ["hang"] as Answer[]],
    ["it answers a control", [{ ok: true, result: { output: [], control: { x: 1 } } }] as Answer[]],
    ["it answers an image", [{ ok: true, result: { output: [{ type: "image", data: "x",
                                                                mimeType: "image/png" }] } }] as Answer[]],
    ["it answers a part that is no text", [{ ok: true, result: { output: [
      { type: "thinking", text: "x" }] } }] as Answer[]],
  ])("fails an afterTool result closed when %s", async (_what, answers) => {
    const { parts, notices } = fake(answers, { timeout: 100 });
    const replaced = await tools(parts).afterTool(CALL, RESULT, api(), ctx);
    expect(replaced).toEqual(failedClosed("pkg:p"));
    expect(JSON.stringify(replaced)).not.toContain("the secret");
    expect(notices).toHaveLength(1);
  });

  it("keeps or replaces an afterTool result as its hook says, the decision by result",
     async () => {
    const { parts, asked } = fake([{ ok: true, result: null },
                                   { ok: true, result: { output: [{ type: "text",
                                                                    text: "[redacted]" }] } }]);
    const hooks = tools(parts);
    const memo = api();
    expect(await hooks.afterTool(CALL, RESULT, memo, ctx)).toBeUndefined();
    expect(await hooks.afterTool(CALL, RESULT, memo, ctx)).toBeUndefined();
    // A rerun whose tool answered otherwise is a new decision.
    expect(await hooks.afterTool(CALL, { output: [{ type: "text", text: "other" }] }, memo,
                                 ctx)).toEqual({ output: [{ type: "text", text: "[redacted]" }],
                                                 diagnostics: [], isError: false });
    expect(asked).toHaveLength(2);
    expect(asked[0]!.input).toEqual({
      call: { id: "call-1", name: "write", arguments: CALL.arguments },
      result: { output: [{ type: "text", text: "the secret" }], isError: false,
                diagnostics: [] } });
  });

  it("continues nothing when onYield fails, and leaves pi's summary when beforeCompact " +
     "does; observe-only points only report", async () => {
    const entries = [{ module: "/m.js", points: ["onYield", "afterResponse", "afterTools",
                                                 "beforeCompact"] }];
    const { parts, notices, asked } = fake([
      { ok: false, error: "threw" }, { ok: false, error: "threw" },
      { ok: false, error: "threw" }, { ok: false, error: "threw" }]);
    const extension = hookExtension(parts, BUNDLE, { extension: "pkg:p", entries });
    const generation = handlers<GenerationHooks>(extension, GenerationTask);
    const compaction = handlers<CompactionHooks>(extension, CompactionTask);
    const answer = says("done");
    expect(await generation.onYield(answer, api(), ctx)).toBeUndefined();
    expect(await generation.afterResponse(answer, api(), ctx)).toBeUndefined();
    expect(await generation.afterTools(1 as never, [2 as never], api(), ctx)).toBeUndefined();
    expect(await compaction.beforeCompact({ reason: "manual" as never, entries: [],
                                            messages: [], firstKept: 3 as never }, api(), ctx))
      .toBeUndefined();
    expect(asked.map((request) => request.point))
      .toEqual(["onYield", "afterResponse", "afterTools", "beforeCompact"]);
    expect(notices).toEqual(["onYield", "afterResponse", "afterTools", "beforeCompact"]
      .map((point) => `${HOOK_FAILED} pkg:p ${point}: threw`));
    const ok = fake([{ ok: true, result: { continue: "more" } },
                     { ok: true, result: { decline: true } }]);
    const fine = hookExtension(ok.parts, BUNDLE, { extension: "pkg:p", entries });
    expect(await handlers<GenerationHooks>(fine, GenerationTask).onYield(answer, api(), ctx))
      .toEqual({ continue: "more" });
    expect(await handlers<CompactionHooks>(fine, CompactionTask).beforeCompact({
      reason: "manual" as never, entries: [], messages: [], firstKept: 3 as never }, api(),
      ctx)).toEqual({ decline: true });
  });

  it.each(["reject-read", "reject-store"] as const)(
    "never fails an afterTool redaction open when the memo's %s (the reviewer's probe)",
    async (memo) => {
      const redacting = fake([{ ok: true, result: { output: [{ type: "text",
                                                               text: "[redacted]" }] } }]);
      const out = await piKeeps(tools(redacting.parts).afterTool, api(memo));
      expect(out).toEqual(failedClosed("pkg:p"));
      expect(redacting.notices).toEqual([`${HOOK_FAILED} pkg:p afterTool: threw`]);
    });

  it("fails closed, and blocks, on an error before any decision is asked", async () => {
    const { parts, asked } = fake([{ ok: true, result: null }]);
    const hooks = tools(parts);
    // A result pi could never hand a hook: its output is no list.
    const broken = { output: 7 } as unknown as ToolExecutionResult;
    expect(await piKeeps(async (call, _result, memo, context) =>
      hooks.afterTool(call, broken, memo, context), api())).toEqual(failedClosed("pkg:p"));
    const throwing = Object.defineProperty({ ...CALL }, "name", {
      get: () => { throw new Error("no name"); } }) as typeof CALL;
    expect(await hooks.beforeTool(throwing, api(), ctx)).toEqual({ block: expect.stringContaining(
      `${HOOK_FAILED_CODE}: pkg:p: no name`) });
    expect(asked).toEqual([]);
  });

  it("keeps the failure a failed memo read decided, so a rerun replays it", async () => {
    const { parts, asked } = fake([{ ok: true, result: null }]);
    const memo = api("reject-read");
    expect(await tools(parts).afterTool(CALL, RESULT, memo, ctx)).toEqual(failedClosed("pkg:p"));
    expect([...memo.memos.values()]).toEqual([{ result: failedClosed("pkg:p") }]);
    expect(asked).toEqual([]);  // no hook asked once the read failed
  });

  it("never fails an afterTool hook open when the notice itself throws", async () => {
    const { parts } = fake([{ ok: false, error: "threw" }]);
    const throwing: HookParts = { ...parts, notice: () => { throw new Error("gate closed"); } };
    expect(await piKeeps(tools(throwing).afterTool, api())).toEqual(failedClosed("pkg:p"));
  });

  it.each(["reject-read", "reject-store"] as const)(
    "falls back by each point's rule when the memo's %s", async (memo) => {
      const entries = [{ module: "/m.js", points: ["beforeTool", "afterTool", "onYield",
                                                   "afterResponse", "afterTools",
                                                   "beforeCompact"] }];
      const ok: Answer[] = [{ ok: true, result: { arguments: { path: "/y.md" } } },
                            { ok: true, result: null }, { ok: true, result: { continue: "x" } },
                            { ok: true, result: null }, { ok: true, result: null },
                            { ok: true, result: { summary: "s" } }];
      const { parts, notices } = fake(ok);
      const extension = hookExtension(parts, BUNDLE, { extension: "pkg:p", entries });
      const tool = handlers<ToolHooks>(extension, ToolTask);
      const generation = handlers<GenerationHooks>(extension, GenerationTask);
      const compaction = handlers<CompactionHooks>(extension, CompactionTask);
      expect(await tool.beforeTool(CALL, api(memo), ctx)).toEqual({ block: expect.stringMatching(
        new RegExp(`^${HOOK_FAILED_CODE}: pkg:p: `)) });
      expect(await tool.afterTool(CALL, RESULT, api(memo), ctx)).toEqual(failedClosed("pkg:p"));
      expect(await generation.onYield(says("done"), api(memo), ctx)).toBeUndefined();
      expect(await generation.afterResponse(says("done"), api(memo), ctx)).toBeUndefined();
      expect(await generation.afterTools(1 as never, [], api(memo), ctx)).toBeUndefined();
      expect(await compaction.beforeCompact({ reason: "manual" as never, entries: [],
                                              messages: [], firstKept: 3 as never },
                                            api(memo), ctx)).toBeUndefined();
      expect(notices).toHaveLength(6);
    });

  it("fails a replaced result or rewrite past 256 KiB closed, never memoizing it", async () => {
    expect(HOOK_DECISION_MAX).toBe(262_144);
    const huge = "x".repeat(HOOK_DECISION_MAX);
    const replacing = fake([{ ok: true, result: { output: [{ type: "text", text: huge }] } }]);
    const memo = api();
    expect(await tools(replacing.parts).afterTool(CALL, RESULT, memo, ctx))
      .toEqual(failedClosed("pkg:p"));
    expect(JSON.stringify([...memo.memos.values()]).length).toBeLessThan(2_000);
    const rewriting = fake([{ ok: true, result: { arguments: { content: huge } } }]);
    expect(await tools(rewriting.parts).beforeTool(CALL, api(), ctx))
      .toEqual({ block: expect.stringContaining(`${HOOK_FAILED_CODE}: pkg:p: invalid`) });
    // Just inside the bound, a replacement stands.
    const inside = "y".repeat(HOOK_DECISION_MAX - 200);
    const fits = fake([{ ok: true, result: { output: [{ type: "text", text: inside }] } }]);
    expect(await tools(fits.parts).afterTool(CALL, RESULT, api(), ctx))
      .toMatchObject({ output: [{ type: "text", text: inside }], isError: false });
  });

  it("gives an observe-only point a short deadline of its own", async () => {
    expect(OBSERVE_DEADLINE_MS).toBe(250);
    const { parts, asked } = fake(["hang"]);
    const extension = hookExtension(parts, BUNDLE, { extension: "pkg:p", entries: [
      { module: "/m.js", points: ["afterResponse"] }] });
    const started = Date.now();
    await handlers<GenerationHooks>(extension, GenerationTask).afterResponse(says("x"), api(),
                                                                             ctx);
    expect(Date.now() - started).toBeLessThan(1_000);
    expect(asked[0]!.deadline_ms).toBe(OBSERVE_DEADLINE_MS);
  });

  it("registers no hook of a point no entry declares", () => {
    const { parts } = fake([]);
    const only = hookExtension(parts, BUNDLE, { extension: "pkg:p", entries: [
      { module: "/m.js", points: ["beforeTool"] }] });
    expect(only.hooks!.map((registration) => registration.task)).toEqual([taskName(ToolTask)]);
    expect(Object.keys(handlers<object>(only, ToolTask))).toEqual(["beforeTool"]);
  });

  it("names a bundle by its owner and its code", () => {
    const modules = { "/m.js": "export {}" };
    expect(bundleOf("alice", modules).id).not.toBe(bundleOf("bob", modules).id);
    expect(bundleOf("alice", modules).id).toBe(BUNDLE.id);
    expect(bundleOf("alice", { "/m.js": "export { }" }).id).not.toBe(BUNDLE.id);
    expect(BUNDLE.id).toMatch(/^alice\/[0-9a-f]{64}$/);
  });
});
