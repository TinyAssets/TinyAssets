# Verification (2026-10-05)

## Final checks

- Linux oracle final selection: **28 passed, zero skips**. Covers renderer units, exact-byte owner download and other-user refusal, traversal and symlink refusal, all six real Chromium tests under the app CSP, frontend storage independence, unchanged folder prompt-size cap, and path-I/O guards.
- Windows renderer and expanded-history selection: **25 passed**. Browser tests exercise actual bundled Mermaid, chart/fallback, links, private download, session changes, and expanded history. The native Browser handoff has a unit test; physical devices are not exercised.
- Windows frontend/path/module/prompt-budget selection: **24 passed, one symlink privilege skip**; that guard passed in Linux. Earlier owner/app/browser/session selection: 96 passed with platform skips. Broader Linux affected selection: 382 passed; its two integration failures were fixed and pass in the final focused run.
- Browser workflow and prompt-budget checks: **14 passed**. The three new trigger paths only widen proof coverage. Harness guidance is 1,263 characters, below the unchanged 1,263 cap.
- Ruff: all changed Python files clean. Plugin build and import probe pass; parity all 606 tracked canonical files matched. Commit hook parity checks every changed canonical file.
- Test hygiene: **14 added, zero removed, zero tampering findings**. No test removals, weakening, or request-answer edits.
- OpenSpec validates and main chat-reply-content spec is synced. Draft PR #4489.

## Review and scope

One cross-family implementation round: Claude found no floor issues and one expanded-history correctness gap. AGREE: expansion now uses ChatRender and a browser regression proves the fix. The first review of the subsequently added three-path CI gate scope returned APPROVE. See review.md.

CI integration findings were fixed without changing assertions or budgets: the route inventory includes the authenticated file route; packaged script loading uses app_modules without consulting owner storage; guidance fits its existing budget; the marked browser test retriggers proof.

Two unrelated local Windows failures reproduce with origin/main universe_tools source: the provider-jail mount destination test and the engine-tool-description budget. Both pass on Linux. Details remain in docs/concerns/windows-provider-jail-destination-test.md and docs/concerns/windows-tool-description-budget.md. No skip or budget relaxation was added.

## Remaining

Not deployed: deployment SHA assertion, real-user production pass, and native installer/device rollout remain. Existing owner-edited AGENTS.md files are preserved; new starter agents receive visual guidance and served tool guidance advertises file delivery to existing agents. Files are read at click time, subject to the existing 8 MiB folder-read bound.

Final prompt wording preserves the existing batching/direct-install contract verbatim; the bounded handoff ran both contract and size tests: 2 passed.
