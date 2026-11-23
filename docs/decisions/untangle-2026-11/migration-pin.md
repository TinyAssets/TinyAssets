# migration-pin (2026-11-21)

The dark redeploy's plan (run 44940429741, mode `plan`, on main ad64eb53f) stopped before any
change: owner's lock pinned 18 migrations where the code ships 20, and `locks.ts digests` also
refuses sandbox (SandboxControl pinned 3, the code ships 4). Migrations reach a live object only
by a deploy, and the kit deploys only pinned code, so each new migration is pinned before the
deploy that first runs it.

`node ci/kit/locks.ts pin owner ad64eb53f` and `pin sandbox ad64eb53f`: both locks already mark
their first deploy, so the pin only appends the new migrations' digests (owner 19 and 20,
SandboxControl 4); the existing pins were checked unchanged first. `digests` now passes for both,
and no other Worker's lock is behind. From this pin on, those migrations' text is frozen: a new
shape is a new migration at the end.

- [ ] live: the dark redeploy's plan passes the locks and applies on this pin.
