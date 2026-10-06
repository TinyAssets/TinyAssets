# Reviewed-PR guard follow-ups

Lane: `chore/guard-followups`. Reconciled against main at `a97c17c26e`.
Existing fixes are retained rather than duplicated or reverted.

1. Added `tests/test_real_browser_import_guard.py`: scan all test modules and conftest files
   without importing them, rejecting collection-time Playwright imports,
   including conditional imports and class bodies. Fixtures and test functions
   may import Playwright when executed. Synthetic regression cases exercise
   both rejected and permitted shapes. This protects collection in the
   Playwright-free slow-tests merge-group venue (#4505 / #4506).
2. Already fixed: `tests/test_command_center_packages.py:380` changes only
   safety findings and asserts that the install-plan digest changes.
   `tinyassets/api/package_requests.py:191` includes safety in the digest.
3. Already fixed: `tests/test_pending_requests.py:283` derives accepted action
   kinds from the validator and system request creators and requires exhaustive,
   disjoint consent classification. The unknown-action test at line 382 proves
   fail-closed behavior. `tinyassets/api/pending_requests.py:2797` protects every
   action outside the explicit non-consent allow-list, including new kinds.
4. #4502 already replaced the 300-character server cap with an 8192-character
   shape bound. Retain that existing bound and its rejection tests. Removed the
   remaining 120-character display truncation and hidden query/fragment in the
   request card. New tests send 430- and 8192-character URLs through validation
   and the shipped renderer, asserting full anchor destination and visible text,
   with wrapping CSS. Existing unsafe-link checks are retained.
5. No global provider-name cooldown remains: `tinyassets/providers/quota.py:54`
   keys writes by `(owner, provider)`; `tinyassets/providers/router.py:901`
   derives ownership from admitted authority, and line 548 passes it to quota.
   Cooldown reasons at line 551 also use owner/provider. Existing
   `tests/test_provider_quota_scope.py` proves owner A's rate limit cannot block
   owner B, including deferred cooling and expiry. No scope rewrite is needed.
6. #4500 already corrected the connection catalogue. Added the correction to
   the companion Muse/pi gap audit too: no LinkedIn/X/TikTok/YouTube/Reddit
   directory connectors; socials use remote MCP, MCP attach is parity-critical,
   and browser support is secondary. This records the founder's supplied
   correction, not a new independent external research claim.

Claude review: ADAPT. AGREE with widening the collection guard to unmarked tests
and conftest files, because pytest collects those before marker deselection too;
added unmarked regression coverage. DISAGREE_EVIDENCE with adding a Node-missing
skip: the user requires no new skips, and the Linux oracle supplies Node. The
new renderer test instead fails explicitly when its required tool is missing.

Verification and final Claude review results are recorded in the draft PR. This lane
requests draft delivery only; it does not claim deployment or a live-user pass.
