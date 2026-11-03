/**
 * The vendor's own sign-in inside a sandbox (slice SIGNIN; `signins.ts`), in workerd with the
 * recording fake behind `SANDBOX`: the owner's door opens the sandbox's lease with no run and
 * asks the sandbox Worker to start the record's login; the runner's `ta.signin` frames keep
 * the vendor's prompt, renew the lease and reach the owner's app socket; a pasted code goes
 * back; the end lets the lease go. A client's sandbox signs in only inside itself. And the
 * owner's past runs, newest first, a page at a time, held to the caller's grant.
 */
import { describe, expect, it } from "vitest";

import { RUN_ENDED } from "../src/runs";
import type { SandboxSignin, SandboxStart } from "../src/sandbox";
import { SIGNIN_LIVE } from "../src/signins";
import { gated, type Gated } from "./gateway-support";
import { MCP, MUSE, signedIn } from "./ledger-support";
import { defineAgent, recorder, runnerBinding, runnerSandbox, startOf, TIERS } from "./runs-support";
import { failure } from "./support";

const URL_SHOWN = "https://claude.ai/oauth/authorize?code=true&client_id=c1&state=s1";

/** The sign-in's runner's binding on the egress host, by its lease's runner secret. */
const runnerOfSignin = (r: Gated, signin: SandboxSignin) =>
  runnerSandbox(r, signin as unknown as SandboxStart);

async function lastSignin(r: Gated): Promise<SandboxSignin> {
  return (await recorder(r.stub)).signins.at(-1)!;
}

const frame = (seq: number, event: Record<string, unknown>) =>
  ({ seq, at: Date.now() / 1000, event: { type: "ta.signin", ...event } });

/** Muse admitted on a full grant: an outside client with a sandbox of its own. */
async function client(r: Gated): Promise<void> {
  const bound = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
  await r.stub.outsideAgents(bound);
  const { revision } = (await r.stub.grantList(r.bind())).clients
    .find((entry) => entry.client === MUSE.client)!;
  await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "full",
                                       scopes: [], expected_revision: revision, name: "Muse" });
}

