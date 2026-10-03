---
severity: P2
title: Model picker discovery delay, narrowed to HTTP sources and the client
filed: '2026-09-16'
summary: native shortlist discovery moved off the picker read; accepted HTTP sources still discover inline, and the client still expires the whole catalogue as one unit
---

# Narrowed: native shortlists are off the read; HTTP sources are not

Filed 2026-09-16. **Part of the server half is fixed.** Kept at P2 and
re-scoped rather than downgraded, because a slow source can still delay the
whole picker -- just a different kind of source.

An earlier revision of this file claimed "the server half is fixed". That was
wrong, found by cross-family review: only NATIVE shortlist discovery moved.

## Fixed (2026-10-03): native shortlist discovery

The original cause was synchronous per-member discovery inside
`providers/served_model_plan.py`: a picker read ran `discover_native_models_sync`
inline for every accepted member, so a slow or expired source delayed selecting
a DIFFERENT accepted source and its execution.

A display read now serves a warm per-source catalogue from
`providers/shortlist_refresh.py` and never runs discovery. Both of the things
this concern asked for are structural there rather than tuned:

- **per-source freshness** — the cache is keyed `(base, owner, universe,
  provider)`, so one source being cold, stale or broken says nothing about any
  other;
- **discovery isolation** — a read is a dict lookup, so the slowest source
  costs nothing. `test_one_wedged_source_does_not_delay_another` pins it with a
  source whose refresh never returns.

Warming happens on connect (`credential_vault._warm_after_deposit`) and then on
a TTL that a read schedules without waiting for it. Execution is untouched:
`prepare_selected_model` still discovers fresh at launch with its own custody
and freshness checks, and an entry past `USABLE_AGE` is withheld rather than
served — the concern's own warning against "enabling stale choices globally".

## What remains, 1: accepted HTTP sources still discover inline

`providers/served_model_plan.py:514` still calls `refresh_model_discovery()`
inline for every accepted `api_key_http:` member on a display read, including
its catalogue HTTP request and an optional benchmark request.
`api/model_options.py:176` does the same for HTTP sources OUTSIDE the accepted
prepared plan. So a slow HTTP source can still delay the entire picker, which
is the original symptom with a different source kind.

The native fix is reusable for this: the cache is keyed per source and holds
whatever discovery returns. What it needs is an HTTP-shaped entry whose
usability is measured against `DiscoverySnapshot`'s own freshness rather than
`NativeDiscoverySnapshot`'s, and a warm hook on definition registration rather
than on credential deposit.

## What remains, 2: the client treats freshness as one global fact

`tinyassets/onboarding/app.html` still models freshness as ONE fact for the
whole catalogue:

- `fresh()` reads a single `this.expires`, set to
  `Math.min(Date.now()+300000, ...expiries)` — the earliest expiry of ANY
  source expires the entire picker;
- `refresh()` sets `this.busy`, which disables every choice while any refresh
  is in flight.

One expired source re-stales the whole list, and a refresh disables choices
that were never affected. Per-source rendering would finish this.

Do not claim this closed by promoting the refresh button. The remaining work is
in the client's freshness model, not in how visible the control is.
