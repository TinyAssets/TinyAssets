# Security policy

## Reporting

Email [info@tinyassets.io](mailto:info@tinyassets.io). That is the mailbox
the maintainer reads. The public site (`WebSite/site-react/lib/site.ts`)
publishes that same address for security reports.

GitHub private vulnerability reporting is **disabled** on this repository
(checked 2026-10-11 via the private-vulnerability-reporting API,
`enabled: false`). A vulnerability does not belong in a public issue.

TODO(owner): turn on private vulnerability reporting if you want a
GitHub-native channel, and publish a disclosure window if you want one.
This file promises neither a bounty nor a response time. There is no
published bug-bounty program.

Please include what you found, where (URL, app surface, or file), and the
impact you believe it has. Leave out session cookies, passwords, and other
people's data. Give the maintainer time to ship a fix before you publish
details.

## In scope

- The site and hosted app at `https://tinyassets.io`
- The public MCP endpoint `https://tinyassets.io/mcp`
- The Android application this project distributes
- The code in this repository

## Out of scope

- The model provider you connect (Claude, ChatGPT, or anyone else). Report
  those to that provider.
- A product bug with no security impact. Use the
  [bug report template](https://github.com/TinyAssets/TinyAssets/issues/new?template=bug_report.yml).
- Social engineering of the maintainer.

## Older documents

`CONTRIBUTING.md` used to say a PGP key lived in `SUCCESSION.md`. No PGP
block is in that file. `SUCCESSION.md` is a 2026-04-19 operational note. It
is not this policy.
