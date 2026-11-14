/**
 * The workerd tests' Worker: test classes of the owner object, each as production builds it
 * but for what its tests reach past the doors: its starter package, its erasure
 * (`FauxOwnerDO`, which no door reaches yet), its GATEWAY calls watched and faulted
 * (`GatedOwnerDO`), and the rest below. Every one reaches the fake sandbox Worker the
 * config binds as `SANDBOX` (`fake-sandbox.ts`).
 */
import type { ChainStep } from "../src/archive";
import { OPEN_GATES } from "../../ci/fixtures";
import type { Erasure } from "../src/erase";
import type { OwnerEnv } from "../src/env";
import { type Internals, OwnerDO as ProductionOwnerDO } from "../src/owner-do";
import { RecordingErasures } from "./erasure-fake";
import { PLATFORM_CALLER } from "../../contract/src/binding";
import type { GatewayService, Resealed } from "../../contract/src/gateway";

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

/** The owner object exactly as production builds it, starter package and all. */
export class SeededOwnerDO extends ProductionOwnerDO {}

/** The owner object, its erasure in the tests' reach. */
export class FauxOwnerDO extends OwnerDO {
  /** The owner's erasure, the internal path no door reaches yet. */
  readonly erasure: Erasure;
  /** The erasure queue, recording, confirming the outside record as the doors would. */
  readonly erasures: RecordingErasures;

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { erasure?: Erasure } = {};
    const erasures = new RecordingErasures();
    super(ctx, { ...env, ERASURES: erasures },
          { erasure: (erasure) => { made.erasure = erasure; }, eraseRetryMs: 500, putGraceMs: 0 });
    this.erasure = made.erasure!;
    this.erasures = erasures;
    erasures.confirm = (owner) => this.erasureOutsideDone(
      { owner, caller: PLATFORM_CALLER, origin: null });
  }
}

/** One chain read held at a step once a test arms it (`Internals.chainStep`). */
class ChainHold {
  #armed: ChainStep | null = null;
  #reached: () => void = () => {};
  #release: () => void = () => {};
  reached: Promise<void> = Promise.resolve();

  arm(step: ChainStep): void {
    this.#armed = step;
    this.reached = new Promise((resolve) => { this.#reached = resolve; });
  }

  async pass(step: ChainStep): Promise<void> {
    if (this.#armed !== step) {
      return;
    }
    this.#armed = null;
    const released = new Promise<void>((resolve) => { this.#release = resolve; });
    this.#reached();
    await released;
  }

  release(): void {
    this.#release();
  }
}

/**
 * The owner object as production builds it, but a test may hold its next chain read at a
 * step (`holdChain`: after its fences unit, or before its hot unit), change the archive
 * there while the `archive` lane moves mail, and let it go on (`releaseChain`).
 */
export class ArchivingOwnerDO extends OwnerDO {
  readonly #hold: ChainHold;

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const hold = new ChainHold();
    super(ctx, env, { chainStep: (step) => hold.pass(step) });
    this.#hold = hold;
  }

  /** Hold the next chain read at `step`. */
  holdChain(step: ChainStep): void {
    this.#hold.arm(step);
  }

  /** Answered once the held chain read waits there. */
  async chainHeld(): Promise<void> {
    await this.#hold.reached;
  }

  /** Let the held chain read go on. */
  releaseChain(): void {
    this.#hold.release();
  }
}

/**
 * The owner object as production builds it, but between each move's two passes the oldest
 * acked hot message's recipient row (a tail move's first record) is changed (its time's
 * last bit, so its line keeps its length), so the second pass makes other bytes than the
 * first hashed: R2's check of the chunk's hash must refuse them.
 */
export class DivergingOwnerDO extends OwnerDO {
  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    super(ctx, env, {
      archiveBetweenPasses: (storage) => {
        storage.sql.exec("UPDATE ledger_recipients SET changed_at = changed_at + 1 - " +
                           "2 * (changed_at % 2) " +
                           "WHERE seq = (SELECT min(seq) FROM ledger_messages WHERE " +
                           "unacked = 0)");
      } });
  }
}

/**
 * The owner object as production builds it, but each move as it saw itself between its
 * passes is kept in a table of the test's own (`test_moves`), so a test of a purge across a
 * run of real-sized segments sees each pass's bound.
 */
export class PurgingOwnerDO extends OwnerDO {
  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    super(ctx, env, {
      archiveBetweenPasses: (storage, move) => {
        storage.sql.exec("CREATE TABLE IF NOT EXISTS test_moves (stream TEXT, replaced " +
                           "INTEGER, records INTEGER)");
        storage.sql.exec("INSERT INTO test_moves VALUES (?, ?, ?)", move.stream,
                         move.replaced, move.records);
      } });
  }
}

/** A control record that reads open three times, then has outside access off; reads counted. */
function switching(): { store: KVNamespace; reads: () => number } {
  let reads = 0;
  const store = {
    get: async () => (reads++ < 3 ? OPEN_GATES : { ...OPEN_GATES, outside: "off" }),
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
  /** Awaited before a reseal's answer is handed back, when set. */
  resealGate: Promise<void> | null;
  /** The next chunk read from the files plane rejected with this, when set, then cleared. */
  chunkRead: unknown;
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
    reseal: async (...args) => {
      calls().push({ method: "reseal", text: JSON.stringify(args) });
      const answer = await gateway.reseal(...args);
      await faults()?.resealGate;
      return answer;
    },
    ringDoorbell: (...args) => gateway.ringDoorbell(...args),
  };
}

/** The files plane, its next chunk read rejected with the fault a test sets, once. */
function faulted(files: OwnerEnv["FILES"], faults: () => GatewayFaults | undefined):
    OwnerEnv["FILES"] {
  return new Proxy(files, {
    get(target, key) {
      if (key === "getChunks") {
        return (...args: Parameters<OwnerEnv["FILES"]["getChunks"]>) => {
          const fault = faults()?.chunkRead;
          if (fault !== undefined && fault !== null) {
            faults()!.chunkRead = null;
            return Promise.reject(fault);
          }
          return target.getChunks(...args);
        };
      }
      // Every other method called on the binding itself, as its RPC stub needs.
      return typeof key === "symbol" || key === "then" ? Reflect.get(target, key)
        : (...args: unknown[]) =>
          (target as unknown as Record<string, (...a: unknown[]) => unknown>)[key]!(...args);
    },
  });
}

/**
 * The owner object as production builds it, its GATEWAY calls watched, and the faults a test
 * sets on them and on its chunk reads.
 */
export class GatedOwnerDO extends OwnerDO {
  calls: GatewayCall[] = [];
  readonly faults: GatewayFaults = { refreshGate: null, refreshed: null, revokeGate: null,
                                      resealGate: null, chunkRead: null };

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { self?: GatedOwnerDO } = {};
    const pending: GatewayCall[] = [];
    super(ctx, { ...env, GATEWAY: watched(env.GATEWAY, () => made.self?.calls ?? pending,
                                          () => made.self?.faults),
                 FILES: faulted(env.FILES, () => made.self?.faults) });
    made.self = this;
    this.calls.push(...pending);
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
      reseal: (...args) => env.GATEWAY.reseal(...args),
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