describe("a sign-in", () => {
  it("opens the owner's sandbox's lease with no run, and starts the record's login there",
     async () => {
    const r = await gated();
    const view = await r.stub.signinStart(r.bind(), { engine: "claude-code" });
    expect(view).toMatchObject({ id: expect.stringMatching(/^si_[0-9a-f]{32}$/),
                                 authority: "owner", engine: "claude-code", mode: "in",
                                 state: "starting", url: null });
    const asked = await lastSignin(r);
    expect(asked).toMatchObject({ id: (view as { id: string }).id, engine: "claude-code",
                                  mode: "in", region: null, authority: "owner",
                                  urls: { events: `https://egress.tinyassets.io/signin/${
                                    (view as { id: string }).id}/events` } });
    // It opened the lease: the sandbox is configured by this start (its tokens, its network).
    expect(asked.tokens).not.toBeNull();
    expect(asked.network).not.toBeNull();
    expect(await r.sql("SELECT authority FROM sandbox_leases")).toEqual([{ authority: "owner" }]);
    // One at a time in a sandbox.
    expect((await failure(() => r.stub.signinStart(r.bind(), { engine: "codex" }))).message)
      .toBe(SIGNIN_LIVE);
  });

  it("is refused for an engine with no vendor sign-in, a region it does not take, or one " +
     "it needs and lacks", async () => {
    const r = await gated();
    for (const request of [{ engine: "pi" }, { engine: "nope" },
                           { engine: "claude-code", region: "global" },
                           { engine: "kimi" }, { engine: "kimi", region: "moon" },
                           { engine: "claude-code", mode: "sideways" }]) {
      expect((await failure(() => r.stub.signinStart(r.bind(), request as never))).message).toBeTruthy();
    }
    expect((await recorder(r.stub)).signins).toEqual([]);
    expect(await r.sql("SELECT authority FROM sandbox_leases")).toEqual([]);
  });

  it("is the owner's own door: no outside client starts one", async () => {
    const r = await gated();
    await client(r);
    const outside = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    expect((await failure(() => r.stub.signinStart(outside, { engine: "claude-code" }))).message).toBeTruthy();
  });

  it("keeps the vendor's prompt, takes a pasted code back, and lets the lease go at its end",
     async () => {
    const r = await gated();
    const { id } = await r.stub.signinStart(r.bind(), { engine: "claude-code" }) as { id: string };
    const signin = await lastSignin(r);
    const runner = runnerOfSignin(r, signin);
    expect(await r.stub.signinEvents(runner, { id, frames: [
      frame(1, { state: "starting" }),
      frame(2, { state: "open", url: URL_SHOWN, code: null, accepts_code: true })] }))
      .toEqual({ accepted: 2, ended: false });
    expect((await r.stub.signinList(r.bind())).signins).toEqual([expect.objectContaining({
      id, state: "open", url: URL_SHOWN, code: null, accepts_code: true })]);
    expect(await r.stub.signinCode(r.bind(), { id, code: "abc#def" }))
      .toEqual({ delivered: true });
    expect((await recorder(r.stub)).codes).toEqual([{ authority: "owner", id, code: "abc#def" }]);
    for (const code of ["", "two\nlines", "\u0000"]) {
      expect((await failure(() => r.stub.signinCode(r.bind(), { id, code }))).message).toBeTruthy();
    }
    expect(await r.stub.signinEvents(runner, { id, frames: [frame(3, { state: "signed_in" })],
                                              end: { exit_code: 0 } }))
      .toEqual({ accepted: 1, ended: true });
    expect((await r.stub.signinList(r.bind())).signins).toEqual([]);
    // No run holds the lease: it goes, and its sandbox is released.
    expect(await r.sql("SELECT authority FROM sandbox_leases")).toEqual([]);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect((await recorder(r.stub)).releases).toEqual([{ lease_id: signin.lease.lease_id }]);
    // Its door is closed after its end.
    expect((await failure(() => r.stub.signinEvents(runner, { id, frames: [] }))).message)
      .toBe(RUN_ENDED);
  });

  it("holds the lease a run joins, and leaves it to the run at its end", async () => {
    const r = await gated();
    const { id } = await r.stub.signinStart(r.bind(), { engine: "claude-code" }) as { id: string };
    const signin = await lastSignin(r);
    await defineAgent(r.stub, r.bind(), "main", { engine: "auto", scopes: [] });
    await r.connect("sk-ant-x");
    const run = await r.stub.agentSubmit(r.bind({ tiers: TIERS }), { message: "go",
                                                                     client_send_id: "j-1" });
    const start = await startOf(r.stub, run.run_id);
    // The run joined the sign-in's lease: no tokens of its own.
    expect(start.tokens).toBeNull();
    expect(start.lease.lease_id).toBe(signin.lease.lease_id);
    await r.stub.signinEvents(runnerOfSignin(r, signin), { id, frames: [
      frame(1, { state: "cancelled" })], end: { exit_code: 1 } });
    expect(await r.sql("SELECT lease_id FROM sandbox_leases"))
      .toEqual([{ lease_id: signin.lease.lease_id }]);
    // The run's end lets it go.
    await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: 0 } });
    expect(await r.sql("SELECT lease_id FROM sandbox_leases")).toEqual([]);
  });

  it("is stopped by the owner, and ends with no sandbox to start in", async () => {
    const r = await gated();
    const { id } = await r.stub.signinStart(r.bind(), { engine: "codex" }) as { id: string };
    expect(await r.stub.signinCancel(r.bind(), { id })).toEqual({ cancelled: true });
    expect((await recorder(r.stub)).signinCancels).toEqual([{ authority: "owner", id }]);
    const s = await gated();
    await s.in((instance) => {
      instance.recorder.signinAnswer = { started: false, code: "sandbox_waiting" };
    });
    expect(await s.stub.signinStart(s.bind(), { engine: "codex" }))
      .toEqual({ started: false, code: "sandbox_waiting" });
    expect((await s.stub.signinList(s.bind())).signins).toEqual([]);
    expect(await s.sql("SELECT authority FROM sandbox_leases")).toEqual([]);
  });

  it("signs a client's sandbox in only inside itself", async () => {
    const r = await gated();
    expect((await failure(() => r.stub.signinStart(r.bind(), { engine: "claude-code",
                                                authority: MUSE.client }))).message).toBeTruthy();
    await client(r);
    const { id } = await r.stub.signinStart(r.bind(), { engine: "claude-code",
                                                        authority: MUSE.client }) as { id: string };
    const signin = await lastSignin(r);
    expect(signin.authority).toBe(MUSE.client);
    expect(await r.sql("SELECT authority FROM sandbox_leases"))
      .toEqual([{ authority: MUSE.client }]);
    // Its presence lands on that sandbox's own lease, never the owner's engines.
    await r.stub.signinReport(runnerOfSignin(r, signin), { signins: [
      { engine: "claude-code", signed_in: true }] });
    expect((await r.stub.signinList(r.bind())).engines
      .filter((each) => each.signed_in)).toEqual([]);
    expect(await r.sql("SELECT signins FROM sandbox_leases"))
      .toEqual([{ signins: "claude-code" }]);
    // The owner's own sandbox may sign in beside it: one at a time a sandbox.
    await r.stub.signinStart(r.bind(), { engine: "claude-code" });
    expect((await r.stub.signinList(r.bind())).signins.map((each) => each.id)).toContain(id);
  });

  it("signs out by the same door, its runner deleting the engine's secrets", async () => {
    const r = await gated();
    await r.stub.signinStart(r.bind(), { engine: "kimi", mode: "out" });
    expect(await lastSignin(r)).toMatchObject({ engine: "kimi", mode: "out", region: null });
  });

  it("is watched on the app socket by its id, the owner's own", async () => {
    const r = await gated();
    const { id } = await r.stub.signinStart(r.bind(), { engine: "claude-code" }) as { id: string };
    const ticket = await r.stub.socketTicket(r.bind({ grant: "g-1",
                                                      expires_at: Date.now() / 1000 + 600 }),
                                             { conversation_id: id });
    expect(ticket.conversation_id).toBe(id);
    expect((await failure(() => r.stub.socketTicket(r.bind({ grant: "g-1", expires_at: Date.now() / 1000 + 600 }),
                                     { conversation_id: `si_${"0".repeat(32)}` }))).message).toBeTruthy();
  });
});

