/**
 * The sandbox Worker against a fake E2B (`fake-e2b.ts`), in workerd: a start creates
 * (`secure`), resumes or joins the authority's sandbox and launches the run's runner; a run
 * on the vendor's sign-in with none there is `signin_required`; past the concurrency ceiling
 * or behind an owner that waited longer a run waits in the platform's line, never refused,
 * until a freed sandbox rings it; a release pauses an idle sandbox, and one whose pause did
 * not land is reaped by the look; the look renews a sandbox before its runtime ceiling, at a
 * quiet moment or forced at the edge, resumes one paused while a lease runs in it, and is
 * armed again on every path (slice S review H-5); an erasure kills every sandbox of the
 * owner, paused and strays included (H-6). The runner is the Worker's own, by digest: a
 * create uploads and installs it before its first command, a resume with it there sends
 * nothing more, an absent one is installed and asked again, bytes not their digest launch
 * nothing, and a sandbox on another runner keeps it while anything lives and adopts the
 * Worker's at the first start that finds nothing living.
 */
import { env, runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

import { OPEN_GATES, writeControl } from "../../ci/fixtures";
import {
  closedNetwork, EGRESS_HOST, egressTokenOf, GENERATION_HEADER, generationOf, runUrls,
  signinEventsUrl, withEgressToken, withGeneration,
} from "../../contract/src/egress";
import { LEASE_TTL_S } from "../../contract/src/limits";
import { MIGRATION_CHANGED, migrate, migrationDigest } from "../../contract/src/migrate";
import { inboxCode, inboxDoc, INBOX, INCOMING, LEASE_FILE, PROTOCOL,
         SPEAKS } from "../runner/protocol";
import { PROTOCOLS, shapeProblems } from "../runner/test/state-shape";
import { runnerBytes, runnerDigest } from "../src/runner-bytes";
import { CONTROL_MIGRATIONS, SLOTS_MIGRATIONS } from "../src/schema";
import { SandboxSlots, SLOTS } from "../src/slots";
import type { SandboxStart } from "../../contract/src/sandbox";

const OPEN = OPEN_GATES;
const CEILING = 3600;
const WINDOW = 600;
const NUMBERS = { sandbox_runtime_ceiling_s: CEILING, sandbox_concurrency_ceiling: 20,
                  sandbox_renewal_window_s: WINDOW };

async function control(value: unknown): Promise<void> {
  if (value === null) {
    await writeControl(env.CONTROL, null);
  } else {
    await writeControl(env.CONTROL, JSON.stringify(value));
  }
}

async function e2b(path: string, body?: unknown): Promise<Record<string, unknown>> {
  const response = await env.E2B.fetch(`https://fake.test${path}`, body === undefined ? {}
    : { method: "POST", body: JSON.stringify(body) });
  return response.json();
}

interface Seen {
  sandboxes: { id: string; state: string; timeout: number; network: { allowOut: string[] };
               template: string; metadata: Record<string, string>; secure: boolean;
               runners: string[] }[];
  calls: { method: string; path: string; body: unknown; query: string }[];
  files: Record<string, string>;
  commands: { sandbox: string; argv: string[]; runner: string | null; user: string }[];
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
/** The lease's egress token, a marker: only the network's transform carries it. */
const EGRESS_TOKEN = "egress-token-0f1e2d3c4b5a";
const NETWORK = {
  network: withEgressToken(withGeneration(closedNetwork(["github.com"]), 1), EGRESS_TOKEN),
  mode: "allowlist", generation: 1, unexpressed: [], receipt: false, boundary: false, note: "",
};

function start(over: Partial<SandboxStart> = {}): SandboxStart {
  return {
    run: RUN, agent: "main", conversation: `cv_${hex(32, "c")}`, engine: "claude-code",
    credential: "vendor_login_in_sandbox", model: null, session_id: null, prompt: "hello",
    unattended: false, authority: "owner", lease: LEASE, route: "r".repeat(64),
    tokens: { sync_token: "sync", runner_token: hex(64, "d") },
    network: NETWORK as never,
    config: { generation: 1, egress: { mode: "allowlist", destinations: [] },
              env: { HOME: "{home}" }, inherit: ["PATH"] },
    urls: runUrls(RUN, hex(64, "e")),
    ...over,
  };
}

const owner = () => `user_${crypto.randomUUID().replace(/-/g, "")}`;
const stub = (who: string) => env.SANDBOXES.getByName(who);
/** How the line stands: its owners in order, read from the object's own table. */
const line = () => runInDurableObject(env.SLOTS.getByName(SLOTS), (_instance, state) =>
  state.storage.sql.exec<{ owner: string }>("SELECT owner FROM waiting ORDER BY seq").toArray()
    .map((row) => row.owner));
const alarmOf = (who: string) => runInDurableObject(stub(who), (_instance, state) =>
  state.storage.getAlarm());

/**
 * The retry schedule as the design states it, written here rather than read from the code:
 * the first retry after the slowest full request measured to E2B, 480 ms
 * (`e2b-resume-measurement.md`), doubling after, and never longer than a lease's TTL
 * (a run the owner has not heard from in `LEASE_TTL_S` is settled lost).
 */
const RETRY_SCHEDULE_MS = [480, 960, 1_920, 3_840, 7_680, 15_360, 30_720, 60_000, 60_000] as const;

/**
 * The renewal's edge as the design states it: the runner's `renewing` (252 ms) and then the
 * longest pause (705 ms), both measured (`sandbox-probe.md`, `e2b-resume-measurement.md`).
 */
const RENEW_EDGE_MS = 252 + 705;

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
    // One opaque name, the owner's tag, beside the sealed route.
    expect(Object.keys(box.metadata).sort()).toEqual(["ta_owner", "ta_route"]);
    expect(box.metadata["ta_owner"]).toMatch(/^[0-9a-f]{32}$/);
    expect(JSON.stringify(box.metadata)).not.toContain(who);
    // A signed-in engine's vendor hosts are let out, the egress door kept last.
    expect(box.network.allowOut.at(-1)).toBe(EGRESS_HOST);
    expect(box.network.allowOut).toEqual(expect.arrayContaining(["claude.ai",
                                                                 "api.anthropic.com"]));
    const create = state.calls.find((call) => call.path === "/v2/sandboxes"
                                    && call.method === "POST")!.body as Record<string, unknown>;
    expect(create).toMatchObject({ secure: true, autoPause: true, autoPauseMemory: true,
                                   autoResume: { enabled: false } });
    // The Worker's runner installed, then prepare, the sign-in check, the launch, each by
    // that runner's digest: as root, in that order.
    const digest = await runnerDigest();
    expect(state.commands.map((command) => command.argv.slice(1, 3))).toEqual([
      ["install", digest], ["prepare", "1.1.1.1"], ["signin-check", "claude-code"],
      ["launch", RUN]]);
    expect(state.commands.slice(1).every((command) => command.runner === digest)).toBe(true);
    expect(state.files[`${box.id}:${INCOMING}/${digest}`])
      .toBe(`sha256:${digest} bytes:${runnerBytes().byteLength}`);
    expect(state.commands.every((command) => command.user === "root")).toBe(true);
    // The lease's secrets in the root-only state directory; the start document without them.
    // Only the two tokens the runner reads: the egress token never enters the VM.
    const leaseFile = state.files[`${box.id}:${LEASE_FILE}`]!;
    const lease = JSON.parse(leaseFile);
    expect(lease.tokens).toEqual({ runner_token: hex(64, "d"), sync_token: "sync" });
    expect(leaseFile).not.toContain(EGRESS_TOKEN);
    const doc = state.files[`${box.id}:${inboxDoc(RUN)}`]!;
    // Both are the documents the runner protocol records, field for field.
    const documents = PROTOCOLS[String(PROTOCOL)]!.documents;
    expect(shapeProblems(lease, documents.lease)).toEqual([]);
    expect(shapeProblems(JSON.parse(doc), documents.start)).toEqual([]);
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
    for (const url of [`http://${EGRESS_HOST}/r/${RUN}/events`,
                       `https://elsewhere.example/r/${RUN}/events`,
                       `https://${EGRESS_HOST}:8443/r/${RUN}/events`]) {
      const refused = await stub(who).start(who, start({ urls: { ...start().urls, events: url } }))
        .then(() => null, (error: unknown) => error);
      expect(String(refused)).toMatch(/https doors/);
    }
    expect((await seen()).calls).toEqual([]);
  });

  it("answers signin_required, and launches nothing, where no login is in that sandbox", async () => {
    const who = owner();
    expect(await stub(who).start(who, start())).toEqual({ started: false, code: "signin_required" });
    const state = await seen();
    expect(state.commands.map((command) => command.argv[1]))
      .toEqual(["install", "prepare", "signin-check"]);
    // The check comes first: no start document waits in the inbox for a run refused.
    expect(Object.keys(state.files).filter((path) => path.includes(`${INBOX}/`)))
      .toEqual([]);
  });

  it("runs an API-key run with no sign-in check and no vendor hosts", async () => {
    const who = owner();
    expect(await stub(who).start(who, start({ credential: "api_key" })))
      .toEqual({ started: true });
    const state = await seen();
    expect(state.sandboxes[0]!.network.allowOut).toEqual(["github.com", EGRESS_HOST]);
    expect(state.commands.map((command) => command.argv[1]))
      .toEqual(["install", "prepare", "launch"]);
    // No scratch retention in the record: the runner deletes no agent's scratch by age.
    const doc = state.files[`${state.sandboxes[0]!.id}:${inboxDoc(RUN)}`]!;
    expect(JSON.parse(doc).scratch_retention_days).toBeNull();
  });

  it("hands the control record's scratch retention to the runner", async () => {
    await control({ ...OPEN, ...NUMBERS, scratch_retention_days: 30 });
    const who = owner();
    expect(await stub(who).start(who, start({ credential: "api_key" })))
      .toEqual({ started: true });
    const state = await seen();
    const doc = state.files[`${state.sandboxes[0]!.id}:${inboxDoc(RUN)}`]!;
    expect(JSON.parse(doc).scratch_retention_days).toBe(30);
  });

  it("keeps the last scratch retention read when the record cannot be read, never none",
     async () => {
    await control({ ...OPEN, ...NUMBERS, scratch_retention_days: 30 });
    const who = owner();
    expect(await stub(who).start(who, start({ credential: "api_key" })))
      .toEqual({ started: true });
    await writeControl(env.CONTROL, "not json");
    const second = `run_${hex(32, "2")}`;
    expect(await stub(who).start(who, start({ run: second, credential: "api_key",
                                             tokens: null, network: null })))
      .toEqual({ started: true });
    const state = await seen();
    const doc = state.files[`${state.sandboxes[0]!.id}:${inboxDoc(second)}`]!;
    expect(JSON.parse(doc).scratch_retention_days).toBe(30);
  });

  // The kept retention is the one a start last handed to a runner: a read that hands none
  // (a start refused before its upload, a renewal look) keeps nothing.
  for (const [what, between] of [
    ["a start refused at its sign-in", async (who: string) => {
      expect(await stub(who).start(who, start({ run: `run_${hex(32, "2")}`, tokens: null,
                                               network: null })))
        .toEqual({ started: false, code: "signin_required" });
    }],
    ["a renewal look", async (who: string) => {
      await e2b("/age", { id: (await seen()).sandboxes[0]!.id, s: CEILING - WINDOW + 5 });
      await runDurableObjectAlarm(stub(who));
    }],
  ] as const) {
    it(`keeps the retention the last start handed, not one ${what} read`, async () => {
      await control({ ...OPEN, ...NUMBERS, scratch_retention_days: 30 });
      const who = owner();
      expect(await stub(who).start(who, start({ credential: "api_key" })))
        .toEqual({ started: true });
      await control({ ...OPEN, ...NUMBERS, scratch_retention_days: 7 });
      await between(who);
      await writeControl(env.CONTROL, "not json");
      const third = `run_${hex(32, "3")}`;
      expect(await stub(who).start(who, start({ run: third, credential: "api_key",
                                               tokens: null, network: null })))
        .toEqual({ started: true });
      const state = await seen();
      const doc = state.files[`${state.sandboxes.at(-1)!.id}:${inboxDoc(third)}`]!;
      expect(JSON.parse(doc).scratch_retention_days).toBe(30);
    });
  }

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
      .toEqual({ started: false, code: "sandbox_lost" });
    expect((await seen()).sandboxes).toHaveLength(0);
  });

  it("starts nothing when no sandbox numbers were ever read", async () => {
    await control(OPEN);
    const who = owner();
    expect(await stub(who).start(who, start())).toEqual({ started: false,
                                                          code: "sandbox_unconfigured" });
    await control(null);
    expect(await stub(who).start(who, start())).toEqual({ started: false,
                                                          code: "sandbox_unconfigured" });
    expect((await seen()).calls).toEqual([]);
  });

  it("starts nothing new once the founder takes the numbers out, kept numbers or not",
     async () => {
    const who = owner();
    expect(await stub(who).start(who, start({ credential: "api_key" })))
      .toEqual({ started: true });
    await control(OPEN);
    expect(await stub(who).start(who, start({ run: `run_${hex(32, "4")}`, credential: "api_key",
                                             tokens: null, network: null })))
      .toEqual({ started: false, code: "sandbox_unconfigured" });
  });

  it("starts on the last numbers read when the record cannot be read", async () => {
    await e2b("/script", { signin: { "claude-code": 0 } });
    const who = owner();
    await stub(who).start(who, start());
    await writeControl(env.CONTROL, "not json");
    expect(await stub(who).start(who, start({ run: `run_${hex(32, "5")}`, tokens: null,
                                             network: null }))).toEqual({ started: true });
  });

  it("resumes the paused sandbox, its memory kept, and prepares it again", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 0, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    expect(await stub(who).release({ lease_id: LEASE.lease_id })).toEqual({ paused: true });
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
    await stub(who).release({ lease_id: LEASE.lease_id });
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
    expect(await stub(who).start(who, start())).toEqual({ started: false, code: "sandbox_waiting" });
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
    expect(await stub(who).start(who, start())).toEqual({ started: false, code: "sandbox_waiting" });
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
                                                              code: "sandbox_waiting" });
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
                                                                code: "sandbox_waiting" });
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
    expect(await stub(holder).release({ lease_id: LEASE.lease_id }))
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
    expect(await stub(who).cancel({ run: RUN, lease_id: LEASE.lease_id }))
      .toEqual({ cancelled: true });
    expect((await seen()).commands.at(-1)!.argv.slice(1)).toEqual(["cancel", RUN]);
    expect(await stub(who).status({ run: RUN, lease_id: LEASE.lease_id }))
      .toEqual({ state: "running" });
    expect(await stub(who).status({ run: `run_${hex(32, "9")}`, lease_id: LEASE.lease_id }))
      .toEqual({ state: "gone" });
  });

  it("pauses on release only when the runner has no live run, and only for its lease",
     async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    expect(await stub(who).release({ lease_id: LEASE.lease_id })).toEqual({ paused: false });
    expect(await stub(who).release({ lease_id: hex(32, "0") })).toEqual({ paused: false });
    await e2b("/script", { state: { live: 0, quiet: true } });
    const second = { lease_id: hex(32, "e"), epoch: 2 };
    await stub(who).start(who, start({ run: `run_${hex(32, "6")}`, lease: second }));
    expect(await stub(who).release({ lease_id: second.lease_id })).toEqual({ paused: true });
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
    expect(await stub(who).release({ lease_id: LEASE.lease_id })).toEqual({ paused: false });
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
    expect(await stub(who).status({ run: RUN, lease_id: LEASE.lease_id }))
      .toEqual({ state: "running" });
  });

  it("is forced at the ceiling's edge, quiet or not", async () => {
    // 200 ms inside the edge: renewed, however the look's own calls take.
    const { calls, commands } = await aged(CEILING - (RENEW_EDGE_MS - 200) / 1000,
                                           { live: 1, quiet: false });
    expect(calls.filter((call) => call.path.endsWith("/pause"))).toHaveLength(1);
    expect(calls.filter((call) => call.path.endsWith("/connect"))).toHaveLength(1);
    expect(commands.at(-1)).toBe("renewed");
  });

  it("is not forced before the edge, while the CLIs are busy", async () => {
    const { calls, box } = await aged(CEILING - (RENEW_EDGE_MS + 2_000) / 1000,
                                      { live: 1, quiet: false });
    expect(calls.filter((call) => call.path.endsWith("/pause"))).toEqual([]);
    expect(box.state).toBe("running");
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
    await writeControl(env.CONTROL, "not json");
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
                                                               code: "sandbox_waiting" });
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
    // Each try arms the next at the schedule's wait, read against the clock around the
    // alarm, never time passed since.
    for (const wait of RETRY_SCHEDULE_MS) {
      await armedAfter(() => runDurableObjectAlarm(stub(who)), () => alarmOf(who), wait);
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
    // Each try arms the next at the schedule's wait, read against the clock around the
    // alarm, never time passed since.
    for (const wait of RETRY_SCHEDULE_MS) {
      await armedAfter(() => runDurableObjectAlarm(stub(who)), () => alarmOf(who), wait);
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
                                                               code: "sandbox_waiting" });
    expect((await seen()).calls.filter((call) => call.path === "/v2/sandboxes"
                                       && call.method === "POST")).toHaveLength(creates);
    // The resume goes: the slot is given back and the line rung.
    await e2b("/script", { refuseResume: false });
    await runDurableObjectAlarm(stub(who));
    expect((await seen()).sandboxes.find((box) => box.id === id)!.state).toBe("running");
    expect((await env.SLOTS.getByName(SLOTS).turn("nobody")).reserved).toBe(0);
    await rung(waiter);
  });

  it("gives a gone renewal's slot back to the line at the next look when the line failed " +
     "to take it", async () => {
    await control({ ...OPEN, ...NUMBERS, sandbox_concurrency_ceiling: 2 });
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/age", { id, s: CEILING - WINDOW + 5 });
    // The renewal's pause lands and its resume is refused: its slot held in the line.
    await e2b("/script", { others: 1, refuseResume: true });
    await runDurableObjectAlarm(stub(who));
    const reserved = async () => (await env.SLOTS.getByName(SLOTS).turn("nobody")).reserved;
    expect(await reserved()).toBe(1);
    // Its sandbox is gone from E2B, and the line refuses the first give-back.
    await e2b("/gone", { id });
    const unreserve = SandboxSlots.prototype.unreserve;
    let refused = 0;
    SandboxSlots.prototype.unreserve = async function (this: SandboxSlots, sandboxId: string) {
      if (refused === 0) {
        refused += 1;
        throw new Error("the line is unreachable");
      }
      return unreserve.call(this, sandboxId);
    };
    try {
      await runDurableObjectAlarm(stub(who));  // the sandbox forgotten; its give-back refused
      expect(refused).toBe(1);
      await runDurableObjectAlarm(stub(who));  // asked again: the line takes it
      expect(await reserved()).toBe(0);
    } finally {
      SandboxSlots.prototype.unreserve = unreserve;
    }
  });

  it("gives a gone renewal's slot back to the line though another sandbox's look fails",
     async () => {
    await control({ ...OPEN, ...NUMBERS, sandbox_concurrency_ceiling: 3 });
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    // A second sandbox of the same owner's, a client's.
    const run = `run_${hex(32, "4")}`;
    await stub(who).start(who, start({ run, authority: hex(64, "c"),
                                       lease: { lease_id: hex(32, "9"), epoch: 1 },
                                       urls: runUrls(run, hex(64, "e")) }));
    expect((await seen()).sandboxes).toHaveLength(2);
    await e2b("/age", { id, s: CEILING - WINDOW + 5 });
    await e2b("/script", { others: 1, refuseResume: true });
    await runDurableObjectAlarm(stub(who));
    const reserved = async () => (await env.SLOTS.getByName(SLOTS).turn("nobody")).reserved;
    expect(await reserved()).toBe(1);
    // The renewal's sandbox gone; the other's runner speaks a protocol this Worker does
    // not, so every look fails after it forgets the gone one.
    await e2b("/gone", { id });
    await e2b("/script", { protocol: PROTOCOL + 5 });
    await runDurableObjectAlarm(stub(who));
    await runDurableObjectAlarm(stub(who));
    expect(await reserved()).toBe(0);
  });

  /** A renewal's slot held in the line, its sandbox then gone from E2B unseen. */
  async function heldThenGone(who: string): Promise<() => Promise<number>> {
    await control({ ...OPEN, ...NUMBERS, sandbox_concurrency_ceiling: 2 });
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: true } });
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/age", { id, s: CEILING - WINDOW + 5 });
    await e2b("/script", { others: 1, refuseResume: true });
    await runDurableObjectAlarm(stub(who));
    const reserved = async () => (await env.SLOTS.getByName(SLOTS).turn("nobody")).reserved;
    expect(await reserved()).toBe(1);
    await e2b("/gone", { id });
    return reserved;
  }

  it("arms the look at once while a slot is owed, so one a release forgot is given back",
     async () => {
    const who = owner();
    const reserved = await heldThenGone(who);
    // The release finds the sandbox gone: forgotten, its slot owed, and no row left to look at.
    expect(await stub(who).release({ lease_id: LEASE.lease_id })).toEqual({ paused: false });
    // The look armed at once takes it to the line, with no one running the alarm by hand.
    const until = Date.now() + 5_000;
    while (await reserved() > 0) {
      expect(Date.now()).toBeLessThan(until);
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
  });

  it("gives back every slot owed before an erasure deletes the object's storage", async () => {
    const who = owner();
    const reserved = await heldThenGone(who);
    const unreserve = SandboxSlots.prototype.unreserve;
    SandboxSlots.prototype.unreserve = async function () {
      throw new Error("the line is unreachable");
    };
    try {
      await runDurableObjectAlarm(stub(who));  // forgotten; its give-back refused, still owed
    } finally {
      SandboxSlots.prototype.unreserve = unreserve;
    }
    expect(await reserved()).toBe(1);
    await stub(who).erase(who);
    expect(await reserved()).toBe(0);
  });

  it("leaves a paused, idle sandbox alone, with no alarm", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 0, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    await stub(who).release({ lease_id: LEASE.lease_id });
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
    await stub(who).release({ lease_id: LEASE.lease_id });
    await stub(who).start(who, start({ run: `run_${hex(32, "3")}`,
                                       lease: { lease_id: hex(32, "f"), epoch: 2 } }));
    const read = await stub(who).meter({ after: 0 });
    expect(read.events.map((event) => [event.authority, event.kind]))
      .toEqual([["owner", "start"], ["owner", "pause"], ["owner", "resume"]]);
    expect(read.events.every((event) => event.at >= before && event.at <= Date.now()))
      .toBe(true);
    expect(read.next).toBe(read.events.at(-1)!.seq);
    // Read past: dropped, and nothing new.
    expect(await stub(who).meter({ after: read.next }))
      .toEqual({ events: [], next: read.next });
  });
});

