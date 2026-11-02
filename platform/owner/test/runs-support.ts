/**
 * Starting runs in the tests as the owner's app does: an agent's `agent.json` written by
 * the owner's file doors, then the run door with the control record's tier numbers in the
 * binding, as the binder hands them, and the start the recording sandbox saw.
 */
import { runInDurableObject } from "cloudflare:test";

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

/** The recorded start of `run`. */
export async function startOf(stub: DurableObjectStub<OwnerDO>, run: string):
    Promise<SandboxStart> {
  const found = (await recorder(stub)).starts.find((start) => start.run === run);
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
