## ADDED Requirements

### Requirement: Engine tools admit current serving owners without vetted membership
The engine tool surface SHALL admit an exact actor/universe pair only while
engine tools are enabled, the universe has an unambiguous serving-binding
creator matching that actor, that actor holds current universe admin permission,
and the principal is not deleted. Admission SHALL be checked at route publication,
route use and tool entry for HTTP and stdio. Env membership SHALL NOT confer or
limit this authority. Canonical operation-specific permissions SHALL remain.

#### Scenario: Newly connected HTTP model can operate its own universe
- **WHEN** an ordinary user connects an authorized HTTP model and approves serving
- **THEN** engine tools are available without operator enrollment
- **AND** foreground and authorized background turns use the same owner-bound tools.

#### Scenario: A stale route or process cannot preserve revoked authority
- **WHEN** admin permission or serving status is revoked, the owner is deleted,
  or a different serving creator makes ownership ambiguous
- **THEN** route reads and tool calls refuse before delegation even before the
  supervisor retires the old process.

#### Scenario: One user's private route does not authorize another user
- **WHEN** a different actor requests the route or a serving creator lacks admin
- **THEN** no route is admitted and no operation is delegated.

#### Scenario: Deployment kill switch remains effective
- **WHEN** engine tools are disabled
- **THEN** route publication, route reading and handler entry refuse regardless
  of prior ownership or route records.

### Requirement: Newly serving HTTP agents wait for startup without replay
The shared HTTP engine-tool client SHALL wait for an authorized route and
listener readiness before its first MCP connection when its process owns the
matching-root supervisor. The wait SHALL be cancellable and bounded by the
smaller of the caller timeout and thirty seconds. The client SHALL recheck
current authority throughout and SHALL NOT retry MCP discovery, tools or a
conversation after dispatch. Route readers and per-tool checks SHALL NOT wait.

#### Scenario: First message races a newly admitted server
- **WHEN** current owner authority exists but its engine listener is starting
- **THEN** the client wakes the existing supervisor and probes loopback readiness
  without sending protocol bytes
- **AND** makes exactly one MCP connection and discovery after readiness.

#### Scenario: Startup cancellation or revoked authority
- **WHEN** the deadline expires, the caller cancels or current authority disappears
- **THEN** no inference or tool operation is dispatched by the waiting turn
- **AND** a background invocation reserved before startup is settled as cancelled
  before launch.

#### Scenario: No local supervisor
- **WHEN** the process owns no supervisor for the requested data root
- **THEN** the client performs the existing immediate route check without a
  startup wait or spawning a replacement supervisor.
