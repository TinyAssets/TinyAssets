# Contributing to TinyAssets

This repository is the product: the hosted app, the public MCP server, the
Android and desktop shells, and the site. It is MIT-licensed. One person
maintains it: Jonathan Farnsworth
([jonathan.m.farnsworth@gmail.com](mailto:jonathan.m.farnsworth@gmail.com),
[@Jonnyton](https://github.com/Jonnyton)).

There is no review-time commitment. A pull request can sit. That is a
capacity fact, not a judgment of the patch.

## What an older edition of this file claimed

The previous text told contributors to use a separate catalog repository, to
add connectors under `connectors/<name>/`, to add a `Signed-off-by` line, and
to expect a first response within 48 hours, with escalation to
`security@tinyassets.io`. That does not describe this repository:

- There is no `connectors/` tree.
- The former catalog prototypes are retired; this repository is the contribution target.
- A `Signed-off-by` trailer is not required.
- Security reports go to [SECURITY.md](SECURITY.md). A quiet pull request is
  not a security incident.

TODO(owner): say whether you want a Developer Certificate of Origin, and
whether any external catalog repository is still a contribution target.

## A code change

1. Branch from `main`. One intent per pull request.
2. Read [AGENTS.md](AGENTS.md). It is the working agreement for this repo.
   Changes to the public MCP or API, storage shape, authority, migrations, or
   money need a spec first. Other changes can be a small diff with no plan.
3. Python 3.11+. From a checkout:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m scripts.ci_structural_guards
pytest -q tests/test_<area>.py
ruff check <changed files>
```

Tests run offline. Providers are mocked. API keys are not required for that
loop. CI runs the rest. Required CI does not run the files listed in
`.github/heavy-test-files.txt`.

4. Open the pull request against `main`.

A second model family reviews floor-class changes and gate-file changes only
([ADR-015](docs/decisions/ADR-015-only-the-floor-blocks-a-deploy.md)). That is
narrower than "every change gets a second review."

TODO(owner): the public developers page says every change gets a review from
a second model family (`WebSite/site-react/app/developers/page.tsx`). That
sentence is broader than ADR-015.

Leave `.github/workflows/` and `deploy/` alone unless the change is
specifically about them. This repository's production path is easy to break
from a docs-shaped pull request that also edits a workflow.

## Words

The user-facing name is **command center**. Code, stored data, and many older
docs still say **universe**. The map is
[docs/architecture.md](docs/architecture.md). A drive-by rename is a large
change of its own; the open note is
`openspec/changes/rename-universe-to-command-center/`.

## Issues

Use the templates:

- [Bug report](https://github.com/TinyAssets/TinyAssets/issues/new?template=bug_report.yml)
- [Feature request](https://github.com/TinyAssets/TinyAssets/issues/new?template=feature_request.yml)
- [Tester feedback](https://github.com/TinyAssets/TinyAssets/issues/new?template=tester_feedback.yml)

Do not paste passwords, setup tokens, or session cookies into an issue.

On 2026-10-11 GitHub reported 1,314 open issues. Open pull requests were in
the single digits on two reads that day. 932 open issues were labeled
`watchdog` and 126 were labeled `deploy-failed`.
Those are opened by repository automation. The `good first issue` label
existed and had zero open issues. Treat the issue list as an automation log
until someone triages it. The templates above do not apply to issues that
Actions opens.

TODO(owner): close or silence the automation backlog, and label a few real
starter tasks `good first issue` if you want outside contributors.

## Conduct and security

- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [SECURITY.md](SECURITY.md)

## Where to start if you are not sending code

Testers: [docs/getting-started/testers.md](docs/getting-started/testers.md).

Self-hosting: [docs/getting-started/self-hosting.md](docs/getting-started/self-hosting.md).
The production server is not a supported install on your own machine.
