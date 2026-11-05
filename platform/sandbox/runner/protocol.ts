/**
 * The Worker↔runner protocol's version (runner by digest; the rule is `state-format.ts`'s):
 * the commands the runner takes, their arguments and what the Worker reads back (`state`'s
 * answer), as `protocol.json` records each version, frozen. The runner reports its own in
 * `state`; the Worker speaks `SPEAKS` and refuses loudly a runner speaking another. A change
 * ships in two deploys: the first Worker speaks both while its runner speaks the old one,
 * the second runner speaks the new one. No imports: the Worker and the runner both take it.
 */

/** The protocol this runner speaks. */
export const PROTOCOL = 1;
/** The runner protocols the Worker speaks: its runner's, and at most the ones either side. */
export const SPEAKS: readonly number[] = Object.freeze([1]);
