---
severity: P2
title: Account deletion only knows the current home, so a former home's data survives a rebind
filed: '2026-10-01'
summary: '`delete_account` resolves one home (`get_founder_home`), and `set_founder_home` overwrites the binding on rebind with no history. A home the account held before a rebind keeps its universe directory and its `.agent-sessions/<home>/` records (session records, `rules.db`) after the account is deleted. Fixing it needs a home-history source; owner-keyed rows already follow the owner.'
---

# Account deletion only knows the current home, so a former home's data survives a rebind

**Filed:** 2026-10-01
**Verified:** 2026-10-01, Windows, origin/main `39b417a7`, by reading the code:
`tinyassets/account_deletion.py` `delete_account` takes `home = get_founder_home(root, principal)` and
removes only that home (`_stage_home(root, home)`). `tinyassets/daemon_server.py` `set_founder_home`
upserts `founder_home` (`ON CONFLICT(founder_sub) DO UPDATE SET universe_id = ...`), so a rebind keeps no
record of the previous home. Raised by the gpt-6-astra refute on #4216.
**Severity:** P2

## What is true

- Rows keyed by owner already follow the owner across rebinds, for example `universe_model_preferences`,
  `agent_turns` and `conversation_run_admissions` (`OWNER_ONLY_TABLES` in `account_deletion.py`).
- Data keyed by home is removed only for the CURRENT home. That covers the universe directory
  `<root>/<home>/` and, since #4216, `<root>/.agent-sessions/<home>/` (session records, Custom Rules and
  review switches in `rules.db`, and S2's `steering.db`).
- After a rebind, nothing records which homes the account held before, so deletion cannot find them.

## What would fix it

A home-history source written at bind time, such as a `founder_home_history` row on every
`set_founder_home` change. `delete_account` would then remove every home the account held and was the
sole founder of, applying the same shared-home blockers it already uses for the current home. Inferring
former homes from adjacent tables (registry rows, grants) is not a substitute: it can name a home someone
else now owns.

## Why P2

A rebind is rare today, and the data left behind is the account's own, not exposed to another user. It is
still a data-retention gap against "delete my account".
