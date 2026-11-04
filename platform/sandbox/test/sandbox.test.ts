/**
 * The sandbox Worker against a fake E2B (`fake-e2b.ts`), in workerd: a start creates
 * (`secure`), resumes or joins the authority's sandbox and launches the run's runner; a run
 * on the vendor's sign-in with none there is `signin_required`; past the concurrency ceiling
 * or behind an owner that waited longer a run waits in the platform's line, never refused,
 * until a freed sandbox rings it; a release pauses an idle sandbox, and one whose pause did
 * not land is reaped by the look; the look renews a sandbox before its runtime ceiling, at a
 * quiet moment or forced at the edge, resumes one paused while a lease runs in it, and is
 * armed again on every path (slice S review H-5); an erasure kills every sandbox of the
 * owner, paused and strays included (H-6).
 */
import { env, runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

import type { SandboxStart } from "../../owner/src/sandbox";
import { LEASE_TTL_S } from "../../owner/src/files";
import { backoff, CODES, ownerTag, RENEW_EDGE_MS, widened } from "../src/control";
import { SLOTS } from "../src/slots";

const OPEN = { outside: "on", ordinary: "all", canary: [], inbound: false };
const CEILING = 3600;
const WINDOW = 600;
const NUMBERS = { sandbox_runtime_ceiling_s: CEILING, sandbox_concurrency_ceiling: 20,
                  sandbox_renewal_window_s: WINDOW };

async function control(value: unknown): Promise<void> {
  if (value === null) {
    await env.CONTROL.delete("c:control");
  } else {
    await env.CONTROL.put("c:control", JSON.stringify(value));
  }
}

async function e2b(path: string, body?: unknown): Promise<Record<string, unknown>> {
  const response = await env.E2B.fetch(`https://fake.test${path}`, body === undefined ? {}
    : { method: "POST", body: JSON.stringify(body) });
  return response.json();
}

interface Seen {
  sandboxes: { id: string; state: string; timeout: number; network: { allowOut: string[] };
               template: string; metadata: Record<string, string>; secure: boolean }[];
  calls: { method: string; path: string; body: unknown; query: string }[];
  files: Record<string, string>;
  commands: { sandbox: string; argv: string[]; user: string }[];
  rings: { owner: string }[];
}

const seen = async (): Promise<Seen> => await e2b("/state") as unknown as Seen;

async function rung(owner: string): Promise<void> {
  const until = Date.now() + 5_000;
  while (!(await seen()).rings.some((ring) => ring.owner === owner)) {
    if (Date.now() > until) {
      throw new Error(`no ring reached ${owner}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
}

const hex = (n: number, c = "a") => c.repeat(n);
const RUN = `run_${hex(32, "1")}`;
const LEASE = { lease_id: hex(32, "b"), epoch: 1 };
const NETWORK = {
  network: { allowOut: ["github.com", "egress.tinyassets.io"], denyOut: ["0.0.0.0/0"],
             dnsServers: ["1.1.1.1"],
             rules: { "egress.tinyassets.io": [{ transform: { headers: {
               "x-ta-sandbox": "token", "x-ta-network-generation": "1" } } }] } },
  mode: "allowlist", generation: 1, unexpressed: [], receipt: false, boundary: false, note: "",
};

function start(over: Partial<SandboxStart> = {}): SandboxStart {
  return {
    run: RUN, agent: "main", conversation: `cv_${hex(32, "c")}`, engine: "claude-code",
    credential: "vendor_login_in_sandbox", model: null, session_id: null, prompt: "hello",
    unattended: false, authority: "owner", lease: LEASE, route: "r".repeat(64),
    tokens: { egress_token: "egress", sync_token: "sync", runner_token: hex(64, "d") },
    network: NETWORK as never,
    config: { generation: 1, egress: { mode: "allowlist", destinations: [] },
              env: { HOME: "{home}" }, inherit: ["PATH"] },
    urls: { mcp: `https://egress.tinyassets.io/r/${RUN}/${hex(64, "e")}/mcp`,
            mcp_bare: `https://egress.tinyassets.io/r/${RUN}/mcp`,
            events: `https://egress.tinyassets.io/r/${RUN}/events`,
            signin: "https://egress.tinyassets.io/signin" },
    ...over,
  };
}

const owner = () => `user_${crypto.randomUUID().replace(/-/g, "")}`;
const stub = (who: string) => env.SANDBOXES.getByName(who);
const line = () => env.SLOTS.getByName(SLOTS).line();
const alarmOf = (who: string) => runInDurableObject(stub(who), (_instance, state) =>
  state.storage.getAlarm());

/**
 * What `act` armed, against the delay the code means: an alarm `delay` on from the moment
 * the code read its clock, which lies between the two reads around `act`. Never a reading
 * of time passed since, which a loaded runner stretches.
 */
async function armedAfter(act: () => Promise<unknown>, read: () => Promise<number | null>,
                          delay: number): Promise<void> {
  const before = Date.now();
  await act();
  const after = Date.now();
  const at = await read();
  expect(at).not.toBeNull();
  expect(at!).toBeGreaterThanOrEqual(before + delay);
  expect(at!).toBeLessThanOrEqual(after + delay);
}

beforeEach(async () => {
  await e2b("/reset", {});
  await control({ ...OPEN, ...NUMBERS });
  for (const who of await line()) {
    await env.SLOTS.getByName(SLOTS).leave(who);
  }
});

describe("a start", () => {
  it("creates the authority's sandbox, secure, from the template and launches the run",
     async () => {
    await e2b("/script", { signin: { "claude-code": 0 } });
    const who = owner();
    const began = Date.now();
    expect(await stub(who).start(who, start())).toEqual({ started: true });
    const state = await seen();
    expect(state.sandboxes).toHaveLength(1);
    const box = state.sandboxes[0]!;
    // envd takes only the sandbox's own token (review M-8).
    expect(box.secure).toBe(true);
    // The plan's runtime ceiling as its timeout; the template the config names.
    expect(box.timeout).toBe(CEILING);
    expect(box.template).toBe(env.E2B_TEMPLATE);
    expect(box.metadata["ta_sandbox"]).toMatch(/^[0-9a-f]{32}$/);
    expect(box.metadata["ta_owner"]).toBe(await ownerTag(who));
    expect(JSON.stringify(box.metadata)).not.toContain(who);
    // A signed-in engine's vendor hosts are let out, the egress door kept last.
    expect(box.network.allowOut.at(-1)).toBe("egress.tinyassets.io");
    expect(box.network.allowOut).toEqual(expect.arrayContaining(["claude.ai",
                                                                 "api.anthropic.com"]));
    const create = state.calls.find((call) => call.path === "/v2/sandboxes"
                                    && call.method === "POST")!.body as Record<string, unknown>;
    expect(create).toMatchObject({ secure: true, autoPause: true, autoPauseMemory: true,
                                   autoResume: { enabled: false } });
    // prepare, the sign-in check, the launch: as root, in that order.
    expect(state.commands.map((command) => command.argv.slice(1, 3))).toEqual([
      ["prepare", "1.1.1.1"], ["signin-check", "claude-code"], ["launch", RUN]]);
    expect(state.commands.every((command) => command.user === "root")).toBe(true);
    // The lease's secrets in the root-only state directory; the start document without them.
    const lease = JSON.parse(state.files[`${box.id}:/var/lib/ta/lease.json`]!);
    expect(lease.tokens.runner_token).toBe(hex(64, "d"));
    const doc = state.files[`${box.id}:/var/lib/ta/inbox/${RUN}.json`]!;
    expect(doc).not.toContain(hex(64, "d"));
    expect(JSON.parse(doc)).not.toHaveProperty("tokens");
    expect(JSON.parse(doc)).not.toHaveProperty("network");
    expect(JSON.parse(doc).prompt).toBe("hello");
    // The look is armed a lease's TTL on, for the idle reap, before its renewal window.
    const look = await alarmOf(who);
    expect(look!).toBeGreaterThanOrEqual(began + LEASE_TTL_S * 1000);
    expect(look!).toBeLessThanOrEqual(Date.now() + LEASE_TTL_S * 1000);
  });

  it("refuses a run whose doors are not https on the egress host", async () => {
    const who = owner();
    for (const url of [`http://egress.tinyassets.io/r/${RUN}/events`,
                       `https://elsewhere.example/r/${RUN}/events`,
                       `https://egress.tinyassets.io:8443/r/${RUN}/events`]) {
      const refused = await stub(who).start(who, start({ urls: { ...start().urls, events: url } }))
        .then(() => null, (error: unknown) => error);
      expect(String(refused)).toMatch(/https doors/);
    }
    expect((await seen()).calls).toEqual([]);
  });

  it("answers signin_required, and launches nothing, where no login is in that sandbox", async () => {
    const who = owner();
    expect(await stub(who).start(who, start())).toEqual({ started: false, code: CODES.signin });
    expect((await seen()).commands.map((command) => command.argv[1]))
      .toEqual(["prepare", "signin-check"]);
  });

  it("runs an API-key run with no sign-in check and no vendor hosts", async () => {
    const who = owner();
    expect(await stub(who).start(who, start({ credential: "api_key" })))
      .toEqual({ started: true });
    const state = await seen();
    expect(state.sandboxes[0]!.network.allowOut).toEqual(["github.com",
                                                          "egress.tinyassets.io"]);
    expect(state.commands.map((command) => command.argv[1])).toEqual(["prepare", "launch"]);
    // No scratch retention in the record: the runner deletes no agent's scratch by age.
    const doc = state.files[`${state.sandboxes[0]!.id}:/var/lib/ta/inbox/${RUN}.json`]!;
    expect(JSON.parse(doc).scratch_retention_days).toBeNull();
  });

  it("hands the control record's scratch retention to the runner", async () => {
    await control({ ...OPEN, ...NUMBERS, scratch_retention_days: 30 });
    const who = owner();
    expect(await stub(who).start(who, start({ credential: "api_key" })))
      .toEqual({ started: true });
    const state = await seen();
    const doc = state.files[`${state.sandboxes[0]!.id}:/var/lib/ta/inbox/${RUN}.json`]!;
    expect(JSON.parse(doc).scratch_retention_days).toBe(30);
  });

  it("keeps the last scratch retention read when the record cannot be read, never none",
     async () => {
    await control({ ...OPEN, ...NUMBERS, scratch_retention_days: 30 });
    const who = owner();
    expect(await stub(who).start(who, start({ credential: "api_key" })))
      .toEqual({ started: true });
    await env.CONTROL.put("c:control", "not json");
    const second = `run_${hex(32, "2")}`;
    expect(await stub(who).start(who, start({ run: second, credential: "api_key",
                                             tokens: null, network: null })))
      .toEqual({ started: true });
    const state = await seen();
    const doc = state.files[`${state.sandboxes[0]!.id}:/var/lib/ta/inbox/${second}.json`]!;
    expect(JSON.parse(doc).scratch_retention_days).toBe(30);
  });

  it("joins the live sandbox with no create and no lease secrets; another engine's hosts added",
     async () => {
    await e2b("/script", { signin: { "claude-code": 0, gemini: 0 } });
    const who = owner();
    await stub(who).start(who, start());
    const second = `run_${hex(32, "2")}`;
    expect(await stub(who).start(who, start({ run: second, engine: "gemini", tokens: null,
                                             network: null }))).toEqual({ started: true });
    const state = await seen();
    expect(state.sandboxes).toHaveLength(1);
    expect(state.calls.filter((call) => call.path === "/v2/sandboxes"
                              && call.method === "POST")).toHaveLength(1);
    expect(Object.keys(state.files).filter((path) => path.endsWith("lease.json"))).toHaveLength(1);
    expect(state.sandboxes[0]!.network.allowOut).toEqual(
      expect.arrayContaining(["accounts.google.com", "claude.ai"]));
    expect(state.commands.at(-1)!.argv.slice(1)).toEqual(["launch", second]);
  });

  it("answers sandbox_lost when a joining run finds no sandbox to run in", async () => {
    const who = owner();
    expect(await stub(who).start(who, start({ tokens: null, network: null })))
      .toEqual({ started: false, code: CODES.lost });
    expect((await seen()).sandboxes).toHaveLength(0);
  });

  it("starts nothing when no sandbox numbers were ever read", async () => {
    await control(OPEN);
    const who = owner();
    expect(await stub(who).start(who, start())).toEqual({ started: false,
                                                          code: CODES.unconfigured });
    await control(null);
    expect(await stub(who).start(who, start())).toEqual({ started: false,
                                                          code: CODES.unconfigured });
    expect((await seen()).calls).toEqual([]);
  });

  it("starts on the last numbers read when the record cannot be read", async () => {
    await e2b("/script", { signin: { "claude-code": 0 } });
    const who = owner();
    await stub(who).start(who, start());
    await env.CONTROL.put("c:control", "not json");
    expect(await stub(who).start(who, start({ run: `run_${hex(32, "5")}`, tokens: null,
                                             network: null }))).toEqual({ started: true });
  });

  it("resumes the paused sandbox, its memory kept, and prepares it again", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 0, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    expect(await stub(who).release(who, { lease_id: LEASE.lease_id })).toEqual({ paused: true });
    expect((await seen()).sandboxes[0]!.state).toBe("paused");
    const next = { lease_id: hex(32, "f"), epoch: 2 };
    expect(await stub(who).start(who, start({ run: `run_${hex(32, "3")}`, lease: next })))
      .toEqual({ started: true });
    const state = await seen();
    expect(state.sandboxes).toHaveLength(1);
    expect(state.sandboxes[0]!.state).toBe("running");
    expect(state.calls.some((call) => call.path.endsWith("/connect"))).toBe(true);
    // The opening start's network applied whole to the resumed sandbox.
    expect(state.calls.some((call) => call.method === "PUT" && call.path.endsWith("/network")))
      .toBe(true);
    // The lockdown re-checked at the resume, not assumed from the boot (review M-7).
    expect(state.commands.filter((command) => command.argv[1] === "prepare")).toHaveLength(2);
  });

  it("restarts a rebooted sandbox's runs from disk when E2B kept its files only", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 0, quiet: true },
                           pauseMode: "filesystem" });
    const who = owner();
    await stub(who).start(who, start());
    await stub(who).release(who, { lease_id: LEASE.lease_id });
    await stub(who).start(who, start({ run: `run_${hex(32, "3")}`,
                                       lease: { lease_id: hex(32, "f"), epoch: 2 } }));
    expect((await seen()).commands.map((command) => command.argv[1]))
      .toContain("recover");
  });
});