describe("the past runs", () => {
  it("are listed newest first, a page at a time, each its receipt without its result",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", { engine: "auto", scopes: [] });
    await r.connect("sk-ant-x");
    const ids: string[] = [];
    for (let i = 0; i < 3; i += 1) {
      const run = await r.stub.agentSubmit(r.bind({ tiers: TIERS }), {
        message: `go ${i}`, client_send_id: `l-${i}` });
      ids.push(run.run_id);
      const start = await startOf(r.stub, run.run_id);
      await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: 0 } });
    }
    const first = await r.stub.runList(r.bind(), { limit: 2 });
    expect(first.runs.map((run) => run.run_id)).toEqual([ids[2], ids[1]]);
    expect(first.runs[0]).not.toHaveProperty("result");
    expect(first.next).toBe(first.runs[1]!.seq);
    const second = await r.stub.runList(r.bind(), { limit: 2, before: first.next! });
    expect(second.runs.map((run) => run.run_id)).toEqual([ids[0]]);
    expect(second.next).toBeNull();
    for (const request of [{ limit: 0 }, { limit: 1.5 }, { before: -1 }, { other: 1 }]) {
      expect((await failure(() => r.stub.runList(r.bind(), request as never))).message).toBeTruthy();
    }
  });

  it("are an outside client's own alone", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", { engine: "auto", scopes: [] });
    await r.connect("sk-ant-x");
    const owners = await r.stub.agentSubmit(r.bind({ tiers: TIERS }), {
      message: "mine", client_send_id: "o-1" });
    await client(r);
    const outside = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    expect((await r.stub.runList(outside, {})).runs).toEqual([]);
    expect((await r.stub.runList(r.bind(), {})).runs.map((run) => run.run_id))
      .toEqual([owners.run_id]);
  });
});
