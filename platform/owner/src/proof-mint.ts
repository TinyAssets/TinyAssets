/**
 * The widenings' proofs' mint (`door.ts`): the runner's alone. Only the runner (`owner-do.ts`)
 * mints, and only the proofs' check (`door.ts`'s `proven`) reads the brand, so they alone
 * import this module (the layer table's `SOLE_IMPORTERS`). A proof's brand is a private
 * field, which only this class's body can read and only its constructor gives: no object made
 * elsewhere holds it, a copy, a spread, a proxy or a prototype of a minted one included. A
 * minted proof has no prototype, so it hands no way back to the class (its `constructor`).
 * And it is live only while the door's call that minted it runs (`Lease`): one kept past it is
 * none.
 */

/** A door's call while it runs: the runner ends it once the call settles (`#leased`). */
export interface Lease {
  live: boolean;
}

/** A minted proof's brand: the lease of the call it was minted in. */
class Minted {
  readonly #lease: Lease;
  constructor(lease: Lease) {
    this.#lease = lease;
  }
  static is(value: unknown): boolean {
    return typeof value === "object" && value !== null && #lease in value
      && (value as Minted).#lease.live;
  }
}

/** A proof, frozen and branded, live for `lease`: the runner's alone (`owner-do.ts`). */
export function mintProof<P extends object>(proof: P, lease: Lease): P {
  return Object.freeze(Object.assign(Object.setPrototypeOf(new Minted(lease), null), proof));
}

/** Whether `value` is a proof this module minted, its call still running: what `proven` reads. */
export function isMinted(value: unknown): boolean {
  return Minted.is(value);
}
