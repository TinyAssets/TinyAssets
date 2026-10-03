---
severity: P3
title: The deploy's in-flight probe reads daemon data outside ta-op's closed mode table
filed: '2026-10-02'
summary: 'deploy/wait_for_turns.sh runs scripts/turns_in_flight.py in a throwaway sibling container (the daemon image, uid 1001, the data volume, no network). It passes the drop-first-exec gate, because it is not an exec into the daemon. But it is arbitrary interpreter access to daemon-owned data that ta-op does not vet. The fix is to ship the probe in the image and add an operand-free ta-op mode for it.'
---

# The deploy's in-flight probe reads daemon data outside ta-op's closed mode table

**Filed:** 2026-10-02 by the turn-handover lane (PR #4278).
**Source:** Codex round-3 refute of #4278, a `DISAGREE_CONCERN` at P2 that did not block.
That review found no defect for the daemon itself.

## What is true

- **Why the probe moved.** `scripts/check_drop_first_exec.py` refuses any repo-authored
  `docker exec` into the daemon unless it goes through `/usr/local/libexec/ta-op <mode>`.
  So the deploy's "is a turn in flight" probe no longer execs into the daemon.
- **How it runs now.** It runs as
  `docker run --rm -i --network none --memory 256m --user 1001:1001 -v tinyassets-data:/data --entrypoint python <daemon image> -`.
  The script body is piped from the checkout and verified by sha256 on the host first.
- **What that contains.** It has less authority than an exec into the daemon: a separate
  process tree, no network, a memory cap, and the daemon's uid. It is still an arbitrary
  interpreter with read-write access to every store on the data volume. It does not go
  through ta-op's closed operation table, and it does not get ta-op's verified empty
  capability posture.
- **Why not the alternatives.** Host python as root could leave root-owned `-wal`/`-shm`
  files the daemon cannot open. A uid-1001 host process cannot traverse `/var/lib/docker`.

## How to resolve

1. Ship `scripts/turns_in_flight.py` in the image (a `Dockerfile` COPY). It is already in
   the build path filter.
2. Add an operand-free mode `turns-in-flight` to `deploy/native/ta_op_modes.tsv` and
   `deploy/native/ta_op.c`. It runs `/opt/venv/bin/python /app/scripts/turns_in_flight.py`
   and prints JSON.
3. Write the deploy-pending marker from the host instead. The mode takes no operands, so
   it cannot carry `--mark-pending`'s values.
4. Switch `deploy/wait_for_turns.sh` to `ta-op turns-in-flight`. Keep the sibling path only
   for images that predate the mode, so the first deploy after the change is still covered.

Delete this file when step 4 ships.
