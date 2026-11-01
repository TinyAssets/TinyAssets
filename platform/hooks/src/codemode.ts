/**
 * pi's code mode sandbox, in the hooks Worker (pi audit F8; pi-codemode's
 * "Usage on Cloudflare Workers"): the VM of an owner's code mode script runs here, in a
 * Durable Object of a separately deployed Worker, so a script that computes never holds
 * the owner's object (pi measured 10 s in the session object or a Dynamic Worker it loads,
 * about 50 ms here). The owner's `RemoteCodemodeSandbox` keeps the tools, the journal, the
 * deadline and the abort; this object only runs the VM, with pi's `serveCodemodeRemote`,
 * calling back into the owner through `exchange` for every tool call.
 *
 * Its limits apply to every request, whatever the owner asks: an interrupt budget well
 * below the Worker's `cpu_ms` (`wrangler.jsonc`), since hitting that resets the object
 * instead of failing one script, and pi's 32 MiB of VM memory. It keeps no state and never
 * touches its storage.
 */
import { CodemodeSandboxDurableObject } from "@earendil-works/pi-codemode/cloudflare";

/** Interrupt polls per execution: pi's default, about 10 s of a tight loop on Cloudflare. */
export const CODEMODE_INTERRUPT_BUDGET = 100_000;
/** VM memory per execution: pi's remote default. */
export const CODEMODE_MEMORY_BYTES = 32 * 1024 * 1024;

export class CodemodeSandbox extends CodemodeSandboxDurableObject {
  protected override maxInterruptBudget = CODEMODE_INTERRUPT_BUDGET;
  protected override maxMemoryLimitBytes = CODEMODE_MEMORY_BYTES;
}
