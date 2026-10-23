## Context

N4 is the view hook in ADR-022 P4. The binding target shape in `owner-daemon-in-box/design.md` T1, T4, T5 and T8 puts private browser, desktop and owner policy in the owner's box. `browser-login-custody` owns saved site sessions.

## Goals / Non-Goals

**Goal:** Let the authenticated owner watch and control their own hosted box for password, 2FA and CAPTCHA interruptions through the interface package.

**Non-goals:** Shared screen processing, agent access to private input, a host computer dependency, or a second browser custody path.

## Decisions

1. `BoxProvider.view` opens the view on the admitted owner's box only. The router authenticates, resolves the positive owner-to-box mapping and forwards opaque frames and input. The box authenticates its router channel. Neither router, broker nor waker decodes, records or reads the stream. A shared screenshot service would violate T1/T4.
2. The interface package renders browser and desktop surfaces from the same box-local view primitive. The owner starts and ends takeover; the box gates agent interaction while private input is active. Credentials and challenge responses entered through the view are delivered solely to that box's focused target; the box keeps them out of agent output, transcripts and stream echoes. Existing broker custody of provider credentials remains unchanged.
3. Capture lifetime, focus and input authorization live in the box. Reconnect requires a current authenticated owner session and fresh box admission. A stopped box may be woken through normal lifecycle routing. No owner content path or capture state is added to shared storage.

## Risks / Trade-offs

- Remote sites and device authenticators can refuse streamed interaction; the view reports that limit instead of claiming completion.
- A broken stream can strand a takeover; box-local teardown releases input control and requires fresh authentication before reconnect.
