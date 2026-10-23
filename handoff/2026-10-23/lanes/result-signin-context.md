Draft PR: [#5231](https://github.com/TinyAssets/TinyAssets/pull/5231)  
Head: `3bc142b3c6f1e1e009cfb67a0f2b105f216afa99`

- Centralized sign-in routing, removed the web popup, and preserved native callback destinations.
- Targeted Linux oracle: **43 passed**. Ruff, JS syntax, and commit hooks passed. Worktree clean.
- **Incomplete:** desktop profile matching needs a native integration/release; CLI flows without configurable callbacks still require manual return. Physical-device verification remains undone.
- Item 8.12 is absent from this checkout; no tracker completion claimed.