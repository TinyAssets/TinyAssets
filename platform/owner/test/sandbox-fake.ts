/**
 * A recording fake behind the `SANDBOX` interface (`sandbox.ts`), in tests only: it records
 * every start, cancel, status and release the owner's object sends, and answers as a test
 * sets it. Nothing is started: slice S's sandbox Worker (`platform/sandbox`) is the real
 * one, tested against a fake E2B in its own package.
 */
import type { SandboxService, SandboxStart, Started } from "../src/sandbox";

export class RecordingSandbox implements SandboxService {
  readonly starts: SandboxStart[] = [];
  readonly cancels: { run: string; lease_id: string }[] = [];
  readonly releases: { lease_id: string }[] = [];
  /**
   * What a start answers: started, a refusal's code, or no answer at all (a throw); a list
   * answers each start in turn, its last answer from then on.
   */
  answer: Started | "throw" | readonly (Started | "throw")[] = { started: true };

  async start(_owner: string, start: SandboxStart): Promise<Started> {
    this.starts.push(start);
    const answers = Array.isArray(this.answer) ? this.answer : [this.answer];
    const answer = answers[Math.min(this.starts.length, answers.length) - 1]!;
    if (answer === "throw") {
      throw new Error("the sandbox did not answer");
    }
    return answer as Started;
  }

  async cancel(_owner: string, request: { run: string; lease_id: string }):
      Promise<{ cancelled: boolean }> {
    this.cancels.push({ run: request.run, lease_id: request.lease_id });
    return { cancelled: true };
  }

  async status(): Promise<{ state: "running" | "exited" | "gone" }> {
    return { state: "running" };
  }

  async release(_owner: string, request: { lease_id: string }): Promise<{ paused: boolean }> {
    this.releases.push({ lease_id: request.lease_id });
    return { paused: true };
  }
}
