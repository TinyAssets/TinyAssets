# What a sandbox creation retry can establish

Reviewed the public provider implementation at revision
[86cb5033](https://github.com/e2b-dev/infra/tree/86cb50338fe3bd73c10b13270aaa576b48f5d582)
against the pending-create rehearsal in [start-faults.md](start-faults.md).

- [x] Supplying a key does not establish that replay was enabled for that
  request. Without the route setting, the handler runs the normal create.
  With it enabled, the fingerprint covers method, request URI and the
  normalized typed request. A different sealed value can change the match.
  Retention begins at reservation. An ordinary replay does not restart it.
  The source default for retention is one day. The deployed configuration
  has not been verified. [Middleware](https://github.com/e2b-dev/infra/blob/86cb50338fe3bd73c10b13270aaa576b48f5d582/packages/api/internal/middleware/idempotency/middleware.go),
  [executor](https://github.com/e2b-dev/infra/blob/86cb50338fe3bd73c10b13270aaa576b48f5d582/packages/api/internal/middleware/idempotency/executor.go)
  and [store](https://github.com/e2b-dev/infra/blob/86cb50338fe3bd73c10b13270aaa576b48f5d582/packages/api/internal/middleware/idempotency/store.go).
- [x] Placement precedes inclusion in the store used for listing. The
  provider describes its team listing as best effort. A recent create or
  pause can be missing, and the same ID can appear on more than one page.
  A successful empty scan therefore cannot authorize another allocation.
  A matching detail read corroborates that one object was visible. It
  does not establish that no other object exists. [Creation](https://github.com/e2b-dev/infra/blob/86cb50338fe3bd73c10b13270aaa576b48f5d582/packages/api/internal/orchestrator/create_instance.go),
  [listing behavior](https://github.com/e2b-dev/infra/blob/86cb50338fe3bd73c10b13270aaa576b48f5d582/packages/api/internal/orchestrator/team_list_snapshot.go)
  and [detail lookup](https://github.com/e2b-dev/infra/blob/86cb50338fe3bd73c10b13270aaa576b48f5d582/packages/api/internal/handlers/sandbox_get.go).
- [ ] Resolve an uncertain placement after interruption without releasing
  its place on an empty listing, duplicating the sandbox or indefinitely
  holding an unrelated start. Account for paused objects and the ordinary
  [shared-capacity regression](https://github.com/TinyAssets/TinyAssets/commit/267144dfe85ea29308f528e25e5063a8f0610b48).
  A read-only source review was performed here. Earlier Worker results
  are recorded separately in the existing decision.
