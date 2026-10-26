## ADDED Requirements

### Requirement: Model calls return ordered typed parts
`model_call` SHALL return ordered text, tool-call, image, audio and video parts according to the connected source's declared or discovered response contract. Media parts SHALL name a MIME type, byte length, digest and owner-box reference; unsupported or malformed parts SHALL fail visibly instead of being discarded or converted to text.

#### Scenario: Mixed text and image output
- **WHEN** a connected source returns text followed by an image
- **THEN** one `ModelResult` preserves that order and types, and its text projection contains only the text

### Requirement: Every admitted model call has one media-linked receipt
Every admitted `model_call` SHALL settle through C3's allocation and return exactly one execution receipt, bound to that call's usage ID, outcome and emitted part references. Partial, cancelled and failed calls SHALL retain their true outcome and any committed parts. A pre-admission refusal SHALL be `not_sent` and SHALL NOT fabricate a provider response or charge.
Input and output media usage SHALL count against the owner's cap within that call. The broker SHALL reserve a bounded slice before dispatch and renew it before a stream exceeds its reservation. An insufficient cap SHALL stop upstream traffic gracefully and settle measured usage in the call's receipt, or retain an unknown amount pending reconciliation. Free-only defaults SHALL remain unchanged.

#### Scenario: Media receipt per call
- **WHEN** two calls each produce media, including one whose stream fails after its first part
- **THEN** each call has exactly one distinct receipt linked to its own parts and parent allocation, with the failed call marked incomplete

#### Scenario: Media stream reaches its cap
- **WHEN** a media call needs another reserved slice and the remaining cap cannot fund it
- **THEN** upstream traffic stops, the caller sees budget exhaustion, and the one receipt records actual metered media usage or an unknown amount pending reconciliation

### Requirement: Media bytes remain in the owner's box
The owner-box result normalizer SHALL store each complete, committed media part in that owner's private store before exposing its owner-scoped reference. Partial frames SHALL remain ephemeral and have no durable reference. The broker SHALL hold bytes only in transit and credentials only in its custody; router and shared services SHALL have no host path to owner media.

#### Scenario: Another owner requests a part
- **WHEN** a caller from another owner presents a valid media reference
- **THEN** the owner-box retrieval refuses it without reading or revealing bytes
