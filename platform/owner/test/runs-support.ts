/**
 * Starting runs in the tests as the owner's app does: an agent's `agent.json` written by
 * the owner's file doors, then the run door with the control record's tier numbers in the
 * binding, as the binder hands them, and the start the recording sandbox saw.
 */
import { createHash } from "node:crypto";

import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";

import { verifyToken } from "../../doors/src/routing-mac";

import type { Binding, OwnerDO } from "../src/owner-do";
import type { RunReceipt } from "../src/runs";
import type { SandboxStart } from "../src/sandbox";
import type { RecordingSandbox } from "./sandbox-fake";

/** The founder's numbers (ADR-034 decision 8), as the tests' control record holds them. */
export const TIERS = Object.freeze({
  free: Object.freeze({ runs: 5, storage_bytes: 25_000_000_000 }),
  paid: Object.freeze({ runs: 10, storage_bytes: 250_000_000_000 }),
});

/** Every scope a run can hold. */
export const ALL_SCOPES = Object.freeze(["read_graph", "write_graph", "propose_policy",
                                         "read_files", "write_files", "converse",
                                         "run_graph"]);

type Recorded = OwnerDO & { readonly recorder: RecordingSandbox };

const bytes = (text: string) => new TextEncoder().encode(text);

/** `agent`'s `agent.json` written as `file`, by the owner (`binding`, origin null). */
export async function defineAgent(stub: DurableObjectStub<OwnerDO>, binding: Binding,
                                  agent: string, file: Record<string, unknown>):
    Promise<void> {
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

/** The starts, cancels and answer of the object's recording sandbox. */
export function recorder(stub: DurableObjectStub<OwnerDO>): Promise<RecordingSandbox> {
  return runInDurableObject(stub, (instance: OwnerDO) => (instance as Recorded).recorder);
}

/** A run's own secret, as its start's MCP URL carries it (`/r/<run>/<secret>/mcp`). */
export function secretOf(start: SandboxStart): string {
  const found = /\/r\/run_[0-9a-f]{32}\/([0-9a-f]{64})\/mcp$/.exec(start.urls.mcp);
  if (found === null) {
    throw new Error(`the start of ${start.run} carries no run secret`);
  }
  return found[1]!;
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
    LEASE_TOKENS.set(start.lease.lease_id, { egress: start.tokens.egress_token,
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
                                                leaseTokens(start).sync)!.hash } });
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
