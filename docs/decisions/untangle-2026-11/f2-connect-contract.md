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
