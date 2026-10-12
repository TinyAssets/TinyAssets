---
severity: note
title: Peer review output loses its deliverable
filed: '2026-09-14'
summary: shared-ledger stop hook can replace a completed review with a recap; scoped recovery retains the review, but the hook still needs repair
---

# Peer review final stdout can omit the actual deliverable

Observed September14 2026 in the assigned Claude Fable5.1 model-setup review,
subprocess28078, exit0 after301seconds. output/model-setup-tools-shape-fable.md
held only a final recap referencing an earlier review. The complete deliverable
was an earlier assistant text at22:25:15UTC in the same scoped CLI transcript;
it has been recovered verbatim to 2026-09-14-model-setup-tools-shape-fable.md (archived in git).

The final recap responded to a continuation about unrelated dispatches, despite
the review brief explicitly forbidding dispatch or work on other sessions. The
peer wrapper persists final stdout, so a later recap is not evidence that the
review's findings/requirements were saved. This is a review-delivery defect,
not a negative verdict and not a reason to run another review blindly.

Follow-up: inspect the scoped stop-hook/wrapper boundary and preserve the review
deliverable across continuation, with a regression using multiple assistant
messages. Do not publish raw transcripts or unrelated session data. Until fixed,
verify the output contract; recover the exact assigned review text when the
terminal output refers to missing material. No wrapper/hook code changed here.
