/**
 * pi-codemode's sandbox, in the codemode Worker (pi-codemode's "Usage on Cloudflare
 * Workers"): the VM of an owner's code mode script (the MCP door's `code`, whichever client
 * or engine calls it) runs here, in a Durable Object of a separately deployed Worker, so a
 * script that computes never holds the owner's object (pi measured 10 s in the session
 * object or a Dynamic Worker it loads, about 50 ms here). The owner's `RemoteCodemodeSandbox`
 * keeps the tools, the journal and the abort (its caller's leaving); this object only runs
 * the VM, with pi-codemode's `serveCodemodeRemote`, calling back into the owner through
 * `exchange` for every tool call.
 *
 * Its limits apply to every request, whatever the owner asks, each a ceiling recorded with
 * its reason in the contract (`contract/src/codemode.ts`, `limits-audit.md`). It keeps no
 * state and never touches its storage.
 */
import { CodemodeSandboxDurableObject } from "@earendil-works/pi-codemode/cloudflare";

import { CODEMODE_INTERRUPT_BUDGET, CODEMODE_MEMORY_BYTES } from "../../contract/src/codemode";

export class CodemodeSandbox extends CodemodeSandboxDurableObject {
  protected override maxInterruptBudget = CODEMODE_INTERRUPT_BUDGET;
  protected override maxMemoryLimitBytes = CODEMODE_MEMORY_BYTES;
}
