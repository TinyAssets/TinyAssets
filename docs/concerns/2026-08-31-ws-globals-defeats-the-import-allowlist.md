---
severity: note
title: The node import allowlist is theatre — delete it, don't fix it
filed: '2026-08-31'
summary: 'filed as P1, INVERTED the same day: the measurement holds, but under the isolation floor (cross-user only) it is not a vulnerability'
---

# The node import allowlist is theatre — delete it, don't fix it

**Superseded 2026-08-31, same day it was filed.** This started as a P1
vulnerability report. The founder's statement of the isolation floor inverts the
conclusion: the finding is real, the severity is not, and the fix is deletion
rather than repair. The original write-up is kept below because the measurement
is still the evidence.

## The floor that changes it

> "the floor is they cant effect other users except what ever ways we build for
> users to interact. but within your own universe you are god as if you have
> your own computer in the cloud with an openclaw ageint on it that has full
> access"

The test is therefore: **does this stop me affecting another user, or the host
everyone shares?** Not "does this stop me doing something dangerous to myself".

## What was found (unchanged, still true)

Untrusted node code reaches the runner module's globals through any injected
capability object:

```python
g = ws.read.__func__.__globals__     # 68 names
sp = g['sub' + 'process']            # the denylist matches literals only
sp.run([...])                        # measured in the real jail: rc=0, stdout '42'
```

`_original_import` — the unrestricted `__import__` the allowlist replaced — is
in there too. Both injected names (`ws`, `invoke_mcp_action`) resolve to the
**same dict**, so wrapping `ws` fixes nothing. The node's own namespace is clean
(4 names); the leak is the shared runner module.

## Why it is not a vulnerability

Verified when it was found, and it decides the severity: the bwrap jail
contains all of it. **No network** (`--unshare-all`, no `--share-net`), **no
`/data`**, no universe root, **no credential mount**, no broker socket — git
runs in the effector, not the node jail. Nothing here crosses the tenant
boundary.

So this is a user's own code, in the user's own universe, reaching the user's
own tools. Under the floor above, that is not an escape. It is Tuesday.

## The actual finding: the guard never worked, and never needed to

`ALLOWED_IMPORTS` and `FORBIDDEN_PATTERNS` exist to stop the author's code from
importing things. They:

* are **defeated by string concatenation** — `'sub' + 'process'` walks past the
  denylist, which is why the measurement above succeeds;
* protect **nobody but the author from themselves**, which the floor says is
  the author's business;
* cost real engineering, and cost it again every time someone finds a new way
  around them;
* actively **mislead**, by implying a boundary that is not where the real one
  is. The real boundary is bwrap and the RPC, and those hold.

**Action: delete `ALLOWED_IMPORTS` and `FORBIDDEN_PATTERNS`, and stop
maintaining the allowlist.** Do not wrap `ws`, do not shrink the runner
namespace, do not add `__globals__` to a denylist — that last one is one more
literal to concatenate around, which is the same mistake one turn later.

## What must NOT be deleted with it

These are the real boundary and they are load-bearing:

* `--unshare-all` with no `--share-net`, `--clearenv`, the bind set, and
  `_NEVER_BIND_PREFIXES = ("/data",)`;
* `RLIMIT_AS` and the aggregate-RSS watchdog — host memory is shared;
* credential blindness **against imported commons code**. Blindness from the
  author's *own* agent is the author's call; blindness from a library they
  pulled from the commons is cross-user, because the commons is a sanctioned
  interaction surface. That distinction belongs in the commons trust model, not
  here.

## Related

Superseded by
`openspec/changes/archive/2026-10-06-script-authoring-surface/design.md` (the floor) and
`docs/concerns/2026-08-31-hard-coded-policy-that-should-be-user-composable.md`
(the same inversion applied to every other cap). Delete this file once the
allowlist is gone.
