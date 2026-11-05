/**
 * The runner this Worker ships (`template/bundle.mjs`'s `runner.mjs`, built by the Worker's
 * own build, `build.command` in `wrangler.jsonc`, and imported as data): the bytes every
 * sandbox runs, and their SHA-256, computed from these bytes. Nothing about the runner is
 * committed or pinned, so nothing can drift from the code that was reviewed and deployed:
 * the digest is what the loader in the template checks before every command. No module
 * state: each call reads the import afresh (`SandboxControl` keeps its digest once an
 * instance).
 */
import bundle from "../dist/runner.mjs";

/** The runner's bytes, a copy of their own: nothing a caller does reaches the import. */
export function runnerBytes(): Uint8Array {
  return new Uint8Array(bundle.slice(0));
}

/** The runner's SHA-256, lower-case hex: the name the sandbox's loader runs it by. */
export async function runnerDigest(): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", bundle);
  return [...new Uint8Array(hash)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
