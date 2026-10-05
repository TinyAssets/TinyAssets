## Context

Repository inspection on 2026-09-14 used a governed Linux checkout of main,
AGENTS.md, PLAN.md, the OpenSpec skill, delivery audit, and searches of current
specs. The audit reported zero claimed delivery WIP. The OpenSpec CLI was absent,
so this proposal uses the skill's documented manual layout.

PLAN.md already requires minimal primitives, community-built compositions,
replaceable agent components, private bindings, and first-class browser users.
It also requires voice to use canonical conversation and the user's serving
provider. These principles apply to the experience around the agent.

Existing integration anchors:
- `openspec/specs/universe-custom-agents/spec.md`: immutable public component
  compositions and private bindings.
- `openspec/specs/universe-personification-and-relay/spec.md`: canonical reply
  text and voice relay.
- `openspec/specs/desktop-host-runtime/spec.md`: existing dashboard, tray, and
  best-effort desktop notifications. This does not prove phone push delivery.
- `tinyassets/onboarding/app.html`: existing app surface, not proof of a generic
  experience interpreter.

The gap this proposal targets is a documented, verifiable composition contract
for the whole interaction experience. This inspection is not an exhaustive
absence audit of every UI extension.

## Scope and first proof

The long-term experience includes command centers, games, spatial offices,
voice-first earbuds, and combinations nobody has named yet. These remain
user-authored designs, never platform enums.

The first proof is one editable experience with a desktop instance board and a
compact phone view. A user replaces the board with an office layout while keeping
the same semantic instance bindings and actions. An event can be represented in
the office, announced through an available authorized speech adapter, or routed
to the user's phone notification destination. The same event identity connects
these representations.

A production 3D editor, every native device adapter, marketplace ranking, and
automatic personalization are outside the first implementation. The format must
preserve unsupported components so this initial renderer does not set a ceiling.

## Composition model

The following are semantic responsibilities, not a proposed list of MCP tools.
Before implementing, map each responsibility to existing primitives and document
only the irreducible gaps.

| Responsibility | Replaceable composition | Enforced boundary |
|---|---|---|
| Presentation | Layout, scene, theme, text, audio and accessibility alternatives | Governed renderer and resource limits |
| Input | Touch, keyboard, voice, gestures and event mappings | Authenticated source and schema validation |
| State binding | Which instance or artifact a component represents | Universe-scoped authorized reads |
| Action binding | Intent mapped to an existing Branch or control operation | Existing action authority and current target |
| Event routing | Filtering, grouping, quiet hours, destination and handoff | Existing channel grants and delivery receipts |
| Device adaptation | Capability predicates and declared alternatives | Honest availability and no silent privilege gain |

Components have stable user-named identifiers, typed inputs and outputs, explicit
references, versioned dependencies, and preserved lineage. A room, avatar, or
notification card has no authority of its own. Executable custom components use
governed adapters; imported HTML or scripts never execute in the authenticated
application's origin with ambient access.

Prefer a namespaced experience representation carried by the existing native
composition/interchange pipeline. Shape review must settle the extension and
renderer contract before adding fields or a new registry. Experience source and
assets should follow the inventory/integrity approach proposed in #3840 when
available; this proposal does not silently depend on an unshipped exporter.

## Definition, installation, and session

A shareable definition contains component source, deliberate demo assets,
capability requirements, schemas, dependency identities, and lineage. It contains
no real instance IDs, device tokens, credentials, conversations, notification
history, learned preferences, or private content. Only explicit publication makes
a definition public. Inspecting or importing a definition does not activate it.

A private installation maps symbolic roles such as `primary-instance` and
`phone-alerts` to the receiving user's authorized resources and channels.
This slice assumes the user's selected private-universe custody mode for these
bindings, without settling private custody for every deployment. Definitions and
private configuration remain separately exportable under that mode's authority.

Session state includes selection, focus, presentation progress and event cursors.
It is separate from authoritative instance/run state. Changing a view cannot
create a new agent identity, reset the conversation, or imply that background work
has stopped. Device-local focus need not synchronize to every other device.

## Devices, actions, and continuity

Each renderer declares available capabilities. A definition declares required
capabilities and intentional alternatives. Missing optional 3D, speech, or push
support selects a declared alternative with a visible explanation. Missing a
required capability prevents activation on that device. Preserve unknown
components for export/remix and identify them as unsupported; never silently
discard them or claim equivalent execution.

A desktop click, phone tap, or committed voice command resolves through the same
semantic action binding. The trusted boundary derives the user and universe;
a component-supplied identity is not authority. Validate the target and current
revision at action time. Denied, revoked, stale, or ambiguous actions return a
visible outcome and do not become an automatic alternate action.

