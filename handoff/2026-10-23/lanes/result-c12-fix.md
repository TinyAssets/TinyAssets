Resolved the seven review findings in the C12 spec and pushed them to PR #5241. The spec now defines the S17 guard’s exact patterns and zero ceiling, T10’s `BOX_PUBLISH` copy, one loss-refusing owner conversion, named deletion targets, every export door’s disposition, and the export manifest import path.

**Head:** `1e90de31ff7563ed0ff6c8f138503d7524a13a7b`

**Checks:** OpenSpec validation passed; 26 targeted Linux tests passed; Ruff and `git diff --check` passed.

**Left for implementation:** The S17 guard and cutover proofs remain tasks in the spec. Setting its ceiling to zero now would fail while the legacy writers still exist.