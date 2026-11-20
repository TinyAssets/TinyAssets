# F2 public connection guide as built (2026-11-18)

The existing site listed four tools by hand. The parked site's guide used
`platform/doors/apps.json`, which moved to `platform/contract/apps.json`. The
current contract supplies six app IDs and the URLs that other platform doors give
owners. Each URL ends in the matching section ID on the public guide.

The static page takes its ordered instructions, app links, example messages,
commands, source links and checked dates from the contract. The ChatGPT section
includes the Codex command. Its old `#codex` fragment reaches the command without
introducing a seventh app. The site gets its public origin, MCP address and app
path from `identity.json`. Both JSON files are site publisher inputs. The guide
does not infer whether a client is registered from the presence of its instructions.

The export sweep compares the entire set of rendered sections and the steps in
each with the app catalog at phone and desktop widths. Build the design system,
install the React site, run the site's tests, then build its static export and run
`scripts/sweep.py` from that site. Real registration still belongs to the stage's
connector checks. Publication follows the route cutover. The site's wider profile
content and the founder's privacy and deletion copy remain with that release.

### Machine-readable connection guide, November 19

The Connect page takes the app and connector addresses from the identity
contract, but the exported llms.txt carried separate literals. The static
text route now uses the same site addresses as the HTML. It preserves the
existing public wording and URL. The ordinary site sweep checks the exported
guide's address list and connection instructions before opening Chromium.

- [x] All 41 site tests passed on the candidate.
- [x] On 08709eb0 the site exported the original 2,551 guide bytes exactly.
  With the developer-profile and mark stages, the publisher's Node 20
  build passed all 41 site tests and emitted the same bytes. Both builds
  finished all 29 static generation steps, including the new text route.
- [x] With a synthetic host and two different paths, the original a2df7280
  HTML changed but its guide retained the previous addresses. The repaired
  export changed every relevant address and preserved all other bytes.
- [ ] The same alternate export revealed eight sitemap entries, the robots
  sitemap reference and the legal page's canonical URL still pointed at
  the old host. Reconcile these public crawl references with the site's
  origin. The fresh browser sweep and route readiness remain open.

- [x] Replaced the copied crawler host in robots.txt and sitemap.xml with
  the site origin. The legal page uses its own route under the existing
  metadata base. The ordinary sweep checks the exported crawler references
  and the legal canonical URL. Both the standalone and combined site test
  suites passed all 41 checks. Final export comparisons follow.
- [x] On 08af61c1, the standalone Node 24 export and joined Node 20 export
  both retained the original robots and sitemap bytes. The new legal
  canonical matched the public legal route, including its trailing slash.
  The separate alternate-host Node 20 export used its own host in every
  reference. On all three exports, the pre-browser checks accepted the
  original files, refused separate stale-host mutations in llms.txt,
  robots.txt and sitemap.xml, and accepted the restored files.
- [ ] The browser previously stopped before navigation. These export checks
  leave the viewport, clipboard and redirect checks open on this revision.
  Release still follows route readiness. None of these changes is on main.
- [x] Corrected the preview guide's instruction that merging does not
  deploy. Read the publisher on f934871ad, the standalone Connect, mark
  and site-cutover plan branches, and the combined Connect/profile tree.
  On each, a change to the Connect page on main matches the push filter
  and a successful build enables the Pages deploy. Changing the preview
  text alone is outside the filter. This was an offline workflow review.
- [x] The combined publisher also watches profiles.json. All three site
  guides copied a shorter list and omitted that input. Each now links to
  the workflow's actual push filter. The three relative links resolved
  to the tracked workflow. Its filter covered the three imported platform
  contracts and the shared public reader in the combined site.

- [x] Compared the crawler files, legal page, site test, sweep and publisher
  at 162 live branch tips, including main f934871ad. Only Connect ba600559
  moved the three public files into export routes. Two historical hotfix
  tips retain different legal, sitemap and guide content, including the
  retired token page. They remain separate from this release.
- [x] Against developer profiles ff77e978, the sweep has one textual
  conflict immediately before the local server starts. Keep both the
  profile catalog read and the crawler checks. The tested joined file
  was exactly the result of retaining both blocks. Against mark bf2c2e59,
  the site boundary test combined automatically and matched the tested
  file byte for byte. The P7 header branch changed publisher comments
  only. Its combined workflow parsed identically to the two-site workflow.
