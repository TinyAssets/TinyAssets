Opened [PR #5241](https://github.com/TinyAssets/TinyAssets/pull/5241) at head `58f6b2f6e7c0f54bb9a89d05f8e89530a607efbe`.

The spec defines four post-C6 slices: file authority, derived retrieval, memory consolidation, and one export. Each names the old paths to delete. The S17 registry row now records the planned convergence guard and remains **unbuilt**.

Validation: strict OpenSpec validation passed; the targeted Linux test passed (21 tests); targeted Ruff passed. Repository-wide Ruff found 24 existing issues in untouched files.

Product implementation remains undone because this was a spec-only request.