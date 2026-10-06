## Context
Account already has memory rows and Undo. Soul is excluded from the agent file jail because it carries executable associations.
## Goals / Non-Goals
Expose three personal documents without expanding agent write authority or migrating existing seeds.
## Decisions
Use /app/soul GET/POST with a fixed three-file allowlist. Resolve only the authenticated owner's home; reject foreign selectors. POST requires the same protected cookie as rules. Record exact bytes in harness history and compare the loaded digest inside its transaction before saving. Permit soul.md only for owner history writes, never agent writes. Existing memory row writes also require the protected session. UI captures login epoch, owner and home; reset clears private drafts. Keep full file editors separate from per-line controls to preserve non-bullet text. Forget is performed by the existing agent file editor, with instructions in the on-demand systems chapter and no chat interception.
## Risks / Trade-offs
Raw Markdown preserves all content but owners can change executable soul declarations intentionally. Conflict checks refuse stale file saves. Broader sparse seed/migration changes belong to clean-agent-start.
## Verification
Linux focused server/history/security and fresh-home tests; real Chromium account edit/delete/reload; static prompt budgets; ruff; mirror; hygiene; Claude review.
