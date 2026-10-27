/** A request the trusted side will not do: the caller lacks the right, or a bound is reached. */
export class Refused extends Error {
  override readonly name = "Refused";
}

/** A request that is malformed: a wrong field, type or size. */
export class Invalid extends Error {
  override readonly name = "Invalid";
}
