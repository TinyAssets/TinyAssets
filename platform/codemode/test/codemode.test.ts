/**
 * The codemode Worker's own door (`tinyassets-codemode`), as the owner calls it through
 * pi-codemode's `RemoteCodemodeSandbox`: a script that computes past the interrupt budget
 * its request carries is stopped there, as a timeout naming that budget. The Worker's own
 * budget, `CODEMODE_INTERRUPT_BUDGET` (1,000,000 polls), is the most any request gets; a
 * tight loop took 343 s to reach it in workerd (measured for untangle B23), past the large
 * class's 286 s, so its production size is H2's live probe's (`deploy/h2-probes.ts` probe 1,
 * against the Worker's `cpu_ms`), not a test's here.
 */
import { env } from "cloudflare:workers";
import { type CodemodeRemote, RemoteCodemodeSandbox } from "@earendil-works/pi-codemode/portable";
import { describe, expect, it } from "vitest";

import type { CodemodeWorker } from "../../contract/src/codemode";
import { refusalOf } from "../../contract/src/refusal";

type Vm = CodemodeWorker<Parameters<CodemodeRemote["execute"]>[0],
                         Parameters<CodemodeRemote["execute"]>[1]>;
const VM = (env as unknown as { CODEMODE_VM: Vm }).CODEMODE_VM;

describe("the codemode Worker", () => {
  it("refuses an execution under no name, as the contract's `invalid`, before any sandbox",
     async () => {
    const error = await VM.codemodeExecute("", {} as never, {} as never)
      .then(() => null, (caught: unknown) => caught);
    expect([refusalOf(error)?.code, (error as Error).message])
      .toEqual(["invalid", "a code mode execution is named"]);
  });

  it("stops a script whose VM computes past the budget its request carries, as a timeout",
     async () => {
    const sandbox = new RemoteCodemodeSandbox({
      timeoutMs: 30_000,
      interruptBudget: 2_000,
      remote: (): CodemodeRemote => ({ execute: (request, exchange) =>
        VM.codemodeExecute(`owner-a/${crypto.randomUUID()}`, request, exchange) }),
    });
    try {
      expect(await sandbox.execute("let n = 0; for (;;) { n++; }")).toMatchObject({
        ok: false, error: { kind: "timeout",
                            message: "Execution exceeded its interrupt budget of 2000" } });
    } finally {
      await sandbox.close();
    }
  });
});
