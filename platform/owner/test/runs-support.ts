/**
 * Starting runs in the tests as the owner's app does: an agent's `agent.json` written by
 * the owner's file doors, then the run door with the control record's tier numbers in the
 * binding, as the binder hands them, and the start the fake sandbox Worker saw
 * (`fake-sandbox.ts`, bound by config behind its gate).
 */
import { createHash } from "node:crypto";

import { env } from "cloudflare:workers";
import { vi } from "vitest";

import { verifyToken } from "../../contract/src/routing-mac";
import { egressDoor, egressTokenOf } from "../../contract/src/egress";

import type { OwnerDO } from "../src/owner-do";
import type { Binding } from "../../contract/src/binding";
import type { RunReceipt } from "../src/runs";
import { HELD_WAIT_MS } from "../../ci/fixtures";
import { LEASE_TTL_S } from "../../contract/src/limits";
import type { Recorded, Script } from "./fake-sandbox";

import { TIERS } from "./support";
import type { SandboxStart } from "../../contract/src/sandbox";

export { TIERS };

/**
 * The object's clock moved on `seconds` (a lease's life and one, by default: every sandbox
 * lease lapses, as when its runner stops beating), by a fake `Date`, which the object reads
 * as the test does; the test makes the clock real again after it (`vi.useRealTimers`).
 */
export function later(seconds = LEASE_TTL_S + 1): void {
  const now = Date.now() + seconds * 1000;
  if (vi.isFakeTimers()) {
    vi.setSystemTime(now);
  } else {
    vi.useFakeTimers({ toFake: ["Date"], now });
  }
}

/** Every scope a run can hold. */
export const ALL_SCOPES = Object.freeze(["read_graph", "write_graph", "propose_policy",
                                         "read_files", "write_files", "converse",
                                         "run_graph"]);

const bytes = (text: string) => new TextEncoder().encode(text);

/**
 * The owner's binding in a live session: an agent's files are a widening, as the owner's app
 * writes them in its session. A binding with a session is kept as it is.
 */
export async function inSession(stub: DurableObjectStub<OwnerDO>, binding: Binding):
    Promise<Binding> {
  if (binding.session !== undefined || binding.origin !== null) {
    return binding;
  }
  const { session } = await stub.sessionOpen({
    ...binding, authenticated_at: Math.floor(Date.now() / 1000) - 5 });
  return { ...binding, session };
}

/** `agent`'s `agent.json` written as `file`, by the owner (`owner`, origin null). */
export async function defineAgent(stub: DurableObjectStub<OwnerDO>, owner: Binding,
                                  agent: string, file: Record<string, unknown>):
    Promise<void> {
  const binding = await inSession(stub, owner);
  for (const path of ["/workspace/agents", `/workspace/agents/${agent}`]) {
    if ((await stub.fileStat(binding, { path })).entry === null) {
      await stub.fileMkdir(binding, { path, expected_etag: null });
    }
  }
  const path = `/workspace/agents/${agent}/agent.json`;
  const { entry } = await stub.fileStat(binding, { path });
  const written = await stub.fileWrite(binding, { path, expected_etag: entry?.etag ?? null,
                                                  content: bytes(JSON.stringify(file)) });
  if (!("entry" in written)) {
    throw new Error("agent.json was not written");
  }
}

/** The owner `stub` is named by. */
function ownerOf(stub: DurableObjectStub<OwnerDO>): string {
  if (stub.name === undefined) {
    throw new Error("the fake sandbox keeps its record by owner: reach the object by its name");
  }
  return stub.name;
}

/** What `stub`'s object asked of the sandbox so far: its starts, cancels, sign-ins and more. */
export async function recorder(stub: DurableObjectStub<OwnerDO>): Promise<Recorded> {
  return await env.SANDBOX_LOG.recorded(ownerOf(stub)) as Recorded;
}

/** How the sandbox answers `stub`'s object from now on: what `script` names, the rest kept. */
export async function scripted(stub: DurableObjectStub<OwnerDO>, script: Partial<Script>):
    Promise<void> {
  await env.SANDBOX_LOG.script(ownerOf(stub), script);
}

/**
 * `call`, with `act` done while its next call of the sandbox's `method` is out: the sandbox
 * has taken it (its record holds it) and its answer is held at the gate until `act` is
 * done, as a sandbox answers late. What `call` answered.
 */
export async function whileOut<R>(method: "start" | "network", call: () => Promise<R>,
                                  act: () => Promise<void>): Promise<R> {
  await env.SANDBOX_GATE.arm({ method, hold: "after" });
  const answered = call();
  const [held] = await env.SANDBOX_GATE.held(HELD_WAIT_MS);
  if (held === undefined) {
    throw new Error(`no ${method} reached the sandbox`);
  }
  try {
    await act();
  } finally {
    await env.SANDBOX_GATE.release(held.id);
  }
  return answered;
}

