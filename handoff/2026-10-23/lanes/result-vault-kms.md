Draft PR [#5234](https://github.com/TinyAssets/TinyAssets/pull/5234) opened.

Head: `ac91b2d678b56d983286ae4e09ae3230334544ad`

- Rewrote spec/tasks for post-C6 custody; added owner envelope keys, rotation, sealed stores, conversion and backup validation.
- Tests: targeted Linux runs **52 passed** and **86 passed**. Ruff, OpenSpec, compose checks and commit hooks passed.
- **Merge blocker:** existing native subscription credentials still reach boxes.
- Founder work remains: KMS setup, production conversion, retained-backup rewrite and live verification. Full disaster recovery still requires reconnect.

No production or secret access. Worktree clean.