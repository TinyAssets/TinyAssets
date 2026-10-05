## Context

Read-only CDP snapshot: build 29fa5b4, zero iframes, AppUI disabled, cloud in bubble
mode. fetchMe returns null on 5xx; enterSignedIn treats this as unavailable and
resets AppUI, then shows chat. open/openBrowse cannot open the disabled UI.
This matches the observed state; no retained network log proves the triggering
request. The empty main.js is not imported; the service worker has no fetch handler.

## Decisions

Use a separate nonce-bearing inline recovery script, outside the main script's
parse/boot failure domain. Controls sit outside the collapsible cloud. Recovery
first remounts the current frame, then navigates to a cache-busted app URL with
session-persisted exponential backoff and a retry ceiling. A visible reload link
survives exhaustion. Broken pages bypass live-turn holds. Drafts are restored only
after matching verified owner, home and addressed agent; storage failure prevents
automatic navigation when it would lose a draft.

Redirect stale valid hashes for existing module names with no-store. Current URLs
remain immutable; unknown names and invalid path shapes still 404. This avoids a
persistent archive and works across fresh processes. The early error listener also
covers module errors while old deployments still return 404.

## Risks

Redirected modules can differ from an old caller; build mismatch recovery replaces
the shell. Retry budgets persist across loads and clear only after stable health.
No changes to frame authority, token handling or the adjacent message renderers.

## Verification

Real Chromium against rendered app/CSP: lost frame, exception, 503, stale module
404, version mismatch while sending, draft isolation, retry exhaustion and clicks.
Windows and Linux oracle, affected tests, ruff, mirror and test hygiene.