describe("the platform's line", () => {
  it("makes a run wait, never refused, when E2B runs as many as the record says", async () => {
    await control({ ...OPEN, ...NUMBERS, sandbox_concurrency_ceiling: 3 });
    await e2b("/script", { others: 3 });
    const who = owner();
    expect(await stub(who).start(who, start())).toEqual({ started: false, code: CODES.waiting });
    expect((await seen()).sandboxes).toHaveLength(0);
    expect(await line()).toEqual([who]);
    // Raised in the record, no deploy: the same start goes, and leaves the line.
    await control({ ...OPEN, ...NUMBERS, sandbox_concurrency_ceiling: 4 });
    await e2b("/script", { signin: { "claude-code": 0 } });
    expect(await stub(who).start(who, start())).toEqual({ started: true });
    expect(await line()).toEqual([]);
  });

  it("makes a run wait when E2B itself refuses for room", async () => {
    await e2b("/script", { full: true });
    const who = owner();
    expect(await stub(who).start(who, start())).toEqual({ started: false, code: CODES.waiting });
    expect(await line()).toEqual([who]);
  });

  it("tries the create when E2B gives no count, and waits only on its refusal (M-4)",
     async () => {
    await e2b("/script", { noCount: true, signin: { "claude-code": 0 } });
    const who = owner();
    expect(await stub(who).start(who, start())).toEqual({ started: true });
    await e2b("/script", { full: true });
    const other = owner();
    expect(await stub(other).start(other, start())).toEqual({ started: false,
                                                              code: CODES.waiting });
  });

  it("keeps the order: a newcomer waits behind an owner that waited longer, whatever the room",
     async () => {
    await e2b("/script", { full: true, signin: { "claude-code": 0 } });
    const first = owner();
    const second = owner();
    await stub(first).start(first, start());
    await e2b("/script", { full: false });
    // Room now, but the first in line has not had its turn.
    expect(await stub(second).start(second, start())).toEqual({ started: false,
                                                                code: CODES.waiting });
    expect(await line()).toEqual([first, second]);
    expect(await stub(first).start(first, start())).toEqual({ started: true });
    // The first took its sandbox and left; the next is rung.
    expect(await line()).toEqual([second]);
    await rung(second);
    expect(await stub(second).start(second, start())).toEqual({ started: true });
    expect(await line()).toEqual([]);
  });

  it("rings the first in line when a sandbox of the platform pauses", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 0, quiet: true } });
    const holder = owner();
    await stub(holder).start(holder, start());
    await e2b("/script", { full: true });
    const waiter = owner();
    await stub(waiter).start(waiter, start());
    expect((await seen()).rings).toEqual([]);
    expect(await stub(holder).release(holder, { lease_id: LEASE.lease_id }))
      .toEqual({ paused: true });
    await rung(waiter);
  });

  it("rings the first in line again, backing off, while no free is seen", async () => {
    await e2b("/script", { full: true });
    const waiter = owner();
    const began = Date.now();
    await stub(waiter).start(waiter, start());
    const slots = env.SLOTS.getByName(SLOTS);
    const lineAlarm = () => runInDurableObject(slots, (_i, state) => state.storage.getAlarm());
    const first = await lineAlarm();
    expect(first!).toBeGreaterThanOrEqual(began + LEASE_TTL_S * 1000);  // LEASE_TTL_S first
    expect(first!).toBeLessThanOrEqual(Date.now() + LEASE_TTL_S * 1000);
    await armedAfter(async () => {  // then doubled
      await runDurableObjectAlarm(slots);
      await rung(waiter);
    }, lineAlarm, 2 * LEASE_TTL_S * 1000);
    // The owner with nothing waiting leaves; the line is empty and unarmed.
    await slots.leave(waiter);
    expect(await runInDurableObject(slots, (_i, state) => state.storage.getAlarm())).toBeNull();
  });

  it("does not count a run that joins a sandbox already running", async () => {
    await e2b("/script", { signin: { "claude-code": 0 } });
    const who = owner();
    await stub(who).start(who, start());
    await e2b("/script", { others: 1000 });
    expect(await stub(who).start(who, start({ run: `run_${hex(32, "4")}`, tokens: null,
                                             network: null }))).toEqual({ started: true });
  });
});

