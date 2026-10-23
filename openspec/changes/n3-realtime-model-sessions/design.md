## Context

Registry N3 follows C3, C9 and `broker-streaming-contract`. `model_call.py` currently returns one buffered result and receipt. The broker contract streams responses but its v1 collects requests and refuses WebSocket upgrades. Browser Voice currently has its own `realtime_voice.py` bridge, `/voice/session`, WebRTC data channel and browser speech turn queue around `converse`. The binding target is owner execution in the box (T8), broker credential custody and one egress (T6), and a router that only forwards (T1).

## Goals / Non-Goals

**Goals:** one session entry, one S3 loop, one model-call accounting path, duplex voice/video/text and broker-owned transport authority.

**Non-Goals:** a platform voice agent, provider adapters in product code, a second transcript, or retention of raw capture by default.

## Decisions

1. **Session and call.** The existing session entry opens one duplex session and one parent allocation. Each model turn inside it is one streaming `model_call` with its own reservation and exactly one receipt; interruption cancels only the current turn and settles its actual or unknown usage. S3 consumes committed input parts and emits ordered output parts under N9. The UI handles capture and playback only. It neither executes a `converse` tool callback nor runs a second turn queue.
2. **Transport.** Extend `broker-streaming-contract` with bounded duplex request frames and a declared-protocol upgrade where the selected source requires it. The box opens only a bound broker stream; the broker authenticates box identity, rechecks grant/admission, applies source credentials upstream and enforces its generation fence, cancellation, deadlines and flow control in both directions. A browser connection terminates at TinyAssets and is forwarded to the owner box; no browser-to-provider media connection, credential or direct box egress exists. The broker holds media bytes only in transit; no shared store or owner-content decision is introduced.
3. **Capability.** The source's declared or discovered contract gives supported media directions, MIME types, framing and transport. Generic protocol machinery uses that contract. Select the strongest connected source under the existing model policy; if it lacks the requested realtime capability, report that gap visibly before capture or dial. Never silently select a weaker source. An owner-selected source change is an explicit new call.
4. **Replacement and storage.** Remove the browser speech `sendVoiceTurn` queue, auxiliary bridge and `/voice/session` signaling path, including the legacy `realtime_voice` descriptor, configuration and public status paths, when the canonical path ships. Keep explicit start, permission disclosure, accessible state, barge-in and teardown in the app. N9 governs durable media: complete media parts are stored in the owner's box before references are published; partial playback frames and raw microphone capture are ephemeral. Keep the committed speech transcript, not input audio, by default. Persist receipts and committed output parts in the box.
5. **Spending.** The broker atomically reserves a bounded time-and-usage slice against the parent's remaining cap before opening upstream media, then renews before each slice expires. Meter input and output media within each turn, not as separate calls. If renewal fails or the cap is reached mid-stream, stop upstream traffic gracefully, expose a budget-exhausted outcome, and settle that turn's one receipt for actual measured usage (or unknown usage pending reconciliation). The session cannot admit another turn until budget authority exists. Free-only defaults remain unchanged; realtime never opts the owner into paid use.

## Risks / Trade-offs

- Duplex transport exceeds the broker's response-only v1 → extend the same broker framing and fence, with bounded bidirectional credit and cancel tests before enabling realtime.
- Connection loss can leave an exchange's outcome uncertain → query the existing operation status; never replay committed input automatically.

## Migration Plan

Replace the browser Voice route and loop in the same cutover that enables streaming sessions. Remove their obsolete spec requirements and paths; a source without a compatible realtime contract reports unavailable while text sessions continue.
