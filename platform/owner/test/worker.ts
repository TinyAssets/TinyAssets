/**
 * The workerd tests' Worker: the owner Worker's own export, and two test classes of the
 * same object: one with its owner-internal paths in the tests' reach (`FauxOwnerDO`: the
 * erasure, which no door reaches yet, and the sandbox's lease API, which the runs slice
 * calls in process), and one with its GATEWAY calls watched and faulted (`GatedOwnerDO`).
 */
import type { GatewayService, Resealed } from "../src/connections";
import type { Erasure } from "../src/erase";
import { type OwnerEnv, OwnerDO, type SandboxLeases } from "../src/owner-do";

export { OwnerDO } from "../src/owner-do";

/** The owner object, its erasure and its sandbox's lease API in the tests' reach. */
export class FauxOwnerDO extends OwnerDO {
  /** The owner's erasure, the internal path no door reaches yet. */
  readonly erasure: Erasure;
  /** The sandbox's lease API, owner-internal (`SandboxLeases`). */
  readonly sandbox: SandboxLeases;

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { erasure?: Erasure; sandbox?: SandboxLeases } = {};
    super(ctx, env, { erasure: (erasure) => { made.erasure = erasure; }, eraseRetryMs: 500,
                      sandbox: (leases) => { made.sandbox = leases; } });
    this.erasure = made.erasure!;
    this.sandbox = made.sandbox!;
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
  calls: GatewayCall[] = [];
  readonly faults: GatewayFaults = { refreshGate: null, refreshed: null, revokeGate: null };

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { self?: GatedOwnerDO; sandbox?: SandboxLeases } = {};
    const pending: GatewayCall[] = [];
    super(ctx, { ...env, GATEWAY: watched(env.GATEWAY, () => made.self?.calls ?? pending,
                                          () => made.self?.faults) },
          { sandbox: (leases) => { made.sandbox = leases; } });
    made.self = this;
    this.sandbox = made.sandbox!;
    this.calls.push(...pending);
  }
}
