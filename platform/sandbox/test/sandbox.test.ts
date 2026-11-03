/**
 * The sandbox Worker against a fake E2B (`fake-e2b.ts`), in workerd: a start creates,
 * resumes or joins the authority's sandbox and launches the run's runner; a run on the
 * vendor's sign-in with none there is `signin_required`; past the concurrency ceiling a run
 * waits, never refused; a release pauses an idle sandbox; and the alarm renews a sandbox
 * before its runtime ceiling, at a quiet moment or forced at the edge, every number read
 * from the control record.
 */
import { env, runDurableObjectAlarm } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

import type { SandboxStart } from "../../owner/src/sandbox";
import { CODES, renewal, widened } from "../src/control";

const OPEN = { outside: "on", ordinary: "all", canary: [], inbound: false };
const CEILING = 3600;

async function control(value: unknown): Promise<void> {
  if (value === null) {
    await env.DIRECTORY.delete("c:control");
  } else {
    await env.DIRECTORY.put("c:control", JSON.stringify(value));
  }
}

async function e2b(path: string, body?: unknown): Promise<Record<string, unknown>> {
  const response = await env.E2B.fetch(`https://fake.test${path}`, body === undefined ? {}
    : { method: "POST", body: JSON.stringify(body) });
  return response.json();
}

interface Seen {
  sandboxes: { id: string; state: string; timeout: number; network: { allowOut: string[] };
               template: string; metadata: Record<string, string> }[];
  calls: { method: string; path: string; body: unknown }[];
  files: Record<string, string>;
  commands: { sandbox: string; argv: string[]; user: string }[];
}

const seen = async (): Promise<Seen> => await e2b("/state") as unknown as Seen;

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
    unattended: false, authority: "owner", lease: LEASE,
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

beforeEach(async () => {
  await e2b("/reset", {});
  await control({ ...OPEN, sandbox_runtime_ceiling_s: CEILING, sandbox_concurrency_ceiling: 20 });
});

