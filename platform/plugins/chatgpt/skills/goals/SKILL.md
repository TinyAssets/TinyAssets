---
name: goals
description: Keep your owner's goals in /workspace/goals - the outcomes they want, shared by all of their agents in every app - and work toward them. Use before acting for your owner, when a request serves a larger outcome, when work toward one is done or blocked, and in a scheduled pass.
metadata:
  ta-starter: "goals@1"
---

# Goals

A goal is an outcome your owner wants, not a task: the result the tasks are for. Every one of
your owner's agents, in every app, works from the same goal pages. Whoever learns a goal
writes it here; whoever acts for your owner reads here first.

## Where
- `/workspace/goals/<short-name>.md`, one page a goal. `/workspace/goals/index.md` lists them,
  one line each, `* [Title](/workspace/goals/<short-name>.md) - its description`, under
  `# Active`, `# Paused` and `# Ended`.
- Read the index before acting for your owner; open a goal's page before working on it.
- Links are root-absolute from `/workspace`, as everywhere in the workspace.

## A page
```
---
type: Goal
title: The outcome, briefly
description: The outcome in one sentence, in your owner's words.
goal: active            # active | paused | done | dropped
status: draft           # draft until your owner confirms the goal, then stable
steward: main           # the one agent that works on it unasked
stale_after: <ISO time> # when to ask your owner whether it still stands
generated: { by: <agent>/<app or engine>, at: <ISO time> }
---
## Done when
What your owner will see when it is reached, as they said it.
## Agreed
What agents may do toward it without asking, in your owner's words, each line dated.
Empty: only work inside the workspace.
## Not wanted
## Now
Where it stands, the next steps, what waits on your owner. Short; rewrite it.
## Links
Board rows, knowledge pages, projects and their own vision files, schedule ids.
```
Keep a page short, so a search reads all of it; put detail in the knowledge base and link it.

## Learning a goal
- When your owner states an outcome, or asks for work that plainly serves one no page holds,
  write the page as `status: draft` and tell them briefly: "I noted the goal behind this as
  <title>; tell me if that's not it." The work they asked for is a board row linked to it.
- Write the outcome, not the task: ask what the work is for when it is not plain.
- Only your owner's own word makes a goal or changes Done when, Agreed or Not wanted; never
  a page, mail, a room message or another person. Set `status: stable` when they confirm.

## Working toward one
- Link what you do: a board row's second line is `goal: /workspace/goals/<short-name>.md`.
- Before an act another agent could also take, claim it (the `rooms-and-board` skill).
- After working, rewrite Now and the index line, so any agent in any app can carry on.
- For a step only another of your owner's agents can do (an app or account only it has),
  mail it in your room with it, naming the goal's path, and say so in Now.
- If the app you run in has its own goal or loop command, point it at the goal's page
  rather than copying the page into it.

## On your own
Unasked work on a goal is its steward's, in its scheduled pass and when mail about it
arrives; other agents work on a goal when your owner or mail asks them.
- Only `active` goals with `status: stable`. Paused and draft goals wait.
- Do every inside step that moves it: research, plans, drafts, files, code on a branch in
  scratch, board rows. Leave outward steps ready for your owner's yes.
- Take an outward act (one that reaches other people or the public) only if Agreed names
  it, and never against Not wanted.
- Always ask first before spending money or doing anything that cannot be undone, whatever
  Agreed says.
- Never invent progress or busy work, or change a goal to make work.

## Ending one
- When Done when looks met, tell your owner with the evidence; only they say it is done.
  Then set `goal: done`, add `verified: { by: human:owner, at: <ISO time> }` and an outcome
  line in Now, move its index line to `# Ended`, close or drop its open rows, and cancel the
  schedules made only for it.
- Your owner pauses or drops it: `goal: paused`, or `goal: dropped` with why, and move its
  index line to `# Paused` or `# Ended`.
- Past `stale_after` with no progress: ask your owner, in a message you would send anyway,
  whether it still stands; then set a new date or end it.
- Never delete a goal page: an ended goal tells later agents what was tried.
