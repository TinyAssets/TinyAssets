# Tasks: run-required-tests-in-linux-oracle

Tasks 1-4 are local preparation and are done. Tasks 5-6 need a new explicit
authorization: the one-off trial #4398 excluded this permanent migration.

- [x] 1. Oracle image: `browser` extra and Chromium at `/opt/playwright`, readable by uid 1001 (`docker/linux-oracle.Dockerfile`).
- [x] 2. `scripts/linux_oracle.py --required-runner`: fixed script, arguments unchanged, short basetemp, refuses weaker venues and unseen outputs; default pytest command unchanged. Tests assert the generated argv and output paths.
- [x] 3. `real-browser-proof`: marked cases and the full `tests/test_ui_preview.py` module in the oracle, with no-skip assertions and both junit artifacts; ordinary triggers kept.
- [x] 4. `required-tests-shard`: both paths through `--required-runner`; plan step, matrix, artifacts and aggregate unchanged. Workflow tests pin the venue, the argv and the absence of host privilege.
- [ ] 5. After authorization: push, then observe one `real-browser-proof` run, one whole-surface six-shard run and one selective merge-group run (the five items in `design.md` D5). Fix or quarantine through the existing ledger anything the gate executes for the first time and finds red; do not raise the skip budget.
- [ ] 6. After the runs are green: independent cross-family review (gate file), then sync this spec and archive.
