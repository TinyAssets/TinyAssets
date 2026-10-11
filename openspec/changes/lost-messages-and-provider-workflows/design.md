## Context

Read-only production evidence: the host journal retained both failures. Turn `438d481e6f3840208005aef3e935bd6c` began at 23:48:11Z and was reconciled at 00:09:10Z after container replacement; `bc81e0480ee848b2adb802991222ffb8` began at 00:45:48Z and was reconciled at 01:06:10Z. The old containers began shutdown at 00:08:35Z and 01:05:32Z respectively. Both remained `native_started` until recovery marked `held_native_unknown`. The conversation contains only recovery notices, because ingress writes the pair after the provider returns. Vector's journald sink survived deployment; these logs are not lost.



## Goals / Non-Goals

Preserve admitted owner messages before dispatch, announce and store each send once, deduplicate retries, and link terminal outcomes and interrupted-work evidence to the admitted message. Do not replay uncertain effects or change production.

## Decisions

- Commit a founder row at admitted ingress and associate terminal writes with that exact row. Failure to commit refuses dispatch. Retain the existing conversation schema where possible and additive identifiers where necessary; never deduplicate by message text. Serialize admission by nonempty client send ID within the authenticated conversation; reconnects acknowledge the stored message without redispatch or a second announcement.
- Store engine activity `turn_id` and `result_text` additively; retain rows instead of pruning the last 200. Model history uses its platform-minted live-turn identity to load only matching tool observations, with bounded excerpts before the original request. Cursor-based history pages contain durable rows only. Terminal rows link to the exact admitted founder turn via `ext_id`; successful replies suppress recovery evidence.
- Preserve the journal's effect uncertainty and supplement failed-turn history with durable tool/run evidence scoped to the same owner and turn. Never synthesize an assistant answer or claim unknown effects succeeded.

## Risks / Trade-offs

- A commit failure now blocks model dispatch, preventing irreversible unrecorded work.
- Native processes can die between external effect and result persistence; report uncertainty and exact known evidence without automatic replay.

## Migration Plan

Deploy through the ordinary image after tests and cross-family floor review. No production mutation in this lane. Existing transcripts remain readable; no historical user text is reconstructed from rendered prompts. Rollback keeps committed transcript rows.

## Review dispositions

- AGREE: large tool results could evict the failed request. Keep full observations durable, but select at most eight recent excerpts and bound the total model projection to about 2 KB before the request. The 10 KB-result regression covers both crash-only and terminal-notice history.
- AGREE: synthetic evidence has no row cursor and must not participate in page sizing. History pagination now uses durable rows only; the regression traverses two exact-size pages containing unfinished turns.
- AGREE: pre-dispatch admission allows another request inside a later terminal span. Return a contiguous learning span only when the admitted request is still adjacent; otherwise the existing cursor guard leaves learning owed. Both overlapping and ordinary completion are tested.
- AGREE (scope review): a failure during turn setup can still store the owner's message before ordinary admission. Announce that fallback write only when no admitted row exists; the injected setup-failure test proves one stored message and one announcement without dispatch.
- DISAGREE_EVIDENCE (scope review, raw evidence and retention): tool results are owner conversation evidence, not the public activity summary. `turn_evidence` selects the authenticated thread and platform-minted turn; `recent` does not expose result text. The injected-fault test excludes foreign-thread results. Account deletion removes the per-universe records (`account_deletion.py`), and model projections are bounded to eight observations/about 2 KB. Restoring destructive row retention or replacing observations with redacted excerpts would discard the failed-turn evidence this change preserves; storage-quota architecture is outside this message repair.