describe("the runner, by digest", () => {
  /** A sandbox made by a start, then its runner marked an older Worker's (`old`). */
  async function bornUnder(who: string, old: string): Promise<string> {
    await e2b("/script", { signin: { "claude-code": 0 } });
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/runners", { id, runners: [old] });
    await runInDurableObject(stub(who), (_instance, state) => {
      state.storage.sql.exec("UPDATE sandboxes SET runner = ?", old);
    });
    return id;
  }

  it("is the Worker's own bytes, named by their SHA-256", async () => {
    const hash = await crypto.subtle.digest("SHA-256", runnerBytes());
    expect(await runnerDigest()).toBe([...new Uint8Array(hash)]
      .map((byte) => byte.toString(16).padStart(2, "0")).join(""));
    expect(runnerBytes().byteLength).toBeGreaterThan(100_000);
  });

  it("sends nothing more to a resumed sandbox that has it", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 0, quiet: true } });
    const who = owner();
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await stub(who).release({ lease_id: LEASE.lease_id });
    const before = await seen();
    expect(before.sandboxes[0]!.state).toBe("paused");
    await stub(who).start(who, start({ run: `run_${hex(32, "2")}` }));
    const after = await seen();
    const commands = after.commands.slice(before.commands.length);
    expect(commands.map((command) => command.argv[1]))
      .toEqual(["prepare", "signin-check", "launch"]);
    expect(Object.keys(after.files).filter((path) => path.startsWith(`${id}:${INCOMING}`)))
      .toHaveLength(1);  // the create's upload alone
  });

  it("is installed and asked again when the sandbox has it no more", async () => {
    const who = owner();
    await e2b("/script", { signin: { "claude-code": 0 } });
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/runners", { id, runners: [] });
    const before = (await seen()).commands.length;
    expect(await stub(who).cancel({ run: RUN, lease_id: LEASE.lease_id }))
      .toEqual({ cancelled: true });
    expect((await seen()).commands.slice(before).map((command) => command.argv[1]))
      .toEqual(["cancel", "install", "cancel"]);
  });

  it("whose bytes are not their digest is refused loudly, and nothing launches", async () => {
    const who = owner();
    await e2b("/script", { signin: { "claude-code": 0 }, tampered: await runnerDigest() });
    const refused = await stub(who).start(who, start()).then(() => null,
                                                              (error: unknown) => error);
    expect(String(refused)).toMatch(/not its digest/);
    expect((await seen()).commands.map((command) => command.argv[1]))
      .toEqual(["install", "prepare"]);
  });

  it("of an older Worker is kept while a run lives, then the Worker's is adopted", async () => {
    const who = owner();
    const old = "0".repeat(64);
    const id = await bornUnder(who, old);
    // A run lives on the old runner: a joining run, and a cancel, keep it.
    await e2b("/script", { state: { live: 1, quiet: false } });
    let before = (await seen()).commands.length;
    await stub(who).start(who, start({ run: `run_${hex(32, "3")}`, tokens: null, network: null }));
    let commands = (await seen()).commands.slice(before);
    expect(commands.map((command) => [command.argv[1], command.runner])).toEqual([
      ["state", old], ["signin-check", old], ["launch", old]]);
    // Nothing lives: the next start installs the Worker's and adopts it, then runs on it.
    await e2b("/script", { state: { live: 0, quiet: true } });
    before = (await seen()).commands.length;
    await stub(who).start(who, start({ run: `run_${hex(32, "4")}`, tokens: null, network: null }));
    const digest = await runnerDigest();
    commands = (await seen()).commands.slice(before);
    // Installed, adopted, the row committed, then the adopted runner's own `prepare`.
    expect(commands.map((command) => [command.argv[1], command.runner])).toEqual([
      ["state", old], ["install", null], ["adopt", digest], ["prepare", digest],
      ["signin-check", digest], ["launch", digest]]);
    // Its `prepare` dropped the old runner's bytes; the Worker's alone stay.
    expect((await seen()).sandboxes.find((box) => box.id === id)!.runners).toEqual([digest]);
    // Adopted once: the next start asks nothing more.
    before = (await seen()).commands.length;
    await stub(who).start(who, start({ run: `run_${hex(32, "5")}`, tokens: null, network: null }));
    expect((await seen()).commands.slice(before).map((command) => command.argv[1]))
      .toEqual(["signin-check", "launch"]);
  });

  it("converges back on a rolled-back Worker's, the same way", async () => {
    // A sandbox on a newer runner than this Worker's (the Worker rolled back): adopted at
    // the first idle start, as any other.
    const who = owner();
    const newer = "f".repeat(64);
    await bornUnder(who, newer);
    await e2b("/script", { state: { live: 0, quiet: true } });
    const before = (await seen()).commands.length;
    await stub(who).start(who, start({ run: `run_${hex(32, "6")}`, tokens: null, network: null }));
    expect((await seen()).commands.slice(before).map((command) => command.runner))
      .toEqual([newer, null, ...Array(4).fill(await runnerDigest())]);
  });

  const rowRunner = (who: string) => runInDurableObject(stub(who), (_instance, state) =>
    state.storage.sql.exec<{ runner: string }>("SELECT runner FROM sandboxes").one().runner);

  it("never leaves the row naming bytes that are gone when an adoption's reply is lost",
     async () => {
    const who = owner();
    const old = "0".repeat(64);
    const id = await bornUnder(who, old);
    // `adopt` runs in the sandbox, but its answer never reaches the Worker.
    await e2b("/script", { state: { live: 0, quiet: true }, lost: "adopt" });
    expect(await stub(who).start(who, start({ run: `run_${hex(32, "3")}`, tokens: null,
                                               network: null }))).toEqual({ started: true });
    // The row still names the old runner, whose bytes are all still there.
    expect(await rowRunner(who)).toBe(old);
    expect((await seen()).sandboxes.find((box) => box.id === id)!.runners)
      .toContain(old);
    expect((await seen()).commands.at(-1)!.runner).toBe(old);
    // The next start adopts it.
    await e2b("/script", { lost: null });
    await stub(who).start(who, start({ run: `run_${hex(32, "4")}`, tokens: null, network: null }));
    expect(await rowRunner(who)).toBe(await runnerDigest());
  });

  it("adopts the Worker's when the row's runner is gone from the sandbox", async () => {
    // The disk lost the row's runner (whatever did it): nothing can live on it.
    const who = owner();
    const id = await bornUnder(who, "0".repeat(64));
    await e2b("/runners", { id, runners: [] });
    await e2b("/script", { state: { live: 0, quiet: true } });
    expect(await stub(who).start(who, start({ run: `run_${hex(32, "3")}`, tokens: null,
                                               network: null }))).toEqual({ started: true });
    expect(await rowRunner(who)).toBe(await runnerDigest());
  });

  it("starts on the runner it has when the Worker's cannot be installed, and tries again",
     async () => {
    const who = owner();
    const old = "0".repeat(64);
    await bornUnder(who, old);
    for (const fault of [{ room: 64 * 1024 }, { broken: "install" }, { broken: "adopt" }]) {
      await e2b("/script", { state: { live: 0, quiet: true }, room: null, broken: null,
                             ...fault });
      const before = (await seen()).commands.length;
      expect(await stub(who).start(who, start({ run: `run_${hex(32, "3")}`, tokens: null,
                                                 network: null }))).toEqual({ started: true });
      expect((await seen()).commands.slice(before).filter((command) =>
        command.argv[1] === "launch").map((command) => command.runner)).toEqual([old]);
      expect(await rowRunner(who)).toBe(old);
    }
    await e2b("/script", { room: null, broken: null });
    await stub(who).start(who, start({ run: `run_${hex(32, "4")}`, tokens: null, network: null }));
    expect(await rowRunner(who)).toBe(await runnerDigest());
  });

  it("runs the adopted runner's prepare, and only it, at a resume", async () => {
    const who = owner();
    const old = "0".repeat(64);
    await bornUnder(who, old);
    await e2b("/script", { state: { live: 0, quiet: true } });
    await stub(who).release({ lease_id: LEASE.lease_id });
    expect((await seen()).sandboxes[0]!.state).toBe("paused");
    const before = (await seen()).commands.length;
    await stub(who).start(who, start({ run: `run_${hex(32, "3")}` }));
    const digest = await runnerDigest();
    expect((await seen()).commands.slice(before).map((command) =>
      [command.argv[1], command.runner])).toEqual([
      ["state", old], ["install", null], ["adopt", digest], ["prepare", digest],
      ["signin-check", digest], ["launch", digest]]);
  });

  it("refuses a runner speaking a protocol this Worker does not, loudly", async () => {
    const who = owner();
    await bornUnder(who, "0".repeat(64));
    for (const protocol of [PROTOCOL - 1, PROTOCOL + 1, PROTOCOL + 5]) {
      await e2b("/script", { state: { live: 1, quiet: false }, protocol });
      const answer = await stub(who).start(who, start({ run: `run_${hex(32, "3")}`,
                                                        tokens: null, network: null }))
        .then((started) => started, (error: unknown) => String(error));
      if (SPEAKS.includes(protocol)) {
        expect(answer).toEqual({ started: true });
      } else {
        expect(String(answer)).toMatch(/sandbox_runner_protocol/);
      }
    }
    // Its own runner's protocol, and only the ones either side of it.
    expect(SPEAKS).toContain(PROTOCOL);
    expect(SPEAKS.every((each) => Math.abs(each - PROTOCOL) <= 1)).toBe(true);
  });

  it("names a runner whose bytes are not their digest for the founder", async () => {
    const who = owner();
    await e2b("/script", { signin: { "claude-code": 0 }, tampered: await runnerDigest() });
    const refused = await stub(who).start(who, start()).then(() => null,
                                                              (error: unknown) => error);
    expect(String(refused)).toMatch(/^RunnerRefused: sandbox_runner_mismatch: /);
  });

  it("looks on when a start adopted another runner under its quiet look", async () => {
    const who = owner();
    const old = "0".repeat(64);
    const id = await bornUnder(who, old);
    await e2b("/age", { id, s: CEILING - WINDOW + 5 });
    // The look's quiet-wait is answered late, after a start adopted and dropped the old.
    await e2b("/script", { state: { live: 0, quiet: true },
                           slow: { command: "quiet-wait", ms: 400 } });
    const look = runDurableObjectAlarm(stub(who));
    await new Promise((resolve) => setTimeout(resolve, 100));
    await stub(who).start(who, start({ run: `run_${hex(32, "3")}`, tokens: null, network: null }));
    await look;
    expect((await seen()).commands.some((command) => command.argv[1] === "quiet-wait"
                                        && command.runner === old)).toBe(true);
    // No failed look counted: nothing was unreachable.
    expect(await runInDurableObject(stub(who), (_instance, state) => state.storage.sql
      .exec<{ failures: number }>("SELECT failures FROM known").one().failures)).toBe(0);
  });
});

