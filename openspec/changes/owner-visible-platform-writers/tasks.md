## 1. Writers

- [x] 1.1 Route both `config.py` config writers through `write_universe_file`
- [x] 1.2 Rename the branch-task and auto-ship locks to dotted platform names; auto-ship opens via `open_lock_file`
- [x] 1.3 Classify both names in `PLATFORM_LOCK_NAMES`; mirror runtime; update pins and tests

## 2. Verify

- [x] 2.1 Touched tests, ruff, Linux oracle, plugin build
- [ ] 2.2 Rerun `core_capability_image.py` unchanged against the rebuilt image
