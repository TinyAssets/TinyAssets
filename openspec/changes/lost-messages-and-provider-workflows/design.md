## Context

Read-only production evidence: the host journal retained both failures. Turn `438d481e6f3840208005aef3e935bd6c` began at 23:48:11Z and was reconciled at 00:09:10Z after container replacement; `bc81e0480ee848b2adb802991222ffb8` began at 00:45:48Z and was reconciled at 01:06:10Z. The old containers began shutdown at 00:08:35Z and 01:05:32Z respectively. Both remained `native_started` until recovery marked `held_native_unknown`. The conversation contains only recovery notices, because ingress writes the pair after the provider returns. Vector's journald sink survived deployment; these logs are not lost.


Codex's retained stderr named both `/tmp/provider-auth` helper refusal and the attempted nested Linux sandbox. Production-image execution also reproduced a relay child retaining stderr after `codex exec` exited.

## Goals / Non-Goals

Preserve admitted messages before any dispatch, expose evidence of interrupted work, and start Codex correctly inside owner cells. Workflow HTTP accounting is deferred to the separate architecture review. Do not replay uncertain effects, change production, or modify PR #4602 or owner work.

## Decisions

- Commit a founder row at admitted ingress and associate terminal writes with that exact row. Failure to commit refuses dispatch. Retain the existing conversation schema where possible and additive identifiers where necessary; never deduplicate by message text. Serialize admission by nonempty client send ID within the authenticated conversation; reconnects acknowledge the stored message without redispatch or a second announcement.
- Store engine activity `turn_id` and `result_text` additively; retain rows instead of pruning the last 200. Model history uses its platform-minted live-turn identity to load only matching tool observations, with bounded excerpts before the original request. Cursor-based history pages contain durable rows only. Terminal rows link to the exact admitted founder turn via `ext_id`; successful replies suppress recovery evidence.
- Preserve the journal's effect uncertainty and supplement failed-turn history with durable tool/run evidence scoped to the same owner and turn. Never synthesize an assistant answer or claim unknown effects succeeded.
- Mount a private provider home outside `/tmp` under the cell's owner identity. All CLI home directories use that home; credential snapshots remain sealed inputs, copied only inside the cell. Keep the OS cell boundary rather than permitting nested sandboxes.
- Disable Codex's inner sandbox at app-server startup while retaining the cell-deny OS boundary. Tie egress relay death to its parent CLI so one-shot completion cannot hang on an inherited stderr pipe.

## Risks / Trade-offs

- A commit failure now blocks model dispatch, preventing irreversible unrecorded work.
- Native processes can die between external effect and result persistence; report uncertainty and exact known evidence without automatic replay.
- CLI startup must be tested against the shipped binaries; synthetic Python launch success is insufficient.

## Migration Plan

Deploy through the ordinary image after tests and cross-family floor review. No production mutation in this lane. Existing transcripts remain readable; no historical user text is reconstructed from rendered prompts. Rollback keeps committed transcript rows.

## Review dispositions

- AGREE (fix round): moving the CLI home outside `/tmp` must preserve a finite tmpfs capacity. `/provider-home` is now explicitly capped at 256 MiB; the real owner-cell probe checks the mounted filesystem capacity for execution and discovery under both owners. This prevents CLI home growth from exhausting shared container memory.
- AGREE: large tool results could evict the failed request. Keep full observations durable, but select at most eight recent excerpts and bound the total model projection to about 2 KB before the request. The 10 KB-result regression covers both crash-only and terminal-notice history.
- AGREE: synthetic evidence has no row cursor and must not participate in page sizing. History pagination now uses durable rows only; the regression traverses two exact-size pages containing unfinished turns.
- AGREE: pre-dispatch admission allows another request inside a later terminal span. Return a contiguous learning span only when the admitted request is still adjacent; otherwise the existing cursor guard leaves learning owed. Both overlapping and ordinary completion are tested.
