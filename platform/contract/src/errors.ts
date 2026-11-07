/**
 * The trusted side's two refusals, which every Worker throws and reads by name across RPC.
 */

/** A request the trusted side will not do: the caller lacks the right, or a bound is reached. */
export class Refused extends Error {
  override readonly name = "Refused";
}

/** A request that is malformed: a wrong field, type or size. */
export class Invalid extends Error {
  override readonly name = "Invalid";
}

/** What every door and lane answers while the owner is erased (`owner/src/erasure.ts`). */
export const ERASING = "this owner is being erased";
