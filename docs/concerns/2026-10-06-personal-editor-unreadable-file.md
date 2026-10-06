---
severity: P3
title: One unreadable personal file blocks loading all personal editors
filed: '2026-10-06'
summary: The bounded personal-file listing fails as a unit when one file exceeds 256 KiB or is not UTF-8, so the owner's other two editors cannot load.
---

Claude review of PR #4505 identified this account-local limitation in
`tinyassets/onboarding/soul.py:listing`. It fails closed without disclosing or
overwriting bytes. The owner can still repair memory through their agent's file
tools. A future change can return per-file errors and keep readable documents
editable, with browser coverage and no weakening of link refusal or size bounds.
