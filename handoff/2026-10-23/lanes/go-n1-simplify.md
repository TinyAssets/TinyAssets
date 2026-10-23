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

SHAPE: read openspec/changes/owner-daemon-in-box/research.md and the "Target shape (binding)" table T1-T10 at the top of
openspec/changes/owner-daemon-in-box/design.md before designing anything. The shared layer (router, broker/egress
proxy, waker) holds no owner business logic and no host path to owner data; owner logic runs in the owner's box;
provider credentials stay in broker custody. Before patching any failing test or finding, ask "can this class exist in
the target shape?" If not, move the work and delete the shared path; never add a guard to shared code. AGENTS.md
Direction: provider-agnostic (no provider-specific code), fewer docs and less process.

HARD CONSTRAINTS: no sub-agents (no scripts/peer_agent.py, no claude/codex subprocess). Work only in the worktree
named above unless this brief says otherwise. No production access, no secrets. Never touch git config. Never use
--no-verify. No full suite: run targeted tests via python scripts/linux_oracle.py -- <files>, plus ruff (and
node --check for JS). No model identifiers in commits or PR text. Commit trailer:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
PR descriptions end with:
Generated with Claude Code - https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
Report: head SHA, what changed, tests run and their result, anything left undone and why.
