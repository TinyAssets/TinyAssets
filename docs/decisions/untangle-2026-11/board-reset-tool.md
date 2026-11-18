# The board digest names the public collection reader

The board's reset tells a client that a deletion it missed is past retention.
The tool description on main `f934871ad` pointed that reader to
`collection_pull`. There is no public tool by that name. The owner method
is `collectionPull`. Its tool in the same `platform/contract/doors.json`
is `collection_list`. The public name now appears in the existing board
description and its two generated TypeScript comments. No alias or new door
is needed. The owner still returns the cursor to resume after rereading.

The real tools-list recorder for all three stock profiles, including the
listed custom client and the code-on and code-off variants, and the account
app produced seven canonical lines. Compared with main, the six MCP lines
each changed exactly this one reference. The account app line was identical.
The other fit limits and the owner controls on a custom profile still apply.
