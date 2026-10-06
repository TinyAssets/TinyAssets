## Context

Publication receipts currently have no share URL. Published immutable agent definitions already supply catalogue metadata and the trusted app has tryPackage plus an install approval sheet. Agent-facing conversation attention counts the opposite direction.

## Goals / Non-Goals

Enable public discovery-to-install and cross-device owner unread state. Do not execute listings on preview or change install consent or agent-facing read receipts.

## Decisions

- Use `/app/run/<definition>` under the existing public app edge route; this avoids a new DNS/Worker routing dependency. Exempt only this exact listing route shape from anonymous auth challenge.
- Resolve only immutable published definitions, never private UI or universe state. Preview images are derived solely from published components, with an explicit unavailable state if rendering fails.
- Carry the listing ID through sign-in using app query state, then invoke the existing tryPackage approval flow for the authenticated owner's home.
- Store exact viewed message/request IDs under the authenticated account and universe. Counts exclude owner messages and completed asks. Viewing never approves an ask. Server receipts provide cross-device state; polling and visibility checks keep the UI current.
- Counts cover all agent threads in the current home and the authoritative pending-request projection, including derived setup/reconnect asks. The additive `owner_view_receipts` table lives in the shared account database and participates in schema-derived account deletion.
- Cache public-component screenshots by immutable definition ID. Older screens use the same isolated renderer lazily; listings without a screen show a workflow illustration. Renderer unavailability remains a 503, never a fabricated screenshot.
- Exact release installs bypass the discovery shortlist but still use the normal capture, validation and approval pin. New accounts need no connected model to request an install.

## Risks / Trade-offs

Untrusted listing content must be HTML escaped; preview rendering uses the existing sandbox. Late arrivals must remain unread by acknowledging exact IDs, not a current-time watermark. Hidden pages must not acknowledge. Existing historical messages count until viewed.

## Migration Plan

Additive receipt table created on first access. Rollback leaves receipts inert. Draft PR records outstanding deployment and live proof.
