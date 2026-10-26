## ADDED Requirements

### Requirement: Branch definitions remain losslessly readable within model limits
The branch read SHALL support field_name="@definition" for bounded, lossless JSON chunks of the authorized definition without execution or mutation. Chunks SHALL include offsets, total lengths and SHA-256. Paged node previews SHALL point to this selector. Consumers MUST verify matching metadata and the reconstructed digest before decoding.

#### Scenario: One source exceeds the reply ceiling
- **WHEN** an authorized caller follows @definition continuations for a branch with an oversized source node
- **THEN** concatenating and JSON-decoding the chunks recovers every source character exactly
- **AND** each response fits the model result ceiling, including foreign-content envelopes
- **AND** metadata names the Unicode-code-point offset unit, full character and UTF-8 byte lengths, SHA-256 and next offset

#### Scenario: Definition changes during pagination
- **WHEN** the authorized branch content changes between reads
- **THEN** the full-definition digest changes, allowing the consumer to reject mixed pages and restart

#### Scenario: Private branch belongs to another owner
- **WHEN** a caller selects @definition on another owner's private branch
- **THEN** the same not-found refusal as an ordinary branch read is returned, without chunks, length or digest

#### Scenario: Invalid continuation
- **WHEN** the selector is unsupported, the offset is invalid or past the definition, or chunk length is outside 1..32768
- **THEN** the read returns an explicit error instead of silently resetting or clipping a chunk