/** A run's own secret, as its start's MCP URL carries it (`/r/<run>/<secret>/mcp`). */
export function secretOf(start: SandboxStart): string {
  const door = egressDoor(new URL(start.urls.mcp).pathname);
  if (door?.door !== "mcp" || door.secret === undefined) {
    throw new Error(`the start of ${start.run} carries no run secret`);
  }
  return door.secret;
}

/** The SHA-256 the binder hands the object of a secret its call presented. */
export function hashed(secret: string): string {
  return createHash("sha256").update(secret, "utf8").digest("hex");
}

/** Each lease's tokens, as the start that opened it got them. */
const LEASE_TOKENS = new Map<string, { egress: string; sync: string; runner: string }>();

/** The tokens of `start`'s lease (recorded from the start that opened it). */
export function leaseTokens(start: SandboxStart): { egress: string; sync: string;
                                                    runner: string } {
  if (start.tokens !== null) {
    LEASE_TOKENS.set(start.lease.lease_id, { egress: egressTokenOf(start.network!.network)!,
                                             sync: start.tokens.sync_token,
                                             runner: start.tokens.runner_token });
  }
  return LEASE_TOKENS.get(start.lease.lease_id)!;
}

/** What makes a binding: the test rig's `bind`. */
type Binder = { readonly bind: (extra?: Partial<Binding>) => Binding };

/**
 * The sandbox's binding on the egress host, as the binder builds it (`bindSandbox`), with
 * what the call presents: a run's own secret's hash, or the runner's.
 */
export function sandboxOf(r: Binder, start: SandboxStart,
                          presented: { secret?: string; runner?: string } = {},
                          run?: string): Binding {
  const token = verifyToken(env.ROUTING_MAC_KEY, "egress", leaseTokens(start).egress)!.hash;
  return r.bind({ sandbox: { kind: "egress", token, generation: 1, ...presented },
                  ...(run === undefined ? {} : { run }) });
}

/** The runner's own secret's hash, as its event and sign-in posts present it. */
export function runnerOf(start: SandboxStart): { runner: string } {
  return { runner: hashed(leaseTokens(start).runner) };
}

/** The run's own MCP binding on the egress host, by its own secret (`bindRun`). */
export function runBinding(r: Binder, start: SandboxStart): Binding {
  return sandboxOf(r, start, { secret: hashed(secretOf(start)) }, start.run);
}

/** The runner's event door for the run, by the runner's secret (`bindRun`). */
export function runnerBinding(r: Binder, start: SandboxStart): Binding {
  return sandboxOf(r, start, runnerOf(start), start.run);
}

/** The runner's sign-in report door, by its secret (`bindSandbox`). */
export function runnerSandbox(r: Binder, start: SandboxStart): Binding {
  return sandboxOf(r, start, runnerOf(start));
}

/** The sandbox's sync agent's binding, by its lease's sync token. */
export function syncOf(r: Binder, start: SandboxStart): Binding {
  return r.bind({ sandbox: { kind: "sync",
                             token: verifyToken(env.ROUTING_MAC_KEY, "sync",
                                                leaseTokens(start).sync)!.hash,
                             egress: verifyToken(env.ROUTING_MAC_KEY, "egress",
                                                 leaseTokens(start).egress)!.hash } });
}

/** The recorded start of `run`. */
export async function startOf(stub: DurableObjectStub<OwnerDO>, run: string):
    Promise<SandboxStart> {
  const starts = (await recorder(stub)).starts;
  for (const start of starts) {
    leaseTokens(start);  // each lease's tokens, from the start that opened it
  }
  const found = starts.find((start) => start.run === run);
  if (found === undefined) {
    throw new Error(`the sandbox saw no start of ${run}`);
  }
  return found;
}

let sent = 0;

/**
 * A run of `agent` (its `agent.json` written as `file` first, when given) by the owner, with
 * the tier numbers in the binding; its receipt and its recorded start.
 */
export async function ownerRun(stub: DurableObjectStub<OwnerDO>, binding: Binding,
                               file?: Record<string, unknown>, agent = "main",
                               extra: Record<string, unknown> = {}):
    Promise<{ run: RunReceipt; start: SandboxStart }> {
  if (file !== undefined) {
    await defineAgent(stub, binding, agent, file);
  }
  const run = await stub.agentSubmit({ ...binding, tiers: TIERS }, {
    message: "do the thing", client_send_id: `send-${++sent}`, agent_id: agent, ...extra });
  return { run, start: await startOf(stub, run.run_id) };
}