- [ ] The fresh ordinary sweep on the corrected branch passed its static
  preflight and stopped at Chromium launch because Playwright's executable
  was unavailable. No browser navigation ran on that attempt.
- [ ] The displayed server address and the actual vendor setup can
  disagree. Changing only identity.host and identity.mcp on the site
  stage left seven old addresses in the instructions for five apps.
  The staged vendor-wake catalog at 17a2e42c added an eighth. Claude's
  connector link also carried the old address in its encoded query.
  The ordinary contract regression changes the identity and reads the
  actual dialog query, Codex command and Cursor config. All three
  assertions failed and seven existing app guards passed. The site's
  top address followed the new identity in both real static exports.
  Use the shared catalog reader for both the site and Worker consumers,
  keep vendor destinations and source text, and test the combined
  vendor-wake entry. The guide's own absolute page address is a separate
  catalog field used by Owner for its per-app links.
- [x] The shared app catalog reader now resolves the MCP resource in
  authored instructions and encodes it once when it is a vendor query
  value. The Connect site reads that same catalog. The site and the
  contract use the same public address exports. Literal historical
  catalogs and explicit guide-page URLs remain supported. The publisher
  watches the four source modules the site now consumes.
  The eight app guards and four Doors served-text guards passed. A real
  alternate-address export including the vendor-wake desktop instruction
  contained zero old addresses in its instruction text. For that join,
  apply the resource reference to the additional desktop step as well.
  All six app records and six guide anchors matched expectations in both
  the shipped and the alternate vendor catalogs.
- [x] On 43b62a21 the serial contract suite passed 298 with 18 large
  cases outside the filter after an earlier parallel worker was killed.
  Full platform TypeScript and both Owner connected-app Worker cases
  passed. The site passed 41 on Node 24 alone and 41 on Node 20 with
  developer profiles and mark. Both static exports completed. Comparing
  their HTML and the alternate vendor export verified all six sections,
  18 steps, four per-app copy texts and three vendor links in each.
  No fresh Chromium navigation is claimed.
- [x] A three-way file merge of the base, 43b62a21 and the vendor-wake
  catalog applies cleanly but silently retains the old server in Grok's
  desktop step. The ordinary app regression now compares every app's
  setup before and after the identity change. The uncorrected catalog
  failed for Grok alone (seven other tests passed). With that step
  using the resource reference, all eight passed on Node 22 and full
  platform types passed. Shipped output and literal custom catalogs
  matched on Node 22 and 24. Recheck the actual combined release head.
- [x] On 982cb100 the alternate-address export changed the footer link
  target but printed the old server beside it on all 27 exported HTML
  files. Home and Fine Print also printed the old server and Start
  printed the old app address. The footer and those three pages now
  format their labels from the same addresses used for navigation.
  The current and alternate exports both passed the HTML comparison on
  all 27 files and all 41 standalone site tests passed. The ordinary
  browser sweep checks the footer label and its destination together.
  Addresses in the founder-owned legal policy remain for the legal
  copy review before the coordinated site release.

### Five staged branches together

- [x] Rehearsed Connect 9d75f896, developer profiles ff77e978, the mark
  bf2c2e59, read status ef837894 and vendor wake 17a2e42c against their
  common main content f934871ad. For the sweep keep the identity/crawler,
  profile catalog and fine-print preflight blocks. In the route loop
  keep the developer tool-list assertions and the new footer assertion.
  The vendor catalog merges textually but needs the resource reference
  in its Grok desktop step. No other conflicting file required a choice.
- [x] From the resulting checkout with clean installs, the Node 24 site
  passed 41 tests and the full static export. On Node 22 all 298 ordinary
  contract tests passed, 18 size cases were excluded, and the full platform
  typecheck passed. Every one of 27 footer labels matched its target.
  All six app sections, 18 steps, four vendor copy texts and three vendor
  links matched the resolved catalog. Fine Print served the sign-in notice
  without a read time. The publisher watched all seven shared platform
  inputs. The ordinary browser sweep passed static preflight and stopped
  at launch because Playwright Chromium was unavailable.
- [ ] Recheck the reviewable release head in a browser and verify route
  readiness before the single coordinated release.
