/**
 * A recording fake behind the `SANDBOX` interface (`sandbox.ts`), in tests only: it records
 * every start, cancel, status, release, unwait and erase the owner's object sends, and
 * answers as a test
 * sets it. Nothing is started: slice S's sandbox Worker (`platform/sandbox`) is the real
 * one, tested against a fake E2B in its own package.
 */
import type {
  MeterEvent, SandboxService, SandboxSignin, SandboxStart, Started,
} from "../../contract/src/sandbox";

export class RecordingSandbox implements SandboxService {
  readonly starts: SandboxStart[] = [];
  readonly cancels: { run: string; lease_id: string }[] = [];
  readonly releases: { lease_id: string }[] = [];
  readonly unwaits: string[] = [];
  readonly erasures: string[] = [];
  readonly signins: SandboxSignin[] = [];
  readonly codes: { authority: string; id: string; code: string }[] = [];
  readonly signinCancels: { authority: string; id: string }[] = [];
  /** What a sign-in's start answers. */
  signinAnswer: Started | "throw" = { started: true };
  /**
   * What a start answers: started, a refusal's code, or no answer at all (a throw); a list
   * answers each start in turn, its last answer from then on.
   */
  answer: Started | "throw" | readonly (Started | "throw")[] = { started: true };
  /** Run before a start answers, with what it was asked: a test's act while it is out. */
  beforeAnswer: ((start: SandboxStart) => Promise<void>) | null = null;

  async start(_owner: string, start: SandboxStart): Promise<Started> {
    this.starts.push(start);
    await this.beforeAnswer?.(start);
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

  async signin(_owner: string, signin: SandboxSignin): Promise<Started> {
    this.signins.push(signin);
    if (this.signinAnswer === "throw") {
      throw new Error("the sandbox did not answer");
    }
    return this.signinAnswer;
  }

  async signinInput(_owner: string, request: { authority: string; id: string; code: string }):
      Promise<{ delivered: boolean }> {
    this.codes.push({ ...request });
    return { delivered: true };
  }

  async signinCancel(_owner: string, request: { authority: string; id: string }):
      Promise<{ cancelled: boolean }> {
    this.signinCancels.push({ ...request });
    return { cancelled: true };
  }

  async unwait(owner: string): Promise<void> {
    this.unwaits.push(owner);
  }

  async erase(owner: string): Promise<{ killed: number }> {
    this.erasures.push(owner);
    return { killed: 1 };
  }

  /** The sandbox Worker's log as a test writes it, and whether a read fails. */
  readonly log: MeterEvent[] = [];
  meterFails = false;

  /** A sandbox started, paused, resumed or killed at `at` (ms): one more log entry. */
  record(authority: string, kind: MeterEvent["kind"], at: number): void {
    this.log.push({ seq: this.log.length + 1, authority, kind, at });
  }

  async meter(_owner: string, request: { after: number }):
      Promise<{ events: MeterEvent[]; next: number }> {
    if (this.meterFails) {
      throw new Error("the sandbox did not answer");
    }
    const events = this.log.filter((event) => event.seq > request.after).slice(0, 1000);
    return { events, next: events.at(-1)?.seq ?? request.after };
  }
}
