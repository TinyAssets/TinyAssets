## ADDED Requirements

### Requirement: Native request decisions remain in the authenticated browser

The app SHALL open native request sheets in the system browser using an expiring opaque owner-bound reference. A bearer or launch reference MUST NOT establish approval proof. Browser sign-in SHALL complete server-owned PKCE before displaying the same request sheet, and a valid browser session SHALL be reused. Decisions, ordinary replies and unrecorded dismissals SHALL use the existing server decision paths. Returning to the app SHALL carry only an opaque completion reference and refresh server state.

#### Scenario: Native cookie isolation
- **WHEN** a signed-in WebView has no protected owner cookie
- **THEN** its approval is refused and a browser handoff can authenticate and decide without transferring the cookie to the WebView

#### Scenario: Browser callback reaches its authority
- **WHEN** an owner-login callback reaches the stateless frontend
- **THEN** the frontend proxies it to the owner process for browser-bound exchange and cookie creation

#### Scenario: Wrong owner or expired handoff
- **WHEN** another owner opens a copied reference or the reference expires
- **THEN** no request is exposed or changed and the user gets a recoverable error

#### Scenario: Return is observational
- **WHEN** a return link is replayed, forged, or delivered after app restart
- **THEN** the app only refreshes its authenticated request list and never interprets the link as a decision

#### Scenario: Public edge retains browser proof
- **WHEN** the app begins sign-in, completes its callback or logs out
- **THEN** the edge preserves separate approved app cookie headers with their secure attributes and expiry
- **AND** Access and unknown cookies remain stripped, and callback refusal logs identify the reason without secrets
