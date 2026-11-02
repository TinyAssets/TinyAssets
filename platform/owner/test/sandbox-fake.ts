/**
 * A recording fake behind the `SANDBOX` interface (`sandbox.ts`), in tests only: it records
 * every start, cancel and status the owner's object sends, and answers as a test sets it.
 * Nothing is started: slice S's sandbox Worker is the real one.
 */
import type { SandboxService, SandboxStart, Started } from "../src/sandbox";

export class RecordingSandbox implements SandboxService {
  readonly starts: SandboxStart[] = [];
  readonly cancels: { run: string; lease_id: string }[] = [];
  /** What a start answers: started, a refusal's code, or no answer at all (a throw). */
  answer: Started | "throw" = { started: true };

  async start(_owner: string, start: SandboxStart): Promise<Started> {
    this.starts.push(start);
    if (this.answer === "throw") {
      throw new Error("the sandbox did not answer");
    }
    return this.answer;
  }

  async cancel(_owner: string, request: { run: string; lease_id: string }):
      Promise<{ cancelled: boolean }> {
    this.cancels.push({ run: request.run, lease_id: request.lease_id });
    return { cancelled: true };
  }

  async status(): Promise<{ state: "running" | "exited" | "gone" }> {
    return { state: "running" };
  }
}
