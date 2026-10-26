---
severity: P2
title: Shell watchdog restarts the daemon when it cannot measure
filed: '2026-10-18'
summary: daemon-watchdog.sh treats a failed docker inspect or systemctl is-active as a stopped daemon and restarts it; a measurement failure must fail loudly instead.
---

# Shell watchdog restarts the daemon when it cannot measure

`deploy/daemon-watchdog.sh` (installed by `deploy/install-host-uptime-services.sh`)
treats a failed `docker inspect` as "not running" (~98) and any failed
`systemctl is-active` as inactive (~212), then restarts the daemon (~217) and
exits 0. A local inspection failure therefore restarts a healthy daemon.

Pre-existing on main; found by the C6 adversarial review of the Python
watchdog's helper-fault contract. Close by giving the shell watchdog the same
contract: could-not-measure is a loud unit failure, never a restart.
