---
severity: P2
title: An agent cannot withdraw its own stale ask
filed: '2026-08-28'
summary: the rail is append-only from the agent side, so asks it knows are obsolete sit in the founder's face until the founder clears them
---

# An agent can raise a request but never withdraw one, so the rail fills with asks it knows are wrong

**Filed:** 2026-08-28
**Verified:** 2026-08-28, observed live in the founder's own rail.
**Severity:** P2 — nothing breaks, but the rail is the founder's front door and
half of what it currently shows is misleading.

## What the founder sees

Four tabs under **WAITING ON YOU**. Two of them are wrong:

| kind | title | action | fields |
|---|---|---|---|
| API | GitHub endpoint so I can update request_theme.json | `connect_http` | door link |
| API | GitHub endpoint so I can update request_theme.json | `connect_http` | door link |
| GitHub | Set Contents to Read and write on the key you already gave me | `answer` | `acknowledgement` |
| GitHub | Extend the existing github connection to request_theme.json | `extend_http` | *(none)* |

The first two ask the founder to paste a GitHub key. That is the wrong action:
the key is already in the vault, it is live and unexpired, and pasting it again
changes nothing — the actual gate is a GitHub *permission* the key lacks. The
agent knows all of this now. It cannot take the tabs down.

## Why

`tinyassets/api/pending_requests.py` exposes `request_from_user` (ask),
`list_requests`, `answer_request` and `unmute_request`. There is **no withdraw,
cancel, supersede, or replace**. A request, once raised, can only be removed by
the user answering or dismissing it.

That is a reasonable default for a *consent* prompt — an agent should not be able
to retract something the user is mid-way through approving, and it must never be
able to clear a warning the user has not seen. But it makes the rail
append-only from the agent's side, and asks go stale for ordinary reasons:

* the platform changed under it (here: `extend_http` shipped, so the paste the
  tabs ask for became unnecessary),
* the agent learned the real blocker was elsewhere,
* the agent rephrased and re-asked, and the dedupe key includes the prose, so
  the reworded copy became a second tab
  (see `2026-08-28-one-key-serves-dedupe-and-muting.md`).

## The structural point

The rail is the founder's front door and its value is that every tab is a thing
worth their attention. An append-only queue whose author cannot retract a stale
entry degrades toward noise with exactly the traffic that makes it useful. Two
of four tabs being obsolete on day one is the shape of that.

`MAX_PENDING = 10` bounds the damage but does not address it: hitting the cap
with stale asks blocks the *real* one from being raised.

## What would fix it

A `withdraw` operation keyed on the agent's own `request_id`, restricted to
requests **the user has not yet interacted with**, recording the withdrawal
rather than deleting silently. That keeps the safety property — a user cannot
have a decision snatched away mid-flight, and nothing they have engaged with can
vanish — while letting an agent clean up after itself when it learns better.

An alternative worth weighing: let a new ask **supersede** an older one on a
narrower key (kind + action + fields, per the dedupe concern), so re-asking
replaces rather than accumulates.

## How to resolve this file

Delete it when an agent that raises an ask, then learns it is obsolete, can take
it down without the user acting — with the user-has-engaged case still refused,
proven by test and observed once on the live rail.

## Status (2026-09-24)

Built in change `agent-access-controls`, not yet deployed:
`write_graph target=pending_request operation=withdraw`. It takes down only
a still-pending ask the agent raised (server-stamped `origin`), records the
reason, and writes no standing decision. Tests are in
`tests/test_agent_access_controls.py`. The file stays open until a
withdrawal is observed once on the live rail.
