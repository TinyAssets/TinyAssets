/**
 * What slice S's sandbox Worker answers changes in the owner's object, in workerd with the
 * recording fake behind `SANDBOX` (`sandbox-fake.ts`):
 *
 * - a start the sandbox has no room for (`sandbox_waiting`: E2B runs as many sandboxes as
 *   its plan allows) puts the run back to `waiting`, never refused, its lease given back and
 *   its grant gone, and the `runs` lane starts it again, admitted whole, from its recheck on;
 * - a lease that ends (its last live run settled) is released to the sandbox Worker, which
 *   pauses that sandbox (zero idle tail).
 */
import { describe, expect, it } from "vitest";

import { SANDBOX_RECHECK_S, SANDBOX_WAITING } from "../src/sandbox";
import { gated } from "./gateway-support";
import { defineAgent, recorder, runnerBinding, startOf, TIERS } from "./runs-support";
import { drainDue } from "./support";

const SIGNED_IN = Object.freeze({ engine: "claude-code", credential: "vendor_login_in_sandbox",
                                  scopes: [] });

describe("a run the sandbox has no room for", () => {
  it("waits, never refused, and starts again from its recheck, admitted whole", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    await r.in((instance) => {
      instance.recorder.answer = [{ started: false, code: SANDBOX_WAITING }, { started: true }];
    });
    const run = await r.stub.agentSubmit(r.bind({ tiers: TIERS }),
                                         { message: "the input", client_send_id: "w-1" });
    expect(run).toMatchObject({ state: "waiting", code: SANDBOX_WAITING });
    // It holds no lease and no grant; its input is kept for its turn.
    expect(await r.sql("SELECT lease_id FROM sandbox_leases")).toEqual([]);
    expect(await r.sql("SELECT run FROM run_grants")).toEqual([]);
    expect(await r.sql("SELECT prompt FROM run_inputs WHERE run = ?", run.run_id))
      .toEqual([{ prompt: "the input" }]);
    const rows = await r.sql("SELECT wait_until FROM runs WHERE id = ?",
                             run.run_id) as { wait_until: number }[];
    expect(rows[0]!.wait_until)
      .toBeGreaterThanOrEqual(Date.now() / 1000 + SANDBOX_RECHECK_S - 5);
    // Not before its recheck: the lane leaves it.
    await drainDue(r.stub);
    expect((await recorder(r.stub)).starts).toHaveLength(1);
    // From its recheck on, the lane starts it again: the same run, its input, a fresh lease.
    await r.sql("UPDATE runs SET wait_until = ? WHERE id = ?", Date.now() / 1000 - 1,
                run.run_id);
    await r.stub.fileMkdir(r.bind(), { path: "/workspace/poke", expected_etag: null });
    await drainDue(r.stub);
    const starts = (await recorder(r.stub)).starts;
    expect(starts).toHaveLength(2);
    expect(starts[1]).toMatchObject({ run: run.run_id, prompt: "the input" });
    expect(starts[1]!.tokens).not.toBeNull();
    expect(await r.stub.agentStatus(r.bind(), { run_id: run.run_id }))
      .toMatchObject({ state: "running", code: null });
  });
});

describe("a lease that ends", () => {
  it("is released to the sandbox Worker once its last run settles", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const run = await r.stub.agentSubmit(r.bind({ tiers: TIERS }),
                                         { message: "x", client_send_id: "l-1" });
    const start = await startOf(r.stub, run.run_id);
    expect((await recorder(r.stub)).releases).toEqual([]);
    await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: 0 } });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect((await recorder(r.stub)).releases).toEqual([{ lease_id: start.lease.lease_id }]);
  });
});
