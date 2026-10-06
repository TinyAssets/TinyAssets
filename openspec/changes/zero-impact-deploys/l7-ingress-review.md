I've read the whole slice. I found no floor or correctness defect; here's the reasoning.

**Cross-user and mutation guards**
- **Principal comes from the token, not the caller.** `tinyassets/ingress.py:86-87` builds the scope from `current_actor_id()`, which has no environment fallback (`tinyassets/api/permissions.py:250`). `graph_id` is the only routing value the caller supplies.
- **The caller's center is checked again under a lock.** `authority()` (`tinyassets/ingress.py:47-55`) needs a non-empty actor equal to `scope.principal_id`. It then opens `cr.authorized_scope`, which requires the current home, owner `admin` in the ACL, and a canonical home directory, all under `BEGIN IMMEDIATE`. That lock is held for the whole journal operation. Finally the full `Scope` must equal `(owner, universe, "principal:{owner}")`. So a `graph_id` that is foreign, uses `../`, or isn't the current home is refused before anything is written.
- **Receipts are scoped to the caller.** `journal.receipt(scope, key)` looks rows up by principal and center, so one user can't read another user's receipt by guessing a send id. Accept, receipt and revocation are all tested (`tests/test_ingress_http.py:64-84`).
- **Mismatched retries are refused.** Same key with a different body returns 409, because the digest check runs inside the authority hold. The admission policy runs only on the first insert. A failed commit returns 503 with no row written, and the test proves that.
- **Bad input never reaches the journal.** Validation rejects duplicate keys, NaN, extra top-level keys, extra or forged arguments (`principal_id`, `model_choice`), non-canonical UUIDs, a non-main agent or non-typed input, and empty messages. A bool `id` is also rejected, because the check is on the exact type.

**Real factory integration**
- **Legacy MCP is unchanged.** `AppIngressMiddleware` sits inside `AuthContextMiddleware` and outside discovery and the transport (`tinyassets/universe_server.py:4843`). A request without the header passes through exactly as before.
- **No path gives a 202 without being enabled and authenticated.** An unknown mode or a wrong path or method gives 400. A missing adapter gives 503, never 202. An unauthenticated request still gets the existing linking tool error, which the test asserts (`tests/test_ingress_http.py:96-103`).
- **The identity reaches the worker thread.** Starlette's `run_in_threadpool` carries the request context into the thread. The positive 202 test can only pass if the bound actor arrives there, so it isn't a false green.
- **Production builds no adapter.** `ingress=None` is the default. The plugin runtime copy of `ingress.py` is identical, and the journal and storage modules it needs already exist there, so plugin startup won't fail on import.
- **The fixture overrides stay in tests.** The cloud-admission and token-issuer stand-ins live only in `tests/fixtures/deploy_ingress_process.py` and the test's `monkeypatch`. Lifespan is off, so no execution owner starts.

**Non-blocking notes (not defects)**
- `json.loads` on raw bytes also accepts UTF-16/32 bodies. The stored body is still the exact bytes, and replay decodes them the same way, so nothing is lost.
- If the second frontend fails to start in the Linux test, the `finally` block closes the old frontend a second time. Only the test's cleanup is affected.
- Execution and import are still fixture-only, long turns are still red, and the full Linux oracle run hasn't happened yet. You stated all of these, and they block enablement, not this disabled component.

I didn't run any tests. I relied on your Windows run (15 passed), so the full affected Linux oracle run is still needed before anyone calls this green. I found no lane collision: the diff only touches these ingress files, the factory signature and middleware wrap, and the traffic test and fixture.

VERDICT: APPROVE
