# Self-hosting status

**Status: the production server is not a supported install.**

Checked against the code in this repository. This page is the status. It
is not a guide for standing up tinyassets.io on another machine.

## Supported

Use the hosted app: [testers.md](testers.md).

Clone the repo and run the offline tests if you are changing code:
[CONTRIBUTING.md](../../CONTRIBUTING.md). That path needs Python 3.11+
and `pip install -e ".[dev]"`. It does not need the production server.

## The production server refuses a machine that is not production

`python -m tinyassets.serve` is the container entry point
(`tinyassets/serve.py`). It starts `tinyassets.universe_server.main`.
That function refuses to boot unless the process is an admitted cloud
runtime (`tinyassets/platform_runtime_provenance.py`). The module states
there is no environment-variable bypass and no degraded local mode.

A checkout, a copied env file, a container name, or a compose label does
not make a process admitted. Copying this repository does not give you
the hosted app.

The same refusal applies to the `tinyassets-universe-server` console
script, which points at `tinyassets.universe_server:main`
(`pyproject.toml`).

## What else exists, and what it is

`pyproject.toml` also installs:

| Script | What it is |
|---|---|
| `tinyassets-mcp` | Local file-interface MCP server (`tinyassets/mcp_server.py`). It is not `https://tinyassets.io/mcp`. Its own instructions still describe an older daemon file interface. |
| `tinyassets-cli` | `python -m tinyassets`. The module docstring still describes a domain-daemon bridge into `fantasy_daemon`. |
| `tinyassets` | Desktop launcher. The public site describes the desktop app as a window over the hosted web app. |

The Android and iOS shells load `https://tinyassets.io/app`
(`mobile/README.md`). Installing the shell does not bring the server
with it.

## Operator notes that are easy to misread

`deploy/README.md` still shows a local `docker run` against port 8001
and says the image is runnable locally for validation. That file is an
operator note. It is not a supported self-host guide. It describes a
smoke that the admission check above does not allow on a machine that
is not the production runtime.

TODO(owner): reconcile `deploy/README.md` with
`require_process_cloud_admission`, or mark that local smoke retired, so
readers stop treating it as an install. Also say whether a supported
self-host (and of which slice: tests only, local file server, or the
full app) is a goal. This page will stay "not supported" until that
decision is written down and the code matches it.

## Names

User-facing copy says command center. Process and storage code still say
universe. [docs/architecture.md](../architecture.md) is the map.
