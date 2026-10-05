# Onboarding Web App

## Purpose

Define hosted model authorization and safe conversation recovery for the authenticated app.

## Requirements

### Requirement: Hosted model authorization survives daemon replacement
Hosted PKCE bindings SHALL survive process restart until their original expiry,
without persisting authorization codes, verifiers, keys or raw flow handles.
One valid owner/home/preset/verifier-matching callback SHALL atomically consume
the binding before exchange; no uncertain exchange SHALL automatically replay.

#### Scenario: Deploy while the user authorizes
- **WHEN** the daemon restarts after begin and before an unexpired callback
- **THEN** the same authorized owner/home/verifier can complete exactly once

#### Scenario: Multiple workers receive the same callback
- **WHEN** two processes attempt to consume one valid binding concurrently
- **THEN** exactly one can proceed to provider exchange

#### Scenario: A mismatched callback arrives
- **WHEN** owner, home, verifier or preset does not match the binding
- **THEN** the attempt is refused without consuming another valid owner's binding

### Requirement: Recovery distinguishes live turns from previous-page records
The app SHALL NOT restore a turn that its current page still owns as abandoned
work. Durable recovery SHALL remain owner/home-scoped and available on reload.

#### Scenario: Status or history completes during a live turn
- **WHEN** a typed or spoken request is still in progress on the current page
- **THEN** status/history restoration adds no duplicate message or false retry offer
- **AND** the original response can render once without a second invocation

#### Scenario: Previous-page observation finishes after a newer send
- **WHEN** a previous-page request is observed while a newer turn starts
- **THEN** clearing the observed request SHALL NOT erase the newer turn's recovery
- **AND** account/home changes fence recovery for any turn, and a typed or spoken turn's late reply or failure from another account or home paints nothing, offers no retry, signs nobody out, and hands no reply to the voice session now on screen

### Requirement: Response completion is correlated and does not wait for stream closure
The app SHALL accept only a JSON-RPC terminal response with the exact outgoing
request ID. It SHALL read SSE incrementally, ignore comment keepalives,
notifications and foreign response IDs, and settle as soon as its own result or
error arrives. Ordinary JSON responses SHALL retain the same correlation rule.

#### Scenario: A completed result arrives on an open connection
- **WHEN** a matching terminal response arrives after notifications or unrelated responses
- **THEN** the app settles that request and releases its reader without waiting for EOF
- **AND** arbitrary UTF-8, CRLF and multi-line SSE chunk boundaries preserve the result

### Requirement: Transport uncertainty is bounded without replaying user intent
The app SHALL bound missing response headers and silence between received bytes,
not the total duration of a healthy keepalive-producing turn. The defaults are
90 seconds for headers and 120 seconds for byte silence. Expiry SHALL be shown
as unconfirmed delivery, not proof of execution failure or cancellation.
Only existing explicitly idempotent reads or proven pre-dispatch refusals may
retry automatically; an ambiguous conversation or connection write SHALL NOT.

#### Scenario: A connection stops producing replies
- **WHEN** headers never arrive or an established response becomes silent
- **THEN** the waiting request settles with an unconfirmed transport outcome
- **AND** recovery data remains available and the composer is released under its owner fence
- **AND** another request's stream is not aborted

#### Scenario: A long turn continues to produce keepalives
- **WHEN** its total duration exceeds the byte-silence bound but bytes keep arriving
- **THEN** the client continues to wait for its matching terminal response

### Requirement: Unconfirmed recovery offers observation and explicit queue resumption
For an unconfirmed default conversation, the app SHALL offer an owner/home/agent/
login-fenced, read-only saved-conversation check, also run on return online or to
the foreground or window focus. It SHALL preserve the draft and never replay a send
automatically. Recovery SHALL check the active turn first and confirm only a
nonempty client_send_id equal to the saved send identity. Saved history uses the
same identity rule. Missing IDs, text matches and timestamps prove nothing.
Messages queued behind an unconfirmed turn SHALL remain held until explicitly
resumed, even after confirming that first turn or sending a separate question.

#### Scenario: Inspect progress before deciding whether to send again
- **WHEN** saved history proves delivery by that identity rule
- **THEN** the app draws only that exchange's reply, forgets only that inflight record,
  and removes the unconfirmed notice and resend button
- **WHEN** instead the pending-turn endpoint shows this message actively running
- **THEN** the app shows it as working and removes the notice
- **WHEN** neither observation proves delivery or active work
- **THEN** one short line preserves uncertainty without dumping saved history
- **AND** no conversation is invoked or queued approval resumed by these reads

