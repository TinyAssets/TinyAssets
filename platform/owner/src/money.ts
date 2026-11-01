/**
 * The arithmetic every model-spend reservation shares, the loop's (`usage.ts`) and a
 * sandbox's (`egress.ts`): the UTC periods its counters are kept by, micro-dollars, and the
 * bisection that finds the most output a cost room takes. Pure: no storage, no engine.
 */

/** The UTC day and month of `now` (seconds). */
export function periods(now: number): { day: string; month: string } {
  const day = new Date(now * 1000).toISOString().slice(0, 10);
  return { day, month: day.slice(0, 7) };
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
