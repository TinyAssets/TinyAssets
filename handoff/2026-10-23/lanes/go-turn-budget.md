LANE: free-model turns always end with a reply or a clear stop. Worktree C:\Users\Jonathan\Projects\wf-turn-budget,
branch conv/turn-reply-budget (from conv/source-records 9ab39b4b886; merges back later). Do NOT edit
providers/served_model_plan.py, broker/*, onboarding/app/main.js, onboarding/source_connect.py,
api/connection_uses.py, api/model_access_requests.py, providers/subscription_*.py (other lanes own them).

Observed live on a free OpenRouter source: a turn made 10 tool calls over 6 provider attempts, then stopped with
"This turn stopped at its request budget (6 provider attempts)… You can request a continuation" and no reply text for
the user. Another turn's first two attempts both failed on models that could not serve (consecutive_failures=2), so
it stopped after 2 attempts with no reply. See tinyassets/request_budget.py (FREE_TURN_ATTEMPTS,
FREE_CONSECUTIVE_FAILURES, TurnRequestBudget) and tinyassets/agent_turn_coordinator.py (_next_after_request_budget).

Fix in the box's loop (T8, no shared code): reserve the last attempt of a turn's budget for a final reply with tools
disabled, so a turn that used its tool steps still answers in words what it did and what is left; and a model that
fails before reaching the provider (not a provider refusal of the request) counts against that model only, so the
turn moves to the next connected candidate instead of stopping. Keep caps and spending rules unchanged; never widen
to paid models. Tests: a turn whose tool steps exhaust the budget ends with a text reply; two consecutive model
failures move to a third candidate when one exists; with none left, the stop message names what failed.

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
