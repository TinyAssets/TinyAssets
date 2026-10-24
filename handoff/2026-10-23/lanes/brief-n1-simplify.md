SIMPLIFY ROUND (third review round, so per AGENTS.md loop 7: simplify, don't patch) on the N1 inbound-door spec,
PR #5228 (worktree C:\Users\Jonathan\Projects\wf-n1-spec). Review:
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-rereview-n1b.md

Keep the founder decision (2026-10-23: tap-to-install shared TinyAssets apps, no owner-made developer apps), but take
ONLY the safe shapes into N1 and delete the general selector machinery:
- Slack: one shared app. Slack signs the whole raw body with the platform signing secret, so after HMAC verification
  the installation identity is authenticated. Identity = the provider's installation id (enterprise_id for an
  org-wide Enterprise Grid install, otherwise team_id), read with a fixed, strict extraction declared as data (object
  keys only, string compared byte-for-byte, duplicate keys rejected, size/depth caps, parse only after HMAC). Events
  that authorize more than one installation (Slack Connect authorizations[]) and unsupported body encodings are
  refused or acknowledged as no-ops; decide which, considering Slack's disable-on-failures rule.
- WhatsApp: shared app with embedded signup, but each owner's business account gets its own callback URL (per-binding
  route through the provider's per-account callback override), so the door reads NO body field. If the override is not
  available, WhatsApp is out of N1.
- Teams: out of N1 (recorded as a follow-up) until a fixture proves an authenticated per-installation identity and
  installation-scoped outbound credentials.
- Generic webhooks: per-binding URLs as already specified.
Resolve the rest: shared-app profiles have one writer, the platform installation authority (never a box); the
identity comes from the provider's server-side token exchange, OAuth state bound to the starting owner and checked in
the same session; conflicting installs check the existing credential with the provider and retire revoked records,
erasure removes them, a refused second install never revokes provider tokens; app-level setup challenges (Slack
url_verification, Meta verification GET) are a one-time platform setup outside the door; unmapped identities on a
shared endpoint are acknowledged as no-ops so one owner's dead install cannot disable the app for everyone; name the
stripped proof headers; forwarded revision is the installation record's; uninstall reconciliation happens when the
box wakes (no shared retry queue); INBOUND_VERIFY only from the router, profile resolved broker-side from the binding.
Delete text made unnecessary by the simplification. Push to the same branch. Keep it short.
