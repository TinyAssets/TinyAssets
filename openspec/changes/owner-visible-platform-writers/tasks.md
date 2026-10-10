## 1. Writers

- [x] 1.1 Route both `config.py` config writers through `write_universe_file`
- [x] 1.2 Rename the branch-task and auto-ship locks to dotted platform names; auto-ship opens via `open_lock_file`
- [x] 1.3 Dot and link-free-open the subscription and bid execution-log locks too
- [x] 1.4 Classify all four names in `PLATFORM_LOCK_NAMES`; mirror runtime; update pins and tests

## 2. Verify

- [x] 2.1 Touched tests, ruff, Linux oracle, plugin build
- [x] 2.2 Rerun `core_capability_image.py` unchanged against the rebuilt image
- [ ] 2.3 After deploy, in a backed-up window, rerun the runbook migration (`--check`, apply, `--check`) to relabel pre-fix daemon-owned visible entries

Cross-family review: Codex AGREE on owner custody and no-follow lock publication. The false claim about post-cutover entries was corrected in e09750aaba3; existing backed-up migration is the repair. Synthetic production-image acceptance at 1871d58cffc completed all 14 core capabilities, including normal config saves and queue/status reads, without fixture ownership normalization. The two subsequent lock renames passed the 247-test Linux oracle. Live repair remains task 2.3.
