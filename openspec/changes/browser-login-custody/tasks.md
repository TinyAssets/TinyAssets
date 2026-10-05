Implementation backlog only; dependent work starts after the prerequisites in design.md.

## 1. Delivery

- [ ] 1.1 Verify D5 substrate and MCP-lane lifecycle prerequisites before enabling browser custody.
- [ ] 1.2 Add typed browser metadata (origin, opaque session ref, revision, generation) preserving HTTP/MCP rows and rollback cleanup.
- [ ] 1.3 Implement protected login capture, origin/session/expiry binding and owner challenge takeover.
- [ ] 1.4 Suspend capture observation and expose only structured post-login actions; prove no evaluate/CDP/storage/profile/network credential reads.
- [ ] 1.5 Reuse coordinator for Stop, account switch, callback replay, recovery, revocation and durable session cleanup.
- [ ] 1.6 Connect an unknown login-only site via the existing card with no provider code; verify model-independent controls.
- [ ] 1.7 Run affected/heavy tests, Linux oracle, ruff and hygiene (0 removed / 0 tampering).
- [ ] 1.8 Assert deployed SHA, real-user connect/cancel/revoke proof and public canary; sync spec and delegations.
