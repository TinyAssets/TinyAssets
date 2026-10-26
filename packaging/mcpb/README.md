# MCPB Bundle Template

`packaging/mcpb/` is the source template for the TinyAssets Server
MCP Bundle. It is not the final packable directory by itself.

Use the build script to stage a minimal bundle that contains:

- the official MCPB `manifest.json`
- a small runtime project with only the FastMCP dependency
- a stdio wrapper server
- a staged copy of `tinyassets/`
- the bundle icon asset

## Cloud-runtime caveat

Staging, validating and packing this bundle is a packaging operation only. It
does not make the bundle a runnable local server: under the cloud-only
serving rule (`cloud-only-runtime-admission`), `universe_server.main` admits
only a cloud-resolved process for every transport, stdio included. A staged
or installed bundle launched off-cloud refuses to start — exit `78`, refusal
token `platform_not_cloud`, no transport. `LOCAL_ACCEPTANCE.md` records what
the packaging proof therefore does and does not establish.

## Commands

Validate the staged bundle:

```powershell
python packaging/mcpb/build_bundle.py --validate
```

Pack the staged bundle into `packaging/dist/tinyassets-universe-server.mcpb`:

```powershell
python packaging/mcpb/build_bundle.py --pack
```

## Why a staging step exists

The repo's runtime source of truth stays in `tinyassets/`. The staging script
copies that package and its public model lists into the bundle so they resolve
from the same layout as the repository.

The current official MCPB validator still requires `server.type` to be one of
`python`, `node`, or `binary`, so the manifest uses `python` while the actual
launcher command still runs the bundled server through `uv`.

Any time the TinyAssets Server MCP surface changes, rebuild the staged bundle
before validating or packing.
