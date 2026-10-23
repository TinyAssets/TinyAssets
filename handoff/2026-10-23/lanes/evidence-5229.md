Lead approval of the N5 and N6 specs (spec only, no product code).

- Review 1 (cross-family, against owner-daemon-in-box T1-T10): DISAGREE_EVIDENCE, 8 findings (per-turn checkpoint cost, restore authority, undo losing state, waiting calls, shared turn pin, ask/allowlist breaking the agent, a second approval store, N5 decision record).
- Founder decisions 2026-10-23 recorded in both designs: N6 undo points are sleep snapshots plus owner-requested checkpoints; N5 stays with open as default, owner-connected destinations allowed, approvals become standing entries through the sole policy writer.
- Review 2 on the fix hunks: all 8 resolved; one new HIGH (box-committed bindings could satisfy allowlist/ask).
- Fix c88238ca207: only scope committed or widened through the protected N10 owner session counts; box commits and extend/full widenings grant nothing; committer recorded on every update; scenario and named test added. Checked by the lead against the finding.
- Validation: strict OpenSpec, targeted Linux oracle (35 passed), ruff.
