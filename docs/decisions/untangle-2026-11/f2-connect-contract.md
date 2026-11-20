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
