/**
 * The widenings' proofs' mint (`door.ts`): the runner's alone. Only the runner (`owner-do.ts`)
 * mints, and only the proofs' check (`door.ts`'s `proven`) reads the brand, so they alone
 * import this module (the layer table's `SOLE_IMPORTERS`). A proof's brand is a private
 * field, which only this class's body can read and only its constructor gives: no object made
 * elsewhere holds it, a copy, a spread, a proxy or a prototype of a minted one included.
 */

/** A minted proof's brand. */
class Minted {
  readonly #minted = true;
  static is(value: unknown): boolean {
    return typeof value === "object" && value !== null && #minted in value;
  }
}

/** A proof, frozen and branded: the runner's alone (`owner-do.ts`). */
export function mintProof<P extends object>(proof: P): P {
  return Object.freeze(Object.assign(new Minted(), proof));
}

/** Whether `value` is a proof this module minted: the brand `proven` reads. */
export function isMinted(value: unknown): boolean {
  return Minted.is(value);
}
