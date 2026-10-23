LANE: sign-in stays in the user's own context (tracker item 8.12 in openspec/changes/owner-daemon-in-box/tasks.md).
Worktree C:\Users\Jonathan\Projects\wf-signin-context, branch feat/signin-in-context (from main eb582d82d08).
Founder rules: a user is never sent somewhere they aren't. Web: the provider sign-in happens in the same tab and returns
to the app. Phones (mobile/, Capacitor): an in-app browser sheet, returning by the app's own scheme. Desktop
(desktop-app/): the system browser, in the browser profile that is already open for the matching account when one is,
then returns to the app via its scheme; never an embedded window. The fewest taps. Applies to every sign-in path
(app sign-in, model source OAuth, subscription CLI logins, connector OAuth), provider-agnostic.
Observed live: the OAuth tab said "you can close this" instead of returning; two browsers with different profiles
sent a sign-in to the wrong account; a pending approval link from another account produced a 409 loop (now dropped).
Audit every place that opens a sign-in URL, route them through one helper per surface, and test each surface's
return. Open a draft PR. Note anything that needs a new native app release.
