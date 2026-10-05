---
severity: P2
title: The 'out of cloud storage' warning fires on every command because platform runtime files are counted
filed: '2026-10-04'
summary: the founder's folder holds 36-37 MB, but status counts about 112 MB of Claude runtime files against the account and warns on every command; #4442's storage change was comment-only
---

# The 'out of cloud storage' warning fires on every command because platform runtime files are counted

The out-of-storage warning appears on every command. The agent reports its own folder at 36 MB (5:27 PM; 37 MB at 3:06 PM), while the status report counts about 112 MB of platform runtime files against the account (3:06 PM: "Claude's own runtime files"). Separately, #4442's round-3 review found its storage-accounting change was comment-only, so it did not change what is counted. Related to the storage incident in 2026-10-04-founder-command-center-bug-proof.md. Decide which runtime artifacts are platform bytes (`tinyassets/storage_accounting.py` _NOT_USER_BYTES) without letting users hide their own data.

**Source:** the founder's command-center agent, in the founder's app thread on 2026-10-04: its 3:06 PM PDT status report and its 5:27 PM PDT live re-test (plus its 5:58 PM and 6:25 PM replies where noted). Its run IDs are in its notes/command-center-playbook.md. Re-verify against current main before fixing.
