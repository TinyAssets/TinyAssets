Implementation backlog only. Card ownership belongs to inline-connect-and-approve; browser and saved extensions have separate changes.

## 1. MCP attach, secret entry and OAuth

- [ ] 1.1 Verify generic OAuth, streaming broker and existing request/card prerequisites; add typed MCP metadata preserving HTTP rows and rollback cleanup.
- [ ] 1.2 Add MCP OAuth metadata discovery, PKCE/resource binding and DCR/CIMD/static registration without per-platform code; retain the optional provider directory.
- [ ] 1.3 Consume streaming broker for initialization, paginated tools, streaming calls and safe renewal; verify cross-chunk scanning, cancellation and uncertain-call non-replay.
- [ ] 1.4 Implement exact-revision jailed stdio and scoped proxy auth; support warned named-own-key opt-in in a separate process/user/filesystem sandbox, with grant/revision isolation and no host fallback. Verify agent/other-author code cannot read server /proc, environment, arguments or files; reuse broker scanning on stdout/stderr and prove split-chunk key bytes never reach model context, logs or transcript. Verify the warning names only that server's code and author as key readers.
- [ ] 1.5 Implement protected secret entry and allowlisted egress slots with owner/incarnation checks and credential-free output/export.
- [ ] 1.6 Wire ta catalog/dispatch and existing inline coordinator; verify stale catalogs, Stop, account switching, callback replay and crash recovery.
- [ ] 1.7 Fence revocation before durable cleanup; preserve independent backing HTTP connections and prevent old-incarnation reuse.
- [ ] 1.8 Connect an unknown MCP server through the existing card with multiple accounts and model-independent controls; prove owner-classified and unknown-effect editable defaults.
- [ ] 1.9 Run affected/heavy tests, Linux oracle, ruff and hygiene (0 removed / 0 tampering).
- [ ] 1.10 Assert deployed SHA, public canary and real-user MCP connect/cancel/revoke pass; sync capability and delegations.
