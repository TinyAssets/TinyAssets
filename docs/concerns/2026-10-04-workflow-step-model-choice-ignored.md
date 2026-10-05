---
severity: P1
title: A workflow step's chosen model is silently ignored
filed: '2026-10-04'
summary: a step set to ChatGPT/Codex runs on Claude Opus with no error, so one model family cannot review another's work inside a workflow
---

# A workflow step's chosen model is silently ignored

Setting one workflow step to a Codex (GPT) model ran that step on Claude Opus, with no error or warning. This happened in two separate tests (10-04 afternoon and evening). That defeats cross-family review inside workflows, which the founder wants. Fail loudly (Hard Rule 8) if the chosen model can't be used, rather than substituting one.

**Source:** the founder's command-center agent re-tested its known issues on 2026-10-05 at 00:27Z (5:27 PM PDT), with live run IDs in its notes/command-center-playbook.md. Main at that time was 51db6894f3 (#4458 not yet merged). Re-verify against current main before fixing.
