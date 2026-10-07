# Tasks — proactive-brain-persistence

## 1. Prompt behavior
- [x] 1.1 Founder-tier reply prompt gains a "How I remember" section: proactively
      `write_brain` durable founder-taught facts (identity/founder/origin/body),
      never ask permission, never invent (honesty floor governs).
- [x] 1.2 Open-question curiosity: on the founder tier, record the answer with
      `write_brain` when taught, so the agent stops re-asking.
- [x] 1.3 Gate both to `tier == interlocutor.FOUNDER`; a visitor is never shown
      the brain-write mechanics. Mirror into the packaging runtime.

## 2. Proof
- [x] 2.1 `test_founder_prompt_instructs_proactive_brain_persistence`: founder
      prompt contains the write_brain/"How I remember"/"ask permission"
      instruction; a lower-tier visitor prompt does not.
- [x] 2.2 Full `test_universe_intelligence.py` + persona + interlocutor suites
      green (85 passed), ruff clean.

## 3. Review + rollout
- [ ] 3.1 Opposite-provider (Codex) review returns `approve`/`adapt`.
- [ ] 3.2 Merge + deploy; confirm prod `release_state.git_sha` contains it.
- [ ] 3.3 Live proof: teach the founder universe a fact (repo/org chart) through a
      surface; confirm it is written to the brain and recalled next turn WITHOUT
      being re-asked or asking permission.

## Follow-ups (prose)
- Make `orgchart.md` writable (add to `write_brain` sections + the universe's
  `soul.edit.md` governed list baseline + an existing-universe migration) so the
  `orgchart` open-question can clear.
- Conversation file-upload (the founder pasted a long doc because upload failed).
