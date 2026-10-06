## Decisions
Add runner_token and settled_reason columns; abandonment records why it closed. The runner_token column with an empty legacy default. Before committing a new turn, hold a unique OS liveness lock for that task. Release it on every coordinator exit; forked children close inherited descriptors. The kernel releases it on process death. Status requires positive runner liveness as well as owner generation. Unknown nonempty claims fail closed for settlement; empty legacy claims are recoverable. Existing owner lease and current-home checks fence every mutation.

Boot and owner/agent-scoped Stop settle orphan rows using existing uncertainty-preserving transitions. No effects are retried. A zero-round failure is abandoned; the writer's safe all-skipped retry starts a new row. Failure notices are persisted in chat and activity events feed the next agent turn. Native activity tool fencing remains mandatory; unsupported starts must return an explicit refusal.

## Verification
Linux regression including cross-process locks, scoped Stop, boot recovery, activity failure, browser Stop/queue delivery; affected suites, ruff, mirror, hygiene and Claude review.
