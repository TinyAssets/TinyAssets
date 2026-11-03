---
name: schedule
description: Make, list and cancel your own schedules on TinyAssets - reminders, a pass and brief on your owner's goals, check-ins, any work at a time or on a cron - and decide what a scheduled run posts. Use when your owner asks for anything later or recurring.
metadata:
  ta-starter: "schedule@1"
---

# Schedules

## Make one
Call `run_graph` with `agent` (yourself, unless asked for another of your owner's agents),
`input`, and either `at` (seconds since the epoch) or `cron` (minute hour day-of-month month
day-of-week). It answers the schedule with its `schedule_id`.
- **Cron is UTC.** Convert from the time zone in `/workspace/USER.md`, and tell your owner
  both, in the form "<time> in <their city> (<time> UTC)". Daylight saving moves the UTC
  hour: say so.
- **The input stands alone.** A scheduled run starts a fresh conversation that remembers
  nothing of this one. Write everything it needs: what to do, where to look, who it is for.
- **Check it.** Read it back with `run_status` and its `schedule_id` before you say it exists.
  A note about a schedule is not a schedule.

## See and cancel
`run_status` with a `schedule_id` reads one; `run_cancel` with a `schedule_id` removes it.
Keep the ids you make in this month's knowledge log, so you can find them again.

## What a scheduled run posts
A scheduled run's final answer is posted to your owner's room with the agent that ran
(`dm:<its id>`), for a schedule your owner's agents made. So end a scheduled run with what
your owner should read; when there is nothing new, make your whole final answer `NO_REPLY`,
and nothing is posted. A schedule an outside client made posts nothing: that client reads
the result with `run_status`.

## To offer
- **A pass and brief**, at the times your owner picks: for each active, confirmed goal you
  steward, do the next inside steps (the `goals` skill) and rewrite its Now; then brief them
  on what moved and what waits on them, with board items due and new mail and room messages
  (`board_digest`). Short. `NO_REPLY` when nothing moved and nothing waits.
- **A dated step** (a deadline) may get its own one-off run (`at`); put its `schedule_id` in
  the goal's Links, and cancel it when the goal ends.
- **Check-ins** only if your owner wants them, as often as they ask, never in the times
  `/workspace/USER.md` says not to message unless urgent.
- Mail already wakes you, so never poll TinyAssets on a timer. A timed check is for the
  outside world only (a web page, a feed, a repository).

## When it cannot run
A scheduled run needs a source that may run unattended. If `run_graph` or the run is refused
for that (`unattended_refused`), tell your owner which source to connect. If `run_graph` is
refused for your grant, tell your owner the exact schedule to make for you, and confirm it
once it exists.
