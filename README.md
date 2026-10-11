# TinyAssets

An agent of your own in the chat bubble: connect any model source, then build what you need from there.

## Try it

The hosted app is the supported way in. Android is in a closed Google Play test, not a public store listing. The steps, what to expect, and how to report a bug are in [docs/getting-started/testers.md](docs/getting-started/testers.md).

- Web app: <https://tinyassets.io/app>
- Start page (web, Claude or ChatGPT connector, Android, desktop): <https://tinyassets.io/start/>
- Android closed test: <https://play.google.com/apps/testing/io.tinyassets.app>

You connect your own model subscription. This project does not supply a model. What is free, and what is not built yet, is on <https://tinyassets.io/fine-print/>.

Running the production server on your own machine is not a supported install. Status: [docs/getting-started/self-hosting.md](docs/getting-started/self-hosting.md).

<!-- direction:start -->
## Direction

The founder owns this section. To re-steer, replace a line; never add one beside it.
`python scripts/sync_direction.py` copies it verbatim into `AGENTS.md`.

1. Product: a default bubble agent that continuously matches and beats the industry-leading main agents (Grok Bot, Muse, ChatGPT dots, Claude), built as a starter package on a pi.dev-modelled harness; users customize the harness and interface to build anything.
2. Plumbing: the model sees 4 tools (read/write/edit/bash) plus `ta`; one extension unit; one agent definition for every model provider; no provider-specific code.
3. The agent's abilities are editable files, skills and packages. The platform does not build feature editors or pre-built features.
4. Users connect any model source; default to their strongest connected source.
5. Foundational patches are brought FORWARD, so pre-migration band-aids never delay positive architectural moves. Weigh the totality of pending work, so each module is always pursuing or maintaining its best architecture, and refactor and reorder the remaining work to get there. (Today that foundation is per-owner isolation, as one clean cutover: the isolated path is the only path, one migration in a short maintenance window after a full backup, and cross-owner app checks the OS now enforces are deleted in the same change.)
6. Ship live fast and verify live. LESS process: delete stale docs, tests and notes rather than adding more.
7. Long-term goal: in both the Google Play and Apple App Store, and listed in the major agents' connector directories (Meta Muse, ChatGPT, Claude) so users find TinyAssets by search, not only as a custom connector, with growing downloads and positive reviews.
8. 24/7 uptime with zero hosts online: every surface works with no host machine on.

No longer the direction: "a global goals engine", Goal ladders, and fantasy as the default domain.
Where any older doc disagrees with this section, this section wins.
<!-- direction:end -->

## Links

- App: <https://tinyassets.io/app>
- MCP connector (add it as a custom connector in Claude or ChatGPT): <https://tinyassets.io/mcp>
- Testers: [docs/getting-started/testers.md](docs/getting-started/testers.md)
- Live status (deployed sha, canary, providers): <https://tinyassets.io/fine-print>

## Contributing

Python 3.11+. Tests run offline; providers are mocked, so no API keys are needed. How to contribute, and what this repo does not promise, is [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
git clone https://github.com/TinyAssets/TinyAssets.git
cd TinyAssets
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m scripts.ci_structural_guards               # the fast guards every PR runs
pytest -q tests/test_<area>.py                       # the tests your change touches
ruff check <changed files>
```

CI runs the rest. How work is done here: [AGENTS.md](AGENTS.md).

## License

MIT — see [LICENSE](LICENSE). Built by Jonathan Farnsworth (jonathan.m.farnsworth@gmail.com, [@Jonnyton](https://github.com/Jonnyton)); the only co-authors are the project's own AI agents.
