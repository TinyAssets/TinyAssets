## ADDED Requirements

### Requirement: Cookie-independent owner callback completion
Owner sign-in SHALL support missing flow cookies using a same-origin JSON completion POST with independently authenticated bearer identity equal to the fresh interactive IdP identity. The server SHALL keep the authorization code and PKCE verifier out of page script, completion URLs and logs, using an expiring opaque completion handle. The original ten-minute state SHALL be consumed once across both completion paths before exchange; bearer possession alone SHALL NOT establish approval proof.

#### Scenario: Cross-site cookie omitted
- **WHEN** the owner callback has a valid code and live state but no flow cookie
- **THEN** the app receives only a completion handle and submits it with its signed-in bearer
- **AND** a matching fresh IdP exchange sets the owner cookie from the same-origin response and returns to the pending approval

#### Scenario: Invalid authority or replay
- **WHEN** the bearer is missing, the exchanged user differs, or the state has expired or been consumed
- **THEN** no owner cookie is issued and the approval remains unavailable

#### Scenario: Original cookie and client handoffs
- **WHEN** the original flow cookie arrives or web, Electron, Android or iOS opens a pending approval
- **THEN** the existing cookie path and owner-bound navigation remain functional without transferring shell credentials

### Requirement: Safe owner callback refusal diagnostics
Every owner callback refusal SHALL log cookie names, Sec-Fetch-Site, Sec-Fetch-Mode, Referer origin and flow age when its row exists, without cookie values, query strings, codes, states, verifiers or tokens. Expired-flow UI SHALL say the sign-in link expired and to start again.

#### Scenario: Expired callback
- **WHEN** a callback references a retained expired flow
- **THEN** diagnostics include its age and the response asks the user to start sign-in again
