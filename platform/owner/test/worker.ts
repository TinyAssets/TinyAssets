/**
 * The workerd tests' Worker: the owner Worker's own export (its `SANDBOX` the real sandbox
 * Worker, run beside it against no E2B), and three test classes of the same object: two
 * with a recording fake behind `SANDBOX` (`sandbox-fake.ts`), one with its owner-internal
 * paths in the tests' reach (`FauxOwnerDO`: the erasure, which no door reaches yet, and the
 * sandbox's lease API), and one with its GATEWAY calls watched and faulted
 * (`GatedOwnerDO`); and one with no `SANDBOX` at all (`UnboundOwnerDO`), whose every run is
 * refused (`SANDBOX_ABSENT`).
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

/** The owner object with no `SANDBOX` bound: every run refused, every cancel too. */
export class UnboundOwnerDO extends OwnerDO {
  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const { SANDBOX: _sandbox, ...rest } = env;
    super(ctx, rest);
  }
}
