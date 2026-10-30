/**
 * The fixed shape of each store-probe case (store-probe.md): the pre-spends and lengths
 * owner.js runs and drive.mjs's reading key is written against. One module, so the two
 * cannot drift; config.mjs copies it beside owner.js for the bundle.
 */
export const SHAPE = Object.freeze({
  /** P1a: calls in one DO RPC invocation, to one store (stub) or over one session. */
  p1aN: 150,
  /** P1b: the arming RPC spends j, the start alarm k, each heartbeat alarm h. */
  p1b: Object.freeze({ j: 8, k: 4, h: 4, perWindow: 45, windows: 4, burst: 150 }),
  /** P1c: one alarm's sequential calls, just past the owner's pinned 10,000. */
  p1cN: 10_100,
  /** P2: pings warmed first, new threads, threads streaming for commit samples. */
  p2: Object.freeze({ warm: 20, threads: 8, streams: 4 }),
  /** P7: calls per shape. */
  p7N: 200,
});
