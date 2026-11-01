/**
 * Code mode (tasks 5, H1; pi audit F8), in workerd with the real hooks Worker behind
 * `HOOKS`: off until the owner's policy turns it on; reached only as `ta call codemode`,
 * its VM in the hooks Worker; `run_code` held explicitly (a scoped client without it, and
 * a full grant, refused); a script's `tools.*` held to its caller's authority (a scoped
 * client's read runs, its shell and write are refused); `tools.model_complete` reserved and
 * charged to the turn, counted in `pi.usage`, held to the turn's caps, through the gate and
 * the packages' hooks, never rerun; a script that starts a script refused, and `codemode`
 * kept out of the scripts' section. And P11's local part: pi's `RemoteCodemodeSandbox` over the service
 * binding, `exchange` an RPC function, a nested call's round trip. CPU limits and isolation
 * are not shown by workerd: owed to H2's live probe.
 */
import { runInDurableObject } from "cloudflare:test";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { env } from "cloudflare:workers";
import {
  type AssistantMessage, fauxAssistantMessage, type FauxContentBlock, fauxToolCall,
  type Message,
} from "@earendil-works/pi-ai";
import {
  type CodemodeRemote, type CodemodeRemoteMessage, RemoteCodemodeSandbox,
} from "@earendil-works/pi-codemode/portable";
import type { JsonObject } from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import { CODEMODE_NESTED, NO_SHELL, NOT_GRANTED, type Stamped } from "../src/authority";
import { CODEMODE_EXTENSION, codemodeExtension } from "../src/codemode";
import type { Origin } from "../src/ledger";
import type { Binding } from "../src/owner-do";
import { MODEL_COMPLETE, UNKNOWN_CAPABILITY } from "../src/ta";
import { gated } from "./gateway-support";
import { DOT, ledgerOn, MCP, MUSE, signedIn } from "./ledger-support";
import { type FauxOwnerDO, says } from "./worker";

const text = (value: string): Uint8Array => new TextEncoder().encode(value);

function calls(...blocks: FauxContentBlock[]): AssistantMessage {
  return fauxAssistantMessage(blocks, { stopReason: "toolUse" });
}

function textOf(message: Message): string {
  const content = message.content;
  return typeof content === "string" ? content
    : content.map((part) => (part.type === "text" ? part.text : "")).join("");
}

/**
 * The model of these tests: `CODE <script>` runs it through `ta`, `TA <json>` calls `ta`;
 * after a tool round it answers with the result's text and error; anything else (a
 * script's own model call among them) it notes.
 */
function step(messages: Message[]): AssistantMessage {
  const last = messages.at(-1);
  if (last?.role === "toolResult") {
    return says(JSON.stringify({ text: textOf(last), error: last.isError }));
  }
  const user = [...messages].reverse().find((message) => message.role === "user");
  const asked = user === undefined ? "" : textOf(user);
  if (asked.startsWith("CODE ")) {
    return calls(fauxToolCall("ta", { op: "call", name: "codemode",
                                      arguments: { code: asked.slice(5) } }));
  }
  if (asked.startsWith("TA ")) {
    return calls(fauxToolCall("ta", JSON.parse(asked.slice(3)) as JsonObject));
  }
  return says(`noted: ${asked}`);
}

type Shown = { text: string; error: boolean };

/** An owner of its own on the faux-model class, signed in; code mode as `on` says. */
async function rig(on = true) {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub = env.AGENT.getByName(owner);
  const at = signedIn();
  const as = (origin: Origin | null = null): Binding =>
    ({ owner, caller: owner, origin, ...(origin === null ? {} : { source: MCP,
                                                                  authenticated_at: at }) });
  const { session } = await stub.sessionOpen({ ...as(), authenticated_at: at });
  if (on) {
    const { generation, policy } = await stub.policyRead(as());
    await stub.policyWrite({ ...as(), session }, { expected_generation: generation,
                                                   policy: { ...policy, hooks: { codemode: true } } });
  }
  return {
    owner, stub, as, session,
    /** The client admitted with its full grant: its stamp, acting in universe alpha. */
    full: async (origin: Origin): Promise<Stamped> => {
      await stub.outsideAgents(as(origin));
      await runInDurableObject(stub, (_instance, state) => ledgerOn(state.storage).register(
        origin, MCP, { command_center_id: "alpha", display_name: "Muse",
                       delivery_method: "pull", recipe_id: null }));
      return { client: origin.client, generation: 1, authenticated_at: at, universe: "alpha",
               agent: "main" };
    },
    /** The client admitted, scoped to `capabilities` in universe alpha as agent main. */
    scoped: async (origin: Origin, capabilities: string[]): Promise<Stamped> => {
      await stub.outsideAgents(as(origin));
      await runInDurableObject(stub, (_instance, state) => ledgerOn(state.storage).register(
        origin, MCP, { command_center_id: "alpha", display_name: "Muse",
                       delivery_method: "pull", recipe_id: null }));
      const row = (await stub.grantList(as())).clients
        .find((client) => client.client === origin.client);
      const { generation } = await stub.grantSet({ ...as(), session }, {
        client: origin.client, source: "mcp", mode: "scoped", name: "",
        scopes: capabilities.map((capability) => ({ universe: "alpha", agent: "main",
                                                    capability })),
        expected_revision: row?.revision ?? 0 });
      return { client: origin.client, generation, authenticated_at: at, universe: "alpha",
               agent: "main" };
    },
    in: <R>(act: (instance: FauxOwnerDO) => R | Promise<R>) => runInDurableObject(stub, act),
  };
}

