# Cross-family review trail — channel-agnostic-outbound

Opposite-provider (Codex) gate per AGENTS.md. Two layers were reviewed: the DESIGN,
then the slice-1 driver CODE.

## Design — VERDICT: approve (after one adapt)
Round 1 adapt (6 items): implementable SSRF contract in the delta spec; delete
in-process "Stage A" and reuse the existing spawned credential-blind broker; resolve
under authenticated-actor+universe+active-grant and hide `credential_ref`; correct the
`outbound-boundary-layer` ownership (it is landed, this change CONSUMES it); Twitter
atomic cutover (no env fallback); semantic-equivalence matrix + Slack/GitHub backfill.
Round 2 approve — all six folded in.

## Slice-1 driver code — VERDICT: approve (after reject→reject→adapt→…→approve)
The SSRF-hardened, credential-blind HTTP `_network_request` driver (dark; nothing routes
through it). Across the build-agent's 4 rounds + 2 independent adversarial passes,
**12 real vulnerabilities were found and fixed, each with a reproducing test**:
base64 Basic-auth echo bypass; caller Content-Length smuggling; custom-auth header-name
bypass; obs-fold CR/LF injection; IPv6 site-local/NAT64/6to4/Teredo misclassification;
secret-bearing `__context__`; SSLKEYLOGFILE TLS-key leak; slowloris (no total deadline)
in header/body/trailer parse; encoded dot-segment allowlist bypass; and the pre-handshake
TCP-connect + TLS-handshake escaping the deadline. Final independent verdict (head
`af3afa68`): **approve — no remaining exploit path in this driver; 70 tests pass, ruff
clean, mirror byte-identical.**

## Documented residuals owed BEFORE activation (not blockers for the dark slice)
1. The response substring scrub is best-effort; the per-connection **endpoint allowlist
   + fixed destination-specific response projections** are the real confidentiality
   boundary (next slice: connection descriptor + allowlist).
2. Org-specific NAT64 prefixes need deployment-aware rejection / egress firewall.
3. DNS resolution is outside the total deadline (getaddrinfo is a blocking OS call);
   a **threaded resolver with its own timeout** closes it at activation.

Next slice (un-darks the driver): connection descriptor + endpoint allowlist, then the
Slack-first migration — which also routes Slice 3's follow-up delivery through the
broker's receipt lifecycle (closing Slice 3 #4/#5).
