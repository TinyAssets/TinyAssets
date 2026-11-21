# i-deletion-withhold as built (2026-11-21)

The second half of the deletion-jobs step before `admit` (`tasks.md` §10 step 5, §9 step 11;
the first half is `i-deletion-compare.md`).

## Research (before the build)

1. **Where a request arrives.** The old app posts `/app/account/delete`
   (`tinyassets/onboarding/__init__.py` `_handle_account_delete`) to the broker's
   `DELETION_JOB` `enqueue` (`broker/server.py`). `JobRows.save` (`broker/deletion_jobs.py`)
   writes the off-host record (`deletion-jobs/<sha256 of the principal>.json`,
   `broker/deletion_records.py`) before the identity row and before the request is answered.
   So the off-host store holds every request the old stack ever acknowledged, and it never
   removes one. The old router has one way in: the tunnel (`tinyassets-tunnel`, cloudflared on
   the host's network to the daemon's loopback port, `deploy/compose.yml`). The old proxy's
   routes and `mcp.` both reach it through that tunnel, and they serve until `routes` (§9 step
   13), two steps after `admit`.
2. **What stood open.** `deletions` read the off-host records and printed matches. `admit`
   read only the window's record and the dead-letter queue. Any request made after the scan,
   during the admit, or between the admit and `routes` went through.
3. **The shapes weighed.**
   - *`admit` reads the requests itself.* This closes the gap between the scan and the admit.
     The networked job sees the bundles alone and never the Spaces key (`window.sh`). So the
     read runs on the host, in the same `admit` dispatch, right before its job. It cannot
     cover a request made while the admits are running, or after them.
   - *Stop the old writer.* Once it stops, a read made after the stop is final. But nothing
     in the job can see the stop without new reach (a probe of the old door from the import
     host: a second network path, and a fragile reading of someone else's answer). On its
     own, a skipped or undone stop fails silently, and the gap between scan and admit stays.
   - *Hand requests onward.* The old broker would have to start the platform's erasure. For
     a dark owner that is the operator's `owner.erase` (`owner-erase-op.md`), whose keyring
     the old stack must never hold. It also needs old-stack code, which is fixes only.
     Rejected.
4. **Chosen: both, each doing what the other cannot.** The intake stops (live, the founder),
   so no request can arrive after a read. `admit` reads the requests itself, so a request
   made before the stop is never missed, whenever the stop happened. The read also never
   rests on anyone remembering to rescan.

## As built

1. **`admit` makes its own check, and only it does.** On the host, window.sh's `admit` lists
   and reads the current records with the read-only Spaces key (stdin, the source pair before
   the job's five). The offline comparison (`deletions`) then writes the check into the
   window's folder, 0600. Last, the networked `admit` runs. Mode `deletions` is gone: it was a
   second door to the same read, and it authorized nothing. The comparison is the same as
   before (full original digest, rehearsal names, a listing missing a backup's job refused). A
   match is no longer an exit 3. It is the withheld list.
2. **The check is fresh by construction.** It names its run and every bundle of the run, by
   key and digest, and the withheld keys. `admit` deletes it as it reads it, before any
   message. A missing check, another run's, or one of other bundles is refused, and nothing is
   sent. So every `admit` follows its own read, and a second `admit` needs a second read.
   window.sh deletes any check left over before it scans, and deletes the scan and the check
   when it exits.
3. **Withheld owners are discarded first, then the rest are admitted.** The checks run first
   (every owner to be admitted verified clean, none the old stack never admitted, the
   dead-letter queue empty). Then each withheld owner whose index is still staged is
   discarded (`discard`, as `wipe` sends it). Its namespace goes in the record, and its
   staging is deleted, so `close` reads it as discarded. Only then is anyone admitted. A
   withheld owner is never sent an `admit`. A discard the doors refuse stops the step before
   any admit. That includes an owner an earlier `admit` already took, which is erased with the
   kit's `erase` instead. That owner exists only when a request came after an earlier admit's
   read, so only when the stop (item 5) was skipped. Its staged index stands after the erase,
   so each later `admit` is refused at it too: going on is the founder's call (review).
4. **`wipe` and `admit` share one discard** (`discardOwner`). Both skip an owner no longer
   staged, so either converges when killed.
5. **When the old service stops.** `live:` the founder, at the real window's opening, once
   Stripe's endpoint is disabled and before the full backup: `gh workflow disable
   deploy-prod.yml`, then on the droplet `sudo systemctl stop daemon-watchdog.timer
   tinyassets-watchdog.timer`, then `sudo docker stop tinyassets-tunnel`. Each watchdog's
   restart re-converges the daemon unit, and the unit's `up -d` would start the tunnel again;
   so would a deploy's `up -d` (deploy-prod runs on every old-stack merge to `main`), and the
   unit at a reboot (then the stops and the check are run again). The review found the
   second watchdog (`tinyassets-watchdog`, `scripts/watchdog.py`) and the deploy. Its check
   is that `curl -s -o /dev/null -w '%{http_code}' https://tinyassets.io/mcp` no longer
   answers the old stack's `401`. From then until `routes`, the old stack takes no request,
   deletions included. At `routes` the doors' erase door takes them instead. That
   is the hand-onward, and it needs no old-stack code. Stopping before the backup also makes
   the backup the old stack's last state, so no owner's later write is silently lost. It also
   keeps the handback's "untouched volumes" true. The latest correct point is before the
   first real `admit`. A rehearsal stops nothing, and its `admit` withholds the same way.
   The canaries' old side reads red from the stop (expected in the window). A handback's
   restart is **Restart daemon** (`restart-daemon.yml`, which `cutover.json` keeps for it;
   the unit's `up -d` starts the tunnel), then both timers started by hand (the cutover's
   change takes its converge), and `deploy-prod.yml` enabled while it is in the tree. That
   reopens the old intake while the platform still holds every admitted owner, so the
   handback's steps now say a request made after it is erased on the platform too (the kit's
   `erase`).

## Left

- [ ] The old stack's intake stopped at the real window's opening, before the full backup,
  by live: the founder (item 5; `tasks.md` §9 step 10). Until it is, a request made while an
  `admit` runs, or after it, reaches no withhold, and is the kit's `erase` once found.
