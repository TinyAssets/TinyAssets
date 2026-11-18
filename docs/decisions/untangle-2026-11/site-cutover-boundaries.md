# Public site cutover boundaries as built (2026-11-18)

Review of `f934871ad` and combined `5db0a6738`. The staged connection instructions and
developer profiles already read their new contracts. Their earlier browser and export
checks remain evidence for those pages. The legacy site branch `c25ce9ed7` still reads the
retired doors-local app list. Three further dependencies apply to the single F2 publication.

| Public consumer | Evidence at the reviewed commits | Dependency before release |
| --- | --- | --- |
| Commons graph sample | `PublicShapes.tsx` renders `lib/mcp-snapshot.json`. The site's refresher calls legacy MCP `read_graph`. The new directory, custom and run tool profiles expose 32, 33 and 29 tools respectively, and none registers that name. A capability of that name elsewhere does not register an MCP tool. | Assign a supported source for public graph data or explicitly retire the sample and its refresh path. Determine how the public page identifies old data if a snapshot is retained. |
| Account deletion | `WebSite/site-react/app/account/page.tsx` tells users to type `DELETE` on web, iOS, Android and desktop. The new contract defines `erase my account` in `platform/contract/src/owner.ts`. The new browser does not call its `account/erase` route. The door's immediate response is `{erasing:true}`; the richer answer belongs to the later shell step per `owner-erase-op.md` §8. | Verify instructions against the client actually available at F2. Test its path and distinguish acceptance from completion. Founder copy and shell release decisions stay open. |
| Handback | Merging watched website paths to main triggers the independent site deployment. The doors' handback switches routes and checks the old service. It does not republish the matching site revision. | Identify which site revision goes with each serving MCP and who republishes it on handback. Include old and new links, instructions and the snapshot disposition in the rehearsal. |

Two offline runs executed the actual snapshot refresher with an in-memory refusal for the
legacy tool, using the clean-sheet profile inventory. The default run exited zero. With
`SNAPSHOT_REQUIRED=1` it exited one. Both retained the checked-in September 2 snapshot
(SHA-256 prefix `08440e98e9c2`). Neither contacted a live MCP or published a site.
This was a review of cutover dependencies; the legacy site's currently serving backend and
customer deletion were outside the exercise.
