/**
 * What the sandbox Worker answers changes in the owner's object, in workerd with the
 * fake sandbox Worker behind `SANDBOX` (`ci/fake-sandbox.ts`):
 *
 * - a start the sandbox has no room for (`sandbox_waiting`: E2B runs as many sandboxes as
 *   its plan allows) puts the run back to `waiting`, never refused, its lease given back and
 *   its grant gone; nothing polls it: the platform's line rings the owner (`sandboxRung`)
 *   and the `runs` lane starts it again, admitted whole; rung with none waiting, the owner
 *   leaves the line;
 * - a lease that ends (its last live run settled) is released to the sandbox Worker, which
 *   pauses that sandbox (zero idle tail).
 */
import { evictDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { until } from "../../ci/workerd";
import { SANDBOX_WAITING } from "../../contract/src/sandbox";
import { gated } from "./gateway-support";
import {
  defineAgent, ownerRun, recorder, runnerBinding, scripted, startOf, whileOut,
} from "./runs-support";
import { drainDue, gateFor } from "./support";

const SIGNED_IN = Object.freeze({ engine: "claude-code", credential: "vendor_login_in_sandbox",
                                  scopes: [] });

describe("a run the sandbox has no room for", () => {
  it("waits, never refused and never polled, and starts again when the line rings",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    await scripted(r.stub, { answer: [{ started: false, code: SANDBOX_WAITING },
                                      { started: true }] });
    const run = await r.stub.agentSubmit(r.bind(),
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

  it("can start after a ring crosses the sandbox's answer that the run must wait", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    await scripted(r.stub, { answer: [{ started: false, code: SANDBOX_WAITING },
                                      { started: true }] });
    const gate = gateFor(env.SANDBOX_GATE, r.name);
    const run = await whileOut(r.name, "start", () =>
      r.stub.agentSubmit(r.bind(), { message: "the input", client_send_id: "early-ring" }),
      async () => { await r.stub.sandboxRung(); });

    await drainDue(r.stub);
    // A second ring can only reach this run if its owner still has a place in the line.
    const calls = (await gate.calls()).filter(call => call.method === "start" ||
                                                       call.method === "unwait");
    if (calls.at(-1)?.method === "start") {
      await r.stub.sandboxRung();
      await drainDue(r.stub);
    }
    expect(await r.stub.agentStatus(r.bind(), { run_id: run.run_id }))
      .toMatchObject({ state: "running", code: null });
    expect((await recorder(r.stub)).starts.map(start => start.run))
      .toEqual([run.run_id, run.run_id]);
  });

  it("cancels a saved sandbox wait and starts only the remaining run after a ring", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    const wait = { started: false, code: SANDBOX_WAITING } as const;
    await scripted(r.stub, { answer: [wait, wait, { started: true }] });
    const submit = (message: string) => r.stub.agentSubmit(r.bind(), { message, client_send_id: message });
    const first = await submit("first");
    const second = await submit("second");
    expect([first.code, second.code]).toEqual([SANDBOX_WAITING, SANDBOX_WAITING]);

    await evictDurableObject(r.stub);
    expect(await r.stub.agentCancel(r.bind(), { run_id: first.run_id }))
      .toMatchObject({ state: "failed", code: "cancelled" });
    expect(await r.sql("SELECT prompt FROM run_inputs WHERE run = ?", first.run_id)).toEqual([]);
    expect(await r.stub.sandboxRung()).toEqual({ waiting: 1 });
    await until(async () => {
      await drainDue(r.stub);
      return (await r.stub.agentStatus(r.bind(), { run_id: second.run_id })).state === "running";
    }, "the remaining run started");
    expect((await recorder(r.stub)).starts.map(start => start.run))
      .toEqual([first.run_id, second.run_id, second.run_id]);
    expect(await r.stub.sandboxRung()).toEqual({ waiting: 0 });
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
    const run = await r.stub.agentSubmit(r.bind(),
                                         { message: "x", client_send_id: "l-1" });
    const start = await startOf(r.stub, run.run_id);
    expect((await recorder(r.stub)).releases).toEqual([]);
    await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: 0 } });
    await until(async () => (await recorder(r.stub)).releases.length > 0, "the release");
    expect((await recorder(r.stub)).releases).toEqual([{ lease_id: start.lease.lease_id }]);
  });
});

describe("the vendor hosts a job hands the sandbox Worker", () => {
  it("are its engine's sign-in and model hosts on the vendor's sign-in, none on a key",
     async () => {
    const r = await gated();
    const signedIn = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    expect(signedIn.start.hosts).toEqual(["claude.ai", "platform.claude.com",
                                          "console.anthropic.com", "api.anthropic.com",
                                          "claude.com"]);
    const connection = await r.connect("sk-good");
    const keyed = await ownerRun(r.stub, r.bind(), { engine: "claude-code",
                                                     credential: "api_key", connection,
                                                     scopes: [] });
    expect(keyed.start.hosts).toEqual([]);
  });

  it("are a sign-in's engine's alone", async () => {
    const r = await gated();
    await r.stub.signinStart(r.bind(), { engine: "codex" });
    expect((await recorder(r.stub)).signins.at(-1)!.hosts)
      .toEqual(["auth.openai.com", "chatgpt.com"]);
  });
});
