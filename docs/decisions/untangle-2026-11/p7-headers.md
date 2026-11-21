# p7-headers as built (2026-11-18)

P7's remaining task (list3 T12) concerns the comments above the workflow triggers.
Of 64 workflows at `f934871ad`, 25 had headers describing superseded workflows,
past incidents, individual pull requests or reviews. Git retains the original accounts.

The headers keep the reasons an operator still needs. A droplet resize runs elsewhere
so it survives shutdown. The release check looks at a successful deploy on main
because an image build can precede a failed deploy or rollback. Dated measurements
that justify a current limit or configuration remain, including the platform canary's
measured schedule delay and the handles canary's response time. The actionlint job's
header now describes its actual command, which checks whole changed workflow files.

The parsed workflows match the base. Every byte from each workflow's `on:` line
onward and every non-comment line before it matches, including shell command text.