describe("cancel, status and release", () => {
  it("cancels through the runner, and reports its status", async () => {
    await e2b("/script", { signin: { "claude-code": 0 } });
    const who = owner();
    await stub(who).start(who, start());
    expect(await stub(who).cancel(who, { run: RUN, lease_id: LEASE.lease_id }))
      .toEqual({ cancelled: true });
    expect((await seen()).commands.at(-1)!.argv.slice(1)).toEqual(["cancel", RUN]);
    expect(await stub(who).status(who, { run: RUN, lease_id: LEASE.lease_id }))
      .toEqual({ state: "running" });
    expect(await stub(who).status(who, { run: `run_${hex(32, "9")}`, lease_id: LEASE.lease_id }))
      .toEqual({ state: "gone" });
  });

  it("pauses on release only when the runner has no live run, and only for its lease",
     async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    expect(await stub(who).release(who, { lease_id: LEASE.lease_id })).toEqual({ paused: false });
    expect(await stub(who).release(who, { lease_id: hex(32, "0") })).toEqual({ paused: false });
    await e2b("/script", { state: { live: 0, quiet: true } });
    const second = { lease_id: hex(32, "e"), epoch: 2 };
    await stub(who).start(who, start({ run: `run_${hex(32, "6")}`, lease: second }));
    expect(await stub(who).release(who, { lease_id: second.lease_id })).toEqual({ paused: true });
    const pauses = (await seen()).calls.filter((call) => call.path.endsWith("/pause"));
    expect(pauses).toHaveLength(1);
    expect(pauses[0]!.body).toEqual({ memory: true });
    // Paused and idle: nothing more to look at.
    expect(await alarmOf(who)).toBeNull();
  });

  it("reaps on its own a sandbox idle for a lease's TTL, its release never come", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: false } });
    const who = owner();
    await stub(who).start(who, start());
    await runDurableObjectAlarm(stub(who));
    expect((await seen()).sandboxes[0]!.state).toBe("running");
    // The run ended; its release never reached this Worker.
    await e2b("/script", { state: { live: 0, quiet: true } });
    await armedAfter(() => runDurableObjectAlarm(stub(who)), () => alarmOf(who),
                     LEASE_TTL_S * 1000);
    expect((await seen()).sandboxes[0]!.state).toBe("running");  // the idle clock starts
    await runInDurableObject(stub(who), (_i, state) => state.storage.sql.exec(
      "UPDATE sandboxes SET idle_since = idle_since - 60000"));
    await runDurableObjectAlarm(stub(who));
    expect((await seen()).sandboxes[0]!.state).toBe("paused");
    expect(await alarmOf(who)).toBeNull();
  });

  it("reaps an idle sandbox whose release's pause did not land (E2B busy)", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 0, quiet: true },
                           busy: 1 });
    const who = owner();
    await stub(who).start(who, start());
    expect(await stub(who).release(who, { lease_id: LEASE.lease_id })).toEqual({ paused: false });
    // The look comes back at once, not at the renewal window (or the runtime ran it already).
    const at = await alarmOf(who);
    expect(at === null || at < Date.now() + 5_000).toBe(true);
    // The look is due at once: the runtime may run it before the test does.
    await runDurableObjectAlarm(stub(who));
    const until = Date.now() + 5_000;
    while ((await seen()).sandboxes[0]!.state !== "paused" && Date.now() < until) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    expect((await seen()).sandboxes[0]!.state).toBe("paused");
    expect(await alarmOf(who)).toBeNull();
  });
});

