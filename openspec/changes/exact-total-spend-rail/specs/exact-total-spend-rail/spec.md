## ADDED Requirements

### Requirement: Spend requires an exact-total one-use owner decision
Every spend-rail dispatch SHALL require protected approval of merchant, exact total including fees, currency and unexpired quote revision. Task/site/always grants SHALL NOT authorize a payment and credential bytes SHALL remain broker-side.

#### Scenario: Price changes after review
- **WHEN** checkout adds tax or changes merchant/currency after the owner reviews the quote
- **THEN** the old approval is refused and a fresh exact-total sheet is required

#### Scenario: Broad grant or unknown price
- **WHEN** a tool presents an always grant or a quote with an unknown final total
- **THEN** no payment is issued and the request reports the missing exact-total decision

### Requirement: Budget reservation and payment recovery prevent duplicate spending
The system SHALL show cap availability at preview without holding a reservation, reserve atomically with the approved dispatch intent and recheck current authority/cap before dispatch. An insufficient cap SHALL leave the request visibly blocked without issuing a payment credential or charge. The system SHALL serialize budget reservations with dispatch intents and reconcile uncertain provider outcomes before retry or release. It SHALL charge each confirmed payment once against its budget.

#### Scenario: Concurrent payments race a budget cap
- **WHEN** two individually affordable quotes would together exceed the remaining cap
- **THEN** only reservations within the cap can dispatch and the other request remains visibly blocked

#### Scenario: Crash after possible charge
- **WHEN** the provider may have charged before a worker crashes
- **THEN** the intent and budget reservation remain unresolved until receipt reconciliation; retry does not issue another payment
