# The scheduled DR drill has no isolated identity provider

The C6 restore review found that `.github/workflows/dr-drill.yml` bootstraps an
unfilled environment template and invokes disaster restore without WorkOS
credentials. Real WorkOS owners therefore fail closed before publication.
Adding production credentials would revoke live users' sessions during a drill.

The verified archive pair and exit-6 assertion are wired. A separate drill venue
must supply isolated recorded providers through the harness-only mounted-module
entry point, or an isolated real provider tenant. Never provide production
WorkOS credentials to this drill. The local archive rehearsal already proves
recorded-provider reconciliation, provider-only erasure, and unreachable-provider
exit 4. No workflow was dispatched during this review.
