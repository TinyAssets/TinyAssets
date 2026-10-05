---
severity: note
title: Windows provider-jail destination test mounts a Windows path
filed: '2026-10-05'
summary: test_a_provider_launch_view_masks_every_hidden_root_file fails on Windows only (C:\ path as jail mount destination); passes in the Linux oracle
---

# Windows provider-jail destination test uses a Windows path as a jail mount

Observed while verifying PR #4489 on 2026-10-05. tests/test_universe_tools.py::test_a_provider_launch_view_masks_every_hidden_root_file fails at provider_jail._validated_view because the generated mount destination is C:\\Users\\... rather than an absolute POSIX jail path. Reproduced after loading origin/main:tinyassets/universe_tools.py into the isolated pytest process. provider_jail.py and this test are unchanged by the chat lane. The same test passed in the Linux oracle. No skip or weakening added; Windows portability remains unresolved outside this lane.
