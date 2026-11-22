/**
 * The arithmetic every model-spend reservation shares, the object's (`usage.ts`) and a
 * sandbox's (`egress.ts`): the UTC periods its counters are kept by and how long each is
 * kept, micro-dollars, and the bisection that finds the most output a cost room takes. Pure:
 * no storage, no engine.
 */
import { EGRESS_BEAT_S } from "../../contract/src/egress";
import { EGRESS_DEADLINE_S } from "../../contract/src/limits";

/** The UTC day and month of `now` (seconds). */
export function periods(now: number): { day: string; month: string } {
  const day = new Date(now * 1000).toISOString().slice(0, 10);
  return { day, month: day.slice(0, 7) };
}

/**
 * The longest a charge stays open, in seconds: a sandbox call's door holds its stream at most
 * its deadline, and the object settles a row still open a beat past that `lost`, its charge
 * `unknown`, which moves no counter (`egress.ts`).
 */
export const SETTLED_WITHIN_S = EGRESS_DEADLINE_S + EGRESS_BEAT_S;

/**
 * The oldest day and month whose spend counters are kept at `now`. The ceilings read the
 * current day and month alone, and a charge settles into the periods it was reserved in, so
 * a period is kept until every charge reserved in it has settled: the settle moves its own
 * counter, never one made anew. Nothing reads an older one.
 */
export function keptFrom(now: number): { day: string; month: string } {
  return periods(now - SETTLED_WITHIN_S);
}

/** µ$ of a cost pi-ai reports in dollars. */
export function costMicros(dollars: number): number {
  return Number.isFinite(dollars) && dollars > 0 ? Math.ceil(dollars * 1_000_000) : 0;
}

/**
 * The largest `n` in `[0, most]` that `fits`, where `fits` holds from 0 up to some point and
 * not past it; -1 when it holds for none. By bisection: about log2(most) calls of `fits`.
 */
export function largestFitting(most: number, fits: (n: number) => boolean): number {
  if (most < 0 || !fits(0)) {
    return -1;
  }
  let low = 0;
  let high = most;
  while (low < high) {
    const middle = low + Math.ceil((high - low) / 2);
    if (fits(middle)) {
      low = middle;
    } else {
      high = middle - 1;
    }
  }
  return low;
}
