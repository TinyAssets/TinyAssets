## Context

Registry N3 follows C3, C9 and `broker-streaming-contract`. `model_call.py` currently returns one buffered result and receipt. The broker contract streams responses but its v1 collects requests and refuses WebSocket upgrades. Browser Voice currently has its own `realtime_voice.py` bridge, `/voice/session`, WebRTC data channel and browser speech turn queue around `converse`. The binding target is owner execution in the box (T8), broker credential custody and one egress (T6), and a router that only forwards (T1).

## Goals / Non-Goals

**Goals:** one session entry, one S3 loop, one model-call accounting path, duplex voice/video/text and broker-owned transport authority.

**Non-Goals:** a platform voice agent, provider adapters in product code, a second transcript, or retention of raw capture by default.

## Decisions

1. **Session and call.** The existing session entry opens an owner-box session. Each model exchange is `model_call` in streaming mode with the same owner, connected-source admission, parent allocation and receipt as a buffered call. S3 consumes committed input parts and emits ordered output parts; interruption cancels the active call and records its settled or unknown outcome. The UI handles capture and playback only. It neither executes a `converse` tool callback nor runs a second turn queue.
2. **Transport.** Extend `broker-streaming-contract` with bounded duplex request frames and a declared-protocol upgrade where the selected source requires it. The box opens only a bound broker stream; the broker authenticates box identity, rechecks grant/admission, applies source credentials upstream and enforces its generation fence, cancellation, deadlines and flow control in both directions. A browser connection terminates at TinyAssets and is forwarded to the owner box; no browser-to-provider media connection, credential or direct box egress exists. The broker holds media bytes only in transit; no shared store or owner-content decision is introduced.
3. **Capability.** The source's declared or discovered contract gives supported media directions, MIME types, framing and transport. Generic protocol machinery uses that contract; an unsupported combination fails before capture or dial. The owner-box session selects the strongest eligible connected source under the existing model policy. A source change is an explicit new call, not a hidden voice fallback.
4. **Replacement.** Remove the browser speech `sendVoiceTurn` queue, auxiliary bridge and `/voice/session` signaling path when the canonical path ships. Keep explicit start, permission disclosure, accessible state, barge-in and teardown in the app. Persist committed conversation and receipts inside the owner's box; partial frames and raw capture remain ephemeral unless the owner elects to save them.

## Risks / Trade-offs

- Duplex transport exceeds the broker's response-only v1 → extend the same broker framing and fence, with bounded bidirectional credit and cancel tests before enabling realtime.
- Connection loss can leave an exchange's outcome uncertain → query the existing operation status; never replay committed input automatically.

## Migration Plan

Replace the browser Voice route and loop in the same cutover that enables streaming sessions. Remove their obsolete spec requirements and paths; a source without a compatible realtime contract reports unavailable while text sessions continue.
