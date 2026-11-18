# Off-account activation and retirement review as built (2026-11-18)

The staged partial-copy fix recognizes a visible failed upload. Its stand-alone tree at
`40e09e71` and the combined tree at `5db0a6738` still infer the lifetime of a backup
destination from completed GitHub jobs. The nightly job validates today's configured
bucket. The rotation gate has to account for every destination that might still need the
old key. A current object in a disconnected destination can survive indefinitely. R4's
lifecycle applies its expiry after a hide or replacement.

| Replayed history | Result through the real watch, firstNightAfter and retirement functions |
| --- | --- |
| Earlier failed attempt, latest rerun skipped | The gate gives the operator the words to retire the key. |
| No visible runs, or a fixture returning 1,000 visible skips with a larger total | The gate gives the retirement words. An API refusal would instead stop the call. |
| Older visible failed or successful copy and no qualifying later night | The gate refuses. |
| Qualifying success inside its retention period | The gate refuses. |
| Forty days after a success with the latest copy skipped | The watch reports waiting. An earlier activation can therefore lose its overdue alarm. |
| Three successful jobs, first destination A then destination B | The gate allows retirement after the hold on B. The same job answers could describe three nights at A. Only in the latter history does the later copy replace the old root at A. |

Eleven synthetic cases exercised the standalone and integrated trees. The job responses
and two destination histories were fixtures. The check performed no uploads and made no
use of live keys, accounts or owner data. GitHub's run jobs request defaults to the latest
execution. Its filtered workflow-runs search also limits returned results. Asking for all
attempts would improve incident evidence while leaving the destination ambiguity.

R4 §2 permits an unused backup to remain dark. R8's `deploy:offsite:restore` record proves
when a copy back to R2 began. It lives in the source account, which the recovery design
allows to be lost. Neither it nor the rotation record identifies every possible upload
destination. The nightly write credential cannot serve as an inventory of former accounts.

**Open decision before Files key retirement and off-account erasure claims.** Establish
where an obligation for a specific destination is made durable before its first write,
how it survives loss of the source account, and what closes it when that destination is
changed or disabled. Review the canary, the operator's checklist and key retirement against
the same evidence. Keep the initially unused case quiet as R4 specifies. A gateway key
does not wrap these files.

Sources: `r4.md` §§2–3, `r8-retire-gate.md` §§2–4, `platform/deploy/offsite.ts`
(`held`, `nights`, `watch`, `firstNightAfter`), `retirement` in `platform/deploy/deploy.ts`,
and the copy and retire jobs. [GitHub workflow jobs](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run)
and [workflow runs](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-workflow).
