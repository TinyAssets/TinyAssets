## MODIFIED Requirements

### Requirement: Delivery control records do not block either party's erasure
Account deletion SHALL remove indirect delivery attempts and personal two-party
receipts before their scoped link, receiver and run parents within the existing
satellite-store transaction. Affected rows SHALL be counted once. Surviving
receiver allowlists SHALL remove only the deleted sender. Unrelated peer records
and peer-owned runs SHALL remain intact. Generic immutable receiver-owned file
objects and run bindings accepted before deletion SHALL remain independent of
these personal controls; the deleted owner's own custody SHALL be tombstoned and
removed through durable cleanup. Legacy inputs held only in an erased delivery
receipt SHALL NOT be represented as preserved merely because a run row survives.

#### Scenario: Either delivery party deletes its account
- **WHEN** a sender or receiver deletes its account after delivery acceptance
- **THEN** scoped foreign-key children are removed without a foreign-key failure
- **AND** no delivery receipt or permitted-sender reference to that principal remains
- **AND** independent peer runs and unrelated connections survive

#### Scenario: A satellite store refuses deletion
- **WHEN** an integrity failure interrupts the transaction
- **THEN** that store's deletes and sender-list edits roll back without committed counts
- **AND** the existing deletion workflow records the unfinished store phase

#### Scenario: Sender deletion races final file acceptance
- **WHEN** sender deletion wins the authoritative tombstone fence before final acceptance
- **THEN** the transfer refuses and no new receiver binding is committed
- **AND** when acceptance won first, the independent receiver-owned copy survives sender deletion without retaining the sender's personal controls

#### Scenario: Receiver deletes its accepted file custody
- **WHEN** a receiver deletes its account with accepted inputs and interrupted physical cleanup
- **THEN** its file reads are denied immediately and cleanup debt remains recoverable
- **AND** sender custody and unrelated peer copies remain unchanged
