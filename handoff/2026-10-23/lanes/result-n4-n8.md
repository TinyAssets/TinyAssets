Opened [PR #5239](https://github.com/TinyAssets/TinyAssets/pull/5239) at head `d7cb2c434b7dd15cf7518f19f0d67e207647ae7a`.

It contains separate N4 and N8 OpenSpec changes, each with a proposal, design, spec delta and tasks. Both pass strict OpenSpec validation and the commit checks. Ruff found 24 existing errors in unrelated scripts.

Implementation and live proof remain unchecked because this lane is spec only. No Python or JS changed, so targeted Linux-oracle tests and `node --check` were not applicable.