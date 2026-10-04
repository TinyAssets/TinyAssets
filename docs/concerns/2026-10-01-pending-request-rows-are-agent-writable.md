---
severity: P1
title: Every ask except publish and install trusts a pending-request row the agent can rewrite
filed: '2026-10-01'
summary: 'The pending-request store (`.pending_requests.db`) lives inside the command-center folder, which the agent writes with `bash`. `displayed_row_matches` compares the row to its own dedupe key, which sits in the same row. An agent that rewrites the title, body, action and key together passes it, so the owner can be shown one thing while another executes. Publish and install now execute a platform-owned pin instead; every other action type still trusts the row.'
---

# Every ask except publish and install trusts an agent-writable row

**Filed:** 2026-10-01, by the cc-package lane (change `command-center-packages`).
**Verified:** the same day, on branch `feat/cc-package-publish`. The test
`tests/test_command_center_packages.py::test_a_rewritten_action_executes_the_pinned_one`
rewrites a row exactly this way through `sqlite3`. A publish executes the pin
regardless; the same rewrite on any other action type would execute.
**Severity:** P1. A forged consent means an owner's click grants something
other than what the tab said. The agent acts as the owner's own principal, so
the reach is the owner's own command center and grants, not another user's.

## Source (verbatim)

gpt-6-astra design refute of `command-center-packages`, round 1, finding 3:

> **P1 — DISAGREE_EVIDENCE: Pinning the payload digest does not authenticate the displayed consent.** [D4] protects the publish digest, but the existing rail renders rows from the writable pending store at [pending_requests.py:2025]. That database lives inside the universe at [storage/pending_requests.py:270]. Its display check compares against another writable field, and even accepts a missing dedupe key at [pending_requests.py:2822].
>
> An agent can leave a valid protected payload unchanged while replacing the displayed title/body with an innocuous confirmation. The owner then confirms something other than the disclosure the platform intended.
>
> **Fix:** Store the complete authoritative consent envelope outside the universe: action type, owner, universe, agent, payload digest, installation plan and lifecycle state. Render publish/install cards from that record and bind the submitted confirmation to its revision. The writable pending row must be only a reference.

## What is fixed, and what is not

- **Fixed for `publish` and `install`.** The consent record is a pin in
  `<data root>/.command-center-packages/packages.db`, keyed by (command center,
  request). `list_requests` renders those rows from the pin
  (`_rendered_from_pin`), and `answer_request` executes the pin's action. A
  row with no pin cannot be confirmed.
- **Not fixed:** `connect_http`, `connect`, `extend_http`, `rotate_http`,
  `remove_http`, `grant_workspace_consent`, `bind_model_access` and the patch
  intake still execute `row["action"]`.

## Fix direction

Generalise the pin. Move the consent record of every action-bearing ask out of
the folder, keeping the row as a reference only. The S3c move of platform state
out of the folder (harness §4.16) would also close it, by moving the whole
store.