describe("the look before the runtime ceiling", () => {
  async function aged(seconds: number, state: { live: number; quiet: boolean }) {
    await e2b("/script", { signin: { "claude-code": 0 }, state });
    const who = owner();
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/age", { id, s: seconds });
    const before = (await seen()).calls.length;
    const commandsBefore = (await seen()).commands.length;
    await runDurableObjectAlarm(stub(who));
    const after = await seen();
    return { who, id, calls: after.calls.slice(before),
             commands: after.commands.slice(commandsBefore).map((command) => command.argv[1]),
             box: after.sandboxes[0]! };
  }

  it("does nothing before the window", async () => {
    const { calls, box } = await aged(60, { live: 1, quiet: true });
    expect(calls.filter((call) => call.path.endsWith("/pause"))).toEqual([]);
    expect(box.state).toBe("running");
  });

  it("pauses and resumes the same sandbox at a quiet moment in the window", async () => {
    const began = Date.now();
    const { calls, commands, box, id, who } = await aged(CEILING - WINDOW + 5,
                                                         { live: 1, quiet: true });
    expect(calls.map((call) => call.path).filter((path) => /pause|connect/.test(path)))
      .toEqual([`/sandboxes/${id}/pause`, `/v2/sandboxes/${id}/connect`]);
    expect(box.state).toBe("running");
    expect(box.id).toBe(id);
    expect(commands).toEqual(["quiet-wait", "state", "renew-wait", "prepare", "renewed"]);
    // The window moved to the resume: the next look is the idle one, a lease's TTL on.
    expect(box.timeout).toBe(CEILING);
    expect(await alarmOf(who)).toBeLessThanOrEqual(Date.now() + 60_000);
    const row = await runInDurableObject(stub(who), (_i, state) => state.storage.sql
      .exec<{ end_at: number }>("SELECT end_at FROM sandboxes").one());
    // A whole ceiling on from the resume, which lies between the two clock reads.
    expect(row.end_at).toBeGreaterThanOrEqual(began + CEILING * 1000);
    expect(row.end_at).toBeLessThanOrEqual(Date.now() + CEILING * 1000);
  });

  it("goes on looking for a quiet moment inside the window, the starts going on", async () => {
    const { calls, who, id } = await aged(CEILING - WINDOW + 5, { live: 1, quiet: false });
    expect(calls.filter((call) => call.path.endsWith("/pause"))).toEqual([]);
    // The runner waits for its CLIs to be quiet to the edge at most (the fake answers at
    // once, as one whose look ran out the alarm's wall clock), and the look goes on.
    const look = (await seen()).commands.find((command) => command.argv[1] === "quiet-wait")!;
    const box = (await seen()).sandboxes.find((each) => each.id === id)!;
    expect(Number(look.argv[2])).toBeLessThanOrEqual(Date.now() + WINDOW * 1000);
    expect(box.state).toBe("running");
    expect(await stub(who).status(who, { run: RUN, lease_id: LEASE.lease_id }))
      .toEqual({ state: "running" });
  });

  it("is forced at the ceiling's edge, quiet or not", async () => {
    const { calls, commands } = await aged(CEILING - RENEW_EDGE_MS / 1000 + 0.5,
                                           { live: 1, quiet: false });
    expect(calls.filter((call) => call.path.endsWith("/pause"))).toHaveLength(1);
    expect(calls.filter((call) => call.path.endsWith("/connect"))).toHaveLength(1);
    expect(commands.at(-1)).toBe("renewed");
  });

  it("pauses a sandbox with no live run instead, and rings the line", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 0, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/script", { full: true });
    const waiter = owner();
    await stub(waiter).start(waiter, start());
    await e2b("/age", { id, s: CEILING - WINDOW + 5 });
    await runDurableObjectAlarm(stub(who));
    const state = await seen();
    expect(state.calls.filter((call) => call.path.endsWith("/connect"))).toEqual([]);
    expect(state.sandboxes.find((box) => box.id === id)!.state).toBe("paused");
    expect(await alarmOf(who)).toBeNull();
    await rung(waiter);
  });

  it("reads the window from the control record: a raised ceiling moves it", async () => {
    await control({ ...OPEN, ...NUMBERS, sandbox_runtime_ceiling_s: 86_400 });
    const { calls, box } = await aged(CEILING - WINDOW + 5, { live: 1, quiet: true });
    expect(box.timeout).toBe(86_400);
    expect(calls.filter((call) => call.path.endsWith("/pause"))).toEqual([]);
  });

  it("renews on the last numbers read when the record is unreadable (H-5)", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/age", { id, s: CEILING - WINDOW + 5 });
    await env.CONTROL.put("c:control", "not json");
    await runDurableObjectAlarm(stub(who));
    expect((await seen()).calls.filter((call) => call.path.endsWith("/connect")))
      .toHaveLength(1);
    // The founder taking the numbers out stops new starts only: renewals go on.
    await control(OPEN);
    await e2b("/age", { id, s: CEILING - WINDOW + 5 });
    await runDurableObjectAlarm(stub(who));
    expect((await seen()).calls.filter((call) => call.path.endsWith("/connect")))
      .toHaveLength(2);
  });

  it("is armed again when a look fails, before the edge (H-5)", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/age", { id, s: CEILING - WINDOW + 5 });
    await e2b("/script", { down: 1000 });
    await runDurableObjectAlarm(stub(who));
    const retry = await alarmOf(who);
    expect(retry).not.toBeNull();
    expect(retry!).toBeLessThan(Date.now() + 2_000);
    // E2B back: the next look renews.
    await e2b("/script", { down: 0 });
    await runDurableObjectAlarm(stub(who));
    expect((await seen()).calls.filter((call) => call.path.endsWith("/connect")))
      .toHaveLength(1);
  });

  it("resumes a sandbox E2B paused while a lease runs in it, its slot its own (H-5)",
     async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: false } });
    const who = owner();
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    // E2B's own timeout paused it (the look was late), and the team is full: other owners
    // wait in line.
    await e2b("/pause", { id });
    await e2b("/script", { others: 1000 });
    const waiter = owner();
    expect(await stub(waiter).start(waiter, start())).toEqual({ started: false,
                                                               code: CODES.waiting });
    await runDurableObjectAlarm(stub(who));
    const state = await seen();
    expect(state.sandboxes.find((box) => box.id === id)!.state).toBe("running");
    expect(state.commands.filter((c) => c.sandbox === id).map((c) => c.argv[1]).slice(-1))
      .toEqual(["prepare"]);
  });

  it("asks again while E2B refuses a renewal's resume, never giving up (H-5)", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/age", { id, s: CEILING - WINDOW + 5 });
    await e2b("/script", { full: true });
    await runDurableObjectAlarm(stub(who));
    expect((await seen()).sandboxes[0]!.state).toBe("paused");
    expect(await alarmOf(who)).toBeLessThan(Date.now() + 5_000);
    await runDurableObjectAlarm(stub(who));
    expect((await seen()).sandboxes[0]!.state).toBe("paused");
    await e2b("/script", { full: false });
    await runDurableObjectAlarm(stub(who));
    const state = await seen();
    expect(state.sandboxes[0]!.state).toBe("running");
    expect(state.commands.at(-1)!.argv[1]).toBe("renewed");
  });

  it("backs off through an E2B outage past the edge, never at alarm speed (F-1)", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: false } });
    const who = owner();
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/age", { id, s: CEILING + 5 });  // past its edge: E2B's own timeout's now
    await e2b("/script", { down: 1_000_000 });
    // Each failed look arms the next at its back-off, doubling: the delay the code
    // schedules, read against the clock around the alarm, never time passed since.
    expect(backoff(2)).toBe(2 * backoff(1));
    for (let tries = 1; tries <= 4; tries += 1) {
      await armedAfter(() => runDurableObjectAlarm(stub(who)), () => alarmOf(who),
                       backoff(tries));
    }
    await e2b("/script", { down: 0 });
  });

  it("asks a paused sandbox a lease runs in to resume backing off while E2B refuses (F-1)",
     async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: false } });
    const who = owner();
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/pause", { id });
    await e2b("/script", { refuseResume: true });
    // Asked at once the first time (the pause), then each refusal arms the next try at
    // its back-off: the delay the code schedules, never time passed since.
    for (let tries = 1; tries <= 4; tries += 1) {
      await armedAfter(() => runDurableObjectAlarm(stub(who)), () => alarmOf(who),
                       backoff(tries));
    }
    await e2b("/script", { refuseResume: false });
    await runDurableObjectAlarm(stub(who));
    expect((await seen()).sandboxes[0]!.state).toBe("running");
  });

  it("keeps a renewal's slot across its pause: the first in line cannot take it (F-2)",
     async () => {
    await control({ ...OPEN, ...NUMBERS, sandbox_concurrency_ceiling: 2 });
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/age", { id, s: CEILING - WINDOW + 5 });
    // The renewal's pause lands, its resume is refused: the slot E2B's count shows free is
    // the renewal's, held in the line.
    await e2b("/script", { others: 1, refuseResume: true });
    await runDurableObjectAlarm(stub(who));
    expect((await seen()).sandboxes[0]!.state).toBe("paused");
    expect((await env.SLOTS.getByName(SLOTS).turn("nobody")).reserved).toBe(1);
    const creates = (await seen()).calls.filter((call) => call.path === "/v2/sandboxes"
                                                && call.method === "POST").length;
    const waiter = owner();
    expect(await stub(waiter).start(waiter, start())).toEqual({ started: false,
                                                               code: CODES.waiting });
    expect((await seen()).calls.filter((call) => call.path === "/v2/sandboxes"
                                       && call.method === "POST")).toHaveLength(creates);
    // The resume goes: the slot is given back and the line rung.
    await e2b("/script", { refuseResume: false });
    await runDurableObjectAlarm(stub(who));
    expect((await seen()).sandboxes.find((box) => box.id === id)!.state).toBe("running");
    expect((await env.SLOTS.getByName(SLOTS).turn("nobody")).reserved).toBe(0);
    await rung(waiter);
  });

  it("leaves a paused, idle sandbox alone, with no alarm", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 0, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    await stub(who).release(who, { lease_id: LEASE.lease_id });
    expect(await alarmOf(who)).toBeNull();
  });
});

