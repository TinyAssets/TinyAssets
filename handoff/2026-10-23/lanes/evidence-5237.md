Approval of one durable send (C11, registry S24). Data-loss class; three review rounds.
- Review 1 (cross-family): DISAGREE_EVIDENCE, 10 findings incl. two HIGH (stored message never run after restart; deleted module still imported by the events endpoint).
- Fix 08917b8c0bb; re-review (xhigh): all 10 resolved; two new MEDIUM (drain/stop turned queued sends into failures; unprojected pre-cutover consumer runs could never reach the transcript).
- Final fix c8860ede6e1, checked by the lead (third round, no further review per AGENTS.md loop 7): each queued row takes its own drain admission and checks stop before any dispatch state change, so drain leaves it pending and startup resume runs it once; legacy consumer runs project once through the existing box-local writer.
- Validation: failing-first tests each round; final 38 targeted Linux oracle tests; ruff, node --check.
