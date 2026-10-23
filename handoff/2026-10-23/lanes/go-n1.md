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