Handoff carries references to the current universe, conversation, instance and
event, not copied authority or a second conversation writer. Voice output preserves
the canonical reply; any optional summary is explicitly labeled and cannot
replace the canonical record. Losing earbuds does not imply permission to read
private text aloud through a speaker.

## Events and notifications

Routing policy is editable composition. Quiet hours, grouping, interruption
preferences, and escalation are user choices. The substrate owns authenticated
delivery, revocation and receipts, not a fixed preference taxonomy.

Use a stable event ID and per-destination delivery identity. Reconnect can replay
events, but presentation must deduplicate within its documented retention window.
An acknowledgment updates the shared event state under a revision check; viewing
a toast is not acceptance of an action. Notification actions reauthenticate and
revalidate the target; an old notification never replays an already-completed
effect. Distinguish queued, delivered, acknowledged, expired, and failed outcomes
only when the underlying adapter provides evidence. Best-effort delivery is not
an exactly-once guarantee.

## Evolution and recovery

Users may edit source or ask their agent to propose a revision. Preview uses
fixture data and cannot invoke live actions, subscribe to private feeds, or send
notifications. Show the definition diff and changed capability requirements.
Activation replaces a selected installation revision under compare-and-swap;
concurrent edits yield a conflict rather than losing a user's changes.

Existing installations pin versions. Publishing a remix or updating a dependency
does not upgrade somebody else's active experience. A rollback restores a prior
compatible definition/binding revision after current permission checks, and
cannot reverse real-world actions already taken. Keep a trusted recovery control
outside custom content so a broken composition can be disabled.

## Acceptance and review decisions

Prove the same first-party composition can be exported, remixed by a second
account, privately rebound, and rendered on desktop and phone with canonical
conversation continuity. Retain rendered interaction evidence and effect receipts;
a schema round-trip alone is insufficient.

Before implementation, settle:
1. The existing native extension point, component schemas, asset bounds and
   governed renderer/adapter isolation contract.
2. The minimal semantic state/action/event interfaces and revision storage,
   based on actual existing handlers rather than a parallel control plane.
3. Capability negotiation and the honest fallback for each supported device.
4. Notification event IDs, replay window, acknowledgment semantics and adapter
   availability; no claim that phone push exists until proved.
5. Cross-family shape review, with AGREE / DISAGREE_EVIDENCE /
   DISAGREE_CONCERN findings and code citations where applicable.

---

## Implementation slice: executable UI bundles (2026-09-26)

The proposal above describes the whole contract. This section settles item 1 of
"Before implementation" — **the governed renderer isolation contract** — and
scopes the first slice that actually ships. Everything the proposal defers
(device negotiation, notification routing, voice handoff) stays deferred.

### What already exists, and what it cannot do

