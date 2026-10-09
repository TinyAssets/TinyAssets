# TinyAssets

An agent of your own in the chat bubble: connect any model source, then build what you need from there.

<!-- direction:start -->
## Direction

The founder owns this section. To re-steer, replace a line; never add one beside it.
`python scripts/sync_direction.py` copies it verbatim into `AGENTS.md`.

1. Product: a Meta-Muse-level agent in the chat bubble, on pi.dev-style plumbing; users build from there.
2. Plumbing: the model sees 4 tools (read/write/edit/bash) plus `ta`; one extension unit; one agent definition for every model provider; no provider-specific code.
3. The agent's abilities are editable files, skills and packages. The platform does not build feature editors or pre-built features.
4. Users connect any model source; default to their strongest connected source.
5. Foundational patches are brought FORWARD, so pre-migration band-aids never delay positive architectural moves. Weigh the totality of pending work, so each module is always pursuing or maintaining its best architecture, and refactor and reorder the remaining work to get there. (Today that foundation is per-owner isolation, as one clean cutover: the isolated path is the only path, one migration in a short maintenance window after a full backup, and cross-owner app checks the OS now enforces are deleted in the same change.)
6. Ship live fast and verify live. LESS process: delete stale docs, tests and notes rather than adding more.
7. Long-term goal: in both the Google Play and Apple App Store, with growing downloads and positive reviews.
8. 24/7 uptime with zero hosts online: every surface works with no host machine on.

No longer the direction: "a global goals engine", Goal ladders, and fantasy as the default domain.
Where any older doc disagrees with this section, this section wins.
<!-- direction:end -->

## Links

- App: <https://tinyassets.io/app>
- MCP connector (add it as a custom connector in Claude or ChatGPT): <https://tinyassets.io/mcp>
- Live status (deployed sha, canary, providers): <https://tinyassets.io/fine-print>

## Contributing

Python 3.11+. Tests run offline; providers are mocked, so no API keys are needed.

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
