/**
 * The owner suite's sandbox Worker, bound by config behind the `SANDBOX` gate
 * (`vitest.config.ts`): every `SandboxService` method the owner's object calls, recorded by
 * owner and answered as the test scripted that owner's answers. Nothing is started: the real
 * sandbox Worker (`platform/sandbox`) is tested against a fake E2B in its own package. A test
 * reads the record and sets the answers through `Log`, an entrypoint bound to the test Worker
 * alone; a test that acts while a call is out holds it at the gate in front.
 *
 * A copy of this Worker runs in each workerd process and outlives a test, so everything is
 * kept by owner, and each test names a fresh one.
 */
import { WorkerEntrypoint } from "cloudflare:workers";

import type {
  MeterEvent, NetworkApplied, NetworkReapply, SandboxSignin, SandboxStart, Started,
} from "../../contract/src/sandbox";

/** What one owner's object asked of the sandbox. */
export interface Recorded {
  readonly starts: SandboxStart[];
  readonly cancels: { run: string; lease_id: string }[];
  readonly releases: { lease_id: string }[];
  readonly networks: NetworkReapply[];
  readonly signins: SandboxSignin[];
  readonly codes: { authority: string; id: string; code: string }[];
  readonly signinCancels: { authority: string; id: string }[];
  /** The owner, once for each time its object left the platform's line. */
  readonly unwaits: string[];
  /** The owner, once for each time its object erased its sandboxes. */
  readonly erasures: string[];
}

/** How the sandbox answers one owner's object, as a test sets it. */
export interface Script {
  /**
   * What a start answers: started, a refusal's code, or no answer at all (a throw); a list
   * answers each start in turn, its last answer from then on.
   */
  readonly answer: Started | "throw" | readonly (Started | "throw")[];
  /** What a sign-in's start answers. */
  readonly signinAnswer: Started | "throw";
  /** What a network re-apply answers, or no answer at all (a throw). */
  readonly networkAnswer: NetworkApplied | "throw";
  /** Whether a read of the log fails. */
  readonly meterFails: boolean;
}

/** Every call answered as a sandbox that took it does, until a test scripts otherwise. */
const STARTED = Object.freeze<Script>({ answer: { started: true },
                                        signinAnswer: { started: true },
                                        networkAnswer: { state: "applied" }, meterFails: false });

const records = new Map<string, Recorded>();
const scripts = new Map<string, Script>();
const logs = new Map<string, MeterEvent[]>();

function recordOf(owner: string): Recorded {
  let record = records.get(owner);
  if (record === undefined) {
    record = { starts: [], cancels: [], releases: [], networks: [], signins: [],
               codes: [], signinCancels: [], unwaits: [], erasures: [] };
    records.set(owner, record);
  }
  return record;
}

const scriptOf = (owner: string): Script => scripts.get(owner) ?? STARTED;

/** `answer`, or no answer at all. */
function answered<T>(answer: T | "throw"): T {
  if (answer === "throw") {
    throw new Error("the sandbox did not answer");
  }
  return answer;
}

/** The sandbox Worker as the owner's object reaches it. */
export default class FakeSandbox extends WorkerEntrypoint {
  async start(owner: string, start: SandboxStart): Promise<Started> {
    const starts = recordOf(owner).starts;
    starts.push(start);
    const { answer } = scriptOf(owner);
    const answers = Array.isArray(answer) ? answer : [answer];
    return answered(answers[Math.min(starts.length, answers.length) - 1]!);
  }

  async cancel(owner: string, request: { run: string; lease_id: string }):
      Promise<{ cancelled: boolean }> {
    recordOf(owner).cancels.push({ run: request.run, lease_id: request.lease_id });
    return { cancelled: true };
  }

  async status(): Promise<{ state: "running" | "exited" | "gone" }> {
    return { state: "running" };
  }

  async release(owner: string, request: { lease_id: string }): Promise<{ paused: boolean }> {
    recordOf(owner).releases.push({ lease_id: request.lease_id });
    return { paused: true };
  }

  async network(owner: string, request: NetworkReapply): Promise<NetworkApplied> {
    recordOf(owner).networks.push(request);
    return answered(scriptOf(owner).networkAnswer);
  }

  async signin(owner: string, signin: SandboxSignin): Promise<Started> {
    recordOf(owner).signins.push(signin);
    return answered(scriptOf(owner).signinAnswer);
  }

  async signinInput(owner: string, request: { authority: string; id: string; code: string }):
      Promise<{ delivered: boolean }> {
    recordOf(owner).codes.push({ authority: request.authority, id: request.id,
                                 code: request.code });
    return { delivered: true };
  }

  async signinCancel(owner: string, request: { authority: string; id: string }):
      Promise<{ cancelled: boolean }> {
    recordOf(owner).signinCancels.push({ authority: request.authority, id: request.id });
    return { cancelled: true };
  }

  async unwait(owner: string): Promise<void> {
    recordOf(owner).unwaits.push(owner);
  }

  async erase(owner: string): Promise<{ killed: number }> {
    recordOf(owner).erasures.push(owner);
    return { killed: 1 };
  }

  async meter(owner: string, request: { after: number }):
      Promise<{ events: MeterEvent[]; next: number }> {
    if (scriptOf(owner).meterFails) {
      throw new Error("the sandbox did not answer");
    }
    const events = (logs.get(owner) ?? []).filter((event) => event.seq > request.after)
      .slice(0, 1000);
    return { events, next: events.at(-1)?.seq ?? request.after };
  }
}

/** The test's handle on the fake: its record, its answers and its log, by owner. */
export class Log extends WorkerEntrypoint {
  /** What `owner`'s object asked, so far. */
  async recorded(owner: string): Promise<Recorded> {
    return recordOf(owner);
  }

  /** `owner`'s answers from now on: the ones given, the rest as they were. */
  async script(owner: string, script: Partial<Script>): Promise<void> {
    scripts.set(owner, { ...scriptOf(owner), ...script });
  }

  /** A sandbox of `owner`'s started, paused, resumed or killed at `at` (ms): one more entry. */
  async event(owner: string, authority: string, kind: MeterEvent["kind"], at: number):
      Promise<void> {
    const log = logs.get(owner) ?? [];
    log.push({ seq: log.length + 1, authority, kind, at });
    logs.set(owner, log);
  }
}
