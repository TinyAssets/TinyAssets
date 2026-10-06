## 1. Contract

- [x] 1.1 Read audit, reconcile overlapping lanes and define manifest, authority and U1 contract.
- [x] 1.2 Obtain Claude proposal review and record AGREE/DISAGREE_EVIDENCE dispositions.

## 2. Implementation

- [x] 2.1 Validate complete v2 manifests and package byte revisions with negative tests.
- [x] 2.2 Add owner-bound install/activate/revoke state with generation conflicts and isolation tests.
- [x] 2.3 Expose lifecycle, contribution discovery and connection requirement dispatch through ta.
- [x] 2.4 Dispatch tools/commands/hooks in today's jail with exact bytes and revocation checks.
- [x] 2.5 Project UI/cards through app_ui and preserve scoped workflow provenance.
- [x] 2.6 Integrate package sharing and authenticated installed-agent resolution without exporting grants.
- [ ] 2.7 Consume U1 package cells for stdio/process admission; remote MCP, git and local bindings use today's broker.

## 3. Verification and delivery

- [x] 3.1 Add on-demand guidance, run affected Linux tests/heavy files, Ruff, mirror and hygiene checks.
- [x] 3.2 Open draft PR, obtain Claude implementation review, fold findings, merge main and push verified commits.
- [ ] 3.3 After deployment assert SHA, run real-user app acceptance and sync verified specs.

## Verified slice (2026-10-06)

Draft PR #4519. Linux oracle: 411 passed, zero skips, covering new unit/jail
tests plus extension boundary, ta, prompt costs, universe tools, package sharing,
installed directory and account deletion. New unit tests also pass on Windows
(31). Changed Python Ruff and plugin mirror/import probe pass. Existing prompt
budgets and test assertions are retained. No affected test file is heavy-listed.

Claude implementation review: ADAPT, no floor break; findings and dispositions
are recorded in review-implementation.md and design.md. Recovery/concurrency
fixes: focused Linux oracle 127 passed, zero skips; Windows unit tests 36 passed.
Hygiene after the initial implementation commit: 0 removed / 0 tampering.
Ruff and rebuilt mirror/import probe pass after the review fixes.

Merged origin/main at 22bc0f728e without conflicts. Post-merge Linux oracle
(starter instructions, extension unit/jail and prompt costs): 57 passed, zero
skips. Final hygiene: 15 tests added, 0 removed, 0 tampering. Plugin mirror/import
probe remains clean after merge. PR stays draft; pending runtime tasks above are
not superseded by these verification results.

2.4 currently supports explicit ta tools/commands/hook entries using read-only
per-bash snapshots; automatic turn-event hook launches are not wired. 2.5 is
metadata-only and reports UI runtime unavailable. 2.6 reuses canonical shareable
package envelopes and folds #4515's resolver/tests, but end-to-end active-revision
publication/recipient activation remains unproven. Connection requirement calls
report binding_required and create no grants. MCP declarations are validated and
discoverable but not attached. U1 admission remains unimplemented here.

No deployment, real-user pass or as-built spec-sync claim. The proposal spec is
the intended complete contract, not a claim that all requirements have shipped.


## Remote MCP continuation (2026-10-06)

Ported #4496's streamable-HTTP protocol client onto today's governed connection
effector, retaining consent/rules/review, OAuth custody and endpoint restrictions.
No U1 broker metadata or stdio launcher dependency. Remote contribution dispatch
supports explicit discovery and catalog-hash-pinned calls, with no tool replay.
Replies are bounded by today's effector, including SSE framing; no persistent
server event subscription is claimed. Private activation bindings pin current
local grants and connection incarnations; rebind requires a new generation.

Linux oracle: 207 passed / zero skips (extension state/jail/remote, ta, existing
authenticated effector, account deletion and static prompt costs). Changed-file
Ruff and plugin mirror/import probe pass. Claude continuation review running;
verdict will be recorded before final push. Git/outside authority, automatic
hooks and UI projection remain in progress. No deployment or live proof claim.


Git continuation: #4513 credential-blind smart HTTP is ported to today's
broker/egress with extension-local grant pins. Linux oracle 174 passed / zero
skips, including real jail clone/fetch/binary push and negative transport tests;
one existing broker teardown warning reported, no test skip or weakening.
Claude git review APPROVE; nonblocking pump-scope recommendation incorporated.
Per-owner git_bridge stays with U1; this remote git path does not wait for it.


## Lifecycle continuation (supersedes the earlier slice limitations above)

Remote MCP and git were pushed as verified slices 0366f1e934, 6eaa04328a and
edb93dc1b0. Claude remote review ADAPT was incorporated (explicit refusal instead
of orphaned per-frame approval); git review APPROVE is recorded in review-git.md.

Automatic input/turn_start/context/before_tool/after_tool/turn_end events now use
the signed triggering turn and current jail. Failures and grant-limited skips
are observational evidence available through extension:events. Cards project
immutable HTML/app_ui JSON into the existing app_ui rows; generation checks hide
stale or edited projections. Local bindings pin private grant/connection IDs and
incarnations per activation; discovery and dispatch remain one ta surface.

Outside-client authority includes the verified-claims probe, exact scoped grants,
protected owner grant editing, durable deny switch, family/auth_time reconnect
fences, signed launches, queued run recovery and scheduled/event work provenance.
Claude lifecycle review ADAPT: all five required findings accepted and addressed;
read design.md for dispositions and precise in-flight-effect semantics. A real
AuthKit check is founder-owned in docs/host-actions.md. There is no claim that
production classification or the durable switch is enabled.

Pre-review broader Linux verification: 431 passed / zero skips, including affected
heavy provider_work_authority, universe_server_isolation and branch_runner, run
admission/waiter recovery, OAuth, git jail and governed outbound calls. Earlier
lifecycle Linux check: 108 passed / zero skips. Post-review verification is recorded
in the final continuation entry below. Static prompt budgets are unchanged.

Only stdio/package-cell runtime admission waits on U1. Draft delivery still does
not claim deployment, live acceptance, or as-built spec sync. Source PRs #4496,
#4511, #4513 and #4501 remain open until this PR's folded content is on main, per
the founder's explicit condition; their future close comments must point to #4519.


Post-review Linux oracle: 286 passed / zero skips (hooks invoking ta without
recursion, UI lifecycle, automatic turns, ta, prompt budgets, outside authority,
automations/events, account deletion); 322 passed / zero skips (OAuth, steering,
protected owner sessions, affected heavy authoring/visibility/cycle tests, remote
MCP, credential-blind git and authenticated effects). Ruff passes all 47 changed
Python files; strict OpenSpec validation and rebuilt plugin/import probe pass.
The protected owner grant endpoint regression additionally passes locally.


Merged origin/main a97c17c26e without conflicts (merge c8d0a84296). Post-merge
Linux oracle: 91 passed / zero skips, including main's package/deletion regression
updates and unchanged prompt budgets. Final outside-authority regression run:
11 passed / zero skips, including protected owner grant editing and resolved
indirect-object confinement. Plugin rebuild/import probe and JavaScript syntax
check pass. Final PR head and hygiene are recorded in #4519; no deployment claim.
