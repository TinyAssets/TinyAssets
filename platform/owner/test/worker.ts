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
import {
  type Internals, type OwnerEnv, OwnerDO as ProductionOwnerDO, type SandboxLeases,
} from "../src/owner-do";
import { RecordingErasures } from "./erasure-fake";
import { RecordingSandbox } from "./sandbox-fake";
import { PLATFORM_CALLER } from "../src/admission";
import { LEDGER_LIMITS } from "../src/ledger";

/**
 * The owner object as production builds it but with no starter package, so the tests of
 * everything but the starter start from an empty workspace (`internals.starter`); every
 * class below is one of these.
 */
export class OwnerDO extends ProductionOwnerDO {
  constructor(ctx: DurableObjectState, env: OwnerEnv, internals: Internals = {}) {
    super(ctx, env, { starter: null, ...internals });
  }
}

/**
 * The owner object exactly as production builds it, starter package and all, with a
 * recording fake behind its `SANDBOX` binding and its lease API in reach (`starter.test.ts`).
 */
export class SeededOwnerDO extends ProductionOwnerDO {
  readonly sandbox: SandboxLeases;
  readonly recorder: RecordingSandbox;

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { sandbox?: SandboxLeases } = {};
    const recorder = new RecordingSandbox();
    super(ctx, { ...env, SANDBOX: recorder }, { sandbox: (leases) => { made.sandbox = leases; } });
    this.sandbox = made.sandbox!;
    this.recorder = recorder;
  }
}

/** The owner object, its erasure and its sandbox's lease API in the tests' reach. */
export class FauxOwnerDO extends OwnerDO {
  /** The owner's erasure, the internal path no door reaches yet. */
  readonly erasure: Erasure;
  /** The sandbox's lease API, owner-internal (`SandboxLeases`). */
  readonly sandbox: SandboxLeases;
  /** The recording fake behind its `SANDBOX` binding. */
  readonly recorder: RecordingSandbox;
  /** The erasure queue, recording, confirming the outside record as the doors would. */
  readonly erasures: RecordingErasures;

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { erasure?: Erasure; sandbox?: SandboxLeases } = {};
    const recorder = new RecordingSandbox();
    const erasures = new RecordingErasures();
    super(ctx, { ...env, SANDBOX: recorder, ERASURES: erasures },
          { erasure: (erasure) => { made.erasure = erasure; }, eraseRetryMs: 500, putGraceMs: 0,
            sandbox: (leases) => { made.sandbox = leases; } });
    this.erasure = made.erasure!;
    this.sandbox = made.sandbox!;
    this.recorder = recorder;
    this.erasures = erasures;
    erasures.confirm = (owner) => this.erasureOutsideDone(
      { owner, caller: PLATFORM_CALLER, origin: null });
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
 * The owner object as production builds it but for the ledger's ceilings: eight messages
 * hot, four unread, a segment sealed at two records, so a test sees the `archive` lane move
 * acked mail, a post wait at the hot ceiling, and a purge rewrite, with a handful of posts.
 */
export class ArchivingOwnerDO extends OwnerDO {
  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    super(ctx, env, { ledgerLimits: { ...LEDGER_LIMITS, maxMessages: 8, maxUnacked: 4,
                                      archiveRecords: 2 } });
  }
}

/**
 * As `ArchivingOwnerDO`, but between each move's two passes the oldest hot message's
 * recipient row is changed (its time's last bit, so its line keeps its length), so the
 * second pass makes other bytes than the first hashed: R2's check of the chunk's hash must
 * refuse them.
 */
export class DivergingOwnerDO extends OwnerDO {
  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    super(ctx, env, {
      ledgerLimits: { ...LEDGER_LIMITS, maxMessages: 8, maxUnacked: 4, archiveRecords: 2 },
      archiveBetweenPasses: (storage) => {
        storage.sql.exec("UPDATE ledger_recipients SET changed_at = changed_at + 1 - " +
                           "2 * (changed_at % 2) " +
                           "WHERE seq = (SELECT min(seq) FROM ledger_messages)");
      } });
  }
}

/**
 * As `ArchivingOwnerDO`, but a segment sealed at six records (or 8 MiB), twelve hot and six
 * unread: a straggler merge meets its byte budget before its record cap (review N1).
 */
export class BigArchivingOwnerDO extends OwnerDO {
  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    super(ctx, env, { ledgerLimits: { ...LEDGER_LIMITS, maxMessages: 12, maxUnacked: 6,
                                      archiveRecords: 6 } });
  }
}

/** A control record that reads open three times, then has outside access off; reads counted. */
function switching(): { store: KVNamespace; reads: () => number } {
  let reads = 0;
  const open = { outside: "on", ordinary: "all", canary: [], inbound: false };
  const store = {
    get: async () => (reads++ < 3 ? open : { ...open, outside: "off" }),
  } as unknown as KVNamespace;
  return { store, reads: () => reads };
}

/**
 * The owner object as production builds it but for its CONTROL namespace (`switching`), and
 * a script's read of it serving no call past its own (`controlFreshMs` 0): a test sees the
 * gates read again at each of a script's calls.
 */
export class SwitchedOwnerDO extends OwnerDO {
  readonly #reads: () => number;

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const control = switching();
    super(ctx, { ...env, CONTROL: control.store }, { controlFreshMs: 0 });
    this.#reads = control.reads;
  }

  /** How many times the object read the control record. */
  controlReads(): number {
    return this.#reads();
  }
}

/** As `SwitchedOwnerDO`, with production's freshness: one read serves a second of calls. */
export class CachedOwnerDO extends OwnerDO {
  readonly #reads: () => number;

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const control = switching();
    super(ctx, { ...env, CONTROL: control.store });
    this.#reads = control.reads;
  }

  /** How many times the object read the control record. */
  controlReads(): number {
    return this.#reads();
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
    ringDoorbell: (...args) => gateway.ringDoorbell(...args),
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

/** One ring the object asked of the gateway (slice W): the subscription and the request. */
export interface Rang {
  readonly subscription: string;
  readonly version: number;
  readonly blob: string;
  readonly previous: { readonly version: number; readonly blob: string } | null;
  readonly request: { readonly headers: Readonly<Record<string, string>>; readonly body: string;
                      readonly event_id: string };
}

/**
 * The owner object as production builds it, each ring it asks of its GATEWAY recorded as it
 * is asked, then made by the real gateway (whose sends reach the fake receiver,
 * `gateway/test/fake-upstream.ts`). `during` runs as a ring is asked, so a test lands a change
 * mid-pass.
 */
export class RungOwnerDO extends OwnerDO {
  readonly rang: Rang[] = [];
  during: (() => Promise<void>) | null = null;

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { self?: RungOwnerDO } = {};
    const gateway: GatewayService = {
      refresh: (...args) => env.GATEWAY.refresh(...args),
      revokeAtProvider: (...args) => env.GATEWAY.revokeAtProvider(...args),
      ringDoorbell: async (owner, subscription, version, blob, previous, request) => {
        made.self?.rang.push({ subscription, version, blob, previous, request });
        await made.self?.during?.();
        return env.GATEWAY.ringDoorbell(owner, subscription, version, blob, previous, request);
      },
    };
    super(ctx, { ...env, GATEWAY: gateway });
    made.self = this;
  }
}