#### Scenario: A disconnected turn is not an owner Stop
- **WHEN** a connection drops, a stream times out, or the app changes visibility
- **THEN** no Stop request is issued
- **AND** the server SHALL attribute an interruption to the owner only when the
  registered live turn has an explicit Stop request

#### Scenario: Chat about a request does not decide it
- **WHEN** the founder sends a chat message about an open request
- **THEN** request history continues to show it as open until Accept, Deny or Clear
- **AND** the chat button labels that it keeps the request open
- **WHEN** the proposed chat is an obvious yes/no decision
- **THEN** an inline nudge directs the founder to Accept or Deny without sending
  the text or granting approval

#### Scenario: A manual question follows an uncertain outcome
- **WHEN** the user asks another question while older queued messages remain held
- **THEN** finishing that question does not send the held messages
- **AND** an explicit queue-resume control sends them only within the same owner, home and login

### Requirement: Working state is the universe's, and waiting lines are ordered last
The app's working indicator SHALL reflect server-reported turn state for the
universe, whatever started the turn -- a typed message, an answered request, a
queued line, another window, another device, or the connector -- and SHALL NOT
depend on the current page having sent anything. `get_status` SHALL carry, gated
on write access to the universe, whether a turn is progressing, since when, and
its journal state, and no prompt or owner; once the running turn has opened a
round it SHALL also carry that step's number, the model id the step is asking
(the same id those readers see on every reply's "Answered by" line) and how long
it has waited. While a step waits on its model the indicator SHALL say which step,
which model and for how long, and after a long wait SHALL offer the owner another
model for their next message -- the owner's choice, never an automatic switch
that abandons a reply still coming. A row past the cap the
coordinator already enforces SHALL be reported as stale and SHALL NOT be painted
as activity; a read that failed SHALL be reported as unreadable rather than as
idle. A message queued behind an in-flight turn SHALL render in the order the
agent will read it -- after that turn's reply -- and SHALL be marked as queued
until its own turn starts.

A progressing row is only activity while a process is executing it. The daemon
SHALL NOT report as working a turn the CURRENT daemon boot is not running, and at
startup SHALL settle every such row into the terminal state the journal already
has for the step that died -- preserving what ran and what is merely uncertain,
never claiming a killed turn completed. A boot owns a turn it created and has not
finished, or one created after the boot began; ownership is process state and
SHALL NOT be inferred from age alone.

#### Scenario: A step waits a long time on its model
- **WHEN** the running turn's step has waited minutes on one model request
- **THEN** the indicator reads like "step 4 · waiting on qwen3.8 for 7 min"
- **AND** after three minutes a "Try another model" action opens the model menu for the next message, and the request in flight is not stopped by it

#### Scenario: A turn this page did not start
- **WHEN** a turn is running for the universe and this page sent nothing
- **THEN** the working indicator is shown, with how long, and that it started elsewhere
- **AND** the status poll asks more often until the universe is idle again

#### Scenario: A reload during a live turn
- **WHEN** the page loads while a turn is still running, so history has no record of it
- **THEN** the same read that returns history reports the turn and the indicator is shown

#### Scenario: A row no client can still verify
- **WHEN** the reported row is older than the served-turn cap, or the journal cannot be read
- **THEN** it is reported rather than hidden, and it is not painted as activity
- **AND** a status poll that fails neither clears the indicator nor claims it indefinitely

#### Scenario: An answer given while an earlier turn is running
- **WHEN** the user answers a request and the earlier turn's reply arrives afterwards
- **THEN** the reply renders above the queued answer, which stays marked queued
- **AND** the queued mark is removed when that answer's own turn starts

#### Scenario: A deploy recreated the daemon mid-turn
- **WHEN** the daemon starts and a turn row is still in a progressing state that no
  process in this boot is running
- **THEN** it is settled to the terminal state its own last committed step implies,
  with the uncertainty of that step preserved
- **AND** it is not reported as working, so no indicator is painted for it

#### Scenario: A turn the current boot is running, older than this boot's start
- **WHEN** startup reconciliation runs while a turn created by this boot is still
  progressing
- **THEN** that turn is left untouched and continues to be reported as working
- **AND** age alone never makes a live turn eligible for settlement

### Requirement: The app reads its owner's data through the owner door, complete
 Every read the app renders (the request rail, restore-access, bindings, the model picker, status, conversation history and message expansion, and the reads a custom UI bundle makes through the bridge) SHALL go through the owner door: `POST /app/api/read` (the `read_graph` arguments) and `POST /app/api/status` (the `get_status` arguments). The owner door SHALL be authenticated by the same bearer middleware as every other `/app` route, SHALL execute each read under the request identity through the same domain function and owner gate the connector uses, and SHALL return the complete document. The owner door SHALL contain no size, limit or truncation logic and SHALL NOT import the model-context ceiling or projection modules. Actions (`converse`, `write_graph`) MAY stay on the connector.

The phone app (Capacitor, `server.url` = the live `/app`) and the desktop app
(Electron over the live SPA) load the same page and therefore the same doors.

#### Scenario: A heavy account gets its whole rail
- **WHEN** an owner has 40 pending requests totalling more than 60 KB
- **THEN** the owner door returns all 40, with no truncation marker
- **AND** the same read on the connector is bounded visibly

#### Scenario: Another account's data is refused exactly as on the connector
- **WHEN** a signed-in account names a universe it does not own
- **THEN** the owner door returns the same refusal the connector returns, and none of that universe's data

#### Scenario: Account type is the only per-account difference
- **WHEN** a free account and a subscription account with the same data read the rail, status and bindings
- **THEN** the documents are identical apart from tier-derived numbers

### Requirement: Open requests stay in view above the composer
Pending requests that need the owner SHALL render in a dedicated region directly
above the composer, outside the scrolling conversation history, so a reader at
the latest messages always sees them without scrolling. The region SHALL be
hidden when nothing is pending, SHALL be bounded in height and scroll on its
own when many are pending, and SHALL use compact one-line tabs at phone width.
Answer paths SHALL be unchanged. Answered request history stays read-only in the
rail. This is the interim placement until the approval sheet and "Needs you"
inbox replace it.

#### Scenario: Requests with a long conversation
- **WHEN** an owner with more than 80 messages is reading the latest messages and two requests are pending
- **THEN** both requests are visible above the composer at phone and desktop width

#### Scenario: A new request arrives
- **WHEN** a request arrives while the owner reads the latest messages
- **THEN** it appears in the pending region without scrolling, and answering it uses the existing path
- **AND** the conversation stays pinned to its latest message when the pending region appears or grows

#### Scenario: A request arrives while reading older messages
- **WHEN** the owner has scrolled up and a request arrives
- **THEN** the conversation preserves the owner's scroll position

### Requirement: An owner surface never vanishes silently

A failed or unreadable owner read SHALL leave its surface visible with a
statement that it could not load and a way to retry. It SHALL NOT be drawn as
empty, and it SHALL NOT be hidden.

#### Scenario: The rail read fails
- **WHEN** the rail read errors, returns an error document, or returns no list
- **THEN** the rail is shown with a line saying it couldn't load what's waiting, and a retry
- **AND** items from an earlier successful load stay (a typed answer is not wiped) under that line, so they are not presented as freshly confirmed

### Requirement: History is paged by an explicit cursor

The status read SHALL report, with every conversation page, whether older turns
exist (`has_more`) and the cursor that reads them (`next_before`). The app SHALL
offer "Show earlier messages" whenever `has_more` is true. No default page SHALL
hide older turns without saying so.

#### Scenario: A long conversation
- **WHEN** an owner's thread holds more turns than one page
- **THEN** the page reports `has_more: true` and a `next_before` cursor
- **AND** following the cursor until `has_more` is false returns every turn exactly once

### Requirement: Agent chats open at the latest message
Every agent chat in the shared web, desktop and mobile SPA SHALL show the latest
message on opening, reopening, reload, return to the foreground, or switching
agent or command center. Following the latest SHALL survive delayed history,
recovery and completed-turn drawing, and later content or viewport resizing.
Only an explicit reader scroll into the past or request for earlier messages
SHALL suspend following, until the reader reaches the bottom or next opens or
returns to the chat.
Focus handoffs to the command-center iframe and upload picker SHALL preserve
the reader's position; they are interactions inside the app, not returns.
Gestures that leave the thread at the bottom SHALL keep following enabled.

#### Scenario: History arrives while the chat is collapsed
- **WHEN** saved history renders while the chat cloud is a bubble
- **AND** the owner opens the cloud
- **THEN** the latest message is visible, including after late content grows

#### Scenario: Reading older messages
- **WHEN** the reader scrolls up and a new message arrives
- **THEN** the reader's position is preserved
- **AND** opening the chat again or returning to the app shows the latest message

#### Scenario: Loading an earlier page
- **WHEN** the reader requests "Show earlier messages"
- **THEN** prepending the page preserves the visible message's position
- **AND** following remains enabled if that position is still at the bottom
- **AND** a return to the app while that request is pending takes precedence,
  so the late older page does not pull the reader away from the latest message

### Requirement: The chat with an agent floats over the command center
The app SHALL present the chat with an agent (thread, request rail, model bar,
composer and status lines) as a floating "chat cloud" above the command-center
stage, which the owner can drag, resize, and shrink to a bubble and expand
again by pointer, touch or keyboard. It SHALL start open and filling the stage
for an owner with no command-center layout, and as a bubble when a layout is
active. Once the owner moves, resizes, shrinks or expands it, the app SHALL
restore that last state instead, remembered per owner, per agent (`main` by
default) and per viewport class (`phone` below 760 px, `wide` otherwise), in
the owner's UI-preference record (`owner-ui-preferences`) with this device's
copy as the fallback. The
cloud and the bubble SHALL stay wholly on the stage whenever it resizes. The
bubble SHALL show when the agent is working and when a reply arrived while it
was shrunk.

#### Scenario: A new owner signs in
- **WHEN** an owner with no command-center layout and no saved cloud state opens the app
- **THEN** the chat cloud is open and fills the stage

#### Scenario: A command-center layout is active
- **WHEN** a custom UI is mounted and the owner has never placed the cloud
- **THEN** the chat is a bubble in the stage's corner over the layout

#### Scenario: The owner placed it before
- **WHEN** the owner moved, resized or shrank the cloud on this viewport class and reloads
- **THEN** it reopens exactly as they left it, layout or not

#### Scenario: The window shrinks
- **WHEN** the stage becomes smaller than where the cloud or bubble sits
- **THEN** it is moved and, if needed, shrunk to stay wholly visible

#### Scenario: A bubble is dragged
- **WHEN** the owner drags the bubble to a new place
- **THEN** it moves there and does not also open

### Requirement: causal return-to-app confirmation (PR 4458 round 2)

The app MUST mint a unique `client_send_id` for each send and retain it in its
inflight record and uncertainty notice. `converse` accepts an optional ASCII
identifier (1–128 letters, digits, underscores or hyphens; empty means legacy).
It echoes it on the scoped active turn and persists it on the saved founder row.
This additive metadata grants no authority and is not an idempotency key.
Legacy rows remain readable without migration on read.

Recovery MUST check the running turn first and match only nonempty equal IDs,
never text or timestamps. Missing IDs retain the cautious resend offer. Only
the matched exchange's reply is drawn. Focus, visibility and online events
retry observations without replaying requests. Optional connection suggestions
MUST NOT appear as Open requests.

#### Scenario: repeated short prompt
- **WHEN** an identical prompt finished ten seconds before a new interrupted send
- **THEN** neither notice nor reload recovery confirms that new send from the old row
- **AND** an exact ID match confirms only its own exchange within the caller's thread

#### Scenario: a running turn without an ID finishes
- **WHEN** a watched running turn has no ID (sent by a connector, or running across a deploy)
- **THEN** the app draws only the replies after the newest matching founder row, for display
- **AND** it confirms no unconfirmed send and forgets no inflight record

Design: nullable/empty-default columns in conversation and steering stores,
validated at the converse boundary; all owner, home and agent resolution stays
unchanged. No lookup, Stop, or replay operation accepts this ID as authority.

### Requirement: Normal web sign-in establishes protected owner proof
Normal web app sign-in SHALL reuse the interactive server-PKCE, browser-bound,
single-use owner login callback to establish both owner proof and app renewal.
The first inline model Connect after that sign-in SHALL proceed to the provider
without another TinyAssets sign-in. Client-PKCE exchange, app/MCP/CLI bearer and
refresh handles SHALL NOT create owner proof. Existing action/session binding,
CSRF, cross-user rejection and copied-callback defenses SHALL remain unchanged.

#### Scenario: Fresh web login then first connect
- **WHEN** a fresh browser completes normal app sign-in and starts inline Connect
- **THEN** the callback has established a live Secure HttpOnly owner cookie
- **AND** the launch goes directly to the provider for that owner
- **AND** app renewal uses the new refresh cookie rather than stale account handles

#### Scenario: Native system browser has no protected session
- **WHEN** native login completes its app-held PKCE exchange in the WebView
- **THEN** the system browser gains no owner proof from the WebView credentials
- **AND** its first Connect requires protected sign-in if its own owner cookie is absent
- **AND** copied URLs or bearer credentials cannot replace that browser proof
