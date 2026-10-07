# HTTP inference lifecycle prerequisite

September 10, 2026. Existing selected-model integration still calls blocking
connection lookup, broker startup and proxy.request directly from async complete.
The broker proxy is also not closed. Both live in api_key_http_provider.py, shared
by legacy/open-selected calls. Fix before adding repeated HTTP tool inference.

## Bounded implementation

Keep existing wire encoding, endpoint/grant checks, response decoding, error
types and provider receipts unchanged in a synchronous private operation.
`complete` runs that entire operation in one executor Future with an explicitly
copied contextvars context and one owner. Do not transfer any router admission lock to the
thread; the existing router remains on its event loop and owns slot/reservation.

Shield the worker from outer task cancellation. If cancellation arrives, remember
it, drain that same worker (including repeated cancellation), then propagate
CancelledError. A late result or provider failure after cancellation must never
become a normal retry/fallback or a successful reply. Do not spawn a replacement.
The router therefore keeps the slot/reservation while network work is active and
uses its existing conservative cancellation settlement when the attempt settles.

This is NOT remote cancellation or an exactly-once delivery claim. A request may
have reached the provider even if its caller disconnects. No new inference or
tool authority, storage/API change, request allowance or fallback activation.
The underlying broker's per-operation network/startup controls are unchanged;
its IPC receive has no independent total deadline. A wedged broker can therefore
delay draining (existing limitation, not a claimed bounded total duration).
Adding an abort/deadline protocol is separate; never release a live worker merely
because an observation timeout expired.

When the executor creates a proxy, close it in finally after request returns or
raises, on that same worker thread. Borrowed proxy_override fixtures retain their
owner's lifecycle. Cleanup failure must not erase a received response, change a
429 into a retryable transport error, or expose raw exception details: emit a
fixed warning while preserving the original outcome. The actual proxy close
terminates/joins its own process if necessary; it is not a provider CLI reset.

Tests: unrelated asyncio task progresses during blocked HTTP; original response/
protocol behavior unchanged; exact once dispatch; cancellation and repeated
cancellation hold until success/error settles; no late exception retry; owned
proxy closes on success and all request errors, borrowed proxy not closed;
router slot remains held and cancellation settlement is conservative. Windows
and actual Linux Docker; one cross-family shape/basic-safety review.

Tool-loop work remains required: protocol tool calls/results, same canonical
MCP tools, bounded per-inference admission, durable completed/ambiguous effects,
and live app proof. This prerequisite does not enable text-only full-agent choice.

## Shape review disposition

Claude ADAPT210s: preserve pre-cleanup latency, keep cancellation dominant over
late worker failures, broad Exception cleanup guard, and do not uncancel. Applied.
Author correction during implementation: use run_in_executor with copy_context
instead of a Task wrapping to_thread. asyncio.run shutdown cancels *all Tasks*,
including a shielded child directly, which could release the outer slot while
its underlying thread still runs. A private executor Future is not in that task
inventory. Add an actual asyncio.run teardown regression before landing.

## Implementation and isolated delivery

Claude implementation review APPROVE224s independently reproduced14 lifecycle
tests and inspected teardown/context propagation, late cancellation dominance,
proxy ownership and selected-authority router slot/reservation retention.
An owned _OpenProxy test fixture now implements close. Optional Python3.14
shield late-error logging remains an acknowledged nonblocking concern; outcome
and secret-free diagnostics are correct, and production3.11 lacks that callback.

The existing legacy executor has the same blocking/lifecycle defect, so the
bounded fix was extracted against origin/main b801ef11 into
codex/nonblocking-http-inference, exact commita15d608c22c0789bdddc348f3185eacfd2b7b52f,
PR3718. It excludes all selected-model/discovery/preferences work and adds
a legacy serving integration regression. That isolated tree passes133 Windows/
135 actual Docker Linux checks (3/1 platform/optional skips). Final exact-head
APPROVE141s and all required CI checks passed. Normal merge at01:28:43UTC,
releaseb9d642646c8d0918c36bb640c470b34123824e15. Build34425633001 and deploy34425981123
succeeded, authenticated SHA/canary verified01:35UTC. As-built lifecycle spec and
full deployment proof are in docs-only PR3722, now merged as9648a0022f4405063ce4e655792d17028a30f2f6
after all required checks passed (required-tests completed01:59:14UTC). No
selector specification is represented as shipped.

Feature integration run September10,01:12 UTC:
`python -m pytest -q tests/test_http_inference_lifecycle.py tests/test_api_key_http_provider.py tests/test_selected_model_authority.py tests/test_provider_served_router.py tests/test_provider_admission.py tests/test_run_provider_session.py tests/test_mirror_parity_gate.py --tb=short -rs`
189 passed,3 platform/optional skips,17.94s on Windows3.14. Actual Docker Linux
run63925 terminated191pass1optional-real-Codex-skip,12.62s (Python3.11.16,
Git2.47.3,bubblewrap0.12.0). No inferred green from missing earlier output.
407 plugin mirrors/import probe passed. Earlier missing broad test output was
not inferred green; this fresh run is the evidence.

Isolated release exact-head review APPROVE141s,15 independent lifecycle tests:
https://github.com/Jonnyton/TinyAssets/pull/3718#issuecomment-5611165147
No selected-model activation in that release. Exact checklist message sent and
verified18:35PDT; app18:38 reports five fresh passes and deleted-webhook404.
Refreshed history revealed the original checklist already completed16:38PDT,
webhook run1fb7cf62e27044ef (200delivery/204cleanup). It now requests repeatable
UUID receiver paths on the existing key; operator opened/read, never accepted.
Those ordinary workflow checks do not prove HTTP cancellation, full HTTP agent
tools, automatic model selection or organic post-fix user use. These remain open.

## Next tool-loop integration seams (inspected, not implemented)

- engine_mcp_server.py already exposes the canonical tools pinned to one owner
  and universe. Do not import it and mutate its module-global environment binding
  between concurrent HTTP turns; use a correctly pinned client transport.
- engine_mcp_http.py supervises loopback per-universe servers. Feature644d6d74
  now adds the shared versioned owner-bound route reader, independently approved;
  isolated draft PR3728 awaits exact-head review/CI. See engine-tool-route.md.
  This does not approve borrowing a route as fresh authority or add the HTTP client.
- served_tools.py owns the existing canonical tool inventory. No new public
  handle or parallel private dispatcher is needed for HTTP agent calls.
- providers/call.py returns response.text; _call_writer in universe_intelligence.py
  consumes that bridge. A structured inference/tool-result loop needs an explicit
  seam, not tool calls stuffed into text or replaying the whole founder request.
- The launch allowance registry is in auth/middleware.py. Each inference must
  pass admission/accounting; an unmetered inner loop would bypass the current
  finite request allowance. Completed/ambiguous tool effects need durable recovery.
- protocol_encoders.py currently handles plain text only. The OpenRouter
  discovery contract also rejects tool-shaped bodies; extend both deliberately
  with protocol tests and authority/cost review, not a relaxed unknown-fields path.

These are source-grounded prerequisites, not new public APIs or a completion
claim. The owner-visible picker must not offer HTTP full-agent operation until
the real tool loop and safe continuation work through the live app.
