Lead approval of the N1 inbound-door spec (spec only, no product code).

- Review 1 (cross-family, against owner-daemon-in-box T1-T10): DISAGREE_EVIDENCE (routing left open; 404 vs maintenance; T1 broker exception; legacy webhooks; one-route test). Fixed.
- Founder decision 2026-10-23: one shared tap-to-install app per channel; the door reads only the installation identity.
- Review 2: three cross-owner HIGHs (shared-app verification data writable by a box; Teams identity unauthenticated; WhatsApp batched entries) plus mediums. Third round, so simplified per AGENTS.md loop 7: Slack shared app with fixed extraction after HMAC; WhatsApp only with per-account callbacks (gated on fixtures); Teams deferred; general selector deleted.
- Review 3 (narrow, xhigh): HIGH 1-3 resolved; MEDIUM 4, 5, 7 resolved; remaining MEDIUM availability gap and LOW scoping fixed in 00c1c734619 (per-installation breaker, owner-scoped retirement and snapshot views, per-channel fixture gates). Checked by the lead.
- Validation: strict OpenSpec, targeted Linux oracle (38 passed), ruff.
