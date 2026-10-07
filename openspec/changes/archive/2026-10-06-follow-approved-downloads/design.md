# Approved download redirects: pre-build proposal

September15 follow-up: [legacy connection identity repair](legacy-incarnation-repair.md)
records a newly reproduced request-creation blocker and its pre-code migration
design. The original transport contract below is unchanged.

September 11, 2026. This is a focused addition to D3, not a new channel or MCP
handle. No implementation or live permission change is claimed. The existing
app agent cannot read an approved CI job log because the general HTTP driver
returns302 without following it. Its ordinary workflow checklist now passes;
that does not exercise this separate download. The feedback PR remains the
agent's work, not the maintainer's replacement implementation.

## Outcome and boundary

An owner can authorize an existing read endpoint to follow a bounded chain of
public HTTPS redirects for text downloads. This retains the existing bounded
UTF-8 decoding with replacement, not a new lossless binary-download contract.
The agent then reads the returned body using
its existing HTTP effect and code-node composition. No new log/tail/grep tool,
GitHub-specific transport, workflow repair, key deposit, or ambient credential.

Connection owners choose this additional authority through the existing request
rail and connection extension. Earlier approvals, including full-channel access,
remain no-follow unless the new permission is explicitly approved. The platform
does not click this approval on the owner's behalf.

The proposed representation is an optional endpoint property `redirect_mode`,
strictly `none` (default) or `public_https_get`. It belongs to the existing
OutboundEndpoint/allowed_endpoints_json contract, not a caller-controlled request
flag. The enabled mode is valid only on GET-only endpoint declarations. The
existing endpoint's host/path/query rules still gate the initial request. A
matching opted-in endpoint grants follow-up GETs to any validated public HTTPS
destination returned by that endpoint, not direct requests to arbitrary hosts.
This is explicitly broader download egress, not a hidden reinterpretation of
the initial host allowlist. It neither adds authenticated destination hosts nor
authorizes POST/PUT/PATCH/DELETE redirects.

Why this mode: download origins and signed URLs can change per response. A
platform-maintained CDN allowlist or one approval for each signed URL would not
provide channel-agnostic capability. The owner instead approves the general
bounded, credential-isolated download permission for a source endpoint they
already authorized. Staying in `none` retains the exact existing behavior.

## Permission propagation and owner experience

Reuse write_graph connection operations connect_http/extend_http and request
action types of the same names. check_primitive_exists action extend_http does
not recognize this operation-style routing; source api/http_connection.py and
api/pending_requests.py establish the actual existing surface. No new action is
justified by that diagnostic false-negative.

The shared endpoint parser validates and projects the new property. Canonical
policy equality, endpoint union/deduplication, request normalization, policy
preview, answer snapshot, consent identity and any authority digest all include
it. Omitted/none are semantically identical. A true permission addition must
not be normalized away, called unchanged, or satisfied by an earlier approved
request with the same title. Preserve all existing endpoints, scopes, credential
reference and connection incarnation. Reordering endpoints cannot change consent.

In particular api/http_connection._extend_preview currently short-circuits full
access as unchanged. Full access covers direct actions on declared hosts, not
this new download authority: a same-host endpoint redirect addition must receive
its own preview/approval even on a full connection. Adding an authenticated host
to a full connection remains outside this slice. Runtime redirect activation
matches the opted-in source endpoint, not any request admitted by full mode.

The redirect-only extension preserves the stored access mode and scopes instead
of deriving reduced scopes from the added GET declaration. On a full connection,
reject any new host and persist the approved endpoint permission rather than
taking the existing mode-only write path. Source path/query matching for redirect
opt-in is independent of full-mode host admission.

The owner-facing preview must explicitly say that approved GETs from the shown
source endpoint may follow public HTTPS redirects, without sharing the key with
another origin. This sentence is generated from validated policy, not supplied
by the agent. The existing details reveal the same permission. The owner can
decline; default behavior is unchanged. Extend owner-time snapshot fencing to
redirect asks in BOTH exact and full modes; it is not currently general. Include
endpoints, scopes, access mode and connection incarnation in the displayed
snapshot and compare all of them at the write. The existing endpoint-only CAS
is insufficient. Old pending requests with no such snapshot cannot authorize
redirects, even when a replacement connection has identical endpoint JSON.
No auth capability changes until the person's existing approval path completes.

## Transport

All hops execute inside the existing credential-blind broker. Keep the current
one-request pinned transport as the leaf. The driver owns the redirect loop;
do not install urllib's automatic redirect handler or HTTPErrorProcessor.

1. Validate initial method, canonical URL and current endpoint authority before
   any socket. GET with a body never enters redirect mode. Requests without a
   matching approved mode retain byte/behavior compatibility with the old path.
2. Follow only301/302/303/307/308 responses to an opted-in bodyless GET, always as
   bodyless GET. Missing/duplicate/invalid Location, loops or an exhausted budget
   refuse with a fixed reason. Never replay a mutating request after a redirect.
3. Resolve permitted relative references against the immediately preceding URL,
   then perform the complete D3 canonical URL, port, global-address, DNS pinning,
   peer and TLS checks before each new socket. Reject controls, userinfo,
   fragments, backslashes and forbidden encodings; do not use urljoin as the
   security validator. Keep proxy/environment handling disabled.
