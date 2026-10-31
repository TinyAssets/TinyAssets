/** Helpers parent.js, door.js and drive.mjs share (facet-probe.md); config.mjs copies it for the bundles. */

export function errText(error) {
  const text = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  return text.slice(0, 500);
}

/** Order statistics of `samples` (ms). */
export function stats(samples) {
  const n = samples.length;
  if (n === 0) {
    return { n: 0 };
  }
  const sorted = Float64Array.from(samples).sort();
  const at = (q) => sorted[Math.min(n - 1, Math.ceil(q * n) - 1)];
  const round = (x) => Math.round(x * 1000) / 1000;
  let sum = 0;
  for (const x of sorted) {
    sum += x;
  }
  return { n, min: round(sorted[0]), p50: round(at(0.5)), p95: round(at(0.95)), p99: round(at(0.99)),
           max: round(sorted[n - 1]), mean: round(sum / n) };
}
