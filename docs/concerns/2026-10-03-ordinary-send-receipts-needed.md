# Ordinary phone sends need exact accepted-request receipts

Owner: mobile send/recovery lane; integration coordinator owns admission/storage
coordination. Branch: `fix/mobile-send-resume`, base `50bc7bf2`.

## Reproduced and bounded repair

Hermetic Chromium runs the real app send, MCP client, SSE reader/watchdog, owner
read client and DOM. After one send, a simulated background transition, truncated
stream and server completion, foreground resume originally fetched no saved reply.
The message and delivery warning remained visible. No message disappearance was
reproduced. `tests/test_app_send_resume_browser.py` failed before the repair with
no saved-reply elements, then passed with the repair. No live account, provider,
effect or Android device was used; synthetic lifecycle events are not Android
background acceptance.

The bounded patch automatically refreshes the existing read-only saved-conversation
snapshot on foreground/online and when a delayed stream failure lands after resume.
It preserves the ambiguous receipt and does not resend, settle the local record,
release held commands, alter watchdogs or infer a match from text. Checks and their
responses are fenced by owner, home, agent and login epoch. Repeated concurrent
resume events coalesce per notice and later checks replace that notice's snapshot.

Exact app boundaries: `offerSavedConversationCheck`, `offerResend`, new
`attachSavedConversationCheck`/`resumeUnconfirmedTurns`, `startSessionKeepAlive`'s
existing visibility callback plus an online listener. No initialization, placement
preference, hydration, `app_ui.js`, server or storage edits.

## Coordinated server follow-up required before claiming full recovery

Ordinary `converse` lacks the custom-conversation durable `request_key`. The
`/app/turn/pending` active projection returns text/start, not exact accepted-request
identity. `restoreInflight` and `finishActiveTurn` still contain older text-based
matching/clearing logic; this patch does not extend that inference. Repeated text,
another surface's same message, reload after completion, uncertain admission or a
server crash cannot be safely settled by this app-only patch. Manual resend can
still duplicate work. Keep the repair draft until integration decides whether to
land this observational improvement separately from the complete receipt repair.

Proposed contract, not implemented or approved for shared-file mutation:

- Persist a client request key before an ordinary send, scoped to authenticated
  owner, home and addressed agent. Bind it to the canonical complete payload,
  including input/model options. Identical keys/payloads return the same accepted
  identity; changed payloads reject. Repeated text with a new key is a new intent.
- Bind admission to the journal turn ID without an effect-before-receipt crash
  window. Exact read-only receipts expose accepted/pending, completed, genuine
  failure or unknown. Absence after a crash is not proof of non-admission and
  never authorizes replay. Keep existing approval/effect and journal fences.
- Reconcile only the exact locally recorded receipt. Multiple pending sends and
  tabs must not overwrite or clear each other's durable records. Preserve queued
  and steered inputs and their existing identities.
- No blanket 502/503, offline, truncated or silent-stream POST retries (#2646).

Likely shared boundaries to coordinate: `tinyassets/universe_server.py::converse`,
`tinyassets/foreground_run_provider.py` (owned by review lane),
`tinyassets/agent_turn_coordinator.py`, `tinyassets/storage/agent_turn_journal.py`,
owner receipt projection in `tinyassets/api/status.py` or the owner read router,
and `tinyassets/onboarding/__init__.py::_handle_turn_pending` only if that route is
extended. Existing `tinyassets/storage/request_admissions.py` and
`tinyassets/consumer_runtime.py` are precedents, not permission to add a writer.

#4308 was inspected at `cedc4f6dff8849051042de08eafba425141138fe`: held owner-lease B1
fences journal mutations and inventories writers. Its branch was not touched.
The integration coordinator must resolve that overlap before any receipt writer.

Outstanding acceptance: server contract/spec and shared-file ownership; exact
receipt tests for before/after admission, completed-but-lost replies, reload,
repeated text, multiple surfaces, auth/account/home/agent changes, uncertain
crashes, and no duplicated provider/effects; independent cross-family review,
protected CI on final head, integration/deployment proof and actual Android
background acceptance. None is implied by hermetic browser success.