describe("a start", () => {
  it("creates the authority's sandbox from the template and launches the run", async () => {
    await e2b("/script", { signin: { "claude-code": 0 } });
    const who = owner();
    expect(await stub(who).start(who, start())).toEqual({ started: true });
    const state = await seen();
    expect(state.sandboxes).toHaveLength(1);
    const box = state.sandboxes[0]!;
    // The plan's runtime ceiling as its timeout; the template the config names.
    expect(box.timeout).toBe(CEILING);
    expect(box.template).toBe(env.E2B_TEMPLATE);
    expect(box.metadata["ta_sandbox"]).toMatch(/^[0-9a-f]{32}$/);
    expect(JSON.stringify(box.metadata)).not.toContain(who);
    // A signed-in engine's vendor hosts are let out, the egress door kept last.
    expect(box.network.allowOut.at(-1)).toBe("egress.tinyassets.io");
    expect(box.network.allowOut).toEqual(expect.arrayContaining(["claude.ai",
                                                                 "api.anthropic.com"]));
    const create = state.calls.find((call) => call.path === "/v2/sandboxes"
                                    && call.method === "POST")!.body as Record<string, unknown>;
    expect(create).toMatchObject({ autoPause: true, autoPauseMemory: true,
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

  it("starts nothing when the control record holds no sandbox numbers", async () => {
    await control(OPEN);
    const who = owner();
    expect(await stub(who).start(who, start())).toEqual({ started: false,
                                                          code: CODES.unconfigured });
    expect((await seen()).calls).toEqual([]);
  });

  it("resumes the paused sandbox, its memory kept, rather than creating one", async () => {
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

describe("the concurrency ceiling", () => {
  it("makes a run wait, never refused, when E2B runs as many as the record says", async () => {
    await control({ ...OPEN, sandbox_runtime_ceiling_s: CEILING, sandbox_concurrency_ceiling: 3 });
    await e2b("/script", { others: 3 });
    const who = owner();
    expect(await stub(who).start(who, start())).toEqual({ started: false, code: CODES.waiting });
    expect((await seen()).sandboxes).toHaveLength(0);
    // Raised in the record, no deploy: the same start goes.
    await control({ ...OPEN, sandbox_runtime_ceiling_s: CEILING, sandbox_concurrency_ceiling: 4 });
    await e2b("/script", { signin: { "claude-code": 0 } });
    expect(await stub(who).start(who, start())).toEqual({ started: true });
  });

  it("makes a run wait when E2B itself refuses for room", async () => {
    await e2b("/script", { full: true });
    const who = owner();
    expect(await stub(who).start(who, start())).toEqual({ started: false, code: CODES.waiting });
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
    await e2b("/script", { state: { live: 0, quiet: true }, busy: 2 });
    expect(await stub(who).release(who, { lease_id: LEASE.lease_id })).toEqual({ paused: true });
    const pauses = (await seen()).calls.filter((call) => call.path.endsWith("/pause"));
    expect(pauses).toHaveLength(3);  // two busy answers, asked again
    expect(pauses[0]!.body).toEqual({ memory: true });
  });
});

describe("renewal before the runtime ceiling", () => {
  async function aged(seconds: number, state: { live: number; quiet: boolean }) {
    await e2b("/script", { signin: { "claude-code": 0 }, state });
    const who = owner();
    await stub(who).start(who, start());
    const id = (await seen()).sandboxes[0]!.id;
    await e2b("/age", { id, s: seconds });
    const before = (await seen()).calls.length;
    await runDurableObjectAlarm(stub(who));
    const after = await seen();
    return { who, id, calls: after.calls.slice(before), commands: after.commands,
             box: after.sandboxes[0]! };
  }

  it("is window and edge proportional to the ceiling, whatever it is", () => {
    expect(renewal(3600)).toEqual({ leadS: 600, edgeS: 120, stepS: 30 });
    expect(renewal(86_400)).toEqual({ leadS: 14_400, edgeS: 2880, stepS: 720 });
    expect(renewal(120)).toEqual({ leadS: 20, edgeS: 5, stepS: 2 });
  });

  it("does nothing before the window", async () => {
    const { calls, box } = await aged(60, { live: 1, quiet: true });
    expect(calls.filter((call) => call.path.endsWith("/pause"))).toEqual([]);
    expect(box.state).toBe("running");
  });

  it("pauses and resumes the same sandbox at a quiet moment in the window", async () => {
    const { leadS } = renewal(CEILING);
    const { calls, commands, box, id } = await aged(CEILING - leadS + 5, { live: 1, quiet: true });
    expect(calls.map((call) => call.path).filter((path) => /pause|connect/.test(path)))
      .toEqual([`/sandboxes/${id}/pause`, `/v2/sandboxes/${id}/connect`]);
    expect(box.state).toBe("running");
    expect(box.id).toBe(id);
    expect(commands.map((command) => command.argv[1]).slice(-2)).toEqual(["renewing", "renewed"]);
  });

  it("waits for a quiet moment inside the window", async () => {
    const { leadS } = renewal(CEILING);
    const { calls, who } = await aged(CEILING - leadS + 5, { live: 1, quiet: false });
    expect(calls.filter((call) => call.path.endsWith("/pause"))).toEqual([]);
    expect(await stub(who).status(who, { run: RUN, lease_id: LEASE.lease_id }))
      .toEqual({ state: "running" });
  });

  it("is forced at the ceiling's edge, quiet or not", async () => {
    const { edgeS } = renewal(CEILING);
    const { calls, commands } = await aged(CEILING - edgeS + 1, { live: 1, quiet: false });
    expect(calls.filter((call) => call.path.endsWith("/pause"))).toHaveLength(1);
    expect(calls.filter((call) => call.path.endsWith("/connect"))).toHaveLength(1);
    expect(commands.at(-1)!.argv[1]).toBe("renewed");
  });

  it("pauses a sandbox with no live run instead", async () => {
    const { leadS } = renewal(CEILING);
    const { calls, box } = await aged(CEILING - leadS + 5, { live: 0, quiet: true });
    expect(calls.filter((call) => call.path.endsWith("/connect"))).toEqual([]);
    expect(box.state).toBe("paused");
  });

  it("reads the ceiling from the control record: a raised one moves the window", async () => {
    await control({ ...OPEN, sandbox_runtime_ceiling_s: 86_400,
                    sandbox_concurrency_ceiling: 20 });
    const { leadS } = renewal(CEILING);
    const { calls, box } = await aged(CEILING - leadS + 5, { live: 1, quiet: true });
    expect(box.timeout).toBe(86_400);
    expect(calls.filter((call) => call.path.endsWith("/pause"))).toEqual([]);
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
