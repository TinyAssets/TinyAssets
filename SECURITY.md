# Security policy

## Reporting

Email [security@tinyassets.io](mailto:security@tinyassets.io). That is the
address the public site already publishes (`WebSite/site-react/lib/site.ts`,
developers page).

The maintainer address published in this repository is
[jonathan.m.farnsworth@gmail.com](mailto:jonathan.m.farnsworth@gmail.com)
(also in `pyproject.toml` and the README). Use it if the `security@` address
bounces.

TODO(owner): `SUCCESSION.md` (dated 2026-04-19) still lists `ops@` and
`security@` routing as an unchecked item, and it says those aliases were "to
be provisioned." Confirm both mailboxes are delivered and monitored, then
update that file. This policy does not prove the mailbox works.

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
