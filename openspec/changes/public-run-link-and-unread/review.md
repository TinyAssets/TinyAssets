# L8 cross-family review

2026-10-05. Claude (`fable`) via the `peer-agents` skill reviewed draft PR #4507 in one read-only round. Original verdict: **ADAPT**. Scope: floor and correctness only. The reviewer checked the actual merged worktree and excluded upstream-only changes from the review diff.

## Dispositions

1. **AGREE — account deletion after a home change.** Schema inference alone would classify `owner_view_receipts` by universe and leave former-home receipts behind. Added the table to `OWNER_ONLY_TABLES` with `owner_user_id`. Regression tests delete an account after rebinding or removing its home and assert that all its receipts disappear while another owner's receipt remains.
2. **AGREE — preview origin.** The absolute production image URL was incompatible with the page's self-only image CSP on alternate hosts. The image source is now relative; the share URL remains the canonical public URL. The route test asserts both properties.

The reviewer found no other floor or correctness findings and no conflicting lane. Confirmed authority traces: public metadata comes only from immutable commons definitions; route exemptions are exact; exact-ID installs still use package/system validation and pinned approval; unread routes require the current founder home, bearer identity and same-origin POST; read receipts are not consent.

## Additional verification

The lead also used the canonical conversation-session parser to reject malformed legacy keys, prevented acknowledgements behind dialogs, consumed the Run URL once, and matched unread ask states to the inbox projection. These have focused regression coverage. `origin/main` was merged cleanly before the final push, including #4503/#4504; merged install/consent/settings and prompt-cost checks passed (334 tests). Focused browser/read-state checks passed (90 tests). Earlier successful runs: 123 focused tests and 23 receipt/public/prompt-cost tests.

Final deletion/public-preview regressions passed: 67 tests on the Linux oracle. The lead's head-bound approval receipt is recorded on PR #4507 after both findings were corrected. No always-sent prompt or static budget changed. Production deployment assertion and live real-user proof remain pending merge/deployment; automated browser tests are not presented as live proof.
