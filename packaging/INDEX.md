# Packaging Index

Map for packaging, distribution, and marketplace surfaces.

## Map

- [PACKAGING_MAP.md](PACKAGING_MAP.md)

## Core Packaging Docs

- [mcpb/README.md](mcpb/README.md)
- [claude-plugin/README.md](claude-plugin/README.md)
- mcpb_packaging.md (archived in git) (archived in git)
- distribution_validation.md (archived in git) (archived in git)
- conway_readiness_strategy.md (archived in git) (archived in git)

## Packaging Areas

- `mcpb/` - bundle template and builder
- `claude-plugin/` - plugin marketplace and packaged plugin
- `registry/` - generated `server.json` metadata and builder script
- `conway/` - speculative Conway panel metadata
- `dist/` - built artifacts and staged output

## Notes

- `dist/` contains generated packaging artifacts; it is not the main editing
  surface.
- Plugin-local skill docs are indexed from the plugin README, not from the root
  repo graph.