4. Never forward request headers wholesale. Redirect requests use fixed safe
   accept/user-agent values; no Referer, cookies, caller-supplied headers or body.
   Auth may be regenerated only while the chain has never crossed origins AND
   the next URL is on the initial origin AND independently authorized by the
   original connection's current endpoint/access-mode rules. This preserves
   legitimate same-origin authenticated redirects without promoting an allowed
   source path into arbitrary credential-bearing paths. Once the chain crosses
   origin, every subsequent hop is anonymous, even if it returns to the original
   origin. OAuth signing, if needed, occurs anew in the child for the exact URL.
5. Refuse a redirect target that contains any raw/resolved auth secret before
   opening that target. Extend the existing bundle/auth-material declassification
   to every intermediate response and accumulate auth material from every
   re-signing. Preserve bounded raw Location multiplicity in the leaf; a headers
   dictionary cannot detect duplicates. Track issued redirect capability material
   inside the child and refuse echoes in final body or server-controlled reason,
   not merely strip Location headers. Signed download capabilities are allowed in
   the child only; Location values, final URL, query/path and response cookies
   must not enter caller evidence, audit events, errors or stored receipts.
6. Establish one chain deadline before initial DNS. Pass remaining time to the
   resolver and leaf transport, replacing the leaf's fresh-per-call deadline for
   this path. Intermediate bodies consume the aggregate body budget too. Per-hop
   header bounds remain; maximum five redirects. Timed-out resolver threads are
   presently abandoned within the child and must remain contained by broker
   process teardown; do not claim instantaneous thread cancellation.
   Add a trusted child-local revalidation hook from the broker to the network
   driver. Before each further socket, including after DNS, compare active grant,
   connection incarnation and exact approved policy with the initial authority.
   Observed revocation/change prevents that next hop; this is not atomic
   revocation and cannot retroactively cancel bytes already sent.
7. Return the final status/body through the existing declassified effect result.
   A fixed redirect count/reason may be exposed, but no Location-derived data.
   Preserve the existing dispatch/effect receipt and ambiguous-outcome semantics;
   this is one admitted effect, not an automatic retry of a failed effect.

The initial no-follow302 may include a fixed actionable explanation: the owner
can enable redirected public HTTPS downloads for this source endpoint. It must
not disclose the signed destination or pretend that unknown additional access
is already granted. No new response-header reference language is added.

## Code seams and proof

Reverified source on September11: storage/outbound_connections.py has the same
content on this checkout and origin/main. Relevant seams are OutboundEndpoint,
_validate_endpoint, _parse_allowed_endpoints, CredentialBlindBroker.dispatch,
_TrustedNetworkDriver, _SsrfHardenedHttpDriver and _execute_pinned_https_request.
api/http_connection.py owns canonical policy, extension preview and CAS write;
api/pending_requests.py owns endpoint request normalization and approval.
effectors/authenticated_external_call.py owns bounded evidence. Existing
EffectChain.effects_view already passes the full body, not the4096-character
preview, to downstream sandbox code. Do not add another processing primitive.

Before runtime code: independent cross-family shape/basic-safety review of this
proposal. Resolve concrete required corrections, then implement in an isolated
branch without changing model PR3832's reviewed head. Reuse this change's existing
authority design; do not add another oversized task list or call this an uptime
emergency to bypass gates.

Tests must prove default no-follow, exact initial authority, full-mode non-opt-in,
explicit request/preview/answer and old-consent non-reuse; updated canonical
identity and stale/incarnation refusal; multiple signed/relative hops; full
per-hop SSRF/TLS and same-host DNS rebinding checks; stripped headers, no auth
cross-origin/back-to-origin; refused secret-bearing Location; no POST replay;
hop/deadline/aggregate-size exhaustion; revocation between hops; no URL leakage;
and final full-body availability in the existing downstream code-node path.
Run actual Linux for socket/lifecycle semantics, plus relevant Windows tests.

After independent implementation review, passing CI and protected deployment/
canary proof, ask the app naturally whether it can finish diagnosing its feedback
check or identify what stops it. Let it compose its own workflow and ask for any
required redirect permission through its normal surface. The maintainer does not
approve the new authority or fetch its logs. Both supported chatbot clients must
accept any changed public request/result shape. Final acceptance is the rendered
agent succeeding with approved redirects, not a green transport unit test alone.

Rollback readers ignore the unknown property and remain no-follow, but older
writers reserialize known fields and can erase it. Support fail-closed loss of
redirect permission with fresh approval required after upgrade, not a promise of
JSON preservation. Bind approval to a rendering that actually displayed the new
authority; an old open tab cannot approve a silently reinterpreted request.
Prove this contract against actual old readers/writers. No permission is
auto-enabled on upgrade or rollback.

### Implemented approval binding

The request layer adds `redirect_consent_version: 1` only after generating the
validated redirect disclosure. Action normalization drops caller-provided
markers. The marker is part of the existing stored consent identity; answering
a legacy unversioned redirect request refuses before deposit/extension. Full
and exact extensions also carry the four-field owner-time policy snapshot from
one SQLite row read. Endpoint extension compares that policy at write time and
requires the original grant still active and the connection unrevoked. These
are implementation details of the explicit-disclosure/fencing contract above,
not automatic owner approval or a new tool.

## Independent shape disposition

September11: independent fallback reviewer completed in150s, VERDICT ADAPT.
All six required corrections are incorporated above: full-mode permission
preservation, exact/full owner-time incarnation fencing, explicit child-local
revalidation, Location/capability declassification, shared DNS/transport deadline,
and fail-closed old-writer rollback. Text-only scope clarification is accepted.
Full review: output/approved-download-redirect-shape-codex-review.md; fallback
authority evidence: docs/reviews/2026-09-11-review-provider-limit.md. No runtime
implementation, permission change or deployment is claimed by this disposition.
