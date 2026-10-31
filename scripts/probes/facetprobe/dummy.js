/**
 * ta-facetprobe-dummy: the dummy service the probe copies bind as `SVC` (facet-probe.md,
 * Q3), so a facet's `env` can show whether the parent's service binding reaches it.
 * Throwaway; no route, no workers.dev URL, no binding of its own.
 */
export default {
  fetch() {
    return new Response("ta-facetprobe-dummy");
  },
};
