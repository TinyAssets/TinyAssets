/**
 * pi's code mode, its VM in the hooks Worker (pi audit F8; tasks 5, H1; pi-durable README
 * "Code Mode"): a tool whose model-written JavaScript calls the conversation's tools as
 * `await tools.<name>(args)`, each a nested call, and survives a restart by its journal.
 *
 * - **Where it runs:** pi's `RemoteCodemodeSandbox`, whose `remote()` is the `HOOKS`
 *   binding's `Codemode` (`codemode(name)`, one sandbox object per execution, named by the
 *   owner and a random id), so the VM never computes on the owner's object, and the owner
 *   config still binds only services. The tools, the journal, the deadline and the abort
 *   stay here; every tool call comes back over RPC through `exchange`.
 * - **How it is reached:** registered callable by tools only, so the model is offered the
 *   same five tools and reaches it as `ta call codemode {code}` (`ta.ts`), which makes its
 *   nested call under `CODEMODE_KEY`; the gate refuses any other `codemode` call, a
 *   script's own included (`authority.ts`), so scripts cannot start scripts.
 * - **What a script reaches:** its `tools.*` are the conversation's callable tools, each a
 *   nested call that `ta.authority` holds to the caller's authority, so a script reaches
 *   what its caller may and nothing more. Its `models.*` are pi's, over the object's own
 *   model source (pi's `api.models`, the gateway provider): the catalog, and `classify` and
 *   `generateImages`, which find no classifier or image model there and fail loudly; and
 *   `models.complete`, `ta`'s `model` call, reserved and charged to the turn (`modelGlobal`).
 * - **When:** only while the owner's policy turns it on (`hooks.codemode`); its extension
 *   is installed and removed as the policy moves (`OwnerAgent.policyChanged`).
 */
import { randomUUID } from "node:crypto";

import { type CodemodeRemote, RemoteCodemodeSandbox } from "@earendil-works/pi-codemode/portable";
import { defineExtension, type Extension, type ToolRegistration } from "@earendil-works/pi-durable";
import { createCodemodeExtension } from "@earendil-works/pi-durable/codemode";

import { CODEMODE_TOOL } from "./authority";
import type { HooksService } from "./hooks";
import { modelGlobal } from "./ta";

/** The object's code mode extension. */
export const CODEMODE_EXTENSION = "ta.codemode";
/** How long one script may run, in ms; a script past it ends with pi's `timeout`. */
export const CODEMODE_TIMEOUT_MS = 60_000;

/** What code mode's sandbox reaches: the hooks Worker, and the owner it runs for. */
export interface CodemodeParts {
  readonly service: () => HooksService | undefined;
  readonly owner: () => string;
}

/** The `ta.codemode` extension: pi's code mode, tools-only, its VM in the hooks Worker. */
export function codemodeExtension(parts: CodemodeParts): Extension {
  const sandbox = new RemoteCodemodeSandbox({
    timeoutMs: CODEMODE_TIMEOUT_MS,
    remote: () => {
      const service = parts.service();
      if (service === undefined) {
        throw new Error("no hooks Worker is bound, so code mode cannot run");
      }
      return service.codemode(`${parts.owner()}/${randomUUID()}`) as CodemodeRemote;
    },
  });
  const pi = createCodemodeExtension({ sandbox, globals: [modelGlobal()] });
  return Object.freeze(defineExtension({
    name: CODEMODE_EXTENSION,
    tools: Object.freeze((pi.tools ?? []).map((tool): ToolRegistration => Object.freeze(
      tool.name === CODEMODE_TOOL ? { ...tool, callers: ["tools"] as const } : tool))),
    sections: Object.freeze([...(pi.sections ?? [])]),
  }));
}
