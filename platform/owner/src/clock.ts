/**
 * The owner's clock: now, in seconds since the epoch, as every owner row keeps its times and
 * the schedule compares them. One module, so every module reads the same time the same way.
 */
export function clock(): number {
  return clockMs() / 1000;
}

/**
 * The same now in whole milliseconds, for what keeps or compares milliseconds: Lifecycle's job
 * times, a manifest root's `at`, a credential's lease, a pass's `until`. Never `clock() * 1000`,
 * which past 2³¹ seconds (2038) is often not the whole number it was.
 */
export function clockMs(): number {
  return Date.now();
}
