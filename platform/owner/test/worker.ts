/**
 * The workerd tests' Worker: the owner Worker's own export (with no `SANDBOX` bound, as
 * production has none until slice S), and two test classes of the same object, each with a
 * recording fake behind `SANDBOX` (`sandbox-fake.ts`): one with its owner-internal paths in
 * the tests' reach (`FauxOwnerDO`: the erasure, which no door reaches yet, and the
 * sandbox's lease API), and one with its GATEWAY calls watched and faulted (`GatedOwnerDO`).
 */
import type { GatewayService, Resealed } from "../src/connections";
import type { Erasure } from "../src/erase";
import { type OwnerEnv, OwnerDO, type SandboxLeases } from "../src/owner-do";
import { RecordingSandbox } from "./sandbox-fake";

export { OwnerDO } from "../src/owner-do";

/** The owner object, its erasure and its sandbox's lease API in the tests' reach. */
export class FauxOwnerDO extends OwnerDO {
  /** The owner's erasure, the internal path no door reaches yet. */
  readonly erasure: Erasure;
  /** The sandbox's lease API, owner-internal (`SandboxLeases`). */
  readonly sandbox: SandboxLeases;
  /** The recording fake behind its `SANDBOX` binding. */
  readonly recorder: RecordingSandbox;

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { erasure?: Erasure; sandbox?: SandboxLeases } = {};
    const recorder = new RecordingSandbox();
    super(ctx, { ...env, SANDBOX: recorder },
          { erasure: (erasure) => { made.erasure = erasure; }, eraseRetryMs: 500,
            sandbox: (leases) => { made.sandbox = leases; } });
    this.erasure = made.erasure!;
    this.sandbox = made.sandbox!;
    this.recorder = recorder;
  }
}

/**
 * The owner object as production builds it but for code mode's bounds, which are small
 * here (production's are `CODEMODE_TIMEOUT_MS` and the hooks Worker's own budget), so a
 * test sees a script stopped past each.
 */
export class BoundedOwnerDO extends OwnerDO {
  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    super(ctx, env, { codemode: { timeoutMs: 2_000, interruptBudget: 2_000 } });
  }
}

/**
 * The owner object as production builds it but for its CONTROL namespace, a fake whose
 * control record reads open three times and then has outside access off, and a script's
 * read of it serving no call past its own (`controlFreshMs` 0): a test sees the gates read
 * again at each of a script's calls.
 */
export class SwitchedOwnerDO extends OwnerDO {
  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    let reads = 0;
    const open = { outside: "on", ordinary: "all", canary: [], inbound: false };
    const control = {
      get: async () => (reads++ < 3 ? open : { ...open, outside: "off" }),
    } as unknown as KVNamespace;
    super(ctx, { ...env, CONTROL: control }, { controlFreshMs: 0 });
  }
}

/** One call the object made to its GATEWAY binding, as text: what crossed the binding. */
export interface GatewayCall {
  readonly method: string;
  readonly text: string;
}

/** Faults a test sets on the GATEWAY binding: a gate a call waits on, an answer changed. */
export interface GatewayFaults {
  /** Awaited before a refresh is sent, when set. */
  refreshGate: Promise<void> | null;
  /** Applied to a refresh's answer, when set. */
  refreshed: ((answer: Resealed & { readonly expires_at: number | null }) =>
    Resealed & { readonly expires_at: number | null }) | null;
  /** Awaited before a provider revoke is sent, when set. */
  revokeGate: Promise<void> | null;
}

/**
 * The GATEWAY binding with every call recorded on `calls()` before it is made (an RPC's
 * arguments), and the faults a test set.
 */
function watched(gateway: GatewayService, calls: () => GatewayCall[],
                 faults: () => GatewayFaults | undefined): GatewayService {
  return {
    refresh: async (...args) => {
      calls().push({ method: "refresh", text: JSON.stringify(args) });
      await faults()?.refreshGate;
      const answer = await gateway.refresh(...args);
      return faults()?.refreshed?.(answer) ?? answer;
    },
    revokeAtProvider: async (...args) => {
      calls().push({ method: "revokeAtProvider", text: JSON.stringify(args) });
      await faults()?.revokeGate;
      return gateway.revokeAtProvider(...args);
    },
  };
}

/**
 * The owner object as production builds it, its GATEWAY calls watched, the faults a test
 * sets on them, and its sandbox's lease API in reach.
 */
export class GatedOwnerDO extends OwnerDO {
  /** The sandbox's lease API, owner-internal (`SandboxLeases`). */
  readonly sandbox: SandboxLeases;
  /** The recording fake behind its `SANDBOX` binding. */
  readonly recorder: RecordingSandbox;
  calls: GatewayCall[] = [];
  readonly faults: GatewayFaults = { refreshGate: null, refreshed: null, revokeGate: null };

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { self?: GatedOwnerDO; sandbox?: SandboxLeases } = {};
    const pending: GatewayCall[] = [];
    const recorder = new RecordingSandbox();
    super(ctx, { ...env, GATEWAY: watched(env.GATEWAY, () => made.self?.calls ?? pending,
                                          () => made.self?.faults), SANDBOX: recorder },
          { sandbox: (leases) => { made.sandbox = leases; } });
    made.self = this;
    this.sandbox = made.sandbox!;
    this.recorder = recorder;
    this.calls.push(...pending);
  }
}

/** One request the doorbell sent, as it left: its URL, headers and body. */
export interface Rang {
  readonly url: string;
  readonly method: string;
  readonly redirect: string;
  readonly headers: Readonly<Record<string, string>>;
  readonly body: string;
}

/**
 * The doorbell's transport in the tests: every request recorded as it left, answered from
 * a script (a status, `throw` for a connection lost after the byte, `hang` for one that
 * never answers), 202 when the script is empty. `during` runs as a request leaves, so a
 * test lands a change mid-pass.
 */
export class RecordingTransport {
  readonly rang: Rang[] = [];
  readonly answers: (number | "throw" | "hang")[] = [];
  readonly retryAfter: (string | null)[] = [];
  during: (() => Promise<void>) | null = null;

  readonly send = async (request: Request): Promise<Response> => {
    this.rang.push({ url: request.url, method: request.method, redirect: request.redirect,
                     headers: Object.fromEntries(request.headers), body: await request.text() });
    await this.during?.();
    const answer = this.answers.shift() ?? 202;
    if (answer === "throw") {
      throw new Error("the connection was lost after the request left");
    }
    if (answer === "hang") {
      return new Promise<Response>(() => undefined);
    }
    const retry = this.retryAfter.shift() ?? null;
    return new Response("not read", { status: answer,
                                      headers: retry === null ? {} : { "Retry-After": retry } });
  };
}

/** The owner object as production builds it, its doorbell's requests recorded, not sent. */
export class RungOwnerDO extends OwnerDO {
  readonly doorbell: RecordingTransport;

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const transport = new RecordingTransport();
    super(ctx, env, { transport: transport.send });
    this.doorbell = transport;
  }
}
