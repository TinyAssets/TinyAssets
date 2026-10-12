## 1. Implementation

- [x] 1.1 Add refusal diagnostics and explicit expired-link text.
- [x] 1.2 Implement bearer-bound single-use completion and app bootstrap.
- [x] 1.3 Prove both routing paths, refusal guards, approval and client handoffs.

## 2. Delivery

- [x] 2.1 Run affected Linux suites, app web tests, ruff, structural guards, plugin build and hygiene.
- [x] 2.2 Sync specs, commit explicit paths, push and open non-draft PR.
- [x] 2.3 Complete one cross-family floor/correctness review and record disposition.

Validation: 423 affected Linux cases, 34 follow-up Linux cases, 585 structural guards, Ruff, plugin build/import, invariants and hygiene passed. App sweep: 1,167 passed, three skipped, one Windows symlink-permission failure subsequently passing Linux. Affected heavy suites: 388 passed and 17 retired deploy-fence failures reproduced unchanged on base 29458f79aa (213 passed / same 17 failed). Worker code was not touched.

Claude review: APPROVE, no floor/correctness findings. AGREE with preserving refusal when no independent browser session exists; native owner callbacks remain in the system browser. PR #4569 is non-draft. No receipt, merge, deployment or live-user acceptance is claimed; the scope gate requires the receipt explicitly excluded by the requested scope.
