## 1. Permission contract

- [x] 1.1 Add validated GET-only redirect policy with omitted/none compatibility; prove parse, projection, canonical equality and old-reader/writer fail-closed behavior.
- [x] 1.2 Carry exact redirect permission through connection extension, owner-generated preview and approval; prove full-mode preservation, consent deduplication and incarnation/mode/policy snapshot fencing.

## 2. Broker execution

- [x] 2.1 Add bounded child-only redirect metadata and shared DNS/transport deadline plumbing without changing no-follow behavior; differential-test the legacy path.
- [x] 2.2 Implement approved redirect chains, per-hop authority/network rechecks, anonymous cross-origin behavior and sensitive-material declassification; prove all design refusal and budget cases.
- [x] 2.3 Verify the real broker/effect/code-node composition returns usable bounded text with no redirect-capability leaks, duplicate effects or private-workflow changes.

## 3. Delivery and acceptance

September15 live follow-up: task3.2 remains OPEN. The app cannot create its
redirect permission request for a legacy blank connection incarnation. Matching
exact/full failures reproduced; pre-code repair design is
[legacy-incarnation-repair.md](legacy-incarnation-repair.md). Existing passing
transport evidence below does not close this live approval blocker.

- [x] 3.1 Run focused Windows and actual Linux tests plus independent exact-head implementation review; address concrete findings and pass CI without weakening gates.
- [ ] 3.2 Deploy, verify protected SHA/canary evidence, and obtain rendered app proof of owner-approved redirected download capability through ordinary conversation; keep both-client compatibility and owner approval boundaries.
- [ ] 3.3 Sync only shipped requirements, archive this delivery and update the broader goal without declaring unrelated channel migration complete.

The source design received independent ADAPT150s; all six required corrections
were incorporated before this delivery extraction. This is the approved-download
capability's first implementation lane, not a fourth model-selection review.
The model review exception remains pending. The broader parent28-task inventory
is preserved rather than mechanically split or relabelled complete.

## Local evidence — September 11, 2026 20:53 UTC

Windows working tree: `python -m pytest -q tests/test_http_redirect_policy.py
tests/test_pending_requests.py tests/test_http_connection_provisioning.py
tests/test_full_channel_access.py tests/test_outbound_http_connection.py
tests/test_outbound_ssrf_driver.py --tb=short` passed313 tests in25.47s,
no skips. Focused ruff checks passed. Plugin mirror build passed its import
probe; strict OpenSpec validation and delivery admission passed.

The30 new policy/request tests cover strict GET-only parsing, omitted/none
identity, explicit permission projection, request normalization/generated
disclosure and rollback loss without changing exact/full mode. The frozen
class, parser and SQL writer match deployed cd28c3806c44ff63612adb8fcc709b622f89a6fd
by AST comparison; five shared leaf validators are unchanged. This is frozen
policy-reader/writer evidence, not old-binary network execution proof.

Task1.2 was incomplete at this checkpoint; the newer evidence below supersedes
that local implementation status. No live capability was established here.

## Approval evidence — September 11, 2026 21:04 UTC

The same Windows command plus `tests/test_http_redirect_approval.py` and
`tests/test_workspace_authority.py` passed409 tests in31.60s, no skips.
The34 new real temporary owner/request/ledger tests began with13 failing cases,
then prove full/exact approval, preserved scopes/mode/credential reference,
changed owner-time policy, changes immediately before SQL write, connection and
grant revocation, missing/partial snapshots, old unversioned requests, equivalent
reordered requests, non-reuse of old no-follow approval and new-key setup.

Redirect approval takes one resource/policy row snapshot and writes under
endpoint/scope/mode/incarnation checks plus active grant and unrevoked connection
checks. Generated request disclosure has a server-owned version marker in the
existing consent identity. A legacy row cannot authorize it after upgrade.
The tool description now explains how users can request this permission;
the existing app renders the server's grant_sentence as text.

Focused ruff, strict spec validation, mirror build/import and diff checks pass.
Task1.2's local approval implementation is complete, not live acceptance.
Transport remains unimplemented. Actual Linux, independent implementation review,
CI, deployment and rendered owner-approved downloads are still required. Do not
ship the permission-only foundation or claim its tests prove network behavior.

## Transport foundation — September11,2026

Task2.1 adds an optional absolute deadline to the existing pinned leaf and a
non-representing metadata object holding bounded raw Location multiplicity and
actual body-byte count. Neither is added to returned result fields. The new
redirect DNS helper uses remaining chain time, validates every address and
checks time again after resolution. These seams are not yet a redirect loop.

Windows `python -m pytest -q tests/test_http_redirect_transport.py
tests/test_outbound_ssrf_driver.py --tb=short --show-capture=no` passed125 tests
in20.44s, no skips. The22 new cases cover frozen-leaf differential responses
(including UTF8 replacement and redirects), differential bound refusals,
duplicate Location metadata, already-expired and reduced shared deadlines,
bounded initial DNS and mixed public/private DNS refusal. The frozen leaf
matches b7cd2a25 by AST; six shared pinned transport helpers remain unchanged.
Ruff, mirror/import, strict spec and diff checks pass.

This does not prove a functioning redirect chain or production TLS against a
remote server. Local socket fixtures retain their explicitly injected
passthrough TLS and loopback address handling. Task2.2 must integrate these seams
with child-local authority rechecks, per-hop credentials and declassification;
task2.3 must prove broker/effect/code composition. Linux and live proof remain.

