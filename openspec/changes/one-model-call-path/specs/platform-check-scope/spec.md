## MODIFIED Requirements

### Requirement: Actionable inference accounting refusal
The broker SHALL refuse an HTTP POST to a connection classified as a model
source, using trusted connection authority, unless the request came from the
model call service. It SHALL refuse before sending anything, with one fixed
diagnostic that names the `model` capability (`ta call model …`) and the
agent's `model` setting as the metered path. The diagnostic SHALL NOT expose
credentials, suggest that owner consent bypasses metering, or steer the
caller to a workflow prompt node.

#### Scenario: Unaccounted direct inference POST
- **WHEN** an agent posts directly to a model connection through `ta connection:<id>:POST`
- **THEN** no request is dispatched
- **AND** the error names the `model` capability as the path to call that model

#### Scenario: The same model through the model capability
- **WHEN** the agent then calls `ta call model` naming that connection's model
- **THEN** the request is dispatched through the broker, metered on the
  turn's allocation, and returns a receipt

### Requirement: Connection calls use connection authority
Ordinary HTTP connection calls SHALL require their active connection grant and
SHALL NOT require an inference usage reservation or broker access to owner
files. A model-source connection SHALL be callable only through the model call
service, which supplies its metering internally.

#### Scenario: Ordinary service POST
- **WHEN** an agent or workflow node posts through an active ordinary HTTP connection
- **THEN** the broker dispatches using the connection grant without inference accounting

#### Scenario: Model POST outside the model call service
- **WHEN** an HTTP model connection is called by anything other than the model call service
- **THEN** the broker refuses before sending

#### Scenario: Revoked service grant
- **WHEN** a service connection grant is revoked
- **THEN** the broker refuses the service call before sending