/** `input` asked in a new thread of `stamp` (the owner's: null): the tool's result shown. */
async function asked(instance: FauxOwnerDO, input: string, stamp: Stamped | null = null):
    Promise<Shown> {
  instance.script = Array.from({ length: 20 }, () => step);
  const thread = await instance.agent.thread("main", stamp, { kind: "converse" });
  const answer = await instance.harness.prompt(input, { session: thread.thread_id });
  expect(answer.status).toBe("done");
  return JSON.parse(answer.text!) as Shown;
}

describe("code mode", () => {
  it("is off until the owner's policy turns it on, and never offered to the model",
     async () => {
    const off = await rig(false);
    const out = await off.in(async (instance) => ({
      called: await asked(instance, "CODE text('hi')"),
      listed: await asked(instance, `TA ${JSON.stringify({ op: "catalog", query: "codemode" })}`),
      offered: instance.offered.flat(),
    }));
    expect(out.called).toEqual({ error: true, text: expect.stringContaining(
      `${UNKNOWN_CAPABILITY}: codemode`) });
    expect(out.listed.text).not.toContain("\"codemode\"");
    const on = await rig();
    const shown = await on.in(async (instance) => ({
      listed: await asked(instance, `TA ${JSON.stringify({ op: "catalog", limit: 100 })}`),
      offered: instance.offered,
      installed: instance.agent.resources !== undefined,
    }));
    const names = (JSON.parse(shown.listed.text) as { capabilities: { name: string }[] })
      .capabilities.map((entry) => entry.name);
    expect(names).toContain("codemode");
    expect(names.filter((name) => name.startsWith("pi.codemode."))).toEqual([]);
    expect(names).not.toContain(MODEL_COMPLETE);
    for (const tools of shown.offered) {
      expect([...tools].sort()).toEqual(["bash", "edit", "read", "ta", "write"]);
    }
    expect(CODEMODE_EXTENSION).toBe("ta.codemode");
  });

  it("is refused to a client its grant does not name run_code for, a full grant's too",
     async () => {
    const r = await rig();
    const scoped = await r.scoped(MUSE, ["converse", "read_files"]);
    const full = await r.full(DOT);
    const out = await r.in(async (instance) => ({
      scoped: await asked(instance, "CODE text('hi')", scoped),
      full: await asked(instance, "CODE text('hi')", full),
      // Refused at ta itself, before it runs: no later hook saw a ta call.
      gated: instance.probes.gated.map((call) => call.name),
      listed: await asked(instance, `TA ${JSON.stringify({ op: "catalog", limit: 100 })}`,
                          scoped),
      owner: await asked(instance, "CODE text('hi')"),
      codemodes: instance.probes.codemodes,
    }));
    expect(out.scoped).toEqual({ error: true, text: expect.stringContaining(
      `Tool call blocked: ${NOT_GRANTED}`) });
    expect(out.full).toEqual({ error: true, text: expect.stringContaining(
      `Tool call blocked: ${NOT_GRANTED}`) });
    expect(out.listed.text).not.toContain("\"codemode\"");
    expect(out.gated).toEqual([]);
    expect(out.owner).toEqual({ error: false, text: expect.stringContaining("hi") });
    expect(out.codemodes).toBe(1);
  });

  it("runs a script whose tools are its caller's: a scoped client with run_code reads, " +
     "and its shell and its write are refused", async () => {
    const r = await rig();
    await r.stub.fileWrite(r.as(), { path: "/workspace/a.md", expected_etag: null,
                                     content: text("alpha") });
    const stamp = await r.scoped(MUSE, ["converse", "read_files", "run_code"]);
    const script = [
      "const shown = [];",
      "shown.push('read: ' + JSON.stringify(await tools.read({ path: '/workspace/a.md' })));",
      "try { await tools.bash({ command: 'ls' }); shown.push('bash ran'); }",
      "catch (error) { shown.push('bash: ' + error.message); }",
      "try { await tools.write({ path: '/workspace/b.md', content: 'x' }); shown.push('write ran'); }",
      "catch (error) { shown.push('write: ' + error.message); }",
      "text(shown.join(' | '));",
    ].join("\n");
    const out = await r.in(async (instance) => ({
      ran: await asked(instance, `CODE ${script}`, stamp),
      listed: await asked(instance, `TA ${JSON.stringify({ op: "catalog", limit: 100 })}`,
                          stamp),
      codemodes: instance.probes.codemodes,
    }));
    expect(out.ran.error).toBe(false);
    expect(out.ran.text).toContain("alpha");
    expect(out.ran.text).toContain(`bash: Tool call blocked: ${NO_SHELL}`);
    expect(out.ran.text).toContain(`write: Tool call blocked: ${NOT_GRANTED}`);
    expect(out.ran.text).not.toContain("ran |");
    expect(out.codemodes).toBe(1);
    expect(out.listed.text).toContain("\"codemode\"");
    expect((await r.stub.fileStat(r.as(), { path: "/workspace/b.md" })).entry).toBeNull();
  });

  it("refuses a script that starts a script", async () => {
    const r = await rig();
    const script = [
      "try { await tools.codemode({ code: \"text('inner')\" }); text('nested ran'); }",
      "catch (error) { text('nested: ' + error.message); }",
    ].join("\n");
    const shown = await r.in((instance) => asked(instance, `CODE ${script}`));
    expect(shown.text).toContain(`nested: Tool call blocked: ${CODEMODE_NESTED}`);
    expect(shown.text).not.toContain("nested ran");
  });

  it("asks the object's model source with tools.model_complete, on the run's own turn",
     async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      const shown = await asked(instance, "CODE const answer = await tools.model_complete({ " +
        "messages: [{ role: 'user', text: 'NESTED question' }] });\ntext('asked: ' + answer.text);");
      return { shown, turns: [...instance.turns] };
    });
    expect(out.shown).toEqual({ error: false,
                                text: expect.stringContaining("asked: noted: NESTED question") });
    // The run's request, the script's, and the run's answer: one turn.
    expect(out.turns).toHaveLength(3);
    expect(new Set(out.turns).size).toBe(1);
  });

  it("counts a script's model call in pi.usage, and keeps codemode out of the scripts' " +
     "section", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      let section = "";
      instance.script = Array.from({ length: 20 }, () => (messages: Message[]) => {
        for (const message of messages as unknown as { role: string;
                                                       sections?: Record<string, string> }[]) {
          if (message.role === "system" && typeof message.sections?.["codemode"] === "string") {
            section = message.sections["codemode"];
          }
        }
        return step(messages);
      });
      const thread = await instance.agent.thread("main", null, { kind: "converse" });
      const answer = await instance.harness.prompt("CODE const answer = await " +
        "tools.model_complete({ messages: [{ role: 'user', text: 'count me' }] });\n" +
        "text(answer.text);", { session: thread.thread_id });
      const usage = await instance.agent.host.use(thread.thread_id,
                                                  ({ pi }) => pi.usage(BACKGROUND_CONTEXT));
      return { answer: answer.text, section, usage };
    });
    expect(out.answer).toContain("noted: count me");
    expect(out.usage.tools[MODEL_COMPLETE]).toBeDefined();
    expect(out.section).toContain(MODEL_COMPLETE);
    expect(out.section).not.toMatch(/codemode\s*\(/);
  });

  it("registers model_complete callable by tools only and never rerun", () => {
    const extension = codemodeExtension({ service: () => undefined, owner: () => "alice" });
    const tool = extension.tools!.find((found) => found.name === MODEL_COMPLETE)!;
    expect([tool.callers, tool.replay]).toEqual([["tools"], "unsafe"]);
    const codemode = extension.tools!.find((found) => found.name === "codemode")!;
    expect([codemode.callers, codemode.replay]).toEqual([["tools"], "safe"]);
  });

  it("holds a script's model call to the packages' hooks", async () => {
    const r = await rig();
    const P = "/workspace/.agents/packages";
    await r.stub.fileWrite(r.as(), { path: `${P}/fence/package.json`, expected_etag: null,
      content: text(JSON.stringify({ name: "fence", tinyassets: { hooks: [
        { module: "fence.js", points: ["beforeTool"], tools: [MODEL_COMPLETE] }] } })) });
    await r.stub.fileWrite(r.as(), { path: `${P}/fence/fence.js`, expected_etag: null,
      content: text("export function beforeTool() { return { block: 'no models here' }; }") });
    const { generation, policy } = await r.stub.policyRead(r.as());
    await r.stub.policyWrite({ ...r.as(), session: r.session }, {
      expected_generation: generation, policy: { ...policy, packages: [`${P}/fence`] } });
    const out = await r.in(async (instance) => ({
      shown: await asked(instance, "CODE try { await tools.model_complete({ messages: [{ " +
        "role: 'user', text: 'x' }] }); text('asked'); } catch (error) { text('refused: ' + " +
        "error.message); }"),
      turns: [...instance.turns],
    }));
    expect(out.shown.text).toContain("refused: Tool call blocked: no models here");
    expect(out.turns).toHaveLength(2);  // the run's two requests: the script asked none
  });

  it("reserves and settles a script's model call through the gateway, on its run's turn",
     async () => {
    const r = await gated();
    await r.connect("sk-codemode");
    const { generation, policy } = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: generation,
                                            policy: { ...policy, hooks: { codemode: true } } });
    const out = await r.in(async (instance) => {
      const first = await instance.harness.prompt("cmd:ta-codemode");
      const messages = (await instance.harness.messages())
        .flatMap((entry) => (entry.model ?? []) as Message[]);
      return { status: first.status,
               result: messages.filter((message) => message.role === "toolResult")
                 .map(textOf) };
    });
    expect(out.status).toBe("done");
    expect(out.result.join("")).toContain("asked: ");
    const rows = await r.sql("SELECT turn, state FROM model_reservations ORDER BY seq");
    expect(rows.map((row) => row["state"])).toEqual(["succeeded", "succeeded", "succeeded"]);
    expect(new Set(rows.map((row) => row["turn"])).size).toBe(1);
    expect(await r.sql("SELECT turn FROM model_turns")).toEqual([]);
  }, 30_000);

  it("holds a script's model call to its run's caps, as the run's own calls are", async () => {
    const r = await gated();
    await r.connect("sk-codemode-capped");
    const { generation, policy } = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: generation,
      policy: { ...policy, hooks: { codemode: true }, models: { turn_requests: 1 } } });
    const result = await r.in(async (instance) => {
      await instance.harness.prompt("cmd:ta-codemode");
      const messages = (await instance.harness.messages())
        .flatMap((entry) => (entry.model ?? []) as Message[]);
      return messages.filter((message) => message.role === "toolResult").map(textOf).join("");
    });
    expect(result).toContain("refused: ");
    expect(result).toContain("model_budget: turn_requests");
    const rows = await r.sql("SELECT state, rule FROM model_reservations ORDER BY seq");
    expect(rows.slice(0, 2)).toEqual([{ state: "succeeded", rule: null },
                                      { state: "refused", rule: "turn_requests" }]);
  }, 30_000);
});