At21:15UTC the full current Windows slice passed431 tests in41.43s, no skips:
`python -m pytest -q tests/test_http_redirect_policy.py
tests/test_http_redirect_approval.py tests/test_http_redirect_transport.py
tests/test_pending_requests.py tests/test_http_connection_provisioning.py
tests/test_full_channel_access.py tests/test_outbound_http_connection.py
tests/test_outbound_ssrf_driver.py tests/test_workspace_authority.py
--tb=short --show-capture=no`. No test process remains running from this check.

## Chain and composition evidence — September11,2026 21:44UTC

The working implementation now follows approved GET redirects through the
existing pinned leaf. A trusted child-local callback compares current active
grant/resource snapshots before DNS and immediately before sockets. One deadline
includes DNS, transport and authority database waits. Auth is regenerated only
for independently authorized same-origin targets, and remains stripped after
crossing origin. Intermediate bodies consume the aggregate allowance.

Windows `python -m pytest -q tests/test_http_redirect_chain.py
tests/test_http_redirect_composition.py --tb=short --show-capture=no` passed52
tests in30.29s. These include50 real local-socket cases and two real spawned
broker/IPC/vault/effect/compiled-graph/code-node cases. The latter prove full
body processing beyond4096characters, one effect/dispatch across two hops,
safe rejection of a reflected signed token, declassified error audit/evidence,
and child teardown. Only child network/TLS are synthetic loopback fixtures.
No private workflow or production data is used. The generic GET effector has
effect-chain evidence, not a separate durable external-write receipt; do not
claim this test proves a nonexistent receipt path or binary fidelity.

An earlier broader run had583passes/1failure in94.46s: the new fixture did not
consume GET request bodies and could parse those bytes as another request,
resetting the socket. The fixture now consumes declared bodies; the52-case
rerun above passes. Full rerun remains required. Ruff, mirror/import and strict
OpenSpec checks pass. Actual Linux and review/deployment/live acceptance remain.

Task2.2 stays open for basic-safety disposition: the current capability scanner
tracks whole Location/URL/path/query, query pairs, decoded forms and individual
query values of at least16characters. That cutoff is NOT proof that shorter
values cannot be capabilities. Independent review must resolve whether this
meets the declared confidentiality contract or needs correction; do not quietly
weaken that contract or call arbitrary encoding/partial reflection protected.

## First independent implementation review and corrections — September11

Claude/Opus approved exact8bc0175b after450s, with no pre-live blockers, then
independently ran50 chain tests (26.18s). The final wrapper output omitted its
earlier full review; it was recovered verbatim from this dispatched review's
own transcript. Durable artifact in the lead checkout:
docs/reviews/2026-09-11-approved-download-implementation-opus.md.
This is the download review, not another model-selection review.

The reviewer accepted the cutoff for conventional long opaque capabilities
versus short control values. That is not a guarantee against arbitrary short
bare-token or adversarially transformed reflections. Complete URLs/paths/query
pairs remain tracked at any length; actual credentials/authenticators remain
tracked at any length. Task2.2's implementation disposition is now complete.

Two suggested improvements were taken: C1 scans long path segments as well as
query values, and C3 propagates authority revocation raised at the actual dial
checkpoint. Four new cases first failed on8bc0175b and now pass. Windows
`python -m pytest -q tests/test_http_redirect_chain.py
tests/test_http_redirect_transport.py tests/test_http_redirect_composition.py
--tb=short --show-capture=no` passed78 in42.91s. Mirror/import passes.
The changed head requires a focused exact-head review before readiness.

Non-blocking post-live follow-ups from the reviewer: C2 distinct fixed reason
for capability-echo refusal; C4 whether mid-chain grant action-cap changes must
be observed beyond this one admitted effect; C5 avoid unused body encoding on
the bodyless redirect branch. No new permission or user workflow work is implied.

Draft PR3837 was opened on8bc0175b for existing Ubuntu3.11 CI. Local Docker
startup failed on a stale inaccessible runtime reparse point; a preserving
rename also refused without modifying it. No reset, data removal or settings
change. The changed runtime paths are HTTP transport/permission handling, not
sandbox, filesystem helpers, process limits or workspace execution. Existing
Linux CI is the available Linux verification path; inspect actual targeted
coverage, never report the unavailable local oracle as passing.

## Release checks and merge — September11,2026 22:28UTC

Exact4e2a0bf3 independently APPROVE275s,54chain cases independently passed.
Windows full group588passed91.50s. Actual Ubuntu3.11 CI34651881696 passed:
JUnit proves142redirect cases, zero failures/errors/skips; whole-suite aggregator
reports zero new failures against the unchanged baseline. Slow tests and builds
pass. Windows installer first timed out (preserved evidence), then the identical
artifact retry103441996512 passed46s with install/health/repair/uninstall logs.
All signing checks pass. No code/deadline/check change to obtain that result.

PR3837 merged as e83ded5839a29bbab8cd26681e2a5b97c9a2882d at22:27:43UTC.
Image build34654104718 running; deployment and rendered acceptance remain open.
Main-spec sync describes the implemented echo-scanner bounds explicitly; it
does not convert bounded reflection detection into a universal guarantee.
