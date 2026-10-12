# Served node edit parity evidence

September 23, 2026 UTC. Base runtime `baee7c79`; proposal commit `0289ee15`.
Local Windows development only, no daemon or infrastructure processes started.

- `openspec validate served-node-edit-parity --strict`: PASS.
- `python scripts/openspec_flow.py check-change served-node-edit-parity --provider codex`: ALLOWED; no claimed provider WIP. Completed predecessor slices remain closed; externally blocked cloud/free-user acceptance remains open.
- `python -m pytest -q tests/test_served_node_edit_parity.py`: three expected assertion failures in 4.78s, no skips. Real served adapter refuses output_keys and timeout_seconds before canonical staging; timeout checked both alone and with workspace binding. Tests store/read temporary synthetic branches, never user workflows.
- Independent Claude Opus pre-build shape review completed211s/exit0, ADAPT against base plus0289ee15. Seven ordinary fields accepted; enabled/retry excluded because graph runtime does not consume them. Required finite/positive canonical timeout validation, resulting workspace/timeout recheck and description/phase text checks. Design/spec adapted before code. Version-pinned snapshot preservation is the actual guarantee; no new immutable guarantee for unpinned runs. Preserve mixed-valid/invalid atomicity coverage when moving IO keys out of the legacy denial tests. Update stale served_tools comments too.
- Review source: current dispatch transcript8744275f-28de-4306-b782-233ba0ae110b (worktree-specific Claude session). Wrapper final output contains the addendum; lead recovered and read the preceding full substantive review, not only the addendum. No exact implementation approval yet.
- Baseline `python -m pytest -q tests/test_engine_mcp_write_graph_patch.py tests/test_served_effect_edit_parity.py tests/test_composite_branch_actions.py tests/test_node_reasoning_effort.py tests/test_run_input_admissions.py`:107 passed, no skips,15.46s on Windows. CI inclusion checked before implementation: only composite-branch-actions is in `.github/heavy-test-files.txt`; no selected module appears in the known-failure ledger. New parity module remains in the normal required selection. Heavy-only canonical tests still need explicit Linux evidence if canonical validation changes.

## Implementation recovery and independent lead review

Claude Opus builder first stopped across a session interruption without runtime
edits or result. Lead verified the handle missing, no matching live process and
no runtime diff before dispatching replacement. Replacement completed code and
tests but exceeded600s during extra discovery checks; wrapper killed its process
tree. Timeout is NOT approval or successful completion. Lead inspected the
recovered diff and current dispatch's test output, then independently reran the
required focused matrix. No further builder/review dispatch is needed for
housekeeping alone.

Implementation uses seven explicit fields and existing canonical staging.
Canonical timeout validation rejects nonfinite/nonpositive/bool/overflow input;
the resulting workspace/timeout pair is checked before persistence. Lead fixed
two inaccurate NaN comments: `not 0 < nan <= 1800` DOES reject NaN; the defect was
write/read validation timing, not a comparison bypass. No behavior change in
this comment correction. Branch versions remain unchanged after edits; tests
prove the actual version store, not a new guarantee for unpinned queued runs.

No local serving, WSL, Docker, account or private-workflow operation. No runtime
release/acceptance claim. Final exact-head evidence and CI remain pending.

Lead candidate checks September23~22:45UTC: the same five baseline modules plus
`tests/test_served_node_edit_parity.py` passed171/0skips in21.25s on Windows
(`python -m pytest -q` with those six paths). The new module contributes64 cases.
`python packaging/claude-plugin/build_plugin.py` import probe passed;
`python scripts/check_mirror_parity.py` matched all499 canonical files.
`python -m ruff check` on four changed modules and two tests reports exactly
three pre-existing E501 findings in api/branches.py. Verified against
`git show origin/main:tinyassets/api/branches.py | python -m ruff check --stdin-filename tinyassets/api/branches.py -`;
same source lines/messages at base, no new lint findings. Other changed files
are clean. `git diff --check` and strict change validation pass.

Canonical specification synchronized in the implementation patch to avoid a
separate specification-only release cycle. Change is NOT archived: hosted CI,
cloud deployment and rendered agent acceptance remain required.

## Accepted September23,2026 UTC

Required CI passed; merged1f121719 has the exact reviewed tree. Authenticated
public handles and protected SHA confirmed cloud deployment23:13UTC. Candidate
Linux artifacts prove all171 relevant tests passed with0skips. Original16:31PDT
app agent reply independently renamed/reran/read output and changed/read timeout,
then explicitly reported both issues fixed. No operator workflow edits. Exact
evidence, scope limits and review:
`2026-09-23-node-edit-parity-lead-review.md (archived in git)`.
Canonical specification shipped with runtime3924; archival changes no runtime.
Organic use and provider reliability remain separate, not false blanket closure.
