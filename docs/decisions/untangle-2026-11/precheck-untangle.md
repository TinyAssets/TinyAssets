# precheck-untangle as built (2026-11-21)

1. **Builders run the required scope check before they push.** Two PRs in one hour (#5723, #5726) went red on the required "Diff scope declared" check, because an owed `- [ ]` line in a body or decision file named no PR, branch or `live:` wait. The check is `review_checks.py --untangle-check`, which any agent can run locally, so the untangle skill now says to run it on the body before each push.
2. **A body edit goes on the open PR.** #5707's recorder shards went red because its body file declared `Recorders-Differ:` while the live PR body did not; CI reads the live body. The same clause says so.
3. Both are one clause in the skill's PR-body bullet, in both copies (34,402 of 34,467 bytes).