describe("the meter's log (slice P)", () => {
  it("logs the create, the pause and the resume, and drops what a read's cursor passed",
     async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 0, quiet: true } });
    const who = owner();
    const before = Date.now();
    await stub(who).start(who, start());
    await stub(who).release(who, { lease_id: LEASE.lease_id });
    await stub(who).start(who, start({ run: `run_${hex(32, "3")}`,
                                       lease: { lease_id: hex(32, "f"), epoch: 2 } }));
    const read = await stub(who).meter(who, { after: 0 });
    expect(read.events.map((event) => [event.authority, event.kind]))
      .toEqual([["owner", "start"], ["owner", "pause"], ["owner", "resume"]]);
    expect(read.events.every((event) => event.at >= before && event.at <= Date.now()))
      .toBe(true);
    expect(read.next).toBe(read.events.at(-1)!.seq);
    // Read past: dropped, and nothing new.
    expect(await stub(who).meter(who, { after: read.next }))
      .toEqual({ events: [], next: read.next });
  });
});

describe("a sign-in", () => {
  const SI = `si_${"5".repeat(32)}`;
  function signin(over: Record<string, unknown> = {}) {
    return { id: SI, engine: "kimi", region: "global", mode: "in" as const, authority: "owner",
             lease: LEASE, tokens: start().tokens, network: NETWORK as never,
             urls: { events: `https://egress.tinyassets.io/signin/${SI}/events`,
                     signin: "https://egress.tinyassets.io/signin" }, ...over };
  }

  it("starts the record's login in the authority's sandbox, its region's hosts let out",
     async () => {
    const who = owner();
    expect(await stub(who).signin(who, signin() as never)).toEqual({ started: true });
    const state = await seen();
    const box = state.sandboxes[0]!;
    expect(box.network.allowOut).toEqual(expect.arrayContaining(["auth.kimi.ai", "api.kimi.ai"]));
    expect(box.network.allowOut).not.toContain("auth.kimi.com");
    expect(state.commands.map((command) => command.argv.slice(1, 3))).toEqual([
      ["prepare", "1.1.1.1"], ["login-launch", SI]]);
    const doc = JSON.parse(state.files[`${box.id}:/var/lib/ta/inbox/${SI}.json`]!);
    expect(doc).toEqual({ id: SI, engine: "kimi", region: "global", mode: "in",
                          authority: "owner", urls: signin().urls });
    expect(JSON.parse(state.files[`${box.id}:/var/lib/ta/lease.json`]!).tokens.runner_token)
      .toBe("d".repeat(64));
    // A sign-in in progress keeps the sandbox leased: its look is the idle one, not a reap.
    expect(await alarmOf(who)).not.toBeNull();
  });

  it("hands a pasted code to the live sign-in, and stops it", async () => {
    const who = owner();
    await stub(who).signin(who, signin({ engine: "claude-code", region: null }) as never);
    expect(await stub(who).signinInput(who, { authority: "owner", id: SI, code: "abc#d" }))
      .toEqual({ delivered: true });
    const state = await seen();
    const box = state.sandboxes[0]!;
    expect(state.files[`${box.id}:/var/lib/ta/inbox/${SI}.code`]).toBe("abc#d");
    expect(state.commands.at(-1)!.argv.slice(1)).toEqual(["login-input", SI]);
    expect(await stub(who).signinCancel(who, { authority: "owner", id: SI }))
      .toEqual({ cancelled: true });
    expect((await seen()).commands.at(-1)!.argv.slice(1)).toEqual(["login-cancel", SI]);
    // No sandbox running in a client's name: nothing to hand a code to.
    expect(await stub(who).signinInput(who, { authority: "c".repeat(64), id: SI, code: "x" }))
      .toEqual({ delivered: false });
  });

  it("drops a pasted code whose hand-over failed: it never rests on disk (review L1)",
     async () => {
    const who = owner();
    await stub(who).signin(who, signin({ engine: "claude-code", region: null }) as never);
    await e2b("/script", { broken: "login-input" });
    const failed = await stub(who).signinInput(who, { authority: "owner", id: SI, code: "abc#d" })
      .then(() => null, (error: unknown) => error);
    expect(failed).toBeInstanceOf(Error);
    expect((await seen()).commands.slice(-2).map((command) => command.argv.slice(1)))
      .toEqual([["login-input", SI], ["login-drop", SI]]);
  });

  it("waits its turn in the line as a start does", async () => {
    await e2b("/script", { full: true });
    const who = owner();
    expect(await stub(who).signin(who, signin() as never)).toEqual({ started: false,
                                                                     code: CODES.waiting });
    expect(await line()).toEqual([who]);
  });

  it("refuses a sign-in that is not one: its id, a region the record lacks, plain HTTP", async () => {
    const who = owner();
    for (const bad of [signin({ id: "run_x" }), signin({ region: "moon" }),
                       signin({ urls: { events: "http://egress.tinyassets.io/x",
                                        signin: "https://egress.tinyassets.io/signin" } })]) {
      const refused = await stub(who).signin(who, bad as never)
        .then(() => null, (error: unknown) => error);
      expect(refused).not.toBeNull();
    }
    expect((await seen()).sandboxes).toEqual([]);
  });
});

