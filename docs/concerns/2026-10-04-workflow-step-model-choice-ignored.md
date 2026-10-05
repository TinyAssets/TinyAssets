---
severity: P1
title: A workflow step's chosen model is silently ignored
filed: '2026-10-04'
summary: a step set to ChatGPT/Codex runs on Claude Opus with no error, so one model family cannot review another's work inside a workflow
---

# A workflow step's chosen model is silently ignored

Setting one workflow step to a Codex (GPT) model ran that step on Claude Opus with no error (3:06 PM report), and a ChatGPT-selected step ran on Claude again in the 5:27 PM re-test. That defeats cross-family review inside workflows, which the founder wants. Fail loudly (Hard Rule 8) if the chosen model can't be used, rather than substituting one.

**Source:** the founder's command-center agent, in the founder's app thread on 2026-10-04: its 3:06 PM PDT status report and its 5:27 PM PDT live re-test (plus its 5:58 PM and 6:25 PM replies where noted). Its run IDs are in its notes/command-center-playbook.md. Re-verify against current main before fixing.
