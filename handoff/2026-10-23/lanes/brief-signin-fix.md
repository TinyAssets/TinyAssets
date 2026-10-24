Fix round on PR #5231 (worktree C:\Users\Jonathan\Projects\wf-signin-context, branch feat/signin-in-context). Review:
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-signin.md
Fix finding 1 (MEDIUM): on native and desktop, "Sign in to approve" (/app/owner-sign-in) must run the owner-proof flow
for the account already signed in, never a full app re-login that can switch accounts; if the owner-proof flow cannot
complete on that surface, do not show the link there and say what the owner does instead. Fix finding 2 (a web
sign-in the user abandons is cancelled server-side when it expires or the user starts another). Finding 4: if either
desktop flow can be made to finish with the existing scheme return, do it; otherwise record it in the PR body.
Finding 3 is accepted; leave it. One focused test per fix that fails first; push to the same branch.
