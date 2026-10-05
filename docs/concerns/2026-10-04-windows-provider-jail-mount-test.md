---
severity: P2
title: Provider launch mask test rejects Windows mount syntax
filed: '2026-10-04'
summary: The unchanged provider launch mask test fails on Windows before checking masks because the jail validator requires a POSIX destination.
---

`tests/test_universe_tools.py::test_a_provider_launch_view_masks_every_hidden_root_file`
fails on Windows Python 3.14 in `provider_jail._validated_view`: the fixture's
`C:\Users\...\data\u-alpha` destination does not start with `/`.

Reproduced in L4 and in a separate, detached checkout of the unchanged base
`9e96ff9595` on 2026-10-04 (1 failed in 0.78s). Neither the test nor the jail
implementation was changed by L4. Hand off to the provider-jail test owner to
make the fixture portable while retaining the mask assertions; do not weaken
the production mount validator. The Linux oracle remains the real jail venue.
