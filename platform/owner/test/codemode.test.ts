/**
 * Code mode's executor (ADR-034 decision 12), in workerd with the real codemode Worker behind
 * `CODEMODE_VM`: a script's VM runs in the codemode Worker, each execution named by its owner
 * and a random id, each tool call an RPC back through `exchange`; and it is off in the owner's
 * policy until the owner turns it on. And pi's `RemoteCodemodeSandbox` over the service
 * binding, a nested call's round trip; and no clock ends a script (pi-codemode's timers faked
 * and moved ten minutes on while its call is pending). The MCP door's `code` tool, which hands
 * a script the door's own tools, is `code-door.test.ts`'s. Workerd shows neither CPU limits nor
 * isolation.
 */
import { env } from "cloudflare:workers";
import {
  type CodemodeRemote, type CodemodeRemoteMessage, RemoteCodemodeSandbox,
} from "@earendil-works/pi-codemode/portable";
import { describe, expect, it, vi } from "vitest";

import {
  CODE_COMPUTE_LIMIT, CODE_OUT_OF_MEMORY, type CodemodeService, codemodeSandbox, metLimit,
} from "../src/codemode";
import { sha256Hex } from "../../contract/src/digest";
import { DEFAULT_POLICY_JSON, validate } from "../../contract/src/policy-document";
import { codemodeSettings } from "../src/policy";

/** The codemode Worker, each execution's name recorded on `names`. */
function named(names: string[]): CodemodeService {
  return {
    codemodeExecute: (name, request, exchange) => {
      names.push(name);
      return env.CODEMODE_VM.codemodeExecute(name, request, exchange);
    },
  };
}

describe("code mode's executor", () => {
  it("runs a script's VM in the codemode Worker, its tools the caller's, named by the owner",
     async () => {
    const names: string[] = [];
    const seen: unknown[] = [];
    const sandbox = codemodeSandbox({
      service: named(names), owner: () => "owner-a",
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
    expect(names[0]).toMatch(new RegExp(`^${sha256Hex("owner-a")}/[0-9a-f-]{36}$`));
    // Each execution is a sandbox object of its own.
    await sandbox.execute("return 1;");
    expect(names).toHaveLength(2);
    expect(names[1]).not.toBe(names[0]);
    await sandbox.close();
  });

  it("never ends a script by a clock: it runs while its caller waits", async () => {
    // pi-codemode's own deadline is a timer in this isolate: faked, and moved ten minutes on
    // (past its default, 5 minutes) while the script's one call is pending.
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    let answer: (value: unknown) => void = () => undefined;
    let called: () => void = () => undefined;
    const pending = new Promise<void>((resolve) => {
      called = resolve;
    });
    const sandbox = codemodeSandbox({ service: env.CODEMODE_VM, owner: () => "owner-a",
      tools: [{ name: "wait", execute: () => {
        called();
        return new Promise((resolve) => {
          answer = resolve;
        });
      } }] });
    try {
      const running = sandbox.execute("return (await tools.wait({})).done;");
      await pending;
      vi.advanceTimersByTime(600_000);
      answer({ done: "late" });
      expect(await running).toMatchObject({ ok: true, value: "late" });
    } finally {
      vi.useRealTimers();
      await sandbox.close();
    }
  });

  it("names the limit a script meets, with what to do instead; any other error as it was said",
     async () => {
    // Its compute, at a budget a request carries below the Worker's: pi's own `timeout`.
    const small = new RemoteCodemodeSandbox({
      timeoutMs: 30_000, interruptBudget: 2_000,
      remote: (): CodemodeRemote => ({ execute: (request, exchange) =>
        env.CODEMODE_VM.codemodeExecute(`owner-a/${crypto.randomUUID()}`, request, exchange) }),
    });
    // Its memory, at the Worker's own cap, as the owner's sandbox asks for it.
    const sandbox = codemodeSandbox({ service: env.CODEMODE_VM, owner: () => "owner-a",
                                      tools: [] });
    try {
      const looped = await small.execute("for (;;) {}");
      const held = await sandbox.execute(
        "const all = []; for (;;) { all.push('x'.repeat(1 << 20) + all.length); }");
      const thrown = await sandbox.execute("throw new Error('boom');");
      expect([looped, held, thrown].map((end) => end.ok ? null : metLimit(end.error))).toEqual([
        { kind: "timeout", message: CODE_COMPUTE_LIMIT },
        { kind: "script", message: CODE_OUT_OF_MEMORY },
        { kind: "script", message: "boom" }]);
    } finally {
      await small.close();
      await sandbox.close();
    }
    expect(CODE_OUT_OF_MEMORY).toMatch(/32 MiB at once.*a file past 4 MiB is uploaded/);
    expect(CODE_COMPUTE_LIMIT).toMatch(/50,000 interrupt polls.*Date\.now\(\) never ends/);
  });

  it("runs for an owner of any principal's length: its name is the principal's digest",
     async () => {
    const names: string[] = [];
    const sandbox = codemodeSandbox({ service: named(names), owner: () => "p".repeat(2048),
                                      tools: [] });
    expect(await sandbox.execute("return 1;")).toMatchObject({ ok: true, value: 1 });
    expect(names[0]).toMatch(new RegExp(`^${sha256Hex("p".repeat(2048))}/[0-9a-f-]{36}$`));
    await sandbox.close();
  });

  it("is refused by the codemode Worker under no name, an empty one or one past 512 " +
     "characters, before any sandbox is reached", async () => {
    const exchange = async () => [];
    for (const name of ["", "x".repeat(513), 7, null]) {
      const answer = await env.CODEMODE_VM.codemodeExecute(name as string, {} as never,
                                                    exchange as never)
        .then(() => "ran", (error: Error) => error.message);
      expect(answer, String(name).slice(0, 8)).toMatch(/a code mode execution is named/);
    }
    // 512 characters is a name: the request itself is then refused by the sandbox, not here.
    const named = await env.CODEMODE_VM.codemodeExecute("x".repeat(512), {} as never,
                                                  exchange as never)
      .then(() => "ran", (error: Error) => error.message);
    expect(named).not.toMatch(/a code mode execution is named/);
  });

  it("is off in the owner's policy until the owner turns it on, its one switch", () => {
    const base = JSON.parse(DEFAULT_POLICY_JSON) as object;
    expect(codemodeSettings(validate(base))).toEqual({ on: false });
    expect(codemodeSettings(validate({ ...base, codemode: { on: true } })))
      .toEqual({ on: true });
    for (const codemode of [{ on: 1 }, { timeout_ms: 2000 }, { cpu_ms: 50 }, []]) {
      expect(() => validate({ ...base, codemode })).toThrow("invalid codemode policy");
    }
    // The table it replaced is no table of the document.
    expect(() => validate({ ...base, hooks: { codemode: true } }))
      .toThrow("invalid policy document");
  });
});

describe("pi's remote sandbox over the CODEMODE_VM service binding", () => {
  it("runs a script's VM in the codemode Worker, each tool call an RPC back through " +
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
          execute: (request, exchange) => env.CODEMODE_VM.codemodeExecute(name, request,
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
});
