## ADDED Requirements

### Requirement: Compute is metered from the box lifecycle

The platform SHALL meter each account's box awake time per second, weighted by
the box's memory ceiling, as an operational measurement. A suspended or stopped
box SHALL NOT be metered. Time an agent turn spends waiting on a model in the
control plane's loop SHALL NOT be metered as box time once its box has
suspended. The platform SHALL NOT enforce any compute-hour budget unless the
founder adopts one. Account limits SHALL remain storage and seats.

#### Scenario: A long model wait costs only the box's awake seconds
- **WHEN** a turn runs a 2-second tool call and then waits 5 minutes for a model reply
- **THEN** the meter records the tool call plus the box's idle grace before suspend, and not the rest of the wait

### Requirement: Host admission is first-come across accounts within each account's seats

A run beyond its account's seats SHALL wait for a seat. Among runs within their
seats, host-capacity admission SHALL be first-come across accounts: a run SHALL
be admitted no later than any later-arriving run of another account. One
account SHALL occupy at most its seats' worth of concurrently awake boxes. A
waiting run SHALL NOT be refused, and its waiting state SHALL name the limit
that applies. Each box SHALL run under a CPU and I/O weight, so that a busy box
cannot starve its neighbours.

#### Scenario: A fan-out cannot jump the queue
- **WHEN** one account starts 20 parallel agents on 2 seats, and then another account starts one run
- **THEN** the first account holds at most 2 awake boxes, and the other account's run is admitted ahead of the first account's waiting agents
