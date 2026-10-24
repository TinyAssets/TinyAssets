Pushed to `spec/n1-inbound-door`, PR #5228.

Head: `1fe715ca0dd327015ba13454c803f9bbf312d70b`

Resolved every finding: opaque binding routing, broker/T1 exception, 503/404 behavior, legacy sender requirements, bounded receipts, owner-scoped credentials, pinned keys, and concrete test assertions/paths.

Passed: strict OpenSpec validation, Linux oracle shape-registry tests (21), ruff, diff checks and commit hooks.

Implementation and live proof remain intentionally unchecked: this was spec-only. Working tree clean.