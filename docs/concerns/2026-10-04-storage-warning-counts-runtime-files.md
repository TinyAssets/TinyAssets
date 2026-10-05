---
severity: P1
title: The 'out of cloud storage' warning fires on every command because platform runtime files are counted
filed: '2026-10-04'
summary: the founder's folder holds 36-37 MB, but status counts about 112 MB of Claude runtime files against the account and warns on every command; #4442's storage change was comment-only
---

# The 'out of cloud storage' warning fires on every command because platform runtime files are counted

The out-of-storage warning appears on every command. The agent's own folder is 36-37 MB, while the status report counts about 112 MB of the provider runtime's own files (Claude's runtime) as user bytes. #4442's storage-accounting change was comments only, per its round-3 review, so it did not change what is counted. Decide which runtime artifacts are platform bytes (`tinyassets/storage_accounting.py` _NOT_USER_BYTES) without letting users hide their own data.

**Source:** the founder's command-center agent re-tested its known issues on 2026-10-05 at 00:27Z (5:27 PM PDT), with live run IDs in its notes/command-center-playbook.md. Main at that time was 51db6894f3 (#4458 not yet merged). Re-verify against current main before fixing.
