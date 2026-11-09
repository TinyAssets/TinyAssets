/**
 * Code mode's executor (tasks 5, H1; pi audit F8; ADR-034 decision 12), in workerd with
 * the real hooks Worker behind `HOOKS`: a script's VM runs in the hooks Worker, each
 * execution named by its owner and a random id, each tool call an RPC back through
 * `exchange`; with no hooks Worker bound it fails loudly; and the owner's policy keeps it
 * off until the owner turns it on. And P11's local part: pi's `RemoteCodemodeSandbox` over
 * the service binding, a nested call's round trip. The MCP door's `code` tool, which hands
 * a script the door's own tools, is slice M's. CPU limits and isolation are not shown by
 * workerd: owed to H2's live probe.
 */
import { env } from "cloudflare:workers";
import {
  type CodemodeRemote, type CodemodeRemoteMessage, RemoteCodemodeSandbox,
} from "@earendil-works/pi-codemode/portable";
import { describe, expect, it } from "vitest";

import { CODEMODE_TIMEOUT_MS, codemodeSandbox, type HooksService } from "../src/codemode";
import { HOOK_DEFAULTS, validate } from "../../contract/src/policy-document";
import { hookSettings } from "../src/policy";

/** The hooks Worker, each execution's name recorded on `names`. */
function named(names: string[]): HooksService {
  return {
    codemodeExecute: (name, request, exchange) => {
      names.push(name);
      return env.HOOKS.codemodeExecute(name, request, exchange);
    },
  };
}

describe("code mode's executor", () => {
  it("runs a script's VM in the hooks Worker, its tools the caller's, named by the owner",
     async () => {
    const names: string[] = [];
    const seen: unknown[] = [];
    const sandbox = codemodeSandbox({
      service: () => named(names), owner: () => "owner-a",
      tools: [{ name: "lookup", execute: async (args) => {
        seen.push(args);
        return { found: (args as { key: string }).key.toUpperCase() };
      } }],
    });
    const result = await sandbox.execute([
      "const one = await tools.lookup({ key: 'a' });",
      "const two = await tools.lookup({ key: 'b' });",
      "return one.found + two.found;",
    ].join("\n"));
    expect(result).toMatchObject({ ok: true, value: "AB" });
    expect(seen).toEqual([{ key: "a" }, { key: "b" }]);
    expect(names).toHaveLength(1);
    expect(names[0]).toMatch(/^owner-a\/[0-9a-f-]{36}$/);
    // Each execution is a sandbox object of its own.
    await sandbox.execute("return 1;");
    expect(names).toHaveLength(2);
    expect(names[1]).not.toBe(names[0]);
    await sandbox.close();
    expect(CODEMODE_TIMEOUT_MS).toBe(300_000);
  });

  it("fails loudly with no hooks Worker bound, never running the script here", async () => {
    const sandbox = codemodeSandbox({ service: () => undefined, owner: () => "owner-b",
                                      tools: [] });
    const ran = await sandbox.execute("return 1;").then((result) => result.ok, () => false);
    expect(ran).toBe(false);
    await sandbox.close();
  });

  it("is refused by the hooks Worker under no name, an empty one or one past 512 " +
     "characters, before any sandbox is reached", async () => {
    const exchange = async () => [];
    for (const name of ["", "x".repeat(513), 7, null]) {
      const answer = await env.HOOKS.codemodeExecute(name as string, {} as never,
                                                    exchange as never)
        .then(() => "ran", (error: Error) => error.message);
      expect(answer, String(name).slice(0, 8)).toMatch(/a code mode execution is named/);
    }
    // 512 characters is a name: the request itself is then refused by the sandbox, not here.
    const named = await env.HOOKS.codemodeExecute("x".repeat(512), {} as never,
                                                  exchange as never)
      .then(() => "ran", (error: Error) => error.message);
    expect(named).not.toMatch(/a code mode execution is named/);
  });

  it("is on in the owner's policy until the owner turns it off, its one switch", () => {
    const base = { entries: [], egress: { mode: "open", destinations: [] },
                   spend: { daily: null, monthly: null, transaction: null, ask_above: null,
                            source_ceilings: {} } };
    expect(HOOK_DEFAULTS).toEqual({ codemode: true });
    expect(hookSettings(validate(base))).toEqual({ codemode: true });
    expect(hookSettings(validate({ ...base, hooks: { codemode: false } })))
      .toEqual({ codemode: false });
    for (const hooks of [{ codemode: 1 }, { timeout_ms: 2000 }, { cpu_ms: 50 }, []]) {
      expect(() => validate({ ...base, hooks })).toThrow("invalid hooks policy");
    }
  });
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
  });
});
