---
severity: P1
title: The DR drill PLAN calls weekly is dispatch-only and last ran 2026-07-24
filed: '2026-10-02'
summary: dr-drill.yml has no schedule trigger and defaults to restoring the primary's own local tarball, so the one host-independent recovery path has gone unexercised for over two months
---

# The DR drill PLAN calls weekly is dispatch-only and last ran 2026-07-24

**Filed:** 2026-10-02
**Verified:** 2026-10-02 against `origin/main` `5eb909de` and `gh run list`.
**Severity:** P1. Production runs on one droplet, so the restore path is the
only recovery from a host or region loss, and nothing exercises it.

## Source (verbatim)

From the staged-architecture review (2026-10-02):

> The PLAN says the DR drill runs weekly. It is dispatch-only and last ran
> 2026-07-24.

## Evidence

- `PLAN.md:872` (§ Module: Uptime & Alarms): "*DR validated end-to-end.* Weekly drill
  provisions a fresh VM, bootstraps, restores `/etc/tinyassets/env` + data
  volume from offsite, starts daemon, asserts canary-green within SLA."
- `.github/workflows/dr-drill.yml:20-21` has an `on:` block with
  `workflow_dispatch` only. There is no `schedule:` trigger.
- `gh run list -R TinyAssets/TinyAssets --workflow dr-drill.yml -L 6`: the
  newest run is 2026-07-24T04:16Z (`workflow_dispatch`, success). Three runs
  before it failed on the same night.
- The default backup input is "latest in /var/backups/tinyassets/" **on the
  primary** (`dr-drill.yml:30` input description; the "Validate selected backup
  on primary" step at `:224`). The drill therefore needs the host whose loss it
  simulates. The drill region is already `nyc3` (`DRILL_REGION`, `:57`), so the
  region is fine.

## What would resolve it

1. Add a weekly `schedule:` to `dr-drill.yml`.
2. Restore from the off-host copy (the object store), not the primary's local
   tarball.
3. Record the measured restore time and the age of the newest restored row
   somewhere a reader can see it.

Then delete this file.
