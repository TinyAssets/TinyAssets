Opened [PR #5240](https://github.com/TinyAssets/TinyAssets/pull/5240) with separate OpenSpec changes for N3 realtime sessions and N9 typed media results. The specs place realtime exchanges in the S3 loop, route provider media through the broker, keep media bytes in the owner’s box, and require a receipt per call. No product code changed.

**Head:** `c2c14ce9cf1f74e9117e48e57f66284ad2eb1313`  
**Checks:** Both OpenSpec changes passed strict validation; 21 targeted Linux oracle tests passed; Ruff, diff check, and commit hooks passed.  
**Left undone:** Implementation and live verification, as requested for this spec-only lane.