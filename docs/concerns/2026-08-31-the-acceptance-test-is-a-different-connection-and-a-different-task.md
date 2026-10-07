---
severity: note
title: 'The acceptance test: a different connection, a different task, zero patches'
filed: '2026-08-31'
summary: 'the founder''s bar for calling the credential work done: another service and another task with NO platform patch'
---

# The acceptance test: a different connection, a different task, zero patches

**Founder, 2026-08-31**, after six consecutive gates fixed in one day:

> "a user should be able to build any workflow and connect to any outside
> connection, github and real full patches is just the current test but if we
> test anything else like another outside connection and another task we
> shouldnt have to do another patch, any workflow the user wants to ground up
> build custom what ever they want, then build custom connection node to what
> ever outside connection they like and do what ever they like"

GitHub is the **test case**, not the feature. So the measure of done is not
"the PR worked". It is:

> Point a universe at a **different outside connection** and a **different
> task**, and count the platform patches required. The number must be zero.

Nothing in this repo has ever been measured that way. Every gate so far was
found by driving the *same* GitHub PR job, which is exactly the shape of test
that cannot distinguish "the platform is general" from "the GitHub path has
been patched until it works".

## This standard immediately fails the fix shipped an hour earlier

`FORGE_GIT_HOSTS = {"api.github.com": "github.com"}` (#2753) is a per-forge
patch. Unknown hosts pass through untouched, so a self-hosted Gitea where the
API and git share a host needs nothing — but **any forge that serves git on a
different host than its API needs another entry from us.** That is a table of
other people's products living inside the platform, and it is precisely the
"another patch" the criterion forbids.

It ships because it unblocks a live run, and it is recorded here as a debt, not
a design.

## Why inference cannot be made to work

The obvious repair — let the connection declare *both* hosts — makes it worse
under the current rule. Measured:

```
git_host_for_endpoints(["api.github.com"])                  -> "github.com"   (via the table)
git_host_for_endpoints(["api.github.com", "github.com"])    -> ""             (ambiguous!)
```

The founder's universe worked this out on its own and raised a rail request to
extend the connection's endpoints with `github.com`. **Approving it would have
broken the connection** — from "wrong git host" to "no git host at all", because
`git_host_for_endpoints` refuses two hosts as ambiguous, correctly.

That is the whole lesson in one measurement: **a real connection legitimately
has many API endpoints AND a git host, so "the one host" is not derivable from
the endpoint list.** No cleverer inference fixes this. The value has to be
stated.

## Where it gets stated

Not in another field on the connection model — that is a storage-shape change
for one forge-shaped problem, and it would still be the platform holding
knowledge about git.

It gets stated in the **user's own workflow**, which is
`openspec/changes/archive/2026-10-06-script-authoring-surface`. There the script says where it
clones from; there is no inference to get wrong, and a user connecting to a
forge nobody has heard of writes it themselves. The inference bug and the
authoring-surface proposal are the same problem seen from two ends.

## What to actually do next

1. **Run the criterion.** Drive a universe through a NON-GitHub connection on a
   NON-PR task — it already holds `x:posting` and a webhook connection — and
   count the patches. Publish the number either way. A low number is evidence;
   a high number is the argument for #2750 finished.
2. Treat every patch that number reveals as a **shape** finding, not a bug to
   fix in place. Six of six so far have been defects in a hand-written
   description of what the code was going to do.
3. Retire `FORGE_GIT_HOSTS` when the script surface lands. It is a bridge.

## Related

* `openspec/changes/archive/2026-10-06-script-authoring-surface/` — the shape argument and the
  four-primitive floor.
* `2026-08-31-fixing-an-authority-key-orphans-the-grants-written-under-the-old-one.md`
  — why the host value could not simply be changed underneath existing grants.
* `2026-08-31-hard-coded-policy-that-should-be-user-composable.md` — the same
  inversion applied to caps and policy.
