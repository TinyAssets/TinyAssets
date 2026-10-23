## Context

N4 is the view hook in ADR-022 P4. The binding target shape in `owner-daemon-in-box/design.md` T1, T4, T5 and T8 puts private browser, desktop and owner policy in the owner's box. `browser-login-custody` already owns the protected live owner view and saved site sessions.

## Goals / Non-Goals

**Goal:** Let the authenticated owner watch and control their own hosted box for password, 2FA and CAPTCHA interruptions through the interface package.

**Non-goals:** Shared screen processing, agent access to private input, a host computer dependency, or a second view or custody path.

## Decisions

1. `BoxProvider.view` opens the view on the admitted owner's box only. The router authenticates, resolves the positive owner-to-box mapping and forwards opaque frames and input. The box authenticates its router channel. Neither router, broker nor waker decodes, records or reads the stream. A shared screenshot service would violate T1/T4.
2. Extend the existing protected browser login view as the one `BoxProvider.view` primitive for browser and desktop. Inside the box, the browser and view server run under a separate OS user and process/mount namespace from the agent. Its capture, input and browser debug sockets are accessible only to the view server; the agent's bash, tools, `/proc`, filesystem and network namespace cannot reach them. Owner input enters that server over the authenticated view channel and never enters agent observations. The owner starts and ends takeover; only sanitized post-takeover results can return to the agent. Broker custody of sealed website sessions and provider credentials remains unchanged.
3. Capture lifetime, focus and input authorization live in the box. Reconnect requires a current authenticated owner session and fresh box admission. A stopped box may be woken through normal lifecycle routing. No owner content path or capture state is added to shared storage.

## Risks / Trade-offs

- Remote sites and device authenticators can refuse streamed interaction; the view reports that limit instead of claiming completion.
- A broken stream can strand a takeover; box-local teardown releases input control and requires fresh authentication before reconnect.
