# A merge lands mid-turn: the deploy waits, the turn finishes

**Run:** 2026-10-02 around 01:47Z to 02:00Z, on Windows 11 with Docker Desktop and Git Bash.
**Image:** `tinyassets-linux-oracle:306e17800aee` (Python 3.11.16, FastMCP 3.4.7). The repo
was mounted read-only at `/repo` from the worktree at commit `f61d59dc`.

```
REPRO_IMAGE=tinyassets-linux-oracle:306e17800aee python run.py wait 600
REPRO_IMAGE=tinyassets-linux-oracle:306e17800aee python run.py cap 600
```

## What runs

- **The daemon:** a real FastMCP and uvicorn server. Its `converse` tool holds a real
  interactive seat through `tinyassets.universe_seats.hold`, the same call a chat turn
  makes, for 600 seconds.
- **The deploy check:** the "Wait for in-flight turns" step, extracted verbatim from
  `.github/workflows/deploy-prod.yml`. Only `ssh` and `scp` are swapped for local
  stand-ins. So the real loop pipes the real `scripts/turns_in_flight.py` into the real
  container.
- **The swap:** `docker compose up -d --timeout 20`, as `deploy_fail_safe.sh` does it.

## Results

| variant | wait outcome | waited | turn reply | turn ended before swap | 502 window | new gen serving |
|---|---|---|---|---|---|---|
| wait (cap 2700s) | `idle` | 605s, 102 polls | `TURN_FINISHED gen=1` after 604.8s | yes | 3.6s | 2 |
| cap (cap 20s) | `cap_reached` | 24s, 5 polls | cut off (`RemoteProtocolError`) | no | 21.5s | 2 |

In both runs, `/data/.deploy-pending.json` read `pending: true`, `in_flight: 1` and the
target sha while the step waited.

**The swap got cheaper once it waited.** With no turn left to drain, the 502 window was
3.6s. When the cap forced the swap over a running turn, it was 21.5s, which matches the
20s drain bound from `docs/audits/2026-10-01-deploy-drain-repro/`.

## Rerun after the Codex refute (round 1 fixes)

**Run:** 2026-10-02, about 02:12Z to 02:23Z, same host and image.

This run used the revised probe, which counts seats by holder liveness and also counts
graph runs. The wait now lives in `deploy/wait_for_turns.sh`, and the workflow step only
runs that script, so the repro runs it unchanged.

| variant | wait outcome | waited | turn reply | turn ended before swap | 502 window | new gen serving |
|---|---|---|---|---|---|---|
| wait (cap 2700s) | `idle` | 596s, 105 polls | `TURN_FINISHED gen=1` after 600.0s | yes | 1.4s | 2 |
| cap (cap 20s) | `cap_reached` | 23s, 5 polls | cut off (`RemoteProtocolError`) | no | 21.2s | 2 |

Every busy poll reported the seat as `"holder": "alive"`. That means the flock liveness
probe works inside a real Linux container, against a lock held by the daemon's own process.

## Rerun on a Linux host after Codex round 2: the probe moved out of the daemon

**Run:** 2026-10-02, about 02:43Z to 02:56Z.
**Host:** WSL2 Ubuntu 24.04, with its own Docker Engine 29.1.3, run as root (the droplet's shape).
**Image:** `turns-repro:1`, which is `python:3.11-slim` plus `pip install fastmcp==3.4.7 uvicorn pyyaml`.

Why it moved: CI's `drop-first-exec` invariant refuses any repo-authored `docker exec`
into the daemon unless it goes through `ta-op`'s closed mode table. So the probe now runs
in a throwaway SIBLING container: the daemon's image and uid 1001, the data volume, no
network.

I also tried host python as root, and it is unsafe. A root sqlite open can create
`-wal`/`-shm` files the daemon then cannot open. Dropping to uid 1001 after entering the
volume did not work either: sqlite and `tempfile` resolve absolute paths through
`/var/lib/docker`, which uid 1001 cannot traverse. This repro caught that, with
"unable to open database file".

```
wsl -u root -e bash -lc 'cd <this dir> && REPRO_IMAGE=turns-repro:1 python3 run.py wait 600'
wsl -u root -e bash -lc 'cd <this dir> && REPRO_IMAGE=turns-repro:1 python3 run.py cap 600'
```

| variant | wait outcome | waited | turn reply | turn ended before swap | 502 window | new gen serving |
|---|---|---|---|---|---|---|
| wait (cap 2700s) | `idle` | 643s, 114 polls | `TURN_FINISHED gen=1` after 600.1s | yes | 1.5s | 2 |
| cap (cap 20s) | `cap_reached` | 23s, 5 polls | cut off (`IncompleteRead`) | no | 21.1s | 2 |

Every busy poll read the seat as `"holder": "alive"`. The flock taken by the daemon's own
process was visible from the sibling container, across the container boundary.

**Why "waited 643s" for a 600s turn is not a release lag.** `waited_s` comes from the wall
clock, and the turn time from `time.monotonic`. On this WSL VM the wall clock runs about 4%
fast: 20.0s monotonic measured as 20.78s wall. A 200s run showed the same proportion.
