# Proactive brain persistence for the served universe agent

## Why

Live founder conversation (2026-08-22): the universe *recited* facts the founder
taught it — its GitHub repo, an org chart it was asked to adopt — but never
**persisted** them, and it **asked permission** to remember ("Do you want me to
treat the repo name as durable memory?"). Its `self_model.open_questions`
(orgchart, origin) never cleared, so it kept re-asking things it had been told.
This is the opposite of the founder's vision that the universe "uses its brain
like a harness / project folder — read, write, and inject the change into the
next turn."

Root cause: the reply-turn system prompt (`_build_persona_system_prompt`) told
the universe to *ask* about open questions but never to *record answers* with
`write_brain`. The universe has `read_brain`/`write_brain` (it uses `read_brain`)
but was never instructed to persist proactively.

## What Changes

- The **founder-tier** reply prompt gains a "How I remember" section instructing
  the universe to durably `write_brain` the facts its founder teaches (identity,
  founder, origin, body) the moment it learns them — and to record answers to its
  open questions rather than only asking — never asking permission to remember,
  never inventing (the honesty floor still governs). Gated to the FOUNDER tier: a
  visitor is never shown the brain-write mechanics, and only founder turns persist
  (`write_brain` is founder-allowlisted).
- No new tool and no change to the governed write path: `write_brain` is already
  section-whitelisted, size-capped, atomic, markdown-only, and daemon-executed
  under founder authority. This change only tells the agent to USE it.
- Writing `founder.md`/`origin.md`/`identity.md`/`body.md` clears their
  `open_questions` automatically (existing `_is_learned` mechanism), so the agent
  stops re-asking answered questions.

## Impact

- Affected specs: `universe-intelligence` (served reply-turn persona behavior).
- Affected code: `tinyassets/universe_intelligence.py` + packaging mirror.
- Out of scope (follow-ups): `orgchart.md` is a `SEED_QUESTION` but is NOT in
  `write_brain`'s section whitelist, so the `orgchart` open-question cannot yet be
  cleared by the agent — that needs the section whitelist + each universe's
  `soul.edit.md` governed list + an existing-universe migration. The org-structure
  fact still persists into `founder.md`/`body.md` meanwhile. Also: conversation
  file-upload (the founder pasted a long doc because upload failed).
