/**
 * Code mode's executor (pi audit F8; tasks 5, H1; ADR-034 decision 12): one script whose
 * JavaScript calls tools as `await tools.<name>(args)`, its VM in the hooks Worker.
 *
 * - **Where it runs:** pi-codemode's `RemoteCodemodeSandbox`, whose `remote()` reaches the
 *   `HOOKS` binding's `codemodeExecute` (one sandbox object per execution, named by the
 *   owner and a random id), so the VM never computes on the owner's object, and the owner
 *   config still binds only services. The tools, the deadline and the abort stay with the
 *   caller; every tool call comes back over RPC through `exchange`.
 * - **What a script reaches:** the tools its caller hands it, each held by the caller to its
 *   own grant. The MCP door's `code` tool (slice M, the object's `codeRun`) hands it the
 *   caller's profile's other tools, each call the door's own, admitted again against the
 *   caller's grant, its profile and the owner's switch.
 * - **No stub is held:** each execution's `remote()` calls `HOOKS.codemodeExecute` with a
 *   name of its own, which resolves when the VM has stopped.
 * - **When:** only while the owner's policy turns it on (`hooks.codemode`, off by default).
 * - **How long:** `CODEMODE_TIMEOUT_MS` of wall time, tool calls included, and the hooks
 *   Worker's interrupt budget of compute between them; the tests pass smaller bounds.
 */
import { randomUUID } from "node:crypto";

import {
  type CodemodeRemote, type CodemodeRemoteExchange, type CodemodeRemoteRequest,
  RemoteCodemodeSandbox,
} from "@earendil-works/pi-codemode/portable";

/** The tools a script calls, as pi-codemode's sandbox takes them. */
export type CodemodeTools = NonNullable<
  ConstructorParameters<typeof RemoteCodemodeSandbox>[0]["tools"]>;

/** How long one script may run, in ms; a script past it ends with pi-codemode's `timeout`. */
export const CODEMODE_TIMEOUT_MS = 300_000;

/** What the hooks Worker serves the owner (`hooks/src/index.ts`): code mode's VMs. */
export interface HooksService {
  codemodeExecute(name: string, request: CodemodeRemoteRequest,
                  exchange: CodemodeRemoteExchange): Promise<void>;
}

/**
 * A script's bounds: its wall time (`CODEMODE_TIMEOUT_MS` unless a test passes less) and
 * the interrupt budget it asks the hooks Worker for (the Worker's own, at most, unless a
 * test asks for less).
 */
export interface CodemodeBounds {
  readonly timeoutMs?: number;
  readonly interruptBudget?: number;
}

/** What code mode's sandbox reaches: the hooks Worker, the owner it runs for, its tools. */
export interface CodemodeParts extends CodemodeBounds {
  readonly service: () => HooksService | undefined;
  readonly owner: () => string;
  readonly tools: CodemodeTools;
}

/**
 * A sandbox whose scripts run in the hooks Worker, each execution named
 * `<owner>/<random id>`; with no hooks Worker bound, an execution fails loudly.
 */
export function codemodeSandbox(parts: CodemodeParts): RemoteCodemodeSandbox {
  return new RemoteCodemodeSandbox({
    timeoutMs: parts.timeoutMs ?? CODEMODE_TIMEOUT_MS,
    ...(parts.interruptBudget === undefined ? {} : { interruptBudget: parts.interruptBudget }),
    tools: [...parts.tools],
    remote: (): CodemodeRemote => {
      const service = parts.service();
      if (service === undefined) {
        throw new Error("no hooks Worker is bound, so code mode cannot run");
      }
      const name = `${parts.owner()}/${randomUUID()}`;
      return { execute: (request, exchange) => service.codemodeExecute(name, request,
                                                                        exchange) };
    },
  });
}
