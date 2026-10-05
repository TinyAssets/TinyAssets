---
severity: P2
title: The app's unread counter keeps growing (177 to 182)
filed: '2026-10-04'
summary: the founder's app shows 177-182 unread messages while they read the thread; #4458 could not identify which counter the badge reads
---

# The app's unread counter keeps growing (177 to 182)

The agent reported the unread count going from 177 to 180 (5:27 PM) and 182 (6:25 PM). The agent suggested a large count could bury request tabs; that is its hypothesis. (Separately, the Claude lead measured the founder's desktop app over CDP at about 2026-10-05 01:30Z, on deployed 381ca66730: the two open request tabs were rendered inside #thread at the top of the conversation, with a bounding top of -11536px while #thread scrollTop was 11773, so they were out of view. That is tracked separately from this counter.) See also return-to-app-live-stop-label.md, which records #4458's partial investigation of the counter.

**Source:** the founder's command-center agent, in the founder's app thread on 2026-10-04: its 3:06 PM PDT status report and its 5:27 PM PDT live re-test (plus its 5:58 PM and 6:25 PM replies where noted). Its run IDs are in its notes/command-center-playbook.md. Re-verify against current main before fixing.
