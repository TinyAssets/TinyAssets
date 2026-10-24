SPEC LANE N1: one inbound door. Worktree C:\Users\Jonathan\Projects\wf-n1-spec, branch spec/n1-inbound-door (from main).
Spec only: an openspec change (proposal, design, spec delta, tasks) — no product code. Open a PR when done.

The Shape registry row N1 in docs/architecture.md: "One inbound door (Slack, Teams, WhatsApp, the owner's email via
their connected account (no platform mail domain), webhooks)"; one path: "/hooks/inbound turns each verified external
event into a session.start input under the owner's grants; channels are connector packages"; guard "inbound-door
(planned): one ingress route, every event lands as session.start". Frontier baseline issue #4624 has the founder's
reasoning. Inbound-webhook trigger work was folded into wake-triggers by C2: read openspec/changes/ for the existing
wake-trigger and automation-event changes and build on them; do not create a second path.

The design must place every piece by the target shape: the shared door only verifies the event's signature or token
and maps it to one owner's box (a positive mapping, T5) and then wakes and forwards; parsing, routing to an agent,
channel logic and replies run in the box as connector packages (T8); channel credentials (bot tokens, OAuth) are in
broker custody and outbound replies go through the egress proxy with credentials injected (T6); the waker is
unchanged (T7). Name exactly which shared code it adds (should be tiny) and which existing paths it deletes. Cover:
event verification per channel as data (no provider-specific code in shared layers), replay/duplicate delivery
(idempotent, at least once), an event for a sleeping box, a disconnected or unadmitted owner, rate limits, the owner's
email through their connected account, and the tests that prove "one ingress route, every event lands as
session.start". Keep it short: decisions, not essays.
