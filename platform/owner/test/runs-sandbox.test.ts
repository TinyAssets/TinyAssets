/**
 * What slice S's sandbox Worker answers changes in the owner's object, in workerd with the
 * fake sandbox Worker behind `SANDBOX` (`fake-sandbox.ts`):
 *
 * - a start the sandbox has no room for (`sandbox_waiting`: E2B runs as many sandboxes as
 *   its plan allows) puts the run back to `waiting`, never refused, its lease given back and
 *   its grant gone; nothing polls it: the platform's line rings the owner (`sandboxRung`)
 *   and the `runs` lane starts it again, admitted whole; rung with none waiting, the owner
 *   leaves the line;
 * - a lease that ends (its last live run settled) is released to the sandbox Worker, which
 *   pauses that sandbox (zero idle tail).
 */
import { describe, expect, it } from "vitest";

import { SANDBOX_WAITING } from "../src/sandbox";
import { gated } from "./gateway-support";
import { defineAgent, recorder, runnerBinding, scripted, startOf, TIERS } from "./runs-support";
import { drainDue } from "./support";

const SIGNED_IN = Object.freeze({ engine: "claude-code", credential: "vendor_login_in_sandbox",
                                  scopes: [] });

describe("a run the sandbox has no room for", () => {
  it("waits, never refused and never polled, and starts again when the line rings",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    await scripted(r.stub, { answer: [{ started: false, code: SANDBOX_WAITING },
                                      { started: true }] });
    const run = await r.stub.agentSubmit(r.bind({ tiers: TIERS }),
                                         { message: "the input", client_send_id: "w-1" });
    expect(run).toMatchObject({ state: "waiting", code: SANDBOX_WAITING });
    // It holds no lease and no grant; its input is kept for its turn.
    expect(await r.sql("SELECT lease_id FROM sandbox_leases")).toEqual([]);
    expect(await r.sql("SELECT run FROM run_grants")).toEqual([]);
    expect(await r.sql("SELECT prompt FROM run_inputs WHERE run = ?", run.run_id))
      .toEqual([{ prompt: "the input" }]);
    expect(await r.sql("SELECT sandbox_wait FROM runs WHERE id = ?", run.run_id))
      .toEqual([{ sandbox_wait: 1 }]);
    // Nothing rings: the lane leaves it, however often it runs.
    await r.stub.fileMkdir(r.bind(), { path: "/workspace/poke", expected_etag: null });
    await drainDue(r.stub);
    expect((await recorder(r.stub)).starts).toHaveLength(1);
    // The line rings: the same run, its input, a fresh lease.
    expect(await r.stub.sandboxRung()).toEqual({ waiting: 1 });
    await drainDue(r.stub);
    const starts = (await recorder(r.stub)).starts;
    expect(starts).toHaveLength(2);
    expect(starts[1]).toMatchObject({ run: run.run_id, prompt: "the input" });
    expect(starts[1]!.tokens).not.toBeNull();
    expect(await r.stub.agentStatus(r.bind(), { run_id: run.run_id }))
      .toMatchObject({ state: "running", code: null });
    expect((await recorder(r.stub)).unwaits).toEqual([]);
  });

  it("leaves the line when rung with no run waiting", async () => {
    const r = await gated();
    expect(await r.stub.sandboxRung()).toEqual({ waiting: 0 });
    expect((await recorder(r.stub)).unwaits).toHaveLength(1);
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
