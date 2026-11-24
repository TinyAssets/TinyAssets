# uncalled-doors (2026-11-24)

`untangle/uncalled-doors`, owed by `door-types-owner.md`: `disableAgent`, `deleteAgent`,
`mailChain`, `grantForgetRevoked`, `fileRedact` and `fileHistoryPurge` had no caller but
tests. Each is either wired where the owner uses it or goes. Also from #5849's review: the run
profile still offered `agent_register`, which a run can never use.

## Research (Fact 14)

Where the owner acts. The owner's app (`doors/app`) reaches a door through an owner-surface
route (`doors/src/app.ts` `routes`) or through a table tool (`tools/<name>`). An MCP tool is
an outside client's: the owner's main agent reaches TinyAssets as an outside client under its
grant (Direction 2), never as the owner itself. A door whose caller is `owner` or `session`
is the owner's own act, so its place is the app. The Direction puts an agent's abilities in
files and rules out feature editors. Account controls (Disconnect, Turn off a key, Erase)
are not feature editors, and the app already has them.

| Door | What it does | Does the owner need it, and where? | What the major agents offer | Erasure or privacy | Verdict |
|---|---|---|---|---|---|
| `disableAgent` | Takes a main agent (an app's registered agent) out of mail and rooms. Its unacked mail is released, and its wakes and subscriptions end. Its rooms' history is kept. Its app registering again, and the owner applying that, turns it back on (`Ledger.register`). | Yes. `ledger_full` names the readers holding the most unread mail, and the ledger's own doc names turning one off as the way to free it (`ledger.ts` bounds). Disconnect (`grants/revoke`) keeps an MCP client's agent so the client can come back (`removeKeyAgents`). So a disconnected app's agent keeps holding its unread mail, and only the owner can free it. The place is the app, on the main agent's card. | Claude, ChatGPT and Grok scheduled tasks each have pause and delete. Connectors have disconnect. Pausing is the reversible half. | None of its own. It keeps the mail. | **Wire**: `agent/disable`, a Turn off button on Connected apps' main-agent card. The ledger's dead outside-caller branch (`origin`) goes: no binding carries an outside origin together with the owner's session to this door. |
| `deleteAgent` | Deletes a main agent: its registry row, its rooms with all their mail (archived mail too, `archive_purges`), its cursor, wakes and subscriptions. | Yes. It is the only erasure of mail short of erasing the whole account ("an archived message is the owner's until the owner deletes its agent", `ledger.ts`). The registry's refusal tells the owner to "delete a disabled agent to make room". A revoked client's grant row that its agent holds is freed for the revoked rows' bound only once the agent goes (`evictRevoked`). The place is the app, beside Turn off. | Deleting a GPT, a Project, a scheduled task or a conversation deletes its data. | Yes: erasure of one conversation partner's mail, which can hold a third party's personal data, without erasing the account. | **Wire**: `agent/delete`, a Delete button behind a confirm. |
| `mailChain` | The owner reads a mail chain whole and in order from any one of its messages, archived messages and mail between agents included, with each message's recipients and how far each got. | Yes. It is the owner's only read of archived mail and of its agents' mail to each other: `mailbox_read` and `inbox_digest` read the owner's own unread mail only. A run's receipt names the mail it answers (`RunReceipt.mail`) so the owner can read that chain. The place is the app, at Past runs. | Every assistant shows a task's or conversation's whole thread, past messages included. | It is the owner's own read of its own mail. | **Wire**: `mail/chain`, a "Read the mail" link on each run that mail started, showing that chain. |
| `grantForgetRevoked` | The owner, in session, forgets revoked outside clients. This lifts an MCP client's revoke: the fence stays, and a sign-in after the revoke records the client again. | No, not as its own door. (1) Room under the bound: `evictRevoked` forgets the oldest revoked row past the bound in every revoke's own unit, and nothing ever refuses for revoked rows. (2) Bringing a disconnected app back: the CIMD apps' client ids are fixed URLs (`apps.json` `registration: cimd`), so a disconnected Claude or ChatGPT keeps being refused "outside client revoked" until the owner lifts the revoke. `grants/set` already does that, keeping the client's name, profile and agent and its fence (`Grants.set`: "a revoked grant set again keeps its fence"). But the app hid revoked rows, so the owner had no way to do it. | Reconnecting is done from the app's own connector page. Neither vendor offers a "forget revoked apps" control. | A revoked row holds the client's id, name, vendor and seen times: the owner's own account metadata, which goes with the account at erasure. | **Goes**, in the two steps every door leaves by: marked dropped now, then taken out with `Grants.forgetRevoked`, its receipt rule and its tests (owed). The app lists disconnected apps with "Let it connect again" (`grants/set`, `mode: full`), so no client stays locked out. |
| `fileRedact` | Drops one kept version's content (a secret written by mistake). The current version is never redacted. | Yes. Retention is off until the owner sets it (Fact 15), so every version is kept, and `file_remove` and a new write both leave old content in history. Without this, a secret that an agent wrote can only be dropped by setting retention for every file, or by erasing the account. It is the owner's own act and never an outside client's: history is the owner's way back from an agent's write, and an agent must not be able to destroy it. The place is the app, at the file's history. | Google Drive deletes one version of a file (Manage versions). Dropbox permanently deletes a version. GitHub's advice for a leaked secret is to rewrite history and rotate the secret. | Yes: a leaked secret, or personal data, must be removable from history. | **Wire**: `files/redact`, "Remove its content" on each older version in the file's history. |
| `fileHistoryPurge` | Drops every kept version of a path but its current one. A removed path's history goes whole. It is never refused for a quota. | Yes. It is the per-file way out of a full history while retention is off, and the true delete of a removed file: retention would prune every file, and `file_remove` keeps history. It is the owner's own act, for the reason above. The place is the app, beside the history. | Drive and Dropbox both permanently delete a file with its versions. | Yes: deleting a file means its content goes. | **Wire**: `files/purge`, "Remove older versions" under the file's history. |

`agent_register` in the run profile: `own_card` refuses every run, because a run's binding
carries no source and a run never files a card (#5849 makes the refusal explicit). The
profile listed a tool that a run's door can only refuse. **Goes** from `contract/profiles.json`
`run`. `agents_list`, which a run is served, named it in its description, so that sentence now
names the owner's approval instead (`contract/guards/tools.test.ts` holds a served description
to its profile's tools).

## Decided

1. **App routes**, each one door (`doors/src/app.ts` `routes`): `agent/disable`
   (`disableAgent`, session), `agent/delete` (`deleteAgent`, session), `mail/chain`
   (`mailChain`), `files/redact` (`fileRedact`), `files/purge` (`fileHistoryPurge`). The table
   declares the answer fields the app reads (`contract/doors.json` `answer`).
2. **The app.** Connected apps: each main agent's card has Turn off (an enabled agent) or says
   it is off and comes back when its app registers again, and has Delete behind a confirm. Its
   list of apps names the disconnected ones, each with "Let it connect again". Past runs: a run
   that mail started links its chain, read a page at a time. Your data: a file's editor shows
   its kept versions, with "Remove its content" on each older one and "Remove older versions".
3. **`grantForgetRevoked` is dropped** (`contract/doors.json` `dropped: true`): the doors'
   `OwnerDoors` lacks it, and the owner still takes it, as `deploy/handed.ts` asks of a door
   that leaves. Once the stage serves doors from this tree, `Grants.forgetRevoked`,
   `MAX_FORGET`, both rows, the rule `outside_forget` and its tests go (owed). The forget that
   the bound makes (`#forget`, `evictRevoked`) and the key's forget (`grantForget`) stay.
4. **`Ledger.disable` loses `origin`**: its one caller is the owner's session door, so the
   outside-caller branch was a test-only path. The step row names its action ("turn off its
   agents") so the runner refuses an outside client in session by its rule, as at every
   owner-only session door. `deleteAgent`'s action names the act ("delete its agents"), not
   policy.
5. **The run profile** drops `agent_register`.

## As built

- `contract/doors.json`: `grantForgetRevoked` dropped; the answer fields the app reads
  declared for `mailChain` (`messages`, `more`) and `fileHistory` (`changes`, `next`);
  `agents_list`'s description. `contract/profiles.json`: the run profile without
  `agent_register`, its `about` saying why.
- `owner/doors.json`: `disableAgent`'s action "turn off its agents", `deleteAgent`'s "delete its
  agents". `owner/src/ledger.ts`: `disable` without `origin`, and its door without the account.
- `doors/src/app.ts`: the five routes. `doors/app/app.js`: `mainAgent` (the card, its Turn
  off and Delete), the disconnected apps, `chain` at Past runs, `versions` in the file editor.
  The app's Turn off names one reason, "turned off by the owner", which the agent reads back in
  `agents_list` (`disabled_reason`).
- Tests: `doors/test/app-routes.test.ts` (an app's agent turned off, back, its mail chain read,
  deleted; a disconnected app's revoke lifted with its fence standing; a file's older content
  and versions removed), `doors/test/app.e2e.mjs` (the card's buttons, the disconnected app,
  a run's mail, a file's versions), `contract/guards/tools.test.ts` (a run never served
  `agent_register`). The ledger's and wakes' tests of the outside-caller branch go with it.

## Found and left

- Disconnecting an app still keeps its MCP client's agent enabled, so the agent can hold
  unread mail until the owner turns it off. Turning it off on revoke would also need a
  re-enable when the owner lets the app back: a design change of its own, not made here.

## Owed

- [ ] live: the stage and its rollback target serve doors from this tree; then
  `grantForgetRevoked` goes whole: both rows, `Grants.forgetRevoked`, `MAX_FORGET`, the rule
  `outside_forget`, its tests in `grants.test.ts` and `proofs.test.ts`, and the grants module's
  lines naming it as the one lift of an MCP revoke (untangle/uncalled-doors-drop).
