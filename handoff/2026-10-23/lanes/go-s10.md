LANE: S10, platform state outside boxes (registry row S10 in docs/architecture.md: second path "daemon_server.py 5261,
27 CREATE TABLE"; guards test_storage_registry(_complete), test_owner_stores; removed by C6). Worktree
C:\Users\Jonathan\Projects\wf-s10-tables, branch conv/s10-platform-state-out (from main eb582d82d08).
After the C6 cutover the daemon (now the router plus owner daemon in the box) should hold no ad-hoc platform tables:
owner state lives in the owner's box stores, shared platform state in storage/ stores with one owner per fact
(design.md T2, D8 "shared stores stay shared, owner stores move"). Inventory the 27 CREATE TABLE sites in
daemon_server.py, move each to its one home (owner store in the box, or a storage/ store), delete dead ones, and make
the registry guard read zero. Use the generic layout reconciliation already on main (sqlite_schema.py ensure_table with
table lifetimes). No data migration may drop rows: if a table holds live data, the move carries it through the
existing one-shot migration mechanism and says so. Open a draft PR.

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
