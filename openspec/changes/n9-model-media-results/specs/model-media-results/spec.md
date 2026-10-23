## ADDED Requirements

### Requirement: Model calls return ordered typed parts
`model_call` SHALL return ordered text, tool-call, image, audio and video parts according to the connected source's declared or discovered response contract. Media parts SHALL name a MIME type, byte length, digest and owner-box reference; unsupported or malformed parts SHALL fail visibly instead of being discarded or converted to text.

#### Scenario: Mixed text and image output
- **WHEN** a connected source returns text followed by an image
- **THEN** one `ModelResult` preserves that order and types, and its text projection contains only the text

### Requirement: Every admitted model call has one media-linked receipt
Every admitted `model_call` SHALL settle through C3's allocation and return exactly one execution receipt, bound to that call's usage ID, outcome and emitted part references. Partial, cancelled and failed calls SHALL retain their true outcome and any committed parts. A pre-admission refusal SHALL be `not_sent` and SHALL NOT fabricate a provider response or charge.

#### Scenario: Media receipt per call
- **WHEN** two calls each produce media, including one whose stream fails after its first part
- **THEN** each call has exactly one distinct receipt linked to its own parts and parent allocation, with the failed call marked incomplete

### Requirement: Media bytes remain in the owner's box
The owner-box result normalizer SHALL store media bytes in that owner's private store and expose only owner-scoped references to callers. The broker SHALL hold bytes only in transit and credentials only in its custody; router and shared services SHALL have no host path to owner media.

#### Scenario: Another owner requests a part
- **WHEN** a caller from another owner presents a valid media reference
- **THEN** the owner-box retrieval refuses it without reading or revealing bytes
