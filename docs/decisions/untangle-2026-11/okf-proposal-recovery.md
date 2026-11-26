# Recover an interrupted OKF proposal

On main `d662e63e`, publishing a branch and opening its draft are separate writes. If the second fails, the next check sees the branch and reports success. The old draft can already have been closed. This is an offline recovery finding. The existing draft #5692 is open.

- [x] Read the matching same-repository proposal history, including closed proposals, before making another draft. For a branch without a proposal, compare its tree and complete parent list with the newly generated commit. Stop on a difference. Create a new ref only if it is still absent.
- [x] Create the new draft before closing older watcher-owned proposals. Leave other authors, forks and newer proposals alone. If an existing draft still has older proposals needing cleanup, report them for review. Preserve a deliberate closure.
- [x] Run the actual shell step with isolated command responses and the actual jq filters. Twelve cases passed, including an interrupted create, a changed base, a changed tree, a lookup failure, an absent branch and a closed PR whose branch disappeared. The same suite on main had ten failures and two passes.
- [ ] Review with the process and starter lanes. Hosted workflow execution and release decisions belong to the coordinator. No external proposal or close operation was exercised by the tests.
