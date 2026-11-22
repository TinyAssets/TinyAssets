/**
 * pi-codemode's sandbox, in the codemode Worker (pi-codemode's "Usage on Cloudflare
 * Workers"): the VM of an owner's code mode script (the MCP door's `code`, whichever client
 * or engine calls it) runs here, in a Durable Object of a separately deployed Worker, so a
 * script that computes never holds the owner's object (pi measured 10 s in the session
 * object or a Dynamic Worker it loads, about 50 ms here). The owner's `RemoteCodemodeSandbox`
 * keeps the tools, the journal, the deadline and the abort; this object only runs the VM,
 * with pi-codemode's `serveCodemodeRemote`, calling back into the owner through `exchange`
 * for every tool call.
 *
 * Its limits apply to every request, whatever the owner asks, each a ceiling of the Worker's
 * own (`limits-audit.md`). It keeps no state and never touches its storage.
 */
import { CodemodeSandboxDurableObject } from "@earendil-works/pi-codemode/cloudflare";

/**
 * Interrupt polls per execution, the script's compute: under the Worker's `cpu_ms`
 * (`wrangler.jsonc`), since a script that reaches that resets the object instead of failing
 * alone, its output and calls lost. pi-codemode measured 100,000 polls at about 10 s of
 * a tight loop on Cloudflare, so this is about 100 s: a third of `cpu_ms`, so a production
 * rate three times slower than that measurement still ends the script first (H2's probe 1
 * measures it live).
 */
const CODEMODE_INTERRUPT_BUDGET = 1_000_000;
/**
 * VM memory per execution: a script past it fails inside itself (`out of memory`), where
 * the isolate's 128 MB, which every VM placed in it shares, would reset them all. A quarter of
 * the isolate, pi-codemode's own default: Cloudflare does not say how many objects of one
 * class it runs in an isolate at once, so H2's probe 6 holds eight scripts at it at once,
 * twice what one isolate fits, each to end by its own value or memory alone.
 */
const CODEMODE_MEMORY_BYTES = 32 * 1024 * 1024;

export class CodemodeSandbox extends CodemodeSandboxDurableObject {
  protected override maxInterruptBudget = CODEMODE_INTERRUPT_BUDGET;
  protected override maxMemoryLimitBytes = CODEMODE_MEMORY_BYTES;
}
