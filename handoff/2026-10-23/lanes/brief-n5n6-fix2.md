Second fix round on the N5 spec, PR #5229 (worktree C:\Users\Jonathan\Projects\wf-n5n6-spec). Finding (HIGH):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-rereview-n5n6.md
The box can commit credential bindings (broker/server.py committed_via="box"; http_connect.py accepts it; extend/full
updates carry no committed_via), so an agent could deposit a binding for any destination and bypass allowlist/ask.
Fix the spec: only bindings committed or widened through the protected owner door (the N10 owner session) satisfy a
restrictive mode; box-committed bindings and box-widened scopes never do. State that extend/full updates record their
committer the same way. Add the spec scenario "a binding the box commits does not satisfy allowlist or ask" and name
the matching test in tests/test_owner_broker_egress.py. One writer of egress permission: the owner. Push to the same
branch. Keep it short.