`tinyassets/onboarding/app_layout.js` (deleted 2026-10-01, see "One system, not
two") read one `tinyassets.app-layout.v1` component and *moved the app's own nodes*. It deliberately carries no HTML, CSS,
script or URL, because nothing in the app can safely execute imported code.
`openspec/specs/governed-agent-consumers/spec.md` states that limitation as a
requirement: the first adapter "SHALL NOT claim arbitrary executable UI".

That is the gap. A user who wants an office-building simulation cannot express it
by reordering four surfaces. The missing primitive is not another fixed surface —
it is a place to put arbitrary code plus a boundary strong enough to run it.

### The boundary, not a sanitizer

A shared UI is hostile input. Sanitizing markup or script is a losing game and is
explicitly **not** attempted. The bundle runs as arbitrary code inside a boundary
that holds regardless of what the code does:

1. **Opaque origin, enforced by response header.** `/mcp/app/ui-frame` serves a
   fixed bootstrap document under
   `Content-Security-Policy: sandbox allow-scripts`. The CSP `sandbox` directive
   applies to the document however it was loaded, so even a direct top-level
   navigation to that URL gets an opaque origin. `allow-same-origin` is never
   granted, so the document cannot reach `sessionStorage` (where `ta_access_token`
   lives, `app.html:710`), `localStorage`, cookies, or the parent DOM.
2. **No network of its own.** The same header sets `connect-src 'none'`,
   `default-src 'none'`, `form-action 'none'` and `img-src data:`. A bundle cannot
   fetch, cannot post a form, and cannot exfiltrate through an image URL. Every
   capability it has arrives through the bridge and nothing else.
3. **No nesting out.** `frame-src` falls back to `default-src 'none'`, so the
   bundle cannot embed a frame to shop for a weaker context, and
   `frame-ancestors 'self'` keeps the bootstrap from being framed off-origin.
4. **The bundle is never in the app's document.** The parent posts bundle source
   into the frame; it is never assigned to any node the app owns. The app's CSP
   gains exactly one term, `frame-src 'self'`, and keeps its nonce-only
   `script-src` — so even a bug that inserted bundle script into `app.html` would
   still not execute it.

Both sandboxes apply: the `<iframe sandbox="allow-scripts">` attribute and the
response-header CSP. Either alone is sufficient; the pair means a mistake in one
is not a breach.

### The bridge is the whole capability surface

Proposed extension (2026-10-04): `command-center-harness-control` owns generic
ta search/describe/call parity and owner-permitted connections, memory and harness
controls through this bridge. This change retains the renderer, frame isolation
and installation binding; do not build a second bridge or duplicate its dispatcher.
The implementation slice below describes the existing limited methods, not an
immutable limit on owner-controlled capabilities.

`app_ui.js` owns the parent half. A message is considered only when
`event.source === frame.contentWindow`; the action is looked up in a frozen map
and an unlisted action is refused by name, never guessed. Each handler builds its
own `MCP.callTool` arguments — a bundle cannot supply `graph_id`, because the
handler pins it to the **viewing** user's current home, captured at enable time
and re-checked against `fetchMe()`. Cross-user reach is therefore not refused by
a check that could be bypassed; it is unrepresentable.

Replies are assembled field by field from picked values. No server payload is
spread into a reply, so a field added upstream later cannot ride out to a bundle.

MVP allowlist: `whoami` (universe id and display name only), `list_agents`,
`send_message`, `read_conversation`. `send_message` addresses a named agent in the
viewer's own universe, which is what makes "click a room, talk to that agent"
work. One `send_message` in flight at a time.

### Where a bundle lives

- **Private, unpublished:** the viewer's own row in `universe_app_ui`, keyed
  `PRIMARY KEY (owner_user_id, universe_id)` and holding `ui_library` and
  `ui_selection` as JSON plus a `revision`. Read with `read_graph target="app_ui"`
  and written with `write_graph target="app_ui" operation="save"` on both the
  public connector and the engine surface, so the universe's own agent can write
  it. Private by default: publishing is a separate, explicit act.
- **Shared:** a `tinyassets.app-ui.v1` component inside a public agent
  definition, via the generic agent `publish`/`remix` path. A
  remix copies the component into the remixer's *own* row, where it runs against
  the remixer's bridge. The author's universe is never addressed.

**Why not an agent binding.** The first cut kept the library in the
`app_experience` `AgentBinding` configuration. A fresh account has no binding,
and a binding needs a published definition, so the next cut made
`agent_bindings.agent_definition_id` nullable. A cross-family review of that
change returned REJECT (2026-09-26): the table rebuild was not atomic, so a crash
or a second initializer stranded every existing binding; definition-less rows
reached model bootstrap, consumer selection, serving and `_serving_hint`, none of
which select by definition; and the bootstrap had no uniqueness to stop two rows.
All three came from one cause: a UI choice is not an agent binding. So it has its
own table, created with `CREATE TABLE IF NOT EXISTS` (no rename, no copy, no
migration), and `agent_bindings` is byte-for-byte what it was.

**Writes.** One compare-and-set statement per save, no read-then-write window:
`expected_revision = 0` is `INSERT ... ON CONFLICT(owner_user_id, universe_id) DO
NOTHING`, anything else is `UPDATE ... WHERE revision = ?`. Two first saves
racing both name 0; the key admits one row and the other is refused as a
conflict. An omitted field keeps its stored value, so saving a choice never
erases the library. `ON CONFLICT DO UPDATE` was considered and not used: it
cannot express "update only at this revision, but never create when a revision
above 0 was named", which is what stops a stale client resurrecting a deleted row.

**Authority.** The row is keyed by the authenticated caller, never by anything
the caller names, and universe access is the same read/write check agent
bindings use (`api/custom_agents._binding_access`). Account deletion removes a
person's rows in every universe by the owner key ONLY (`OWNER_ONLY_TABLES`),
never by the universe sweep: a collaborator's choice about a deleted owner's
universe is the collaborator's, and a universe sweep could take it because a
save can commit between the foreign-row check and the delete (review,
2026-09-26).

`ui_library` is a **list** of any length -- no count cap, because the founder's
rule is to limit usage, never structure -- each entry a `ui_id`-keyed object with
no duplicate ids. Its one bound is total canonical-JSON bytes,
`MAX_APP_UI_LIBRARY_BYTES` (4 MiB), sized so a light user never meets it: 85 UIs
at the per-UI maximum still fit. No per-universe storage quota covers database
rows yet; when one exists, the library should be charged against it instead. The
app checks the same number before a write, against the row that write read, and a
test ties the JS constant to the Python one. `ui_selection` is capped separately (1 KiB), because a partial
save never sees the other field.

### One system, not two

**Superseded 2026-10-01 (founder, live):** "there should only be switch ui",
and the App design controls "are better as things that should be part of the
custom ui". The App design dialog, its four-surface layout component and
`app_layout.js` are deleted (production held zero layout definitions and zero
installations). What it did moved as follows: arranging and spacing are what a
custom UI is; searching, installing and publishing designs is the universe's own
`app_ui` writes plus the consented `publish` ask; conversation behaviour is two
bridge actions, `conversation_design` and `set_conversation_design`, where the
bundle asks and the person approves in the app's own prompt. Trusted recovery
(restore default / previous conversation) lives in the Switch UI dialog. The
`app_experience` binding is unchanged: it is the server's turn-consumer
installation. The UI controller owns its own read and its one CAS write path.

### Deferred, and named so it is not mistaken for shipped

Device capability negotiation, notification routing, voice handoff, multi-file
bundles with binary assets, and a real per-universe file store. A rendered
real-browser proof through `ui-test` is required before this is called
user-ready; test-harness evidence is not that proof.

### Cross-family review round (Codex gpt-6-astra, 2026-09-26, head `6b85ee1e`)

Verdict ADAPT: three P1 and two P2, all real, all fixed. Recorded because four of
the five are things the *shape* got wrong, not typos.

1. **CSP does not cover every egress path.** `connect-src 'none'` stops fetch,
   beacon and WebSocket, but **not WebRTC**: ICE gathering resolves
   attacker-controlled STUN hostnames, so a bundle could encode what the bridge
   showed it into DNS lookups with no permission prompt and nothing for the policy
   to see. DNS prefetch is the same class. There is no CSP directive for either,
   so a directive would have been a comfort, not a control. The frame's bootstrap
   now **removes the capability from the realm** (`RTCPeerConnection` and friends,
   non-configurable and non-writable) and the response carries
   `X-DNS-Prefetch-Control: off`. Removal holds only because there is no route
   back to a pristine realm — nested frame, worker and popup are each refused by
   this document's own policy, which is why those three absences are now asserted
   next to the removal rather than separately.
2. **A home change did not revoke a mounted bundle.** The same account can move
   home mid-session; `converse` and `get_status` resolve the *caller's current*
   home, so a bundle granted access to one home would have been served the next
   one's conversation. Sign-out tore the bridge down; this path did not. Now:
   `AppUI.homeChanged` on the single transition funnel (`setQueueScope`),
   `AppUI.reset` on the account-scoped clear, a `verify()` against `fetchMe`
   before every action, and the conversation read pinned to `this.home` with the
   **answer's** universe checked. Four layers because each covers a different
   window, and a test proves each one fires on its own.
3. **Installing could erase what it could not read.** `adopt` emptied its cache
   when any stored bundle was unsupported, and `install` rebuilt `ui_library` from
   that cache — so installing next to a future-version bundle deleted it, and CAS
   could not object because the revision was current. An unreadable library is now
   remembered *as unreadable*, install refuses on it, and the guarded mutation
   rebuilds from the configuration the write actually observed.
4. **Replies were fenced to the account, not the asking frame.** Switching bundles
   did not advance any generation, and both bootstraps number requests from `r1`,
   so bundle A's answer could settle bundle B's identically-named promise. A frame
   generation is now captured per request.
5. **Sizes were counted in UTF-16 units against a byte cap.** Three separately
   accepted CJK bundles could bust `MAX_AGENT_JSON_BYTES` at write time, surfacing
   as an unexplained "uncertain" editor. Sizes are UTF-8 bytes now, the
   whole-configuration size is checked before the write *and* inside it, and a
   test ties `MAX_CONFIG_BYTES` to the Python constant.

One guard was **deleted** rather than kept: a generation check in `post` that no
mutation could make fire, because `serve` already fenced every async reply. A
guard no test can turn red is decoration, and decoration next to real locks is
worse than nothing — it invites trusting the wrong line.

**Residual, accepted, and not claimed as closed.** A bundle can navigate *itself*
away; the parent's `frame-src 'self'` bounds where to, so it cannot carry data to
a third party, but it can put it in this origin's own access log. And a bundle
keeps whatever the bridge already showed it — which is why the switcher dialog now
says so in plain words rather than only listing the four actions.
