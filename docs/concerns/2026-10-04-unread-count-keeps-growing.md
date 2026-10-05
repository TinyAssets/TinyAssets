---
severity: P2
title: The app's unread counter keeps growing (177 to 182)
filed: '2026-10-04'
summary: the founder's app shows 177-182 unread messages while they read the thread; #4458 could not identify which counter the badge reads
---

# The app's unread counter keeps growing (177 to 182)

The unread badge went from 177 to 182 during the day while the founder was actively reading. #4458's investigation found a counter of messages unread by the agent, but not the badge's source. A large unread count also buries request tabs.

**Source:** the founder's command-center agent re-tested its known issues on 2026-10-05 at 00:27Z (5:27 PM PDT), with live run IDs in its notes/command-center-playbook.md. Main at that time was 51db6894f3 (#4458 not yet merged). Re-verify against current main before fixing.
