## Design and coordination
- [x] Reproduce the phone failure hermetically and preserve the observational patch.
- [x] Inspect actual held #4308 fences and steering/history transaction semantics.
- [x] Write exact receipt, custody, projection and read contract before schema code.
- [ ] Obtain independent security/concurrency design review and resolve P1 findings.
- [ ] Obtain coordinated fenced baseline plus queue/reset/deletion ownership.
## Implementation and proof (blocked by design gate)
- [ ] Implement fenced receipt admission and internal journal linkage.
- [ ] Implement coordinated durable input custody and exact terminal/history projection.
- [ ] Add strictly read-only scoped receipt endpoint and exact client recovery.
- [ ] Prove concurrency/crash/isolation and consumer/legacy compatibility synthetically.
- [ ] Obtain exact-head implementation review and protected/browser CI.
- [ ] Hand off separate draft patch for serialized integration and Android acceptance.
