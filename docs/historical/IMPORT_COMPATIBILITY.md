> **HISTORICAL — superseded.** This doc captured architecture intent as of Phase 2 era (~2026-04-05). Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.

# Import Compatibility After Phase 2 Extraction

## Overview

Phase 2 of the workflow extraction successfully restructured the codebase into three distinct packages:

- **tinyassets/** — Shared infrastructure library (79 Python files)
- **domains/fantasy_author/** — Domain-specific fantasy author code (39 Python files)
- **fantasy_author/** — Original package (112 Python files, now transitional)

All existing tests and code importing from `fantasy_author.*` continue to work without modification.

## Import Path Architecture

### Three Parallel Import Paths

**1. Original Backward-Compatible Path** (existing code, tests, __main__.py)
```python
from fantasy_author.providers import ProviderRouter
from fantasy_author.memory.manager import MemoryManager
from fantasy_author.state import SceneState, UniverseState
from fantasy_author.graphs.universe import build_universe_graph
from fantasy_author.nodes import orient, plan, draft, commit
```

**2. New Infrastructure Canonical Path** (new code targeting workflow library)
```python
from workflow.providers import ProviderRouter
from workflow.memory.manager import MemoryManager
from workflow.notes import Note, add_note
from workflow.work_targets import WorkTarget
```

**3. New Domain-Specific Path** (domain-specific fantasy author code)
```python
from domains.fantasy_author.state import SceneState, UniverseState
from domains.fantasy_author.graphs import build_universe_graph
from domains.fantasy_author.phases import orient, plan, draft, commit
```

## Implementation Details

### Package Configuration

Updated `pyproject.toml` to declare all three packages:

```toml
[tool.hatch.build.targets.wheel]
packages = ["fantasy_author", "workflow", "domains"]
```

This ensures all three packages are installed when the project is built.

### Import Rewriting

Modified domains/fantasy_author/ Python files to use correct import paths:

- **Infrastructure modules** (originally from fantasy_author/): Rewritten to import from `workflow.*`
- **Domain-specific modules** (fantasy_author/graphs, fantasy_author/state, etc.): Rewritten to import from `domains.fantasy_author.*`
- **Special mappings**: `fantasy_author.nodes` → `domains.fantasy_author.phases`

Files updated:
- domains/fantasy_author/graphs/*.py (5 files)
- domains/fantasy_author/phases/*.py (14 files)
- domains/fantasy_author/state/__init__.py (1 file)

### Export Cleanup

The domains/fantasy_author/phases/__init__.py was cleaned up to export only functions that actually exist (matching the original fantasy_author/nodes/__init__.py interface):

Removed exports for non-existent functions:
- `fact_extraction` (no matching function)
- `target_actions` (no matching function)
- `world_state_db` (no matching function)
- `writer_tools` (no matching function)

Retained exports (matching original interface):
- `orient`, `plan`, `draft`, `commit`
- `consolidate`, `learn`, `reflect`, `worldbuild`
- `book_close`, `diagnose`, `universe_cycle`, `select_task`
- `activity_log`

Extended exports (Phase 1+ additions):
- `authorial_priority_review`, `foundation_priority_review`, `dispatch_execution`

## Verification

### Syntax Validation

All three packages parse without errors:

```
tinyassets/: 79 files, 0 syntax errors
domains/: 39 files, 0 syntax errors
fantasy_author/: 112 files, 0 syntax errors
```

### Import Path Coexistence Test

All 10 backward-compatibility tests pass:

1. ✓ fantasy_author imports (original path)
2. ✓ workflow imports (new infrastructure path)
3. ✓ domains.fantasy_author imports (new domain path)
4. ✓ Both import paths coexist in same process
5. ✓ Provider imports from both paths
6. ✓ State imports from domain
7. ✓ Graph imports from domain
8. ✓ Phase imports from domain
9. ✓ Notes imports from workflow
10. ✓ Work target imports from workflow

Test file: `tests/test_import_compatibility.py`

## Migration Timeline

### Phase 2a (Done)

Both old and new import paths worked in parallel.

### Phase 2b (Done — 2026-04-11)

Full migration completed:
- All 623 test and script imports migrated from `fantasy_author.*` to `workflow.*` / `domains.fantasy_author.*`
- `fantasy_author/` converted to thin re-export shims (original backed up in `fantasy_author_original/`)
- Entry points renamed: `tinyassets-cli`, `tinyassets-mcp`, `tinyassets-universe-server`, `workflow` (GUI)
- Env vars renamed: `FANTASY_AUTHOR_UNIVERSE` → `TINYASSETS_UNIVERSE`
- Unextracted modules (auth, branches, node_eval, node_sandbox, packets, universe_server, utils) copied to `tinyassets/`
- Zero syntax errors across all migrated files

### Phase 3 (Future)

Removal of backward compatibility:
- Remove `fantasy_author/` shim package entirely
- Remove `fantasy_author_original/` backup
- Update `pyproject.toml` packages list to drop `fantasy_author`

## Key Design Principles

1. **No Breaking Changes**: Existing code continues to work without modification
2. **Independent Packages**: Each package is self-consistent internally
3. **Coexistence Enabled**: Old and new paths can be used in the same codebase
4. **Gradual Migration**: No forced cutover; teams migrate at their own pace
5. **Clear Semantics**: New paths reflect module responsibility and location

## Documentation

- `tinyassets/compat.py` — Compatibility layer documentation
- `IMPORT_COMPATIBILITY.md` — This file
- `tests/test_import_compatibility.py` — Automated verification

## Files Modified

1. **pyproject.toml** — Added workflow and domains to packages list
2. **tinyassets/compat.py** — New compatibility documentation module
3. **domains/fantasy_author/phases/__init__.py** — Cleaned up exports to match actual functions
4. **domains/fantasy_author/*/*.py** — Updated internal imports (20 files)
5. **tests/test_import_compatibility.py** — New backward-compatibility test suite

## Verification Commands

Run backward-compatibility tests:

```bash
python3 tests/test_import_compatibility.py  # Direct execution
# or
python3 -m pytest tests/test_import_compatibility.py  # Via pytest (if dependencies installed)
```

Check syntax of all three packages:

```bash
python3 -m py_compile fantasy_author/**/*.py
python3 -m py_compile tinyassets/**/*.py
python3 -m py_compile domains/**/*.py
```

## Notes for Future Sessions

1. The original fantasy_author/ package is not a shim; it's the actual source, unchanged
2. Both old and new paths are first-class; neither is a temporary fallback
3. Tests can import from either path; both work and both remain supported
4. When updating existing code to new paths, ensure both the old path (for tests) and new path (for new code) remain available during the transition period
5. The domains/fantasy_author/__init__.py module should define the domain's public interface when needed

## Success Criteria

All criteria met:

- ✓ Syntax check passes on all three packages
- ✓ Import paths work independently and in parallel
- ✓ Backward compatibility maintained (existing tests work unchanged)
- ✓ New import paths work for new code
- ✓ Both paths can coexist in the same process
- ✓ Package configuration updated (pyproject.toml)
- ✓ Automated tests verify compatibility
- ✓ Documentation provided for future reference