describe("P11: pi's remote sandbox over the HOOKS service binding", () => {
  it("runs a script's VM in the hooks Worker, each tool call an RPC back through " +
     "exchange", async () => {
    const exchanges: string[][] = [];
    const added: unknown[] = [];
    const sandbox = new RemoteCodemodeSandbox({
      timeoutMs: 10_000,
      tools: [{ name: "add", execute: async (args) => {
        added.push(args);
        const { a, b } = args as { a: number; b: number };
        return { sum: a + b };
      } }],
      remote: (): CodemodeRemote => {
        const name = `p11/${crypto.randomUUID()}`;
        return {
          execute: (request, exchange) => env.HOOKS.codemodeExecute(name, request,
            async (messages: CodemodeRemoteMessage[]) => {
              exchanges.push(messages.map((message) => message.type));
              return exchange(messages);
            }),
        };
      },
    });
    const result = await sandbox.execute([
      "const one = await tools.add({ a: 1, b: 2 });",
      "const two = await tools.add({ a: one.sum, b: 4 });",
      "text('two is ' + two.sum);",
      "return two.sum;",
    ].join("\n"));
    expect(result).toMatchObject({ ok: true, value: 7,
                                   output: [{ type: "text", text: "two is 7" }] });
    expect(added).toEqual([{ a: 1, b: 2 }, { a: 3, b: 4 }]);
    // One exchange per tool call, the nested second made from the first's answer, then the
    // terminal message.
    const flat = exchanges.flat();
    expect(flat.filter((type) => type === "call")).toHaveLength(2);
    expect(flat.at(-1)).toBe("done");
    await sandbox.close();
  });

  it("fails a script whose VM computes past the interrupt budget as a timeout", async () => {
    const sandbox = new RemoteCodemodeSandbox({
      timeoutMs: 30_000,
      interruptBudget: 2_000,
      remote: (): CodemodeRemote => ({ execute: (request, exchange) =>
        env.HOOKS.codemodeExecute(`p11/${crypto.randomUUID()}`, request, exchange) }),
    });
    const result = await sandbox.execute("let n = 0; for (;;) { n++; }");
    expect(result).toMatchObject({ ok: false, error: { kind: "timeout" } });
    await sandbox.close();
  }, 30_000);
});
