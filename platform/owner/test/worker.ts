/**
 * The workerd tests' Worker: the owner object with no starter package (`OwnerDO`), so the
 * tests of everything but the starter start from an empty workspace, and the object exactly
 * as production builds it (`SeededOwnerDO`). Each reaches the gates and the fakes the config
 * binds as `FILES`, `GATEWAY`, `SANDBOX` and `ERASURES`.
 */
import type { OwnerEnv } from "../src/env";
import { OwnerDO as ProductionOwnerDO } from "../src/owner-do";

/** The owner object as production builds it but with no starter package. */
export class OwnerDO extends ProductionOwnerDO {
  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    super(ctx, env, { starter: null });
  }
}

/** The owner object exactly as production builds it, starter package and all. */
export class SeededOwnerDO extends ProductionOwnerDO {}
