Fix round on the C12 / S17 spec, PR #5241 (worktree C:\Users\Jonathan\Projects\wf-c12-spec). Review
(DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-c12.md
Resolve every finding by deciding: 1 make the S17 guard concrete (exact paths/globs/regexes the guard rejects, the test
name, the check_shapes.py ceiling); 2 add the T10 requirement (published knowledge is copied out of the box at publish
time via BOX_PUBLISH; no shared reader of private knowledge); 3 the conversion never silently drops data (counts per
source, refuses on loss, reports what it could not convert); 4 each slice names the exact modules/files it deletes;
5 decide every duplicate export door; 6 name the import format (the export manifest, with boxd.py import_bundle);
7 cut what the review called over-built and close what it called open. Push to the same branch. Keep it short.
