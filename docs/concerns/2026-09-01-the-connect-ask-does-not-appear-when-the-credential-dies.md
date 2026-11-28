---
severity: P2
title: The connect ask does not appear when the credential dies
filed: '2026-09-01'
summary: the rail asks only when NO binding exists, so a bound-but-expired universe is unserved and un-asked
---

# The "connect a model" ask appears only when nothing is bound, not when the credential dies

**Filed:** 2026-09-01, from the founder's instruction after the founder's universe stopped
answering:

> "i cant do as you ask and reconnect cause a naevie user wouldnt know that. if
> that is the case then the request for that should pop up."

**Severity:** P2 — it strands an owner in a state they cannot diagnose or exit
without being told where to look.

## The gap

Originally, `list_requests` prepended the synthesized connect ask when
`not _serving_llm_bound(...)` (`tinyassets/api/pending_requests.py`). That helper
resolved only the serving **binding row** and returned True whenever one existed.
September 9 correction replaces that test with the canonical
`resolve_current_serving_provider_authority` check. It verifies local custody,
assignment and connection grants. PR #3676 is deployed: authenticated release
34394967360 verified ff498d301c41 at 19:26 UTC and release 34395463075 verified
descendant 5c991f432566 at 19:31 UTC. Both canaries passed. This check cannot by
itself detect remote expiration of an otherwise unchanged credential.

So there are two different unserved states and only one of them asks:

| state | binding row | ask appears |
|---|---|---|
| never connected | absent | **yes** |
| connected, credential expired or revoked | present | **no** on the original implementation |

In the second, the owner is bound, unserved, and un-asked. Every turn fails, the
rail shows nothing to do, and the only route out is knowing to go and reconnect
a provider that still *looks* connected.

## Why the notice cannot paper over it

The honest-failure change in this same PR adds an `auth_invalid` notice. The
first draft said *"there is a request waiting for you to do exactly that"* —
which would be **false in precisely this case**, and would have been the same
confident-wrong shape that change exists to remove. The notice now describes the
action without asserting a rail row exists. That is a workaround, not the fix.

## The fix

Broaden the condition from "is a binding row present" to "is this universe
actually servable": a binding whose credential is unusable should raise the ask
the same way no binding does.

The existing canonical `resolve_current_serving_provider_authority` in
`tinyassets/provider_serving_binding.py` is the local authority signal. It reuses
`_current_serving_authority`, which checks the owner's subscription custody or
open-connection grant. It is broader than inspecting only subscription material
and does not assume a provider family. `_usable_subscription_record` alone does
not establish that a credential is still valid at the remote provider.

**Do not use `api/status.py::_provider_auth_snapshot` for this.** It reads the
shared-volume host auth paths, which is why it reported codex `"ok"` on
2026-09-01 while the founder's universe could not run at all. Gating a
per-universe ask on host credentials would reproduce the original confusion in a
new place.

Cost: `list_requests` is a hot read on every rail poll, so the check needs to be
cheap or cached; a filesystem stat of the vault record is probably acceptable,
a live provider probe is not.

## September 9 local evidence and remaining scope

`tests/test_pending_requests_power.py` reproduced stale binding behavior for
real SQLite grant revocation and credential-reference rotation, then passed
with canonical authority validation. It also exercises the actual request rail
before and after test subscription custody removal and verifies repeated polling
does not duplicate the synthesized request. No production credentials or user
workflows were changed. Remote auth failure feedback, upstream quota/health,
the owner's two-request OpenRouter/generic-LLM flow and rendered
recovery remain unproven. Keep this concern open until recovery is proven across
the reported failure, not merely for locally revoked authority.

Source recheck September 9 at fc617195: `list_requests` still synthesizes only
`sys_connect_llm`. The app's `railBody` handles that action by opening the generic
connection form. There is no automatic OpenRouter request with the direct key
page link yet. Reuse the secure deposit/register/serve flow; do not make an
unpowered model generate the request, ask the user for endpoint internals, or
equate a text-only HTTP provider with a working full-agent connection.
