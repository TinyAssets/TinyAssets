---
severity: P1
title: The refresh endpoint is trusted from an unverified id_token
filed: '2026-09-26'
summary: the issuer and client are read from an unsigned identity token and the refresh token is then sent there. Transport hardening and single-read resolution are done; the signature check against the issuer's JWKS is not, and it must NOT verify `exp`
---

# Endpoint trust permits exfiltration and bypasses hardened egress

**Filed:** 2026-09-26
**Verified:** 2026-09-26, Windows/Python 3.14, PR #4032 at `776abaaf`
**Severity:** P1

## Source (verbatim)

> Endpoint trust permits exfiltration and bypasses hardened egress.

Source: finding 2 of the Codex refute-review of PR #4032 (head `6a94d242`), recorded as a comment on that PR. The finding is quoted above in full; the review transcript is not kept in the repo.

Code at that commit: `subscription_refresh.py:205-215, 331, 352, 378-440; connection_oauth/transport.py:43-62`.

The PR comment carries the reviewer's verification commands and observed outputs.
Concurrent working-tree fixes were not reviewed; this finding is pinned to the
requested committed head, not a claim about those edits.

## Closure

Bind issuer/client/endpoint to accepted connection provenance, resolve against the locked document, and use hardened OAuth transport for the POST.


## Partially closed on `claude/credential-refresh`

**Verified:** 2026-09-26, Windows/Python 3.14, at the branch head that carries the
fix (not `776abaaf`, which this finding was pinned to). Two of the three legs are closed. The refresh POST goes through the SSRF-hardened broker transport with every carried value declared as a secret (`test_the_refresh_token_is_sent_through_the_hardened_transport`, `test_a_non_https_endpoint_is_refused_before_anything_is_spent`), and the endpoint is now resolved from the document RE-READ under the locks, so a credential can no longer be spent at another issuer's endpoint (`test_the_endpoint_comes_from_the_document_read_under_the_locks`).

STILL OPEN by design, and worth a decision rather than a fix: the issuer still comes from an UNVERIFIED `id_token`. The alternative -- compiling each source's endpoint into the platform -- is what the channel-agnostic ratchet refuses. A signature check against the issuer's published keys would close it without naming any source.

## Scoping the signature check (read-only, 2026-09-26)

Smaller than it looks, and one trap that would make it self-defeating:

- `pyjwt[crypto]` is already a dependency (`pyproject.toml`), and `PyJWKClient` +
  `jwt.decode` were used for exactly this shape in the deleted (never-wired)
  `tinyassets/auth/host_binding.py` (`git log --diff-filter=D -- tinyassets/auth/host_binding.py`).
  Nothing new to add, nothing vendor-specific.
- The JWKS URI belongs to the issuer's own metadata, so it comes from the same
  RFC 8414 / OpenID document the token endpoint does. `connection_oauth.discovery.
  ServerMetadata` does not currently carry `jwks_uri`, so that field has to be
  added there -- a small, vendor-neutral addition, and the right place for it.

**The trap: do NOT verify `exp`.** A stale credential's identity token is very
likely expired, and expiry is precisely the condition under which the refresh is
wanted. Verifying `exp` would make signature checking fail exactly when the
refresh is needed, converting a working credential into a dead one. The check that
is wanted is signature + `iss` only (`options={"verify_exp": False,
"verify_aud": False}`): the question being asked is "did this issuer really mint
this token", not "is this token still valid". The access token's own expiry is
already read separately, as a freshness hint.

Verify each cited symbol before acting -- this note is a snapshot.