describe("a sign-in", () => {
  const SI = `si_${"5".repeat(32)}`;
  const ROUTE = "s".repeat(64);
  function signin(over: Record<string, unknown> = {}) {
    return { id: SI, engine: "kimi", region: "global", mode: "in" as const, authority: "owner",
             lease: LEASE, tokens: start().tokens, network: NETWORK as never, route: ROUTE,
             urls: { events: signinEventsUrl(SI), signin: start().urls.signin }, ...over };
  }

  it("starts the record's login in the authority's sandbox, its region's hosts let out",
     async () => {
    const who = owner();
    expect(await stub(who).signin(who, signin() as never)).toEqual({ started: true });
    const state = await seen();
    const box = state.sandboxes[0]!;
    expect(box.network.allowOut).toEqual(expect.arrayContaining(["auth.kimi.ai", "api.kimi.ai"]));
    expect(box.network.allowOut).not.toContain("auth.kimi.com");
    // The sandbox a sign-in first makes carries its route, so its lifecycle events reach the
    // owner's object as a run's sandbox's do (S14).
    expect(box.metadata["ta_route"]).toBe(ROUTE);
    expect(state.commands.map((command) => command.argv.slice(1, 3))).toEqual([
      ["install", await runnerDigest()], ["prepare", "1.1.1.1"], ["login-launch", SI]]);
    const doc = JSON.parse(state.files[`${box.id}:${inboxDoc(SI)}`]!);
    expect(doc).toEqual({ id: SI, engine: "kimi", region: "global", mode: "in",
                          authority: "owner", urls: signin().urls });
    expect(shapeProblems(doc, PROTOCOLS[String(PROTOCOL)]!.documents.signin)).toEqual([]);
    // A sign-in's start that opens the lease hands the runner the same two tokens, no more.
    const leaseFile = state.files[`${box.id}:${LEASE_FILE}`]!;
    expect(JSON.parse(leaseFile).tokens).toEqual({ runner_token: "d".repeat(64),
                                                   sync_token: "sync" });
    expect(leaseFile).not.toContain(EGRESS_TOKEN);
    // A sign-in in progress keeps the sandbox leased: its look is the idle one, not a reap.
    expect(await alarmOf(who)).not.toBeNull();
  });

  it("hands a pasted code to the live sign-in, and stops it", async () => {
    const who = owner();
    await stub(who).signin(who, signin({ engine: "claude-code", region: null }) as never);
    expect(await stub(who).signinInput({ authority: "owner", id: SI, code: "abc#d" }))
      .toEqual({ delivered: true });
    const state = await seen();
    const box = state.sandboxes[0]!;
    expect(state.files[`${box.id}:${inboxCode(SI)}`]).toBe("abc#d");
    expect(state.commands.at(-1)!.argv.slice(1)).toEqual(["login-input", SI]);
    expect(await stub(who).signinCancel({ authority: "owner", id: SI }))
      .toEqual({ cancelled: true });
    expect((await seen()).commands.at(-1)!.argv.slice(1)).toEqual(["login-cancel", SI]);
    // No sandbox running in a client's name: nothing to hand a code to.
    expect(await stub(who).signinInput({ authority: "c".repeat(64), id: SI, code: "x" }))
      .toEqual({ delivered: false });
  });

  it("drops a pasted code whose hand-over failed: it never rests on disk (review L1)",
     async () => {
    const who = owner();
    await stub(who).signin(who, signin({ engine: "claude-code", region: null }) as never);
    await e2b("/script", { broken: "login-input" });
    const failed = await stub(who).signinInput({ authority: "owner", id: SI, code: "abc#d" })
      .then(() => null, (error: unknown) => error);
    expect(failed).toBeInstanceOf(Error);
    expect((await seen()).commands.slice(-2).map((command) => command.argv.slice(1)))
      .toEqual([["login-input", SI], ["login-drop", SI]]);
  });

  it("waits its turn in the line as a start does", async () => {
    await e2b("/script", { full: true });
    const who = owner();
    expect(await stub(who).signin(who, signin() as never)).toEqual({ started: false,
                                                                     code: "sandbox_waiting" });
    expect(await line()).toEqual([who]);
  });

  it("refuses a sign-in that is not one: its id, a region the record lacks, plain HTTP", async () => {
    const who = owner();
    for (const bad of [signin({ id: "run_x" }), signin({ region: "moon" }),
                       signin({ route: undefined }), signin({ route: "short" }),
                       signin({ urls: { events: `http://${EGRESS_HOST}/x`,
                                        signin: start().urls.signin } })]) {
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
    await stub(who).release({ lease_id: LEASE.lease_id });
    // Both authorities' sandboxes carry the owner's one tag, and a stray under it is theirs.
    const tags = new Set((await seen()).sandboxes.map((box) => box.metadata["ta_owner"]));
    expect(tags.size).toBe(1);
    const stray = await e2b("/stray", { metadata: { ta_owner: [...tags][0]! } });
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

describe("the schema (S25)", () => {
  it("is each object's migrations, applied once, each one's digest kept: an applied one " +
     "changed since stops the object", async () => {
    const who = owner();
    await stub(who).start(who, start());
    const kept = (state: DurableObjectState) => state.storage.sql
      .exec("SELECT version, sha256 FROM schema_digests").toArray();
    const digests = (migrations: readonly (readonly string[])[]) =>
      migrations.map((each, i) => ({ version: i + 1, sha256: migrationDigest(each) }));
    await runInDurableObject(stub(who), (_instance, state) => {
      expect(kept(state)).toEqual(digests(CONTROL_MIGRATIONS));
      const edited = [[...CONTROL_MIGRATIONS[0]!, "CREATE TABLE probe (n INTEGER)"],
                      ...CONTROL_MIGRATIONS.slice(1)];
      expect(() => migrate(state.storage, edited)).toThrow(MIGRATION_CHANGED);
    });
    await runInDurableObject(env.SLOTS.getByName(SLOTS), (_instance, state) => {
      expect(kept(state)).toEqual(digests(SLOTS_MIGRATIONS));
    });
    // An erasure deletes the storage whole, and the schema is made again from its migrations.
    await stub(who).erase(who);
    await runInDurableObject(stub(who), (_instance, state) => {
      expect(kept(state)).toEqual(digests(CONTROL_MIGRATIONS));
    });
  });
});

describe("the network", () => {
  const updates = async () => (await seen()).calls
    .filter((call) => call.method === "PUT" && call.path.endsWith("/network")).length;

  it("is widened to vendor hosts only when it lists names, the egress door last", async () => {
    await e2b("/script", { signin: { "claude-code": 0, gemini: 0 } });
    const who = owner();
    expect(await stub(who).start(who, start())).toEqual({ started: true });
    const hosts = (await seen()).sandboxes[0]!.network.allowOut;
    expect(hosts.at(-1)).toBe(EGRESS_HOST);
    expect(hosts).toEqual(expect.arrayContaining(["claude.ai", "github.com"]));
    // A joining run whose vendor's hosts are listed already leaves the network as it is.
    expect(await stub(who).start(who, start({ run: `run_${hex(32, "2")}`, tokens: null,
                                             network: null }))).toEqual({ started: true });
    expect(await updates()).toBe(0);
  });

  it("is left as it is when it lets every name out", async () => {
    await e2b("/script", { signin: { "claude-code": 0, gemini: 0 } });
    const open = { ...NETWORK, network: { ...NETWORK.network, allowOut: ["*", EGRESS_HOST] } };
    const who = owner();
    expect(await stub(who).start(who, start({ network: open as never })))
      .toEqual({ started: true });
    expect((await seen()).sandboxes[0]!.network.allowOut)
      .toEqual(["*", EGRESS_HOST]);
    expect(await stub(who).start(who, start({ run: `run_${hex(32, "2")}`, engine: "gemini",
                                             tokens: null, network: null })))
      .toEqual({ started: true });
    expect(await updates()).toBe(0);
  });

  it("hands prepare the network's own resolvers, and none it made up", async () => {
    const who = owner();
    const none = { ...NETWORK, network: { ...NETWORK.network, dnsServers: [] } };
    await stub(who).start(who, start({ credential: "api_key", network: none as never }));
    const prepare = (await seen()).commands.find((command) => command.argv[1] === "prepare")!;
    expect(prepare.argv.slice(1)).toEqual(["prepare"]);
  });
});

describe("a live lease's network re-applied (E2)", () => {
  /** A re-apply's plan under `generation`: the policy's `hosts`, its generation, no token. */
  const at = (generation: number, hosts: string[] = []) =>
    ({ ...NETWORK, generation, network: withGeneration(closedNetwork(hosts), generation) });
  const PLAN = at(2);
  const reapply = (over: Record<string, unknown> = {}) =>
    ({ authority: "owner", lease: LEASE, network: PLAN, ...over }) as never;
  /** A sandbox's network as the fake E2B holds it: its names, its token and its generation. */
  const networkOf = async (index = 0) => {
    const { network } = (await seen()).sandboxes[index]!;
    return { allowOut: network.allowOut, token: egressTokenOf(network),
             generation: generationOf(network) };
  };

  it("replaces a running sandbox's network whole, keeping its live egress token and every " +
     "launched job's vendor hosts", async () => {
    await e2b("/script", { signin: { "claude-code": 0, gemini: 0 } });
    const who = owner();
    await stub(who).start(who, start());
    // A run joins on another engine's sign-in: its vendor's hosts are let out.
    await stub(who).start(who, start({ run: `run_${hex(32, "2")}`, engine: "gemini",
                                       tokens: null, network: null }));
    expect(await stub(who).network(reapply())).toEqual({ state: "applied" });
    const network = await networkOf();
    expect([network.token, network.generation]).toEqual([EGRESS_TOKEN, 2]);
    // The policy's own names are the plan's (`github.com` is gone); the jobs' vendors stay.
    expect(network.allowOut).not.toContain("github.com");
    expect(network.allowOut).toEqual(expect.arrayContaining(["claude.ai", "accounts.google.com"]));
    expect(network.allowOut.at(-1)).toBe(EGRESS_HOST);
  });

  it("answers lost for a lease it has no sandbox of, and refuses a plan with a token or " +
     "no generation", async () => {
    const who = owner();
    expect(await stub(who).network(reapply())).toEqual({ state: "lost" });
    await stub(who).start(who, start({ credential: "api_key" }));
    expect(await stub(who).network(reapply({ lease: { lease_id: hex(32, "9"), epoch: 2 } })))
      .toEqual({ state: "lost" });
    const tokened = { ...PLAN, network: withEgressToken(PLAN.network, "forged") };
    const refused = await stub(who).network(reapply({ network: tokened }))
      .then(() => null, (error: unknown) => error);
    expect(String(refused)).toMatch(/no token/);
    // A plan whose transform names no generation is refused, never applied as "not newer".
    const unnamed = { ...PLAN, network: { ...PLAN.network, rules: { [EGRESS_HOST]: [
      { transform: { headers: { [GENERATION_HEADER]: "x" } } }] } } };
    expect(String(await stub(who).network(reapply({ network: unnamed }))
      .then(() => null, (error: unknown) => error))).toMatch(/generation header/);
    expect((await networkOf()).generation).toBe(1);
  });

  it("fails loudly on a live network that carries no generation", async () => {
    const who = owner();
    const bare = { ...NETWORK, network: withEgressToken(closedNetwork(["github.com"]),
                                                        EGRESS_TOKEN) };
    await stub(who).start(who, start({ credential: "api_key", network: bare as never }));
    expect(String(await stub(who).network(reapply()).then(() => null, (error: unknown) => error)))
      .toMatch(/the egress generation it was made under/);
    expect((await networkOf()).generation).toBeNull();
  });

  it("keeps it for a paused sandbox, applied as the sandbox resumes", async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: false } });
    const who = owner();
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/pause", { id });
    expect(await stub(who).network(reapply())).toEqual({ state: "at_resume" });
    // Nothing is sent to a paused sandbox: E2B holds its old network until it resumes.
    expect((await networkOf()).generation).toBe(1);
    await runDurableObjectAlarm(stub(who));
    expect((await seen()).sandboxes[0]!.state).toBe("running");
    const network = await networkOf();
    expect([network.token, network.generation]).toEqual([EGRESS_TOKEN, 2]);
    expect(network.allowOut).toEqual(expect.arrayContaining(["claude.ai"]));
    expect(network.allowOut).not.toContain("github.com");
  });

  it("keeps a joining job's vendor hosts when the start that opened its lease runs after it",
     async () => {
    await e2b("/script", { signin: { "claude-code": 0, gemini: 0 } });
    const who = owner();
    // The sandbox runs under an older lease; then a new lease's joining job (gemini, on its
    // own sign-in) reaches the Worker before the start that opened that lease.
    await stub(who).start(who, start({ credential: "api_key" }));
    const next = { lease_id: hex(32, "c"), epoch: 2 };
    expect(await stub(who).start(who, start({ run: `run_${hex(32, "2")}`, engine: "gemini",
                                             lease: next, tokens: null, network: null })))
      .toEqual({ started: true });
    expect((await networkOf()).allowOut).toEqual(expect.arrayContaining(["accounts.google.com"]));
    // The opener's network replaces the live one whole, and keeps the joiner's vendor.
    expect(await stub(who).start(who, start({ run: `run_${hex(32, "3")}`, lease: next,
                                             credential: "api_key" })))
      .toEqual({ started: true });
    expect((await networkOf()).allowOut).toEqual(expect.arrayContaining(["accounts.google.com"]));
  });

  it("never puts an older generation's network over a newer one's, running or paused",
     async () => {
    await e2b("/script", { signin: { "claude-code": 0 }, state: { live: 1, quiet: false } });
    const who = owner();
    await stub(who).start(who, start());
    // Generation 3's re-apply lands first; generation 2's, sent before it, arrives after.
    expect(await stub(who).network(reapply({ network: at(3) })))
      .toEqual({ state: "applied" });
    expect(await stub(who).network(reapply({
      network: at(2, ["old.example"]) }))).toEqual({ state: "applied" });
    expect((await networkOf()).generation).toBe(3);
    expect((await networkOf()).allowOut).not.toContain("old.example");
    // Paused: the newer kept plan stays kept.
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/pause", { id });
    expect(await stub(who).network(reapply({ network: at(4) })))
      .toEqual({ state: "at_resume" });
    expect(await stub(who).network(reapply({
      network: at(2, ["old.example"]) }))).toEqual({ state: "applied" });
    await runDurableObjectAlarm(stub(who));
    expect((await networkOf()).generation).toBe(4);
    expect((await networkOf()).allowOut).not.toContain("old.example");
  });
});
