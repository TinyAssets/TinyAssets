## Context

Registry N9 follows C3. `ModelResult` currently wraps `ProviderResponse` and `ExecutionReceipt`, and `.text` projects response text. Media bytes must follow target-shape T4/T8 into the owner's box; broker T6 remains the only upstream egress and credential holder.

## Goals / Non-Goals

**Goals:** ordered typed output parts, owner-box media bytes and one receipt for every model call.

**Non-Goals:** a media generation endpoint, provider-specific parser in product code, or shared media storage.

## Decisions

1. **Result contract.** Extend `ModelResult` with ordered parts: text, tool call, image, audio or video. A media part carries MIME type, byte length, digest and an owner-scoped box reference; video may carry timing. `.text` remains a projection of text parts. Malformed or unsupported source parts fail with a typed outcome rather than being silently dropped or presented as text.
2. **One receipt and cap.** C3's allocation, dispatch and settlement remain the only accounting path. Each admitted call returns exactly one receipt bound to its usage ID and outcome, including cancellation or partial output; the receipt identifies its emitted part references. Input and output media usage is metered inside that call and counted against the owner's cap. Reserve a bounded slice before dispatch, renew before it expires on a stream, and stop upstream traffic gracefully when the cap cannot fund renewal. Settle actual usage or hold an unknown amount for reconciliation. A refusal before allocation reports `not_sent` without fabricating a provider response. No media-part subcall or separate spend path exists. Free-only defaults remain unchanged.
3. **Placement.** The broker streams upstream bytes and injects credentials but stores no owner media. Generic normalization in the owner's box uses the source's declared or discovered media contract and writes complete, committed parts to its private store before publishing references to that owner. Partial frames remain ephemeral, including realtime playback in N3; they do not receive durable references. The router never opens a media file or owner host path. Retrieval is owner authenticated and box served.

## Risks / Trade-offs

- A media stream can fail after some parts arrive → keep committed parts and mark the single receipt failed or unknown; never imply a complete result.
- Large output can exhaust memory → stream into bounded owner-box storage under existing owner limits and broker backpressure.

## Migration Plan

Add typed parts to the current result while preserving `.text` for text callers; migrate consumers to inspect part types where they render media. Do not add a second model call path.
