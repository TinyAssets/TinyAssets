# Delivery evidence — 2026-10-04

Scope: implement and push feat/first-run-model-connect, no PR or deployment.
Parent feat/inline-connect-and-approve merged through 46e943bd76 (including
the earlier 1e475b9f09 update). Merge commits preserve the stack.

## Behavior

- Deterministic unpowered converse returns generic provider-data connection card metadata.
- Bubble keeps the exact message and offers the installed primary provider or existing Other AI options.
- Popup/system-browser PKCE is sealed server-side; launch and callback require the matching protected owner session. System-browser login returns only to that owner's waiting flow.
- Free-model bootstrap uses the existing confirmation path. Server-confirmed readiness resumes the original once; account/home/agent fences and a browser lock protect continuation.
- Cancel/failure retains the message, releases cancelled flow slots, and offers retry. A consumed uncertain exchange is not replayed.
- Fixed the inherited phone header MutationObserver loop discovered by the real browser proof.

## Validation

490 distinct cases passed on Windows and 490 on the Python 3.11.16 Linux oracle,
with no skips. Counts exclude repeat executions during diagnosis and post-merge verification.

- Backend/SPA: 454 distinct cases (initial 417-case affected set plus 37 approval/storage cases after merging the parent).
- Chromium: 36 cases — 17 first-run cases, 15 existing send/resume cases, 4 existing inline approval cases. Includes 390px and desktop layouts, cancellation/retry, provider-driven labels, queued messages, owner changes, restored pending messages, late cancellation, and a simulated native Browser plugin handoff.
- Linux backend runs used the normal unprivileged oracle with bubblewrap. Browser runs used the same oracle's --as-root option and a temporary /out pytest bootstrap to install Playwright/Chromium in the disposable container. No sandbox tests were included in that root subset.
- New real-store tests cover owner/home isolation, copied launch links, callback browser binding, cancellation, once-only exchange, failed-exchange recovery, and repeated cancellation beyond the pending-flow limit.
- Cross-family review and AGREE/DISAGREE_EVIDENCE responses: openspec/changes/first-run-model-connect/review.md.
- Plugin mirror regenerated after parent merges; touched Python files pass ruff; diff whitespace checks pass; change and synced spec both pass strict OpenSpec validation.

The phone Connected/reply screenshot was visually inspected. No live provider
account or physical Android/iOS device was used. Native handoff is simulated in
Chromium. No production deployment, deployed-SHA assertion, real-user production
pass, reviewer key, or PR is claimed; this delivery is the requested branch push.
