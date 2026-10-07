---
severity: P2
title: Hard-coded policy the user should be composing instead
filed: '2026-08-31'
summary: 'five workflow-shape caps and six platform-selected policies constrain the user''s own work; the deeper pattern is one hard-coded policy manufacturing the justification for another Also: inventory of graph-shape limits that constrain only a user''s own workflow rather than protecting another user or the host'
---

# Hard-coded policy the user should be composing instead

**Founder directive, 2026-08-31.** After four consecutive gates that were all
defects in the graph DSL rather than in doing the work:

> "seems we are still doing patch for patch for patch instead of letting our
> users build their workflows. like why cant the user just build .py's"

and then, on being told resumability was a "cost" of a plain-script surface:

> "all these things about wiether or not a graph can be resumed or not or the
> condisions thier of are all just more example of things that should be user
> buildable into the graph. infact i bet if we keep looking around we will find
> more things we have been hard coding that should just be user graph buildable
> things"

The bet was correct. This file is the audit.

## The discriminator

**Does this protect other users and the host, or does it only constrain the
user's own work?** The first is platform. The second is theirs to compose.

This is not a new principle. ADR-013 already says *limit
USAGE (admissions, budget, consent), never SHAPE*. Most of what follows is that
rule un-applied.

## Shape caps that only constrain the user's own workflow

All in `tinyassets/node_sandbox.py` unless noted.

| Constant | Line | What it actually decides |
|---|---|---|
| `MAX_RPC_CALLS = 32` | 126 | how many times a workflow may call out, ever |
| `MAX_WORKSPACE_COMMANDS = 64` | 145 | how many commands a step may run |
| `MAX_WORKSPACE_OUTPUT_BYTES = 1 MiB` | 146 | how much a step may produce |
| `MAX_WORKSPACE_READ_BYTES = 1 MiB` | 147 | a workflow cannot process a 2 MiB file |
| `MAX_WORKSPACE_GLOB_RESULTS = 10_000` | 148 | how many files may be enumerated |

`MAX_WORKSPACE_COMMANDS`'s own comment is explicit that it exists to bound
loops: *"per NODE, not per command, so a loop of small commands is bounded by
the same numbers."* Bounding a loop is a workflow decision.

## Workflow policy decided by the platform

| Site | Policy |
|---|---|
| `effectors/workspace.py:1217` | branch name is forced to `refs/heads/tiny/<universe>/<slug>` |
| `engine_mcp_server.py:1104` | "never the default branch", with no override |
| `effectors/workspace.py:949` | `provision` refused — "not available in this release" |
| `effectors/workspace.py:1087` | re-opening a permanent workspace refused, same reason |
| `runs.py` sweep path | recovery is "swept once then retried once" |
| `workspace_git.py:788` | "only HTTPS on 443 is transported" — blocks a self-hosted forge on another port |

Branch naming is the clearest single case: a user whose workflow publishes to
`release/x`, or who wants to open a PR from a differently-named branch, cannot,
because the platform picked the shape of their git workflow.

## The finding that matters most: hard-coding that manufactures more hard-coding

`MAX_WORKSPACE_TIMEOUT_SECONDS = 1800.0` (`node_sandbox.py:153`) is justified in
the code as genuine multi-tenant safety:

> "A workspace node holds the universe's job lock and the host-wide slot for
> its whole run, so an unbounded one is a denial of service on every other
> universe."

That reasoning is sound **given the layer beneath it** — and that layer is
itself a hard-coded policy: *one host-wide workspace slot*
(`workspace_pool.py`, `SCOPE_HOST`). So:

1. a hard-coded concurrency policy (one host-wide slot) creates contention,
2. which forces a hard-coded 30-minute ceiling to stop one tenant starving
   the rest,
3. which contradicts the founder's own law that **a turn runs until finished,
   not wall-clock** ([[turn-runs-until-finished-not-wall-clock]]).

Three layers, each defensible given the one above it, and the bottom one is a
choice nobody had to make. Real isolation (per-tenant quota, queueing, or
per-universe slots) removes the need for layers 2 and 3 entirely.

**That is the pattern to hunt, not single magic numbers:** a hard-coded policy
whose only justification is a problem created by another hard-coded policy.

## What is genuinely platform — keep

Named so the audit is not read as "remove all limits":

* `_NEVER_BIND_PREFIXES = ("/data",)` — tenant isolation.
* `RLIMIT_AS` and the aggregate-RSS watchdog — host memory; nothing else bounds
  the sum across a process tree.
* `MAX_INPUT_BYTES` / `MAX_STDERR_BYTES` — protocol and host safety.
* Credential blindness: the node never holds a credential, and effects are
  performed by the parent. This is the boundary that makes everything else
  safe to relax.

## Direction

Each item above becomes either a **user-set parameter with a safe default**, or
a **primitive the user composes** — never a platform refusal. Retry, recovery,
branch naming and loop bounds belong in the workflow the user builds. The
platform keeps only what protects someone other than the author.

Sequence this behind the authoring-surface question (why the user cannot simply
write a `.py`), since several of these caps exist only to bound the DSL's node
shape and would have no meaning in a plain-script surface. Related:
[[no-structural-caps-on-graph-size]], [[enabling-primitives-not-prebuilt-complexity]],
[[capabilities-are-user-declared-not-platform-policy]],
[[shape-architecture-to-the-design-mental-model]].