describe("an erasure", () => {
  it("kills every sandbox of the owner, paused and strays included, and forgets the owner",
     async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 0, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    const client = hex(64, "c");
    await stub(who).start(who, start({ run: `run_${hex(32, "7")}`, authority: client,
                                       lease: { lease_id: hex(32, "7"), epoch: 1 } }));
    await stub(who).release(who, { lease_id: LEASE.lease_id });
    const stray = await e2b("/stray", { metadata: { ta_owner: await ownerTag(who) } });
    const other = owner();
    await stub(other).start(other, start());
    await e2b("/script", { full: true });
    await stub(who).start(who, start({ run: `run_${hex(32, "8")}`, tokens: null,
                                       network: null, authority: hex(64, "9") }));
    expect(await env.SANDBOXES.get(env.SANDBOXES.idFromName(who)).erase(who))
      .toEqual({ killed: 3 });
    const left = (await seen()).sandboxes;
    expect(left.map((box) => box.id)).not.toContain(stray["id"]);
    expect(left).toHaveLength(1);  // the other owner's alone
    expect(await line()).not.toContain(who);
    expect(await runInDurableObject(stub(who), (_i, state) => state.storage.sql
      .exec("SELECT COUNT(*) AS n FROM sandboxes").one().n)).toBe(0);
    expect(await alarmOf(who)).toBeNull();
  });
});

describe("the network", () => {
  it("is widened to vendor hosts only when it lists names, the egress door last", () => {
    const base = NETWORK.network as never;
    expect(widened(base, ["claude.ai", "github.com"])!.allowOut)
      .toEqual(["claude.ai", "github.com", "egress.tinyassets.io"]);
    expect(widened(base, ["github.com"])).toBeNull();
    expect(widened({ ...NETWORK.network, allowOut: ["*", "egress.tinyassets.io"] } as never,
                   ["claude.ai"])).toBeNull();
  });
});
