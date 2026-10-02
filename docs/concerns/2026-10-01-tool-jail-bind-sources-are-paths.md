---
severity: P2
title: Tool-jail bind sources are validated as paths, then reopened by bubblewrap
filed: '2026-10-01'
summary: provider_jail resolves and checks each bind source, but bubblewrap reopens it by name later; a workflow provider jail binds the universe read-write and could swap a visible root entry for a link in that window
---

# Tool-jail bind sources are validated as paths, then reopened by bubblewrap

**Found:** 2026-10-01, gpt-6-astra refute of harness W2 (PR #4194), point 1.
**Severity:** P2. It predates W2. W2 closed the new instance it would have
added, and this one is left. **Owner:** the harness lane
(`universe-agent-harness`).

## What is open

`provider_jail._validated_view` resolves each bind source once and checks that
it is inside the universe, then passes the pathname to bubblewrap, which opens
it later. A workflow's provider jail binds the universe read-write, so in that
window a process there could rename a visible root entry (for example
`notes/`) and put a link in its place. Bubblewrap would then follow the link
when it binds `/u/notes`.

The agent's own four-tool jail cannot create links (seccomp), and the window is
short. A workflow the universe runs can.

## What W2 already closed

The agent workspace itself (`.agent-workspace/`, bound as `/u`) is created
before any launch is built, provider or tool. Every provider launch then finds
it present and masks it, so a workflow jail can never create or replace that
name (`provider_jail.ensure_agent_workspace`).

## Resolution

Open bind sources by an anchored, no-follow walk and keep the descriptors
through mount setup: bubblewrap `--bind-fd` / `--ro-bind-fd`, or an equivalent
mount-by-descriptor helper. Never validate a path and then reopen it by name.
Placeholder mountpoints left in the workspace by root-entry binds also need
reconciliation without deleting agent content.

Delete this file when both land.
