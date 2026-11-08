/**
 * The owner's clock: now, in seconds since the epoch, as every owner row keeps its times and
 * the schedule compares them. One function, so every module reads the same time the same way.
 */
export function clock(): number {
  return Date.now() / 1000;
}
