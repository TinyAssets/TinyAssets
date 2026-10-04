## ADDED Requirements

### Requirement: A released seat leaves one usage record of credited agent time

When a seat is finally released, the platform SHALL append exactly one durable usage record for that seat, naming the account, universe, seat class, kind, run or turn id, inference seconds, tool seconds, credited seconds, terminal outcome, the set of participating piece versions, and the UTC month. The seat SHALL be the only place agent-call run time is metered: no other execution path SHALL keep a second run-time counter. Writing the record SHALL NOT change seat admission, ordering, release or reclamation, and a failure to write it SHALL be retried until it lands, without holding the seat. A lent seat's nested calls SHALL add to the same record instead of writing records of their own. A seat reclaimed after its holder's proven death SHALL record the time measured up to the last completed model round, and its outcome SHALL be `holder_died`.

#### Scenario: A finished agent call is recorded once
- **WHEN** a graph agent node holds a seat for one call with two completed model rounds and then releases it
- **THEN** exactly one usage record exists for that seat, with the inference and tool seconds of those rounds

#### Scenario: A nested call does not double count
- **WHEN** a blocking nested call re-enters its parent's seat and both finish
- **THEN** one usage record covers both, and its piece set is the union of the pieces of both calls

#### Scenario: A failed record write never holds a seat
- **WHEN** the usage store is briefly locked at release
- **THEN** the seat is released on schedule and the record is written by a later retry
