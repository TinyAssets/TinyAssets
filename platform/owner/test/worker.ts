/**
 * The workerd tests' Worker: test classes of the owner object, each as production builds it
 * but for what its tests reach past the doors: its starter package, its erasure
 * (`FauxOwnerDO`, which no door reaches yet), and its control record (`SwitchedOwnerDO`,
 * `CachedOwnerDO`). Every one reaches the gates and the fake sandbox Worker the config binds
 * as `FILES`, `GATEWAY` and `SANDBOX`.
 */
import { OPEN_GATES } from "../../ci/fixtures";
import type { Erasure } from "../src/erase";
import type { OwnerEnv } from "../src/env";
import { type Internals, OwnerDO as ProductionOwnerDO } from "../src/owner-do";
import { RecordingErasures } from "./erasure-fake";
import { PLATFORM_CALLER } from "../../contract/src/binding";

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
