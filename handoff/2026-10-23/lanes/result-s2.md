Draft PR [#5243](https://github.com/TinyAssets/TinyAssets/pull/5243) is open at head `d3cc1aed019a565b6dd7f851919559636a545209`.

I measured four live agent ingress sites, removed the unused background workflow executor, tightened the start-path checks, set the S2 ceiling to zero, and marked S2 converged. The owner message send path was untouched.

Targeted Linux oracle tests passed **28/28**; Ruff and `git diff --check` passed. No S2 work remains. Unifying the two execution loops remains tracked under C9.