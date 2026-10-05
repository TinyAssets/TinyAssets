## ADDED Requirements

### Requirement: Payment approval defaults remain owner editable
Owner-declared tool/effect classification SHALL be trusted. Tool hints alone cannot grant authority; unknown effects SHALL follow the owner's editable default (starter default: ask through the approval sheet), never refusal merely for being unknown. Exact-total once-only approval is the editable starter default for payments, not an immutable platform rule. The owner SHALL be able to authorize a spend grant bounded by an owner-editable budget cap, destination/action scope and expiry; dispatch rechecks that grant and atomically reserves against the cap. Unknown payment totals need an enforceable maximum within that grant, or return to the owner's approval sheet. Cross-user isolation is the only immutable platform behavioral invariant. Credential bytes SHALL remain broker-side for this rail.

#### Scenario: Price changes under the starter once policy
- **WHEN** checkout changes the merchant, total or currency after once approval
- **THEN** the old approval no longer authorizes dispatch and the sheet requests a fresh decision

#### Scenario: Owner selects a standing spend grant
- **WHEN** a payment matches an owner-set spend grant with a sufficient budget cap
- **THEN** the rail reserves its exact amount or enforceable maximum and dispatches without another once decision
- **AND** a generic connector grant with no spend budget supplies no payment authority

### Requirement: Budget reservation and payment recovery prevent duplicate spending
The system SHALL show cap availability at preview without holding a reservation, reserve atomically with the approved dispatch intent and recheck current authority/cap before dispatch. An insufficient cap SHALL leave the request visibly blocked without issuing a payment credential or charge. The system SHALL serialize budget reservations with dispatch intents and reconcile uncertain provider outcomes before retry or release. It SHALL charge each confirmed payment once against its budget.

#### Scenario: Concurrent payments race a budget cap
- **WHEN** two individually affordable quotes would together exceed the remaining cap
- **THEN** only reservations within the cap can dispatch and the other request remains visibly blocked

#### Scenario: Crash after possible charge
- **WHEN** the provider may have charged before a worker crashes
- **THEN** the intent and budget reservation remain unresolved until receipt reconciliation; retry does not issue another payment
