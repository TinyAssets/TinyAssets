/**
 * A runner-shaped bundle installed in a sandbox by the template's loader, the one way it is
 * done: its bytes uploaded to the loader's incoming directory by their SHA-256, then
 * `ta-runner install <sha>`, which checks them and moves them into place (`template/ta-runner`).
 * The sandbox Worker installs its runner so (`control.ts`), and the template's checks install
 * the runner and then the probes so (`template/checks.ts`). Anything but exit 0 throws. It
 * answers how long the upload and the install took.
 */
import type { E2b, Handle } from "./e2b";
import { EXIT, incoming, INSTALL, LOADER } from "../runner/protocol";

export async function install(e2b: E2b, handle: Handle, bytes: Uint8Array, sha: string,
                              timeoutMs: number):
    Promise<{ readonly upload_ms: number; readonly install_ms: number }> {
  const began = Date.now();
  await e2b.upload(handle, incoming(sha), bytes);
  const uploaded = Date.now();
  const done = await e2b.exec(handle, [LOADER, INSTALL, sha], { timeoutMs });
  if (done.exitCode !== EXIT.ok) {
    throw new Error(`the runner ${sha} was not installed (${done.exitCode}): ${done.stderr}`);
  }
  return { upload_ms: uploaded - began, install_ms: Date.now() - uploaded };
}
