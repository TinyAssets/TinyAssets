# Conventions

## Files

- Keep `AGENTS.md` and `docs/architecture.md` short enough to scan; `AGENTS.md` has a
  HARD budget enforced by the `context-budget` invariant.
- Push durable reasoning into `docs/design-notes/`.
- Push formal accepted decisions into `docs/decisions/`.
- Use `INDEX.md` files to keep note graphs connected.
- Use `docs/exec-plans/` for larger multi-step delivery maps.
- Use `ideas/` for stray ideas that are not yet ready for the work board.

## Naming

- Design notes: `YYYY-MM-DD-topic.md`
- ADRs: `ADR-###-topic.md`
- Specs: `feature-or-change-name.md`
- Execution plans: `YYYY-MM-DD-topic.md` or `topic.md`

## Linking

- Every new durable note should be linked from at least one index page.
- Prefer explicit Markdown links.
- Add sideways links between related notes, not just parent links.

## Verification Language

- `current:` means verified in the current environment.
- `historical:` means true when last checked, not revalidated now.
- `contradicted:` means current evidence says the older claim is no longer true.
- `unknown:` means not yet checked or not confidently reconstructable.

## Large Documents

- Use `python scripts/docview.py` before any raw whole-file read of large
  Markdown, text, or JSON artifacts.
- If `docview.py` says a result is too large, narrow the query again.

## MCP Actions Reference

- New MCP actions and field additions are catalogued in `docs/mcp-actions/`.
- When a chatbot encounters an unexpected response field or a new action verb,
  check `docs/mcp-actions/` before assuming a schema error.
- Index: `docs/mcp-actions/2026-04-25-session-additions.md`

## Frontmatter `status:` field

Reverse-engineered from the in-tree convention (Task #18 audit, 2026-04-28). Documents what the codebase already does so future audits don't re-litigate the format.

**Where it applies:**
- `docs/design-notes/*.md` — REQUIRED. 80/80 notes carry it.
- `docs/specs/*.md` — REQUIRED. 32/32 specs carry it (excludes `INDEX.md`).
- `docs/exec-plans/active/*.md` and `docs/exec-plans/completed/*.md` — present in some, encoded by directory placement.
- `docs/audits/*.md` — OPTIONAL, descriptive (not lifecycle). Audits are event-records; ~25% carry a bespoke `status:` describing the audit's role ("read-only discovery audit", "diagnostic record", etc.). Don't retrofit lifecycle values onto audits.

**Five lifecycle values** (use exactly one):

| Value | Meaning |
|---|---|
| `active` | Work is in flight or upcoming. No newer doc supersedes it. |
| `shipped` | Work landed. Body or git history references the landing commit. |
| `superseded` | Newer doc replaces this one. **Pair with `superseded_by:` field** holding the relative path to the successor. |
| `research` | Exploratory thinking; no implementation intent and no STATUS row claims it. |
| `historical` | Captures past state for posterity (incident postmortem, retired-with-stamp doc, pre-rename architecture). |

**Format:** bare value, body details optional (e.g. `status: shipped` plus a `**Status:**` line in the body referencing the landing commit). Compound forms like `status: shipped: <date>` are NOT the convention — date detail goes in the body.

**Tie-breakers** (applied during the 2026-04-28 audit pass):
- If `docs/host-actions.md` cites the doc as pending → `active`. (Consumption pattern trumps internal status.)
- If git log is silent and no STATUS row references it → `research`.
- "Almost shipped" with one open item → `active` with a body note explaining what's open.
- False-shipped is worse than false-active. When uncertain, classify `active`.

**Companion fields:**
- `superseded_by: <relative-path-from-repo-root>` — required when `status: superseded`. Path must resolve.
- `status_detail: <free-text>` — optional. Used by some notes to add nuance under a lifecycle value.

## Gate-branch shape

- Standalone gate branches (`gate_investigation_v1`, `gate_review_v1`, etc.)
  emit a uniform verdict shape so conditional-edge routing works generically
  and chatbots converge on one consumer contract.
- Verdict enum: `pass` / `send_back` / `reject` / `hold` / `rollback`.
- See `docs/conventions/gate-branch-shape.md` for the full contract,
  per-verdict required fields, worked example, and forward-compat plan
  with the unified Evaluator type.
